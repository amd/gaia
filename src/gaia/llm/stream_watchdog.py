# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Notice a model stream that has gone quiet, and end it.

A socket read timeout cannot tell a slow local prefill from a dead stream: both
send nothing. So a stream gets two limits on how long it may stay silent:

* **Before its first output chunk.** A local model is prefilling the prompt,
  which on a long document legitimately takes many minutes, so it gets the
  request budget (``request_budget_seconds()``). A hosted model prefills in
  seconds, so it gets the idle limit.
* **After its first output chunk**, any model gets the idle limit
  (``GAIA_LLM_STREAM_IDLE_TIMEOUT``, 120 s by default).

Silence is measured from the last *byte* received, not the last parsed chunk,
so a server that trickles keep-alives or a slow SSE frame is never cut off.

The stream is read on a worker thread and handed over through a queue, so the
consumer regains control on a stall (or on Stop) even while the socket read is
still blocked. The worker's reads wait in short slices, so once the stream is
abandoned it stops within one slice and closes its own connection.
"""

from __future__ import annotations

import contextvars
import queue
import ssl
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Optional, TypeVar

import httpcore
import httpx

from gaia.llm.retry import (
    StreamCancelledError,
    StreamStalledError,
    current_cancel_check,
    stream_idle_timeout,
)
from gaia.utils import awake_clock

T = TypeVar("T")

#: Longest stretch between two checks of Stop, or of an abandoned reader's flag.
_POLL_INTERVAL = 0.25
#: Seconds the HTTP client's own read timeout runs past the stall limit, so the
#: watchdog, with its actionable message, is always the one that fires.
STREAM_READ_BACKSTOP = 30


@dataclass(frozen=True)
class StallLimits:
    """Seconds of silence a stream may have before it counts as stalled."""

    #: Before the first output chunk.
    first_output: float
    #: After it.
    idle: float
    #: The model is hosted (proxied by Lemonade), not run on this machine.
    remote: bool


def stall_limits(*, remote: bool, request_budget: float) -> StallLimits:
    """The limits for one stream; *request_budget* is the request's timeout."""
    idle = min(stream_idle_timeout(), float(request_budget))
    return StallLimits(
        first_output=idle if remote else float(request_budget),
        idle=idle,
        remote=remote,
    )


class ByteActivity:
    """When a stream last received anything, and how to cut it off.

    Times are read from *clock*, by default :func:`awake_clock.monotonic`, so
    time the PC spends asleep never counts as silence.
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self.clock = clock or awake_clock.monotonic
        self.last = self.clock()
        self._aborted = threading.Event()

    def touch(self) -> None:
        self.last = self.clock()

    @property
    def aborted(self) -> bool:
        return self._aborted.is_set()

    def abort(self) -> None:
        """Make every read on this stream's connections give up.

        Readers wait on the socket in short slices and check this between
        them, because no cross-thread socket call wakes a blocked read on
        every OS (``shutdown`` does not on macOS). The reader then closes its
        own socket, so a descriptor is never freed under a read in flight.
        """
        self._aborted.set()


class _AbandonedStream(httpcore.ReadError):
    def __init__(self) -> None:
        super().__init__("The model stream was abandoned by the stall watchdog.")


class _AbortableStream(httpcore.NetworkStream):
    """A connection whose reads wait in short slices and stop once aborted."""

    def __init__(self, inner: httpcore.NetworkStream, activity: ByteActivity):
        # Deadlines are read on the activity's clock, so they skip sleep too.
        self._inner = inner
        self._activity = activity

    def read(self, max_bytes: int, timeout: Optional[float] = None) -> bytes:
        # Time the PC spends asleep during the read does not use up its timeout.
        awake = self._activity.clock
        deadline = None if timeout is None else awake() + timeout
        while True:
            if self._activity.aborted:
                raise _AbandonedStream()
            wait = _POLL_INTERVAL
            if deadline is not None:
                left = deadline - awake()
                if left <= 0:
                    raise httpcore.ReadTimeout("timed out")
                wait = min(wait, left)
            try:
                data = self._inner.read(max_bytes, wait)
            except httpcore.ReadTimeout:
                continue
            except httpcore.ReadError:
                if self._activity.aborted:
                    raise _AbandonedStream() from None
                raise
            if self._activity.aborted:
                raise _AbandonedStream()
            return data

    def write(self, buffer: bytes, timeout: Optional[float] = None) -> None:
        if self._activity.aborted:
            raise httpcore.WriteError("The model stream was abandoned.")
        self._inner.write(buffer, timeout)

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> httpcore.NetworkStream:
        tls = self._inner.start_tls(ssl_context, server_hostname, timeout)
        return _AbortableStream(tls, self._activity)

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class _AbortableBackend(httpcore.NetworkBackend):
    def __init__(self, inner: httpcore.NetworkBackend, activity: ByteActivity):
        self._inner = inner
        self._activity = activity

    def _connect(self, connect: Callable[[], httpcore.NetworkStream]):
        if self._activity.aborted:
            raise httpcore.ConnectError("The model stream was abandoned.")
        return _AbortableStream(connect(), self._activity)

    def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ) -> httpcore.NetworkStream:
        return self._connect(
            lambda: self._inner.connect_tcp(
                host, port, timeout, local_address, socket_options
            )
        )

    def connect_unix_socket(
        self, path, timeout=None, socket_options=None
    ) -> httpcore.NetworkStream:
        return self._connect(
            lambda: self._inner.connect_unix_socket(path, timeout, socket_options)
        )

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class _ActivityStream(httpx.SyncByteStream):
    def __init__(self, inner: Any, activity: ByteActivity):
        self._inner = inner
        self._activity = activity

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._inner:
            self._activity.touch()
            yield chunk

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


class _ActivityTransport(httpx.BaseTransport):
    """An HTTP transport that reports every byte it receives to *activity*."""

    def __init__(self, inner: httpx.BaseTransport, activity: ByteActivity):
        self._inner = inner
        self._activity = activity

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._inner.handle_request(request)
        self._activity.touch()
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_ActivityStream(response.stream, self._activity),
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._inner.close()


def activity_http_client(activity: ByteActivity) -> httpx.Client:
    """An ``httpx.Client`` for the OpenAI SDK that feeds *activity* and whose
    reads end once *activity* is aborted."""
    transport = httpx.HTTPTransport()
    pool = getattr(transport, "_pool", None)
    if not isinstance(getattr(pool, "_network_backend", None), httpcore.NetworkBackend):
        raise RuntimeError(
            f"httpx {httpx.__version__} / httpcore {httpcore.__version__} no "
            "longer exposes the connection pool's network backend, so a stalled "
            "model stream could not be abandoned. Install the httpx version "
            "pinned in GAIA's setup.py and report this at "
            "https://github.com/amd/gaia/issues."
        )
    pool._network_backend = _AbortableBackend(pool._network_backend, activity)
    return httpx.Client(
        transport=_ActivityTransport(transport, activity),
        follow_redirects=True,
    )


_ITEM, _ERROR, _DONE = "item", "error", "done"


def watch_stream(
    produce: Callable[[], Iterable[T]],
    *,
    limits: StallLimits,
    activity: ByteActivity,
    is_output: Callable[[T], bool],
) -> Iterator[T]:
    """Yield from ``produce()``, raising :class:`StreamStalledError` once it
    has been silent longer than *limits* allow, and
    :class:`StreamCancelledError` when Stop is pressed while it is silent.

    *activity* must be the one fed by the connection ``produce`` reads from;
    every item ``produce`` yields counts as activity too. Silence is measured
    on *activity*'s clock, so time the PC spends asleep is not counted.
    """
    handoff: "queue.Queue[tuple]" = queue.Queue()
    stop = threading.Event()

    def run() -> None:
        try:
            for item in produce():
                activity.touch()
                handoff.put((_ITEM, item))
                if stop.is_set():
                    return
        except BaseException as error:  # noqa: BLE001 - re-raised by the consumer
            handoff.put((_ERROR, error))
        else:
            handoff.put((_DONE, None))

    context = contextvars.copy_context()
    worker = threading.Thread(
        target=context.run, args=(run,), name="gaia-model-stream", daemon=True
    )
    activity.touch()
    worker.start()
    is_cancelled = current_cancel_check()
    seen_output = False
    finished = False
    clock = activity.clock
    try:
        while True:
            try:
                kind, value = handoff.get_nowait()
            except queue.Empty:
                if is_cancelled is not None and is_cancelled():
                    raise StreamCancelledError(
                        "Stopped while waiting for the model to respond."
                    ) from None
                limit = limits.idle if seen_output else limits.first_output
                remaining = limit - (clock() - activity.last)
                if remaining <= 0:
                    raise StreamStalledError(
                        limit=limit,
                        remote=limits.remote,
                        before_output=not seen_output,
                    ) from None
                try:
                    kind, value = handoff.get(timeout=min(remaining, _POLL_INTERVAL))
                except queue.Empty:
                    continue
            if kind == _ITEM:
                if not seen_output and is_output(value):
                    seen_output = True
                yield value
            elif kind == _ERROR:
                finished = True
                raise value
            else:
                finished = True
                return
    finally:
        stop.set()
        if not finished and worker.is_alive():
            activity.abort()
