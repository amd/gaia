# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reply stream from an NPU (FastFlowLM) model is never cut off mid-reply.

Lemonade's watchdog restarts FastFlowLM when a client hangs up on a stream it
is still sending, so on the NPU a stream the caller leaves early is read to its
end instead, and a call cancelled while queued for the model is never sent.
A GPU stream is still cut off at once.
"""

import json
import select
import socket
import threading
import time
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from gaia.chat.sdk import AgentSDK, LLMCallCancelled
from gaia.daemon.broker_client import LeaseWaitCancelled, lease_cancel_scope
from gaia.llm import lemonade_client, stream_watchdog
from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.retry import StreamStalledError
from gaia.llm.stream_watchdog import ByteActivity, StallLimits, watch_stream

pytestmark = pytest.mark.allow_network

NPU_MODEL = "gemma4-it-e4b-FLM"
GPU_MODEL = "Gemma-4-E4B-it-GGUF"
FAR = StallLimits(first_output=60.0, idle=60.0, remote=False)
FRAMES = 20
#: Seconds between two frames: long enough for an early hang-up to be seen.
FRAME_GAP = 0.05


def _wait_for(condition, within=5.0):
    end = time.monotonic() + within
    while not condition() and time.monotonic() < end:
        time.sleep(0.02)
    return condition()


class _Produce:
    """Yields *count* items, remembering how many were read and whether it ended."""

    def __init__(self, count=FRAMES):
        self.count = count
        self.read = 0
        self.ended = threading.Event()

    def __call__(self):
        try:
            for i in range(self.count):
                time.sleep(0.01)
                self.read += 1
                yield i
        finally:
            self.ended.set()


def _leave_after_first(stream):
    for _ in stream:
        break
    stream.close()


def test_a_drained_stream_left_early_is_read_to_its_end():
    produce, activity = _Produce(), ByteActivity()
    _leave_after_first(
        watch_stream(
            produce,
            limits=FAR,
            activity=activity,
            is_output=lambda _: True,
            drain_on_abandon=True,
        )
    )
    assert produce.ended.wait(5)
    assert produce.read == FRAMES
    assert not activity.aborted


def test_an_undrained_stream_left_early_is_cut_off():
    produce, activity = _Produce(), ByteActivity()
    _leave_after_first(
        watch_stream(produce, limits=FAR, activity=activity, is_output=lambda _: True)
    )
    assert activity.aborted
    assert produce.ended.wait(5)
    assert produce.read < FRAMES


def test_a_drain_that_outlives_its_limit_is_cut_off(monkeypatch):
    """A long reply must not hold the model for minutes after Stop."""
    monkeypatch.setattr(stream_watchdog, "DRAIN_LIMIT_S", 0.3)
    activity = ByteActivity()

    def produce():
        while not activity.aborted:
            time.sleep(0.01)
            yield "token"

    _leave_after_first(
        watch_stream(
            produce,
            limits=FAR,
            activity=activity,
            is_output=lambda _: True,
            drain_on_abandon=True,
        )
    )
    assert not activity.aborted
    assert _wait_for(lambda: activity.aborted)


def test_a_drain_that_ends_early_cancels_its_cutoff_timer(monkeypatch):
    """An old drain timer must not abort an already-finished later request."""
    monkeypatch.setattr(stream_watchdog, "DRAIN_LIMIT_S", 0.1)
    produce, activity = _Produce(count=2), ByteActivity()

    _leave_after_first(
        watch_stream(
            produce,
            limits=FAR,
            activity=activity,
            is_output=lambda _: True,
            drain_on_abandon=True,
        )
    )

    assert produce.ended.wait(5)
    time.sleep(0.15)
    assert not activity.aborted


def test_a_drain_honors_the_callers_model_wait_limit():
    """A title/background call can time out behind a stopped NPU reply."""
    from gaia.daemon import broker_client

    end = lemonade_client._begin_drain(NPU_MODEL)
    try:
        with broker_client.lease_wait_limit(0.05):
            with pytest.raises(broker_client.BrokerUnavailableError) as error:
                lemonade_client._wait_for_drains(NPU_MODEL)
    finally:
        end()

    assert error.value.status_code == 504
    assert "finishing" in str(error.value)


def test_a_stalled_stream_is_cut_off_even_when_drained():
    release = threading.Event()

    def produce():
        yield "first"
        release.wait(5)

    activity = ByteActivity()
    try:
        with pytest.raises(StreamStalledError):
            list(
                watch_stream(
                    produce,
                    limits=StallLimits(first_output=0.3, idle=0.3, remote=False),
                    activity=activity,
                    is_output=lambda _: True,
                    drain_on_abandon=True,
                )
            )
        assert activity.aborted
    finally:
        release.set()


class _SseServer:
    """Serves one chat-completions stream of FRAMES frames, a frame at a time.

    ``hung_up`` is set when the client closed before the last frame was sent,
    ``completed`` once every frame and the end of the stream were sent.
    """

    def __init__(self):
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.hung_up = threading.Event()
        self.completed = threading.Event()
        #: Set just before the end of the stream is sent.
        self.sending_end = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    @staticmethod
    def _frame(payload) -> bytes:
        data = f"data: {payload}\n\n".encode()
        return f"{len(data):x}\r\n".encode() + data + b"\r\n"

    @staticmethod
    def _closed(conn) -> bool:
        readable, _, _ = select.select([conn], [], [], 0)
        if not readable:
            return False
        try:
            return conn.recv(65536) == b""
        except OSError:
            return True

    def _serve(self):
        conn, _ = self.listener.accept()
        with conn:
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            for i in range(FRAMES):
                last = i == FRAMES - 1
                chunk = {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "m",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": f"t{i} "},
                            "finish_reason": "stop" if last else None,
                        }
                    ],
                }
                try:
                    if self._closed(conn):
                        self.hung_up.set()
                        return
                    conn.sendall(self._frame(json.dumps(chunk)))
                except OSError:
                    self.hung_up.set()
                    return
                time.sleep(FRAME_GAP)
            self.sending_end.set()
            try:
                conn.sendall(self._frame("[DONE]") + b"0\r\n\r\n")
            except OSError:
                self.hung_up.set()
                return
            self.completed.set()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.listener.close()


def _stream_and_leave_early(model, after_close=lambda: None):
    with _SseServer() as server:
        client = LemonadeClient(
            base_url=f"http://127.0.0.1:{server.port}/api/v1", verbose=False
        )
        with (
            patch.object(client, "_ensure_model_loaded"),
            patch.object(client, "_model_slot_lease", return_value=nullcontext()),
        ):
            stream = client._stream_chat_completions_with_openai(
                model=model, messages=[{"role": "user", "content": "hi"}]
            )
            first = next(stream)
            assert first["choices"][0]["delta"]["content"] == "t0 "
            stream.close()
            after_close()
        _wait_for(lambda: server.completed.is_set() or server.hung_up.is_set())
        return server


def test_an_npu_stream_left_early_is_read_to_its_end_by_the_client():
    server = _stream_and_leave_early(NPU_MODEL)
    assert server.completed.is_set(), "the client hung up on the NPU mid-reply"
    assert not server.hung_up.is_set()


def test_the_model_stays_busy_in_this_process_until_the_drain_ends():
    """The next request waits for the drained reply instead of reaching a
    server that is still generating it."""
    entered = {}

    def next_request():
        with lemonade_client._holding_local_request_lock(NPU_MODEL):
            entered["drain_done"] = server_box[0].sending_end.is_set()

    server_box = []
    real = _SseServer.__enter__

    def remember(self):
        server_box.append(self)
        return real(self)

    with patch.object(_SseServer, "__enter__", remember):
        _stream_and_leave_early(NPU_MODEL, after_close=next_request)
    assert entered == {"drain_done": True}


def test_a_gpu_stream_left_early_is_still_cut_off():
    server = _stream_and_leave_early(GPU_MODEL)
    assert server.hung_up.is_set(), "a GPU stream was read to its end"


def test_a_call_cancelled_while_queued_for_the_model_is_never_sent():
    """Memory extraction cancelled by a user turn stops waiting for the model
    instead of sending its prompt and hanging up on the reply."""
    sent = []

    def stream(*_args, **_kwargs):
        with lemonade_client._holding_local_request_lock(NPU_MODEL):
            sent.append(True)
            yield None

    held, release = threading.Event(), threading.Event()

    def other_request():
        with lemonade_client._holding_local_request_lock(NPU_MODEL):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=other_request, daemon=True)
    holder.start()
    assert held.wait(5)
    try:
        sdk = AgentSDK.__new__(AgentSDK)
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        with patch.object(sdk, "send_messages_stream", side_effect=stream):
            with pytest.raises(LLMCallCancelled):
                sdk._send_messages_cancellable(
                    [{"role": "user", "content": "hi"}], None, None, cancel
                )
    finally:
        release.set()
        holder.join(5)
    assert not sent


def test_a_call_cancelled_while_the_model_loads_is_never_sent():
    cancel = threading.Event()
    client = LemonadeClient(base_url="http://127.0.0.1:9/api/v1", verbose=False)
    with (
        patch.object(
            client, "_ensure_model_loaded", side_effect=lambda *a, **k: cancel.set()
        ),
        patch.object(client, "_model_slot_lease", return_value=nullcontext()),
        patch.object(lemonade_client, "OpenAI") as openai_client,
        lease_cancel_scope(cancel),
    ):
        stream = client._stream_chat_completions_with_openai(
            model=NPU_MODEL, messages=[{"role": "user", "content": "hi"}]
        )
        with pytest.raises(LeaseWaitCancelled):
            next(stream)
    openai_client.assert_not_called()
