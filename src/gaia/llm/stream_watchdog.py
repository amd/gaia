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
still blocked. The worker's socket is then closed to unblock it.
"""

from __future__ import annotations

import contextvars
import logging
import queue
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, List, TypeVar

import httpx

from gaia.llm.retry import (
    StreamCancelledError,
    StreamStalledError,
    current_cancel_check,
    stream_idle_timeout,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Longest stretch between two checks of the Stop hook while a stream is quiet.
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
    """When a stream last received anything, and how to cut it off."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.last = clock()
        self._responses: List[httpx.Response] = []
        self._lock = threading.Lock()

    def touch(self) -> None:
        self.last = self._clock()

    def track(self, response: httpx.Response) -> None:
        with self._lock:
            self._responses.append(response)

    def abort(self) -> None:
        """Close the tracked connections so a read blocked on them returns.

        ``shutdown`` wakes the reader on Linux and macOS, ``close`` on Windows.
        """
        with self._lock:
            responses = list(self._responses)
        for response in responses:
            stream = response.extensions.get("network_stream")
            get_info = getattr(stream, "get_extra_info", None)
            sock = get_info("socket") if callable(get_info) else None
            if not isinstance(sock, socket.socket):
                logger.warning(
                    "Could not reach the socket of a stalled model stream; its "
                    "reader thread ends when the read timeout fires."
                )
                continue
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError as e:
                logger.debug("Stalled stream socket already shut down: %s", e)
            sock.close()


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
        self._activity.track(response)
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_ActivityStream(response.stream, self._activity),
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._inner.close()


def activity_http_client(activity: ByteActivity) -> httpx.Client:
    """An ``httpx.Client`` for the OpenAI SDK that feeds *activity*."""
    return httpx.Client(
        transport=_ActivityTransport(httpx.HTTPTransport(), activity),
        follow_redirects=True,
    )


_ITEM, _ERROR, _DONE = "item", "error", "done"


def watch_stream(
    produce: Callable[[], Iterable[T]],
    *,
    limits: StallLimits,
    activity: ByteActivity,
    is_output: Callable[[T], bool],
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[T]:
    """Yield from ``produce()``, raising :class:`StreamStalledError` once it
    has been silent longer than *limits* allow, and
    :class:`StreamCancelledError` when Stop is pressed while it is silent.

    *activity* must be the one fed by the connection ``produce`` reads from;
    every item ``produce`` yields counts as activity too.
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
