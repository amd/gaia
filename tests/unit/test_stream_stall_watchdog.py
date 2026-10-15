# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A model stream that goes silent is ended and retried, not waited on for the
whole request budget.

Real sockets and the real OpenAI SDK against a tiny server that stalls on
purpose: after its headers, mid-stream, on every attempt, during a Stop. The
other side is what must NOT be cut off: a server trickling bytes just under the
idle limit, and a local model prefilling for longer than the idle limit.
"""

import json
import threading
import time
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gaia.agents.base.agent import Agent
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.retry import (
    ModelCallRetrier,
    RetryPolicy,
    RetryState,
    StreamStalledError,
    attach_retry_state,
    classify_exception,
    model_retry_notices,
    retry_state_of,
    stream_idle_timeout,
)
from gaia.llm.stream_watchdog import stall_limits

pytestmark = pytest.mark.allow_network

MODEL = "Fake-Model-GGUF"
IDLE = 2.0
#: How long a stalling handler holds its connection open unless released.
HOLD = 30.0


def _chunk(content=None, role=None, finish=None):
    delta = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _frames(*chunks, done=True):
    out = [f"data: {json.dumps(c)}\n\n".encode() for c in chunks]
    if done:
        out.append(b"data: [DONE]\n\n")
    return out


GOOD = _frames(_chunk(role="assistant"), _chunk("Hel"), _chunk("lo", finish="stop"))


class StallServer:
    """Runs one behaviour per request. A behaviour is a function of
    ``(send_headers, send, finish, hold)``."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = 0
        self.release = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                server.requests += 1
                behaviour = server.script.pop(0)

                def send_headers():
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.flush()

                def send(payload: bytes):
                    self.wfile.write(
                        f"{len(payload):x}\r\n".encode() + payload + b"\r\n"
                    )
                    self.wfile.flush()

                def finish():
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()

                def hold():
                    server.release.wait(HOLD)

                self.close_connection = True
                try:
                    behaviour(send_headers, send, finish, hold)
                except (ConnectionError, OSError):
                    pass  # The client hung up on a stall: what we want.

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def good(send_headers, send, finish, hold):
    send_headers()
    for frame in GOOD:
        send(frame)
    finish()


def stall_after_headers(send_headers, send, finish, hold):
    send_headers()
    hold()


def stall_mid_stream(send_headers, send, finish, hold):
    send_headers()
    for frame in _frames(_chunk(role="assistant"), _chunk("Hel"), done=False):
        send(frame)
    hold()


def trickle_then_answer(send_headers, send, finish, hold):
    """Four gaps of 75% of the idle limit (3x the limit in all) before the
    first token, each ended by one byte of an SSE comment."""
    send_headers()
    for byte in (b":", b"\n", b":", b"\n"):
        time.sleep(IDLE * 0.75)
        send(byte)
    for frame in GOOD:
        send(frame)
    finish()


def prefill_after_headers(send_headers, send, finish, hold):
    """Headers at once, then 2x the idle limit of silence, then the answer."""
    send_headers()
    time.sleep(IDLE * 2)
    for frame in GOOD:
        send(frame)
    finish()


def prefill_before_headers(send_headers, send, finish, hold):
    """llama.cpp's shape: nothing at all, headers included, until the first
    token."""
    time.sleep(IDLE * 2)
    good(send_headers, send, finish, hold)


@pytest.fixture
def make_client(monkeypatch):
    for name in (
        "GAIA_LLM_RETRY_ATTEMPTS",
        "GAIA_LLM_RETRY_BASE_DELAY",
        "GAIA_LLM_RETRY_MAX_DELAY",
        "GAIA_LLM_RETRY_DEADLINE",
        "GAIA_LEMONADE_REQUEST_BUDGET",
        "LEMONADE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GAIA_LLM_STREAM_IDLE_TIMEOUT", str(IDLE))

    def make(server, *, remote):
        client = LemonadeClient(host="127.0.0.1", port=server.port, verbose=False)
        client._ensure_model_loaded = lambda *a, **k: None
        client._model_slot_lease = lambda *a, **k: nullcontext()
        client.cloud_model_provider = (
            (lambda *a, **k: "amd") if remote else (lambda *a, **k: None)
        )
        client.sleeps = []
        client._retry_sleep = client.sleeps.append
        return client

    return make


def _run(client, timeout=600):
    """Stream one reply; return (text, seconds taken)."""
    started = time.monotonic()
    chunks = list(
        client.chat_completions(
            model=MODEL,
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
            timeout=timeout,
        )
    )
    text = "".join(
        (c["choices"][0]["delta"].get("content") or "") for c in chunks if c["choices"]
    )
    return text, time.monotonic() - started


def _no_reader_left(deadline=5.0):
    """The stalled attempt's reader thread must not outlive the stall."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if not any(t.name == "gaia-model-stream" for t in threading.enumerate()):
            return True
        time.sleep(0.05)
    return False


# ── the stall is noticed and retried ─────────────────────────────────────


def test_hosted_stall_after_headers_is_retried(make_client):
    """The reported hang: 200 OK, then nothing. The second attempt answers."""
    with StallServer([stall_after_headers, good]) as server:
        client = make_client(server, remote=True)
        text, took = _run(client)
    assert text == "Hello"
    assert server.requests == 2
    assert client.last_request_retries == 1
    assert took < IDLE + 6, f"stall took {took:.1f}s to notice"
    assert _no_reader_left()


def test_stall_on_every_attempt_fails_loudly_in_bounded_time(make_client):
    """Never more than two stalled attempts, whatever the retry budget, and a
    message that says what happened and what to do."""
    with StallServer([stall_after_headers] * 5) as server:
        client = make_client(server, remote=True)
        with pytest.raises(LemonadeClientError) as raised:
            _run(client)
        took_requests = server.requests
    message = str(raised.value)
    assert took_requests == 2
    assert message.startswith("The hosted model accepted the request but sent nothing")
    assert "tried 2 times" in message
    assert "GAIA_LLM_STREAM_IDLE_TIMEOUT" in message
    # Not the misleading "no error code" wording the hang used to end with.
    assert "error code" not in message
    state = retry_state_of(raised.value)
    assert state is not None and state.exhausted
    assert state.last_reason == "stream stalled"
    assert _no_reader_left()


def test_bounded_time_counts_real_seconds(make_client):
    with StallServer([stall_after_headers] * 5) as server:
        client = make_client(server, remote=True)
        started = time.monotonic()
        with pytest.raises(LemonadeClientError):
            _run(client)
        took = time.monotonic() - started
    # Two idle limits plus scheduling slack; the request budget is 600 s.
    assert took < 2 * IDLE + 8, f"gave up after {took:.1f}s"


@pytest.mark.parametrize("remote", [True, False], ids=["hosted", "local"])
def test_stall_mid_stream_ends_without_a_retry(make_client, remote):
    """After the first token a retry would duplicate output: end loudly."""
    with StallServer([stall_mid_stream, good]) as server:
        client = make_client(server, remote=remote)
        started = time.monotonic()
        with pytest.raises(
            LemonadeClientError, match="stopped sending partway"
        ) as raised:
            _run(client)
        took = time.monotonic() - started
        assert server.requests == 1
    assert took < IDLE + 6
    state = retry_state_of(raised.value)
    assert state is not None and state.output_delivered
    assert _no_reader_left()


# ── what must NOT be cut off ─────────────────────────────────────────────


def test_trickle_just_under_the_idle_limit_is_not_killed(make_client):
    """Silence is measured from the last byte, not the last parsed chunk: an
    SSE comment trickling in keeps the stream alive."""
    with StallServer([trickle_then_answer]) as server:
        client = make_client(server, remote=True)
        text, took = _run(client)
    assert text == "Hello"
    assert server.requests == 1
    assert took > 2 * IDLE, "the trickle must have outlasted one idle limit"


@pytest.mark.parametrize("behaviour", [prefill_after_headers, prefill_before_headers])
def test_slow_local_prefill_is_not_killed(make_client, behaviour):
    """Before its first token a local model gets the whole request budget, not
    the idle limit: a long prompt legitimately sends nothing for a while."""
    with StallServer([behaviour]) as server:
        client = make_client(server, remote=False)
        text, took = _run(client, timeout=IDLE * 5)
    assert text == "Hello"
    assert server.requests == 1
    assert took > IDLE * 2


def test_local_prefill_past_its_budget_is_not_retried(make_client):
    """The same request would prefill just as long again."""
    with StallServer([stall_after_headers, good]) as server:
        client = make_client(server, remote=False)
        with pytest.raises(LemonadeClientError) as raised:
            _run(client, timeout=IDLE * 1.5)
        assert server.requests == 1
    message = str(raised.value)
    assert message.startswith("The local model produced no output within 3s")
    assert "GAIA_LEMONADE_REQUEST_BUDGET" in message


# ── Stop ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("remote", [True, False], ids=["hosted", "local"])
def test_stop_during_a_stall_ends_the_stream_promptly(make_client, monkeypatch, remote):
    """Stop must not wait out the idle limit (or a local prefill budget)."""
    monkeypatch.setenv("GAIA_LLM_STREAM_IDLE_TIMEOUT", "60")
    stop_at = time.monotonic() + 1.0
    with StallServer([stall_after_headers, good]) as server:
        client = make_client(server, remote=remote)
        started = time.monotonic()
        with model_retry_notices(
            lambda notice: None, lambda: time.monotonic() > stop_at
        ):
            with pytest.raises(LemonadeClientError) as raised:
                _run(client, timeout=600)
        took = time.monotonic() - started
        assert server.requests == 1, "a Stop must never be retried"
    assert took < 5, f"Stop took {took:.1f}s to end the stream"
    assert retry_state_of(raised.value) is None
    assert _no_reader_left()


# ── the retry policy around a stall ──────────────────────────────────────


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_a_stalled_attempt_does_not_spend_the_retry_deadline():
    """The reported log line: "giving up after 1 attempts, retry budget of 120s
    spent". A stall longer than the deadline must still get its retry."""
    clock = FakeClock()
    attempts = []

    def open_stream():
        attempts.append(clock.now)
        if len(attempts) == 1:
            clock.now += 500.0
            raise StreamStalledError(limit=500.0, remote=True, before_output=False)
        return iter([{"choices": [{"delta": {"content": "ok"}}]}])

    retrier = ModelCallRetrier(
        RetryPolicy(deadline=120.0), sleep=lambda s: None, clock=clock
    )
    items = list(retrier.stream(open_stream, is_output=lambda item: True))
    assert len(attempts) == 2
    assert items == [{"choices": [{"delta": {"content": "ok"}}]}]


def test_stall_attempts_are_capped_below_max_attempts():
    clock = FakeClock()
    calls = []

    def open_stream():
        calls.append(1)
        clock.now += 10.0
        raise StreamStalledError(limit=10.0, remote=True, before_output=True)

    retrier = ModelCallRetrier(
        RetryPolicy(max_attempts=10, max_stall_attempts=3),
        sleep=lambda s: None,
        clock=clock,
    )
    with pytest.raises(StreamStalledError) as raised:
        list(retrier.stream(open_stream, is_output=lambda item: True))
    assert len(calls) == 3
    assert retry_state_of(raised.value).last_reason == "stream stalled"


def test_local_prefill_stall_is_not_retryable():
    error = StreamStalledError(limit=2048, remote=False, before_output=True)
    assert classify_exception(error) is None
    assert classify_exception(
        StreamStalledError(limit=120, remote=False, before_output=False)
    ) == ("stream stalled")


def test_agent_does_not_add_step_retries_on_top_of_a_stall():
    """The client already retried; a step retry would multiply the wait."""
    state = RetryState(attempts=2, last_reason="stream stalled", exhausted=True)
    error = attach_retry_state(LemonadeClientError("stalled"), state)
    assert Agent._transient_model_failure(error) is None


# ── limits ───────────────────────────────────────────────────────────────


def test_limits_split_local_prefill_from_hosted(monkeypatch):
    monkeypatch.delenv("GAIA_LLM_STREAM_IDLE_TIMEOUT", raising=False)
    hosted = stall_limits(remote=True, request_budget=2048)
    local = stall_limits(remote=False, request_budget=2048)
    assert (hosted.first_output, hosted.idle) == (120.0, 120.0)
    assert (local.first_output, local.idle) == (2048.0, 120.0)
    # An explicit short timeout still wins.
    assert stall_limits(remote=True, request_budget=30).idle == 30.0


@pytest.mark.parametrize("bad", ["0", "-5", "soon"])
def test_a_bad_idle_setting_is_an_error_not_a_default(monkeypatch, bad):
    monkeypatch.setenv("GAIA_LLM_STREAM_IDLE_TIMEOUT", bad)
    with pytest.raises(ValueError, match="GAIA_LLM_STREAM_IDLE_TIMEOUT"):
        stream_idle_timeout()
