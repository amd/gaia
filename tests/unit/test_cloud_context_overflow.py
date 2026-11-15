# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cloud and Claude context overflows reach the agent's trim-and-retry (#5061).

Before: a cloud 400 became "Cloud request ... failed (HTTP 400). Check that the
server is reachable" before anything read the provider's error, and Claude's
"prompt is too long" matched no overflow phrase, so the turn ended instead of
trimming old tool output and asking again.
"""

# pylint: disable=protected-access

import json
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest
import responses
from openai import OpenAI

from gaia.llm.lemonade_client import (
    CloudContextOverflowError,
    LemonadeClient,
    LemonadeClientError,
    is_context_overflow_error,
    is_provider_context_overflow,
)
from gaia.llm.providers.lemonade import (
    LemonadeContextOverflowError,
    classify_lemonade_exception,
)

# ── provider error bodies, in the shapes the providers send ─────────────────

#: OpenAI-compatible gateway (AMD LLM gateway, OpenRouter, vLLM via LiteLLM).
OPENAI_OVERFLOW = {
    "error": {
        "message": (
            "This model's maximum context length is 131072 tokens. However, your "
            "messages resulted in 140211 tokens. Please reduce the length of the "
            "messages."
        ),
        "type": "invalid_request_error",
        "param": "messages",
        "code": "context_length_exceeded",
    }
}
#: Fireworks: no overflow code, only the message says so.
FIREWORKS_OVERFLOW = {
    "error": {
        "object": "error",
        "type": "invalid_request_error",
        "code": "invalid_request_error",
        "message": (
            "The prompt is too long: 205018, model maximum context length: 163839"
        ),
    }
}
#: Anthropic Messages API.
ANTHROPIC_OVERFLOW = {
    "type": "error",
    "error": {
        "type": "invalid_request_error",
        "message": "prompt is too long: 210000 tokens > 200000 maximum",
    },
}
ANTHROPIC_TOO_LARGE = {
    "type": "error",
    "error": {
        "type": "request_too_large",
        "message": "Request exceeds the maximum size",
    },
}
#: Lemonade wrapping an upstream cloud body: as an object, and as text.
LEMONADE_WRAPPED = {
    "error": {
        "message": "cloud (amd) request failed",
        "type": "backend_error",
        "details": {"status_code": 400, "response": OPENAI_OVERFLOW},
    }
}
LEMONADE_WRAPPED_TEXT = {
    "error": {
        "message": "cloud (fireworks) request failed",
        "type": "backend_error",
        "details": {"status_code": 400, "response": json.dumps(FIREWORKS_OVERFLOW)},
    }
}

OVERFLOW_400S = [
    OPENAI_OVERFLOW,
    FIREWORKS_OVERFLOW,
    ANTHROPIC_OVERFLOW,
    LEMONADE_WRAPPED,
    LEMONADE_WRAPPED_TEXT,
]
OVERFLOW_IDS = ["openai", "fireworks", "anthropic", "lemonade-obj", "lemonade-text"]

#: 400s that are not about length and must keep their own error.
OTHER_400S = [
    {
        "error": {
            "message": "Invalid value for 'tool_choice': no function named 'x'.",
            "type": "invalid_request_error",
            "param": "tool_choice",
            "code": "invalid_value",
        }
    },
    {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "messages: roles must alternate between user and assistant",
        },
    },
    {
        "error": {
            "message": "max_tokens: 900000 > 32768, which is the maximum allowed",
            "type": "invalid_request_error",
            "code": "invalid_value",
        }
    },
    # The overflow words only in fields that are never read: an echoed request
    # and the param name.
    {
        "error": {
            "message": "Unsupported parameter: 'reasoning_effort'.",
            "type": "invalid_request_error",
            "param": "context_length_exceeded",
        },
        "request": {
            "messages": [{"role": "user", "content": "the prompt is too long"}]
        },
    },
    {"error": "Bad Request"},
    "",
    "<html><body>400 Bad Request</body></html>",
]
OTHER_IDS = [
    "tool-choice",
    "roles",
    "max-tokens",
    "echoed-words",
    "string-error",
    "empty",
    "html",
]

PROVIDER_ONLY = "205018"  # a figure only the provider's body carries


# ── the classifier ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", OVERFLOW_400S, ids=OVERFLOW_IDS)
@pytest.mark.parametrize("as_text", [False, True], ids=["parsed", "text"])
def test_overflow_bodies_are_recognised(body, as_text):
    assert is_provider_context_overflow(400, json.dumps(body) if as_text else body)


@pytest.mark.parametrize("body", OTHER_400S, ids=OTHER_IDS)
def test_other_400s_are_not_overflow(body):
    assert not is_provider_context_overflow(400, body)
    if not isinstance(body, str):
        assert not is_provider_context_overflow(400, json.dumps(body))


@pytest.mark.parametrize("status", [None, 401, 404, 422, 429, 500, 503])
def test_only_a_400_or_413_can_be_overflow(status):
    assert not is_provider_context_overflow(status, OPENAI_OVERFLOW)


def test_any_413_is_too_large():
    assert is_provider_context_overflow(413, None)
    assert is_provider_context_overflow(413, ANTHROPIC_TOO_LARGE)


def test_a_plain_text_overflow_body_is_recognised():
    assert is_provider_context_overflow(400, b"prompt is too long: 9 tokens > 8")


def test_a_huge_body_is_read_only_up_to_its_bound():
    """The phrase past the read limit is never scanned."""
    body = "x" * 100_000 + " prompt is too long"
    assert not is_provider_context_overflow(400, body)


def test_a_deeply_nested_body_stops_instead_of_recursing_forever():
    node = OPENAI_OVERFLOW
    for _ in range(50):
        node = {"error": node}
    assert not is_provider_context_overflow(400, node)


@pytest.mark.parametrize(
    "text",
    [
        OPENAI_OVERFLOW["error"]["message"],
        FIREWORKS_OVERFLOW["error"]["message"],
        ANTHROPIC_OVERFLOW["error"]["message"],
        str(CloudContextOverflowError(400)),
        str(LemonadeContextOverflowError()),
    ],
)
def test_the_agent_loop_classifier_matches_every_provider(text):
    assert is_context_overflow_error(text)


# ── through LemonadeClient, as the agent calls it ───────────────────────────


@pytest.fixture
def client(monkeypatch):
    def no_local(*args, **kwargs):
        pytest.fail("Cloud inference attempted a local model operation")

    client = LemonadeClient(verbose=False, ctx_size_override=1024)
    monkeypatch.setattr(client, "get_status", no_local)
    monkeypatch.setattr(client, "_ensure_pinned_load", no_local)
    monkeypatch.setattr(client, "_load_model_leased", no_local)
    monkeypatch.setattr("gaia.daemon.broker_client.model_lease", no_local)
    client._retry_sleep = lambda _seconds: None
    return client


MESSAGES = [{"role": "user", "content": "summarise the documents"}]


def _assert_overflow(error):
    assert isinstance(error, CloudContextOverflowError)
    assert is_context_overflow_error(str(error))
    assert PROVIDER_ONLY not in str(error)
    assert "Check that the server is reachable" not in str(error)
    typed = classify_lemonade_exception(error)
    assert isinstance(typed, LemonadeContextOverflowError)
    assert typed.retryable is False


@pytest.mark.parametrize("body", OVERFLOW_400S, ids=OVERFLOW_IDS)
@pytest.mark.parametrize("model", ["fireworks.kimi-k2p6", "amd.DeepSeek-V4.1-Flash"])
@pytest.mark.parametrize("effort", [None, "high"])
def test_a_cloud_overflow_raises_the_typed_error(client, body, model, effort):
    sent = []

    def handle(request):
        sent.append(json.loads(request.body))
        return 400, {"Content-Type": "application/json"}, json.dumps(body)

    kwargs = {"reasoning_effort": effort} if effort else {}
    with responses.RequestsMock() as mock:
        mock.add_callback(
            responses.POST, f"{client.base_url}/chat/completions", callback=handle
        )
        with pytest.raises(LemonadeClientError) as raised:
            client.chat_completions(model, MESSAGES, **kwargs)

    # One request, the conversation as sent: an overflow is never re-sent.
    assert len(sent) == 1
    assert sent[0]["model"] == model
    assert sent[0]["messages"] == MESSAGES
    _assert_overflow(raised.value)
    assert "reasoning_effort" not in str(raised.value)


@pytest.mark.parametrize("body", OTHER_400S, ids=OTHER_IDS)
def test_other_cloud_400s_keep_their_own_message(client, body):
    text = body if isinstance(body, str) else json.dumps(body)
    with responses.RequestsMock() as mock:
        mock.add(
            responses.POST, f"{client.base_url}/chat/completions", status=400, body=text
        )
        with pytest.raises(LemonadeClientError) as raised:
            client.chat_completions("fireworks.kimi-k2p6", MESSAGES)

    assert not isinstance(raised.value, CloudContextOverflowError)
    assert "HTTP 400" in str(raised.value)
    assert not is_context_overflow_error(str(raised.value))
    assert not isinstance(
        classify_lemonade_exception(raised.value), LemonadeContextOverflowError
    )


def _stream(client, monkeypatch, handle, model="amd.DeepSeek-V4.1-Flash"):
    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        with OpenAI(
            base_url=client.base_url, api_key="test-placeholder", http_client=transport
        ) as sdk:
            monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", lambda **_: sdk)
            with pytest.raises(LemonadeClientError) as raised:
                list(client.chat_completions(model, MESSAGES, stream=True))
    return raised.value


@pytest.mark.parametrize("body", OVERFLOW_400S, ids=OVERFLOW_IDS)
def test_a_streamed_cloud_overflow_raises_the_typed_error(client, monkeypatch, body):
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(400, json=body)

    _assert_overflow(_stream(client, monkeypatch, handle))
    assert len(sent) == 1
    assert sent[0]["messages"] == MESSAGES and sent[0]["stream"] is True


def test_an_overflow_in_lemonade_s_stream_frame_is_recognised(client, monkeypatch):
    frame = {"error": LEMONADE_WRAPPED["error"]}

    def handle(request):
        return httpx.Response(
            200,
            content=f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    _assert_overflow(_stream(client, monkeypatch, handle))


@pytest.mark.parametrize("body", OTHER_400S[:4], ids=OTHER_IDS[:4])
def test_other_streamed_400s_are_not_overflow(client, monkeypatch, body):
    def handle(request):
        return httpx.Response(400, json=body)

    error = _stream(client, monkeypatch, handle)
    assert not isinstance(error, CloudContextOverflowError)
    assert "HTTP 400" in str(error)


# ── Claude ──────────────────────────────────────────────────────────────────


def _anthropic_error(status, body):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, json=body, request=request)
    cls = {400: anthropic.BadRequestError, 413: anthropic.RequestTooLargeError}
    return cls[status](f"Error code: {status} - {body}", response=response, body=body)


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-unit-test")
    from gaia.llm.providers.claude import ClaudeProvider

    provider = ClaudeProvider(model="claude-sonnet-5")
    provider._client = MagicMock()
    return provider


@pytest.mark.parametrize(
    "status,body", [(400, ANTHROPIC_OVERFLOW), (413, ANTHROPIC_TOO_LARGE)]
)
@pytest.mark.parametrize("stream", [False, True])
def test_a_claude_overflow_raises_the_typed_error(claude, status, body, stream):
    error = _anthropic_error(status, body)
    claude._client.messages.create.side_effect = error

    with pytest.raises(LemonadeContextOverflowError) as raised:
        result = claude.chat(MESSAGES, stream=stream)
        if stream:
            list(result)

    assert raised.value.__cause__ is error
    assert raised.value.retryable is False
    assert is_context_overflow_error(str(raised.value))
    sent = claude._client.messages.create.call_args.kwargs
    assert sent["messages"][0]["role"] == "user"


def test_other_claude_400s_keep_their_own_message(claude):
    body = OTHER_400S[1]
    claude._client.messages.create.side_effect = _anthropic_error(400, body)
    with pytest.raises(RuntimeError, match=r"Anthropic API error \(HTTP 400\)"):
        claude.chat(MESSAGES)


# ── the agent loop trims and asks again ─────────────────────────────────────


@pytest.fixture
def agent():
    from gaia.agents.base.agent import Agent

    class _Agent(Agent):
        def _get_system_prompt(self) -> str:
            return "You are a test agent."

        def _register_tools(self) -> None:
            pass

    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _Agent(silent_mode=True, skip_lemonade=True)
    a.streaming = False
    a.model_id = "amd.DeepSeek-V4.1-Flash"
    return a


def _research_turn():
    messages = [{"role": "user", "content": "what did document 1 say?"}]
    for n in range(1, 7):
        call = {
            "id": f"c{n}",
            "type": "function",
            "function": {"name": "read_doc", "arguments": "{}"},
        }
        messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{n}",
                "name": "read_doc",
                "content": "filler " * 1000 + f"FACT-{n}",
            }
        )
    return messages


@pytest.mark.parametrize(
    "overflow",
    [
        CloudContextOverflowError(400),
        RuntimeError(f"Error in send_messages: {CloudContextOverflowError(400)}"),
        LemonadeContextOverflowError(),
    ],
    ids=["cloud", "cloud-wrapped", "claude"],
)
def test_one_overflow_then_the_answer(agent, overflow):
    agent.conversation_history = _research_turn()
    replies = [overflow, json.dumps({"thought": "ok", "answer": "done"})]
    sent = []

    def _send(messages, **_):
        sent.append(messages)
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        response = MagicMock()
        response.text, response.stats = reply, {}
        return response

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)
    shrink = MagicMock(wraps=agent._retry_after_overflow_shrink)
    agent._retry_after_overflow_shrink = shrink

    result = agent.process_query("what did document 1 say?", max_steps=3)

    assert shrink.call_count == 1
    assert len(sent) == 2
    assert result["result"] == "done"
    assert agent.error_history[-1]["type"] == "llm_context_overflow_trimmed"
