# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``tool_choice`` reaches the OpenAI-compatible request body unchanged.

The agent's step-limit call keeps the loop's tools and sends
``tool_choice="none"`` so the model can answer but not call a tool. These tests
assert what goes over the wire on both Lemonade paths (non-streaming
``requests.post`` and the streaming OpenAI client), not just that a method ran.
"""

import json
from contextlib import nullcontext
from unittest.mock import MagicMock

import httpx
import pytest
import responses
from openai import OpenAI

from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.providers.lemonade import LemonadeProvider

_LOCAL_MODEL = "Gemma-4-E4B-it-GGUF"
_NO_TOOL_MODEL = "gemma4-it-e2b-FLM"
_TOOLS = [{"type": "function", "function": {"name": "get_weather"}}]
_MESSAGES = [{"role": "user", "content": "What's the weather in Paris?"}]
_REPLY = {
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Sunny."},
            "finish_reason": "stop",
        }
    ]
}


@pytest.fixture
def client(monkeypatch):
    """A client for a local model that never loads it or takes a broker lease."""
    client = LemonadeClient(verbose=False)
    monkeypatch.setattr(client, "_ensure_model_loaded", lambda *a, **k: None)
    monkeypatch.setattr(client, "_model_slot_lease", lambda model: nullcontext())
    return client


@pytest.fixture
def wire(client, monkeypatch):
    """Request bodies the streaming path sends, via a real OpenAI client."""
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        chunk = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": _LOCAL_MODEL,
            "choices": [
                {"index": 0, "delta": {"content": "Sunny."}, "finish_reason": None}
            ],
        }
        return httpx.Response(
            200,
            content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    sdk = OpenAI(
        base_url=client.base_url,
        api_key="test-placeholder",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", lambda **kwargs: sdk)
    return sent


def _provider(client) -> LemonadeProvider:
    provider = LemonadeProvider(model=_LOCAL_MODEL)
    provider._backend = client
    return provider


# ── LemonadeClient.chat_completions ─────────────────────────────────────


@responses.activate
def test_non_streaming_body_carries_tool_choice(client):
    responses.post(f"{client.base_url}/chat/completions", json=_REPLY)

    client.chat_completions(_LOCAL_MODEL, _MESSAGES, tools=_TOOLS, tool_choice="none")

    body = json.loads(responses.calls[0].request.body)
    assert body["tools"] == _TOOLS
    assert body["tool_choice"] == "none"


@responses.activate
def test_non_streaming_body_has_no_tool_choice_unless_set(client):
    responses.post(f"{client.base_url}/chat/completions", json=_REPLY)

    client.chat_completions(_LOCAL_MODEL, _MESSAGES, tools=_TOOLS)

    assert "tool_choice" not in json.loads(responses.calls[0].request.body)


def test_streaming_body_carries_tool_choice(client, wire):
    list(
        client.chat_completions(
            _LOCAL_MODEL, _MESSAGES, stream=True, tools=_TOOLS, tool_choice="none"
        )
    )

    assert wire[0]["tools"] == _TOOLS
    assert wire[0]["tool_choice"] == "none"


def test_streaming_body_has_no_tool_choice_unless_set(client, wire):
    list(client.chat_completions(_LOCAL_MODEL, _MESSAGES, stream=True, tools=_TOOLS))

    assert "tool_choice" not in wire[0]


@responses.activate
@pytest.mark.parametrize("stream", [False, True])
def test_tool_choice_without_tools_fails_before_sending(client, stream):
    with pytest.raises(ValueError, match="without tools"):
        client.chat_completions(
            _LOCAL_MODEL, _MESSAGES, stream=stream, tool_choice="none"
        )
    assert not responses.calls


# ── LemonadeProvider.chat ───────────────────────────────────────────────


@responses.activate
def test_provider_sends_tool_choice_through_to_the_body(client):
    responses.post(f"{client.base_url}/chat/completions", json=_REPLY)

    answer = _provider(client).chat(_MESSAGES, tools=_TOOLS, tool_choice="none")

    assert answer == "Sunny."
    body = json.loads(responses.calls[0].request.body)
    assert body["tools"] == _TOOLS
    assert body["tool_choice"] == "none"


def test_provider_withholds_tool_choice_with_the_tools(client):
    """A model without native tool calls gets neither tools nor tool_choice."""
    client.chat_completions = MagicMock(return_value=_REPLY)

    _provider(client).chat(
        _MESSAGES, model=_NO_TOOL_MODEL, tools=_TOOLS, tool_choice="none"
    )

    sent = client.chat_completions.call_args.kwargs
    assert sent["tools"] is None
    assert "tool_choice" not in sent


def test_provider_rejects_tool_choice_without_tools(client):
    client.chat_completions = MagicMock(return_value=_REPLY)

    with pytest.raises(ValueError, match="without tools"):
        _provider(client).chat(_MESSAGES, tool_choice="none")
    client.chat_completions.assert_not_called()
