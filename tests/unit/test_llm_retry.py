# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Classification, backoff and retry semantics of ``gaia.llm.retry``.

Nothing here sleeps: every timing assertion runs on an injected clock, an
injected sleep, and a seeded ``random.Random``.
"""

import random
from unittest.mock import MagicMock

import httpx
import openai
import pytest
import requests

from gaia.llm.retry import (
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BASE_DELAY,
    DEFAULT_RETRY_DEADLINE,
    DEFAULT_RETRY_MAX_DELAY,
    ModelCallRetrier,
    RetryPolicy,
    RetryState,
    attach_retry_state,
    backoff_delay,
    classify_exception,
    classify_http_failure,
    parse_retry_after,
    retry_state_of,
    tag_http_failure,
)

# ── classify_http_failure ────────────────────────────────────────────────

OVERFLOW_BODY = (
    '{"error": {"code": 400, "message": "the request exceeds the available '
    'context size, try increasing it", "type": "exceed_context_size_error"}}'
)


@pytest.mark.parametrize(
    "status, body, retried",
    [
        # Transient by status.
        (408, "", True),
        (429, "", True),
        (502, "", True),
        (503, "", True),
        (504, "", True),
        (504, "<html>Gateway Time-out</html>", True),
        # 500: retried unless the body says the request is at fault.
        (500, "", True),
        (500, '{"error": {"message": "Internal server error"}}', True),
        (500, "An error occurred while sending the request.", True),
        (500, "Error while copying content to a stream.", True),
        (502, "An error occurred while sending the request.", True),
        (500, "Request type is not supported for this deployment.", False),
        (500, '{"error": {"type": "invalid_request_error"}}', False),
        (500, '{"error": {"code": "model_not_found"}}', False),
        # A hung local model call: #1030's own remediation, never a retry.
        (500, '{"error": {"message": "CURL error: Timeout was reached"}}', False),
        # A transient wrapper wins over upstream words inside it.
        (
            500,
            "An error occurred while sending the request: operation unsupported",
            True,
        ),
        # Never retried, whatever the body.
        (400, "", False),
        (400, '{"error": {"code": "content_filter"}}', False),
        (400, "An error occurred while sending the request.", False),
        (401, "", False),
        (402, "", False),
        (403, "", False),
        (404, "", False),
        (412, "", False),
        (413, "", False),
        (422, "", False),
        (501, "", False),
        (505, "", False),
        # Context overflow is never retried, even under a retryable status.
        (400, OVERFLOW_BODY, False),
        (500, OVERFLOW_BODY, False),
        (503, OVERFLOW_BODY, False),
        # A content-filter verdict behind a proxy status is still a verdict.
        (503, '{"error": {"code": "content_filter"}}', False),
        # No status (an error event inside a stream): only an upstream hop.
        (None, "An error occurred while sending the request.", True),
        (None, "upstream connect error or disconnect/reset", True),
        (None, "something odd", False),
        (None, "", False),
    ],
)
def test_classify_http_failure(status, body, retried):
    reason = classify_http_failure(status, body)
    assert (reason is not None) is retried, (status, body, reason)


def test_reason_names_the_status_and_never_the_body():
    body = "An error occurred while sending the request. secret-token-123"
    reason = classify_http_failure(500, body)
    assert reason == "HTTP 500"
    assert "secret" not in reason


# ── classify_exception ───────────────────────────────────────────────────


def _status_error(status, body=None, headers=None):
    request = httpx.Request("POST", "http://test/v1/chat/completions")
    response = httpx.Response(status, request=request, headers=headers or {})
    cls = {
        400: openai.BadRequestError,
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
        404: openai.NotFoundError,
        422: openai.UnprocessableEntityError,
        429: openai.RateLimitError,
    }.get(
        status, openai.InternalServerError if status >= 500 else openai.APIStatusError
    )
    return cls("upstream said no", response=response, body=body)


def _request():
    return httpx.Request("POST", "http://test/v1/chat/completions")


@pytest.mark.parametrize(
    "make_error, retried",
    [
        # requests (the non-streaming path)
        (lambda: requests.exceptions.ConnectionError("reset"), True),
        (lambda: requests.exceptions.ConnectTimeout("slow connect"), True),
        (lambda: requests.exceptions.ReadTimeout("slow read"), True),
        (lambda: requests.exceptions.ChunkedEncodingError("cut"), True),
        (lambda: requests.exceptions.SSLError("bad cert"), False),
        (lambda: requests.exceptions.InvalidURL("nope"), False),
        # OpenAI SDK (the streaming path)
        (lambda: openai.APIConnectionError(request=_request()), True),
        (lambda: openai.APITimeoutError(request=_request()), True),
        (lambda: _status_error(400), False),
        (lambda: _status_error(400, {"code": "content_filter"}), False),
        (lambda: _status_error(401), False),
        (lambda: _status_error(403), False),
        (lambda: _status_error(404), False),
        (lambda: _status_error(422), False),
        (lambda: _status_error(429), True),
        (lambda: _status_error(500), True),
        (lambda: _status_error(500, {"message": "x is not supported"}), False),
        (lambda: _status_error(502), True),
        (lambda: _status_error(503), True),
        (lambda: _status_error(504), True),
        (
            lambda: openai.APIError(
                "An error occurred during streaming",
                request=_request(),
                body={"message": "An error occurred while sending the request."},
            ),
            True,
        ),
        (
            lambda: openai.APIError("bad", request=_request(), body={"message": "x"}),
            False,
        ),
        # httpx transport errors escaping a stream mid-read
        (lambda: httpx.RemoteProtocolError("peer closed"), True),
        (lambda: httpx.ReadError("reset"), True),
        (lambda: httpx.ReadTimeout("slow"), True),
        (lambda: httpx.UnsupportedProtocol("ftp"), False),
        # builtins
        (lambda: ConnectionResetError("reset"), True),
        (lambda: TimeoutError("slow"), True),
        (lambda: ValueError("bad json"), False),
        (lambda: RuntimeError("boom"), False),
    ],
)
def test_classify_exception(make_error, retried):
    error = make_error()
    assert (classify_exception(error) is not None) is retried, repr(error)


def test_tls_failure_under_a_connection_error_is_not_retried():
    import ssl

    try:
        try:
            raise ssl.SSLError("certificate verify failed")
        except ssl.SSLError as inner:
            raise openai.APIConnectionError(request=_request()) from inner
    except openai.APIConnectionError as outer:
        assert classify_exception(outer) is None


def test_tagged_error_uses_the_verdict_from_its_discarded_body():
    permanent = tag_http_failure(
        RuntimeError("Cloud request failed (HTTP 500)"),
        500,
        "Request type is not supported for this deployment.",
    )
    transient = tag_http_failure(RuntimeError("Cloud request failed (HTTP 500)"), 500)
    assert classify_exception(permanent) is None
    assert classify_exception(transient) == "HTTP 500"


def test_tag_tolerates_test_doubles():
    error = tag_http_failure(RuntimeError("x"), MagicMock(), MagicMock(), MagicMock())
    assert classify_exception(error) is None
    assert getattr(error, "retry_after") is None


# ── Retry-After ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "headers, expected",
    [
        (None, None),
        ({}, None),
        ({"retry-after": "7"}, 7.0),
        ({"Retry-After": "2.5"}, 2.5),
        ({"retry-after-ms": "1500"}, 1.5),
        ({"retry-after": "-3"}, 0.0),
        ({"retry-after": "soon"}, None),
        ({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}, 0.0),  # in the past
    ],
)
def test_parse_retry_after(headers, expected):
    assert parse_retry_after(headers) == expected


def test_parse_retry_after_reads_case_insensitive_headers():
    assert (
        parse_retry_after(requests.structures.CaseInsensitiveDict({"RETRY-AFTER": "4"}))
        == 4.0
    )
    assert parse_retry_after(httpx.Headers({"Retry-After": "3"})) == 3.0


# ── backoff_delay ────────────────────────────────────────────────────────


def test_backoff_is_full_jitter_under_an_exponential_ceiling():
    policy = RetryPolicy(max_attempts=10, base_delay=2.0, max_delay=30.0, deadline=999)
    rng = random.Random(1234)
    for retry_index, ceiling in enumerate([2, 4, 8, 16, 30, 30, 30]):
        samples = [backoff_delay(retry_index, policy, rng) for _ in range(400)]
        assert all(0.0 <= s <= ceiling for s in samples)
        # Full jitter spans the whole window, not a band near its top.
        assert min(samples) < ceiling * 0.1
        assert max(samples) > ceiling * 0.9


def test_backoff_is_deterministic_for_a_seed():
    policy = RetryPolicy()
    first = [backoff_delay(i, policy, random.Random(7)) for i in range(4)]
    second = [backoff_delay(i, policy, random.Random(7)) for i in range(4)]
    assert first == second


def test_retry_after_replaces_jitter_and_is_capped():
    policy = RetryPolicy(base_delay=2.0, max_delay=30.0)
    rng = random.Random(0)
    assert backoff_delay(0, policy, rng, retry_after=12.0) == 12.0
    assert backoff_delay(0, policy, rng, retry_after=600.0) == 30.0
    assert backoff_delay(3, policy, rng, retry_after=0.0) == 0.0


# ── RetryPolicy.from_env ─────────────────────────────────────────────────


def test_policy_defaults(monkeypatch):
    for name in (
        "GAIA_LLM_RETRY_ATTEMPTS",
        "GAIA_LLM_RETRY_BASE_DELAY",
        "GAIA_LLM_RETRY_MAX_DELAY",
        "GAIA_LLM_RETRY_DEADLINE",
    ):
        monkeypatch.delenv(name, raising=False)
    policy = RetryPolicy.from_env()
    assert policy == RetryPolicy(
        DEFAULT_RETRY_ATTEMPTS,
        DEFAULT_RETRY_BASE_DELAY,
        DEFAULT_RETRY_MAX_DELAY,
        DEFAULT_RETRY_DEADLINE,
    )


def test_policy_reads_env(monkeypatch):
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv("GAIA_LLM_RETRY_BASE_DELAY", "0.5")
    monkeypatch.setenv("GAIA_LLM_RETRY_MAX_DELAY", "10")
    monkeypatch.setenv("GAIA_LLM_RETRY_DEADLINE", "60")
    assert RetryPolicy.from_env() == RetryPolicy(1, 0.5, 10.0, 60.0)


@pytest.mark.parametrize(
    "name, value",
    [
        ("GAIA_LLM_RETRY_ATTEMPTS", "0"),
        ("GAIA_LLM_RETRY_ATTEMPTS", "two"),
        ("GAIA_LLM_RETRY_ATTEMPTS", "2.5"),
        ("GAIA_LLM_RETRY_BASE_DELAY", "-1"),
        ("GAIA_LLM_RETRY_DEADLINE", "forever"),
    ],
)
def test_policy_rejects_invalid_env(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        RetryPolicy.from_env()


# ── ModelCallRetrier.call ────────────────────────────────────────────────


class FakeClock:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _retrier(clock, policy=None, seed=42):
    return ModelCallRetrier(
        policy
        or RetryPolicy(max_attempts=5, base_delay=2.0, max_delay=30.0, deadline=120),
        sleep=clock.sleep,
        clock=clock,
        rng=random.Random(seed),
    )


def _failing(errors, result="ok"):
    """A callable that raises each of *errors* in turn, then returns *result*."""
    calls = {"n": 0}
    pending = list(errors)

    def fn():
        calls["n"] += 1
        if pending:
            raise pending.pop(0)
        return result

    return fn, calls


def test_transient_failures_are_retried_until_success():
    clock = FakeClock()
    fn, calls = _failing([_status_error(503), requests.exceptions.ConnectionError("x")])
    state = RetryState()
    assert _retrier(clock).call(fn, state=state) == "ok"
    assert calls["n"] == 3
    assert state.attempts == 3 and state.retries == 2
    assert len(clock.sleeps) == 2
    # Retry 0 waits within [0, 2], retry 1 within [0, 4].
    assert 0 <= clock.sleeps[0] <= 2 and 0 <= clock.sleeps[1] <= 4
    assert state.waited == pytest.approx(sum(clock.sleeps))
    assert not state.exhausted


def test_sleeps_follow_the_seeded_jitter_exactly():
    clock = FakeClock()
    fn, _ = _failing([_status_error(502)] * 3)
    _retrier(clock, seed=99).call(fn)
    rng = random.Random(99)
    expected = [rng.uniform(0, 2), rng.uniform(0, 4), rng.uniform(0, 8)]
    assert clock.sleeps == pytest.approx(expected)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_permanent_failures_are_not_retried(status):
    clock = FakeClock()
    error = _status_error(status)
    fn, calls = _failing([error])
    with pytest.raises(type(error)) as raised:
        _retrier(clock).call(fn)
    assert raised.value is error
    assert calls["n"] == 1
    assert clock.sleeps == []
    # Not transient, so no give-up state for an outer layer to act on.
    assert retry_state_of(raised.value) is None


def test_gives_up_after_max_attempts_and_reraises_the_original():
    clock = FakeClock()
    errors = [_status_error(503) for _ in range(9)]
    fn, calls = _failing(errors)
    with pytest.raises(openai.InternalServerError) as raised:
        _retrier(clock).call(fn)
    assert calls["n"] == 5
    assert len(clock.sleeps) == 4
    state = retry_state_of(raised.value)
    assert state is not None and state.exhausted
    assert state.attempts == 5 and state.last_reason == "HTTP 503"


def test_never_waits_past_the_deadline():
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=50, base_delay=10.0, max_delay=30.0, deadline=45)
    fn, calls = _failing([requests.exceptions.ConnectionError("x")] * 60)
    with pytest.raises(requests.exceptions.ConnectionError) as raised:
        _retrier(clock, policy).call(fn)
    assert clock.now - 1000.0 <= 45
    assert calls["n"] < 50
    assert retry_state_of(raised.value).exhausted


def test_slow_attempts_count_against_the_deadline():
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=1.0, deadline=100)

    def slow_failure():
        clock.now += 90  # the attempt itself took 90 s before failing
        raise requests.exceptions.ReadTimeout("slow")

    with pytest.raises(requests.exceptions.ReadTimeout):
        _retrier(clock, policy).call(slow_failure)
    # 90 s gone after one attempt: a second (another ~90 s) cannot fit.
    assert len(clock.sleeps) <= 1


def test_retry_after_is_honoured():
    clock = FakeClock()
    fn, _ = _failing([_status_error(429, headers={"retry-after": "11"})])
    _retrier(clock).call(fn)
    assert clock.sleeps == [11.0]


def test_single_attempt_policy_disables_retry():
    clock = FakeClock()
    fn, calls = _failing([_status_error(503)])
    with pytest.raises(openai.InternalServerError) as raised:
        _retrier(clock, RetryPolicy(max_attempts=1)).call(fn)
    assert calls["n"] == 1 and clock.sleeps == []
    assert retry_state_of(raised.value).attempts == 1


def test_retry_log_lines_carry_no_body(caplog):
    clock = FakeClock()
    error = _status_error(503, body={"message": "secret-body-text"})
    fn, _ = _failing([error])
    with caplog.at_level("WARNING", logger="gaia.llm.retry"):
        _retrier(clock).call(fn, what="Chat completion")
    assert "Chat completion failed (HTTP 503); retrying in" in caplog.text
    assert "secret-body-text" not in caplog.text


# ── ModelCallRetrier.stream ──────────────────────────────────────────────


def _chunk(text=None, role=None):
    delta = {}
    if role:
        delta["role"] = role
    if text:
        delta["content"] = text
    return {"choices": [{"delta": delta}]}


def _has_text(chunk):
    return bool(chunk["choices"] and chunk["choices"][0]["delta"].get("content"))


def _stream_attempts(*attempts):
    """open_stream() whose n-th call yields attempts[n]'s items, raising any
    exception item instead of yielding it."""
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

    return open_stream, opened


def test_stream_failing_before_output_is_retried_without_duplication():
    clock = FakeClock()
    open_stream, opened = _stream_attempts(
        [_status_error(503)],
        [_chunk(role="assistant"), httpx.RemoteProtocolError("peer closed")],
        [_chunk(role="assistant"), _chunk("Hel"), _chunk("lo")],
    )
    state = RetryState()
    out = list(_retrier(clock).stream(open_stream, is_output=_has_text, state=state))
    assert opened["n"] == 3
    assert (
        "".join(c["choices"][0]["delta"].get("content") or "" for c in out) == "Hello"
    )
    # The failed attempt's role-only frame was held back, not delivered twice.
    assert sum(1 for c in out if c["choices"][0]["delta"].get("role")) == 1
    assert state.retries == 2 and not state.exhausted


def test_stream_failing_after_output_is_not_retried():
    clock = FakeClock()
    open_stream, opened = _stream_attempts(
        [_chunk("partial "), httpx.ReadError("reset")],
        [_chunk("never sent")],
    )
    received = []
    with pytest.raises(httpx.ReadError) as raised:
        for chunk in _retrier(clock).stream(open_stream, is_output=_has_text):
            received.append(chunk)
    assert opened["n"] == 1
    assert received == [_chunk("partial ")]
    state = retry_state_of(raised.value)
    assert state.output_delivered and state.exhausted
    assert clock.sleeps == []


def test_stream_permanent_failure_after_output_carries_no_state():
    clock = FakeClock()
    open_stream, _ = _stream_attempts([_chunk("partial"), ValueError("bad frame")])
    with pytest.raises(ValueError) as raised:
        list(_retrier(clock).stream(open_stream, is_output=_has_text))
    assert retry_state_of(raised.value) is None


def test_stream_with_only_non_output_frames_still_delivers_them():
    clock = FakeClock()
    usage = {"choices": [], "usage": {"total_tokens": 3}}
    open_stream, _ = _stream_attempts([_chunk(role="assistant"), usage])
    out = list(_retrier(clock).stream(open_stream, is_output=_has_text))
    assert out == [_chunk(role="assistant"), usage]


def test_stream_closes_each_failed_attempt():
    clock = FakeClock()
    closed = []

    class Closing:
        def __init__(self, items):
            self.items = iter(items)

        def __iter__(self):
            return self

        def __next__(self):
            item = next(self.items)
            if isinstance(item, BaseException):
                raise item
            return item

        def close(self):
            closed.append(True)

    attempts = iter([Closing([_status_error(502)]), Closing([_chunk("ok")])])
    out = list(_retrier(clock).stream(lambda: next(attempts), is_output=_has_text))
    assert out == [_chunk("ok")]
    assert closed == [True, True]


# ── retry_state_of ───────────────────────────────────────────────────────


def test_retry_state_survives_translation_and_wrapping():
    state = RetryState(attempts=5, last_reason="HTTP 503", exhausted=True)
    translated = attach_retry_state(RuntimeError("translated"), state)
    try:
        try:
            raise translated
        except RuntimeError as inner:
            raise ConnectionError("wrapped by an outer layer") from inner
    except ConnectionError as outer:
        assert retry_state_of(outer) is state


def test_retry_state_not_attached_unless_exhausted():
    error = attach_retry_state(RuntimeError("x"), RetryState(attempts=1))
    assert retry_state_of(error) is None
