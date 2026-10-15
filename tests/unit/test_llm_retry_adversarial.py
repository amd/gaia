# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hostile inputs for model-call retry: absurd or malformed ``Retry-After``,
a 429 storm, a stream that dies after real tokens, permanent statuses dressed
up as transient ones, a connection that closes before the first byte, a run
whose every step is flaky, and Stop pressed during a backoff wait."""

import email.utils
import json
import random
import threading
import time
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest
import requests

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.retry import (
    ModelCallRetrier,
    RetryPolicy,
    RetryState,
    backoff_delay,
    model_retry_notices,
    parse_retry_after,
    retry_state_of,
    tag_http_failure,
)
from tests.unit.agents import test_model_unreachable_retry as _base
from tests.unit.agents.test_model_unreachable_retry import (
    _ANSWER,
    _TOOL_RUNS,
    _make_agent,
    _script,
    _tool_call,
    _transient,
)

# The shared fixture, bound by name so pytest finds it in this module.
clean_registry = _base.clean_registry

POLICY = RetryPolicy(max_attempts=5, base_delay=2.0, max_delay=30.0, deadline=120.0)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _retrier(clock, policy=POLICY):
    return ModelCallRetrier(
        policy, sleep=clock.sleep, clock=clock, rng=random.Random(7)
    )


def _status_error(status, headers=None, body=None):
    request = httpx.Request("POST", "http://test/v1/chat/completions")
    response = httpx.Response(status, request=request, headers=headers or {})
    cls = {
        400: openai.BadRequestError,
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
        404: openai.NotFoundError,
        429: openai.RateLimitError,
    }.get(status, openai.InternalServerError)
    return cls("upstream said no", response=response, body=body)


def _always(error_factory):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise error_factory()

    return fn, calls


# ── Retry-After that a server should never send ──────────────────────────


@pytest.mark.parametrize(
    "value, expected_wait",
    [
        ("1000000000", 30.0),  # 1e9 s: capped, never a 31-year sleep
        ("1e9", 30.0),
        ("inf", 30.0),
        ("-5", 0.0),
        ("-inf", 0.0),
        ("nan", 0.0),
    ],
)
def test_absurd_retry_after_is_clamped_to_the_policy(value, expected_wait):
    clock = FakeClock()
    fn, calls = _always(lambda: _status_error(429, headers={"retry-after": value}))
    with pytest.raises(openai.RateLimitError):
        _retrier(clock, RetryPolicy(max_attempts=2, deadline=1e12)).call(fn)
    assert calls["n"] == 2
    assert sum(clock.sleeps) == expected_wait


@pytest.mark.parametrize("value", ["soon", "0x10", "", "Mon, 99 Foo 2015 99:99 GMT"])
def test_malformed_retry_after_falls_back_to_jitter(value):
    assert parse_retry_after({"retry-after": value}) is None
    clock = FakeClock()
    fn, _ = _always(lambda: _status_error(429, headers={"retry-after": value}))
    with pytest.raises(openai.RateLimitError):
        _retrier(clock, RetryPolicy(max_attempts=2)).call(fn)
    assert 0.0 <= clock.sleeps[0] <= 2.0


def test_retry_after_http_date_in_the_future_is_honoured():
    when = email.utils.formatdate(time.time() + 20, usegmt=True)
    assert parse_retry_after({"Retry-After": when}) == pytest.approx(20, abs=2)


def test_retry_after_http_date_with_unknown_zone_is_read_as_utc():
    """``-0000`` parses to a naive datetime; reading it as local time would
    skew the wait by the machine's UTC offset (hours)."""
    when = email.utils.formatdate(time.time() + 20)  # "... -0000"
    assert when.endswith("-0000")
    assert parse_retry_after({"Retry-After": when}) == pytest.approx(20, abs=2)


def test_retry_after_ms_garbage_falls_through_to_seconds_header():
    assert parse_retry_after({"retry-after-ms": "junk", "retry-after": "5"}) == 5.0
    assert parse_retry_after({"retry-after-ms": "-100"}) == 0.0


def test_retry_after_never_exceeds_max_delay_even_with_huge_base():
    policy = RetryPolicy(max_attempts=50, base_delay=1e9, max_delay=30.0)
    rng = random.Random(1)
    assert all(0 <= backoff_delay(i, policy, rng) <= 30.0 for i in range(60))


# ── 429 storm ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("retry_after", [None, "1e9", "29"])
def test_429_storm_gives_up_within_the_deadline(retry_after):
    clock = FakeClock()
    headers = {"retry-after": retry_after} if retry_after else {}
    fn, calls = _always(lambda: _status_error(429, headers=headers))
    with pytest.raises(openai.RateLimitError) as raised:
        _retrier(clock).call(fn)
    assert calls["n"] <= POLICY.max_attempts
    assert clock.now <= POLICY.deadline
    state = retry_state_of(raised.value)
    assert state is not None and state.exhausted
    assert state.last_reason == "HTTP 429"


def test_429_storm_ends_the_run_with_its_work_kept(clean_registry):
    """Every request 429s: the client gives up within its deadline each time,
    the step retries run out, and the turn ends with the tool work kept."""
    agent = _make_agent(streaming=False)
    client_sleeps = []
    clock = {"now": 0.0}

    def sleep(seconds):
        client_sleeps.append(seconds)
        clock["now"] += seconds

    retrier = ModelCallRetrier(
        POLICY, sleep=sleep, clock=lambda: clock["now"], rng=random.Random(3)
    )
    replies = [_tool_call(1)]
    asked = {"n": 0}

    def send(*_, **__):
        asked["n"] += 1
        if replies:
            return SimpleNamespace(text=replies.pop(0), stats={})
        clock["now"] = 0.0  # every request gets its own deadline

        def storm():
            raise tag_http_failure(
                LemonadeClientError("Cloud rate limit reached (HTTP 429)."),
                429,
                "",
                {"Retry-After": "1e9"},
            )

        return SimpleNamespace(text=retrier.call(storm), stats={})

    agent.chat = MagicMock()
    agent.chat.get_stats = MagicMock(return_value={})
    agent.chat.send_messages = MagicMock(side_effect=send)

    result = agent.process_query("Check the ledger", max_steps=10)
    assert result["status"] == "failed"
    assert result["model_unreachable"] is True
    assert "I stopped at step 2" in result["result"]
    assert "(read_ledger_for_retry_test 1x)" in result["result"]
    assert _TOOL_RUNS == [1]
    # 1 good call + the first failing step + 2 step retries.
    assert asked["n"] == 4
    assert all(s <= POLICY.max_delay for s in client_sleeps)
    assert sum(client_sleeps) <= 3 * POLICY.deadline


# ── Permanent statuses dressed as transient ──────────────────────────────


@pytest.mark.parametrize("status", [400, 401, 403, 404])
@pytest.mark.parametrize(
    "body",
    [
        None,
        {"message": "server is overloaded, upstream connect error"},
        {"message": "connection reset by peer"},
    ],
)
def test_client_errors_are_never_retried_even_with_retry_after(status, body):
    clock = FakeClock()
    fn, calls = _always(
        lambda: _status_error(status, headers={"retry-after": "1"}, body=body)
    )
    with pytest.raises(openai.APIStatusError) as raised:
        _retrier(clock).call(fn)
    assert calls["n"] == 1
    assert clock.sleeps == []
    assert retry_state_of(raised.value) is None


# ── Mid-stream drops ─────────────────────────────────────────────────────


def _chunk(text=None, role=None):
    delta = {"role": role} if role else {}
    if text:
        delta["content"] = text
    return {"choices": [{"delta": delta}]}


def _has_text(chunk):
    return bool(chunk["choices"][0]["delta"].get("content"))


@pytest.mark.parametrize("tokens_before_drop", [1, 2, 50])
def test_stream_drop_after_tokens_never_replays_them(tokens_before_drop):
    clock = FakeClock()
    opened = {"n": 0}
    tokens = [f"t{i} " for i in range(tokens_before_drop)]

    def open_stream():
        opened["n"] += 1

        def gen():
            yield _chunk(role="assistant")
            for t in tokens:
                yield _chunk(t)
            raise httpx.RemoteProtocolError("peer closed")

        return gen()

    got = []
    state = RetryState()
    with pytest.raises(httpx.RemoteProtocolError) as raised:
        for c in _retrier(clock).stream(open_stream, is_output=_has_text, state=state):
            got.append(c["choices"][0]["delta"].get("content") or "")
    assert opened["n"] == 1
    assert clock.sleeps == []
    assert "".join(got) == "".join(tokens)
    assert state.output_delivered
    assert retry_state_of(raised.value).output_delivered


def test_stream_with_keepalives_then_drop_before_text_is_retried_once_cleanly():
    """Many role/keep-alive frames, then a drop: none of them count as output,
    so the retry delivers the role frame once, not once per attempt."""
    clock = FakeClock()
    attempts = [
        [_chunk(role="assistant")] * 20 + [httpx.ReadError("reset")],
        [_chunk(role="assistant"), _chunk("Hi")],
    ]
    opened = {"n": 0}

    def open_stream():
        items = attempts[opened["n"]]
        opened["n"] += 1

        def gen():
            for item in items:
                if isinstance(item, BaseException):
                    raise item
                yield item

        return gen()

    got = list(_retrier(clock).stream(open_stream, is_output=_has_text))
    assert opened["n"] == 2
    assert got == [_chunk(role="assistant"), _chunk("Hi")]


def test_agent_shows_cut_off_text_once_and_answers_once(clean_registry):
    """A stream that dies after 50 tokens: the step is asked again and the
    answer contains the new reply exactly once, with no replay of the cut text."""
    agent = _make_agent(streaming=True)
    from gaia.llm.retry import attach_retry_state

    shown = []
    agent.console.print_streaming_text = lambda text, end_of_stream=False: shown.append(
        text
    )
    calls = []

    def _stream(*_, **__):
        calls.append(1)
        if len(calls) == 1:
            for i in range(50):
                yield SimpleNamespace(text=f"w{i} ", is_complete=False, stats=None)
            raise attach_retry_state(
                LemonadeClientError("Streaming request failed: peer closed"),
                RetryState(
                    attempts=1,
                    last_reason="connection dropped",
                    output_delivered=True,
                    exhausted=True,
                ),
            )
        yield SimpleNamespace(text=_ANSWER, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    agent.chat = MagicMock()
    agent.chat.get_stats = MagicMock(return_value={})
    agent.chat.send_messages_stream = MagicMock(side_effect=_stream)

    result = agent.process_query("Check the ledger", max_steps=5)
    assert len(calls) == 2
    assert result["result"].count(_ANSWER) == 1
    assert "w0 " not in result["result"]
    # Each cut-off token was streamed to the user exactly once.
    assert shown.count("w0 ") == 1 and shown.count("w49 ") == 1


# ── Side-effecting tools across step retries ─────────────────────────────


def test_flaky_every_step_never_reruns_any_tool(clean_registry):
    agent = _make_agent(streaming=False)
    sent = _script(
        agent,
        _transient(),
        _tool_call(1),
        _transient(),
        _transient(),
        _tool_call(2),
        _transient(reason="connection dropped"),
        _tool_call(3),
        _transient(reason="HTTP 502"),
        _transient(reason="HTTP 502"),
        _ANSWER,
    )
    result = agent.process_query("Check the ledger", max_steps=20)
    assert _ANSWER in result["result"]
    assert _TOOL_RUNS == [1, 2, 3]
    assert len(sent) == 10
    # Every retried step resent exactly the conversation of its failed call.
    assert sent[1] == sent[0] and sent[3] == sent[2] == sent[4]
    assert result["model_retries"]["step_retries"] == 6


# ── Stop during a client backoff wait ────────────────────────────────────


def test_stop_during_client_backoff_ends_the_wait_promptly():
    clock = FakeClock()
    stop = threading.Event()

    def sleep(seconds):
        clock.sleep(seconds)
        stop.set()  # the user presses Stop during the first slice

    retrier = ModelCallRetrier(POLICY, sleep=sleep, clock=clock)
    fn, calls = _always(lambda: _status_error(429, headers={"retry-after": "30"}))
    with model_retry_notices(lambda _n: None, stop.is_set):
        with pytest.raises(openai.RateLimitError) as raised:
            retrier.call(fn)
    assert calls["n"] == 1
    assert sum(clock.sleeps) <= 0.5
    # Not a transient give-up: nothing upstream should wait and retry it.
    assert retry_state_of(raised.value) is None


def test_stop_already_pressed_skips_even_a_zero_wait():
    clock = FakeClock()
    fn, calls = _always(lambda: _status_error(429, headers={"retry-after": "0"}))
    with model_retry_notices(lambda _n: None, lambda: True):
        with pytest.raises(openai.RateLimitError):
            _retrier(clock).call(fn)
    assert calls["n"] == 1 and clock.sleeps == []


def test_stop_during_stream_backoff_ends_promptly():
    clock = FakeClock()
    stop = threading.Event()
    opened = {"n": 0}

    def open_stream():
        opened["n"] += 1
        raise _status_error(503)

    def sleep(seconds):
        clock.sleep(seconds)
        stop.set()

    retrier = ModelCallRetrier(POLICY, sleep=sleep, clock=clock)
    with model_retry_notices(lambda _n: None, stop.is_set):
        with pytest.raises(openai.InternalServerError):
            list(retrier.stream(open_stream, is_output=_has_text))
    assert opened["n"] == 1


def test_stop_during_client_backoff_cancels_the_turn(clean_registry):
    """In the TUI/Agent UI the Stop event is on the console; a 30 s
    Retry-After must not hold the turn hostage."""
    agent = _make_agent(streaming=False)
    agent.console.cancelled = threading.Event()
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        agent.console.cancelled.set()

    retrier = ModelCallRetrier(POLICY, sleep=sleep, clock=lambda: 0.0)
    asked = {"n": 0}

    def send(*_, **__):
        asked["n"] += 1

        def rate_limited():
            raise tag_http_failure(
                LemonadeClientError("Cloud rate limit reached (HTTP 429)."),
                429,
                "",
                {"Retry-After": "30"},
            )

        return SimpleNamespace(text=retrier.call(rate_limited), stats={})

    agent.chat = MagicMock()
    agent.chat.get_stats = MagicMock(return_value={})
    agent.chat.send_messages = MagicMock(side_effect=send)

    result = agent.process_query("Check the ledger", max_steps=5)
    assert result["status"] == "cancelled"
    assert asked["n"] == 1
    assert sum(slept) <= 0.5
    assert agent.sleeps == []  # no step-level cool-down either


# ── Real sockets: connection closed before the first byte ────────────────


class _DropServer:
    """Each POST pops one action: ``"drop"`` closes the socket without a
    response; ``(status, body, headers)`` answers normally."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests += 1
                action = outer.script.pop(0)
                if action == "drop":
                    self.close_connection = True
                    return
                status, body, headers = action
                self.send_response(status)
                for k, v in {"Content-Type": "application/json", **headers}.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


_OK = (
    200,
    json.dumps(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "M",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello"},
                    "finish_reason": "stop",
                }
            ],
        }
    ).encode(),
    {},
)


@pytest.fixture
def client_for(monkeypatch):
    for name in (
        "GAIA_LLM_RETRY_ATTEMPTS",
        "GAIA_LLM_RETRY_BASE_DELAY",
        "GAIA_LLM_RETRY_MAX_DELAY",
        "GAIA_LLM_RETRY_DEADLINE",
        "LEMONADE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    def make(server):
        client = LemonadeClient(host="127.0.0.1", port=server.port, verbose=False)
        client._ensure_model_loaded = lambda *a, **k: None
        client._model_slot_lease = lambda *a, **k: nullcontext()
        client.cloud_model_provider = lambda *a, **k: None
        client.sleeps = []
        client._retry_sleep = client.sleeps.append
        return client

    return make


def _ask(client):
    return client.chat_completions(
        model="M", messages=[{"role": "user", "content": "hi"}], stream=False
    )["choices"][0]["message"]["content"]


@pytest.mark.allow_network
def test_connection_closed_before_first_byte_is_retried(client_for):
    with _DropServer(["drop", "drop", _OK]) as server:
        client = client_for(server)
        assert _ask(client) == "Hello"
    assert server.requests == 3
    assert client.last_request_retries == 2


@pytest.mark.allow_network
@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_real_client_error_status_is_sent_once(client_for, status):
    body = json.dumps({"error": {"message": "server is overloaded"}}).encode()
    with _DropServer([(status, body, {"Retry-After": "1"}), _OK]) as server:
        client = client_for(server)
        with pytest.raises((LemonadeClientError, requests.RequestException)):
            _ask(client)
    assert server.requests == 1
    assert client.sleeps == []
