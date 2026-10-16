# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which model-endpoint failures an agent waits out, on every provider path.

Transient (wait and ask again): 408, 429, 500, 502, 503, 504, 529, a refused
or reset connection, a timeout. Permanent (fail at once): 400, 401, 403, 404,
413, 422, a context overflow, an unknown model, a content-policy verdict.

The Claude provider is exercised with the real ``anthropic`` exception classes
built on real ``httpx`` responses, so the shapes are the SDK's own; only the
network call is replaced.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.llm.retry import (
    RetryState,
    attach_retry_state,
    classify_anthropic_error,
    classify_http_failure,
    retry_after_in_chain,
    retry_state_of,
    tag_http_failure,
)

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx")

_URL = "https://api.anthropic.com/v1/messages"


def _response(status, headers=None):
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("POST", _URL)
    )


def _status_error(cls, status, error_type, headers=None):
    body = {"type": "error", "error": {"type": error_type, "message": "nope"}}
    return cls(json.dumps(body), response=_response(status, headers), body=body)


# ── the shared status table ──────────────────────────────────────────────


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
def test_transient_statuses_are_retried(status):
    assert classify_http_failure(status, "") == f"HTTP {status}"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422])
def test_permanent_statuses_are_not(status):
    assert classify_http_failure(status, "") is None


def test_a_context_overflow_is_never_transient_whatever_its_status():
    body = "the request exceeds the available context size, try increasing it"
    assert classify_http_failure(503, body) is None


# ── anthropic SDK errors ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error, reason",
    [
        (
            lambda: _status_error(anthropic.RateLimitError, 429, "rate_limit_error"),
            "HTTP 429",
        ),
        (
            lambda: _status_error(anthropic.OverloadedError, 529, "overloaded_error"),
            "HTTP 529",
        ),
        (
            lambda: _status_error(anthropic.InternalServerError, 503, "api_error"),
            "HTTP 503",
        ),
        (
            lambda: anthropic.APIConnectionError(request=httpx.Request("POST", _URL)),
            "connection error",
        ),
        (
            lambda: anthropic.APITimeoutError(request=httpx.Request("POST", _URL)),
            "read timeout",
        ),
        # An error event inside an open stream rides the stream's 200.
        (
            lambda: _status_error(anthropic.APIStatusError, 200, "overloaded_error"),
            "HTTP 529",
        ),
        (
            lambda: _status_error(anthropic.APIStatusError, 200, "rate_limit_error"),
            "HTTP 429",
        ),
    ],
    ids=["429", "529", "503", "refused", "timeout", "stream-overloaded", "stream-429"],
)
def test_anthropic_transient_errors(error, reason):
    assert classify_anthropic_error(error(), anthropic) == reason


@pytest.mark.parametrize(
    "error",
    [
        lambda: _status_error(anthropic.BadRequestError, 400, "invalid_request_error"),
        lambda: _status_error(
            anthropic.AuthenticationError, 401, "authentication_error"
        ),
        lambda: _status_error(anthropic.PermissionDeniedError, 403, "permission_error"),
        lambda: _status_error(anthropic.NotFoundError, 404, "not_found_error"),
        lambda: _status_error(anthropic.RequestTooLargeError, 413, "request_too_large"),
        lambda: _status_error(
            anthropic.UnprocessableEntityError, 422, "invalid_request_error"
        ),
        lambda: _status_error(anthropic.APIStatusError, 200, "invalid_request_error"),
        lambda: ValueError("not an SDK error at all"),
    ],
    ids=["400", "401", "403", "404", "413", "422", "stream-invalid", "foreign"],
)
def test_anthropic_permanent_errors(error):
    assert classify_anthropic_error(error(), anthropic) is None


# ── Retry-After through wrappers ─────────────────────────────────────────


def test_retry_after_is_found_on_a_wrapped_cause():
    inner = tag_http_failure(RuntimeError("429"), 429, "", {"Retry-After": "17"})
    try:
        try:
            raise inner
        except RuntimeError as e:
            raise ConnectionError("Failed to connect to LLM server") from e
    except ConnectionError as outer:
        assert retry_after_in_chain(outer) == 17.0


def test_retry_after_ms_is_read_from_an_sdk_response():
    error = _status_error(
        anthropic.RateLimitError, 429, "rate_limit_error", {"retry-after-ms": "2500"}
    )
    assert retry_after_in_chain(error) == 2.5


def test_no_retry_after_is_none_and_a_cycle_terminates():
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__cause__, b.__cause__ = b, a
    assert retry_after_in_chain(a) is None


# ── the Claude provider hands the agent a retry record ───────────────────


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-unit-test")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    from gaia.llm.providers.claude import ClaudeProvider

    instance = ClaudeProvider(max_retries=3)
    instance.create = MagicMock()
    monkeypatch.setattr(instance._client.messages, "create", instance.create)
    return instance


def _message(text):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=5, output_tokens=2),
        stop_reason="end_turn",
    )


def test_claude_names_its_endpoint(provider):
    assert provider.base_url.startswith("https://api.anthropic.com")


def test_claude_rate_limit_carries_a_transient_record(provider):
    provider.create.side_effect = _status_error(
        anthropic.RateLimitError, 429, "rate_limit_error", {"retry-after": "12"}
    )
    with pytest.raises(RuntimeError, match="429") as raised:
        provider.chat([{"role": "user", "content": "hi"}])
    state = retry_state_of(raised.value)
    assert state is not None
    assert state.last_reason == "HTTP 429"
    # The SDK's own attempts: the first plus max_retries.
    assert state.attempts == 4
    assert retry_after_in_chain(raised.value) == 12.0


@pytest.mark.parametrize(
    "error",
    [
        lambda: _status_error(
            anthropic.AuthenticationError, 401, "authentication_error"
        ),
        lambda: _status_error(anthropic.BadRequestError, 400, "invalid_request_error"),
    ],
    ids=["401", "400"],
)
def test_claude_permanent_errors_carry_no_record(provider, error):
    provider.create.side_effect = error()
    with pytest.raises(Exception) as raised:
        provider.chat([{"role": "user", "content": "hi"}])
    assert retry_state_of(raised.value) is None


def _event(etype, **fields):
    return SimpleNamespace(type=etype, **fields)


def test_claude_stream_cut_after_text_is_marked_delivered(provider):
    def _events():
        yield _event("message_start", message=SimpleNamespace(usage=None))
        yield _event(
            "content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="Hel"),
        )
        raise _status_error(anthropic.APIStatusError, 200, "overloaded_error")

    provider.create.return_value = _events()
    stream = provider.chat([{"role": "user", "content": "hi"}], stream=True)
    assert next(stream) == "Hel"
    with pytest.raises(RuntimeError) as raised:
        next(stream)
    state = retry_state_of(raised.value)
    assert state is not None
    assert state.output_delivered is True
    assert state.attempts == 1
    assert state.last_reason == "HTTP 529"


def test_claude_stream_refused_before_any_text_is_not_marked_delivered(provider):
    provider.create.side_effect = anthropic.APIConnectionError(
        request=httpx.Request("POST", _URL)
    )
    stream = provider.chat([{"role": "user", "content": "hi"}], stream=True)
    with pytest.raises(RuntimeError) as raised:
        next(stream)
    state = retry_state_of(raised.value)
    assert state is not None and state.output_delivered is False


# ── the agent rides out a Claude outage end to end ───────────────────────


@pytest.fixture
def clean_registry():
    from gaia.agents.base.tools import _TOOL_REGISTRY

    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def test_agent_on_claude_recovers_from_a_rate_limit(
    provider, monkeypatch, clean_registry
):  # pylint: disable=unused-argument
    from tests.unit.agents.test_model_outage_recovery import (
        FakeClock,
        _make_agent,
        _retry_lines,
    )

    monkeypatch.delenv("GAIA_AGENT_MODEL_OUTAGE_BUDGET", raising=False)
    provider.create.side_effect = [
        _status_error(
            anthropic.RateLimitError, 429, "rate_limit_error", {"retry-after": "30"}
        ),
        _status_error(anthropic.OverloadedError, 529, "overloaded_error"),
        _message("All three pages balance."),
    ]
    agent = _make_agent(False, FakeClock())
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.llm_client = provider
    chat.send_messages = MagicMock(
        side_effect=lambda messages, **_: SimpleNamespace(
            text=provider.chat(messages), stats={}
        )
    )
    agent.chat = chat

    result = agent.process_query("Do the pages balance?", max_steps=5)

    assert "All three pages balance." in result["result"]
    assert provider.create.call_count == 3
    # The same request each time.
    first, second, third = (c.kwargs for c in provider.create.call_args_list)
    assert first == second == third
    assert agent.waits == [30.0, 10.0]
    lines = _retry_lines(agent)
    assert "(HTTP 429)" in lines[0] and "(HTTP 529)" in lines[1]


def test_attach_retry_state_ignores_a_record_that_did_not_give_up():
    error = attach_retry_state(RuntimeError("x"), RetryState(attempts=1))
    assert retry_state_of(error) is None


def test_lemonade_cloud_429_and_claude_429_read_the_same():
    """One status line vocabulary whichever provider failed."""
    cloud = tag_http_failure(
        RuntimeError("Cloud rate limit reached (HTTP 429)."),
        429,
        '{"error":{"message":"cloud request failed","status_code":429}}',
    )
    claude = _status_error(anthropic.RateLimitError, 429, "rate_limit_error")
    assert getattr(cloud, "retry_reason") == classify_anthropic_error(claude, anthropic)
