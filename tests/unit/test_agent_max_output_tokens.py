# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent's per-reply output cap must reach the wire, sized per model.

Cloud reasoning models spend output tokens on thinking, so an 8K cap sized for a
local 32K context truncates them. These tests read the JSON body Lemonade
actually receives, not the kwargs handed to an internal client.
"""

import json
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import responses

from gaia.agents.base.agent import (
    CLOUD_MAX_OUTPUT_TOKENS,
    LOCAL_MAX_OUTPUT_TOKENS,
    Agent,
)
from gaia.llm.lemonade_client import LemonadeClient

BASE_URL = "http://127.0.0.1:9/api/v1"
CLOUD_MODEL = "fireworks.deepseek-v4p1-flash"
LOCAL_MODEL = "Gemma-4-E4B-it-GGUF"


class _Agent(Agent):
    def _register_tools(self):
        return None


@pytest.fixture
def no_local_model_management(monkeypatch):
    """Local model loading is not what these tests exercise; the request is."""
    monkeypatch.setattr(LemonadeClient, "_ensure_model_loaded", lambda *a, **k: None)
    monkeypatch.setattr(LemonadeClient, "health_check", lambda self: {"ok": True})


def _request_bodies(model_id, **agent_kwargs):
    reply = {
        "choices": [
            {
                "message": {"role": "assistant", "content": '{"answer": "done"}'},
                "finish_reason": "stop",
            }
        ]
    }
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.post(f"{BASE_URL}/chat/completions", json=reply)
        agent = _Agent(
            model_id=model_id,
            base_url=BASE_URL,
            skip_lemonade=True,
            silent_mode=True,
            max_steps=2,
            **agent_kwargs,
        )
        agent.process_query("hi")
        bodies = [
            json.loads(call.request.body)
            for call in rsps.calls
            if call.request.url.endswith("/chat/completions")
        ]
    assert bodies, "the agent never called /chat/completions"
    return bodies


@pytest.mark.parametrize(
    "model_id,expected",
    [
        (CLOUD_MODEL, CLOUD_MAX_OUTPUT_TOKENS),
        (LOCAL_MODEL, LOCAL_MAX_OUTPUT_TOKENS),
    ],
)
def test_request_body_carries_model_sized_output_cap(
    no_local_model_management, model_id, expected
):
    bodies = _request_bodies(model_id)
    assert {b["max_completion_tokens"] for b in bodies} == {expected}


def test_explicit_cap_overrides_the_cloud_default(no_local_model_management):
    bodies = _request_bodies(CLOUD_MODEL, max_output_tokens=12000)
    assert {b["max_completion_tokens"] for b in bodies} == {12000}


def test_cap_follows_a_mid_session_switch_to_a_cloud_model(no_local_model_management):
    agent = _Agent(
        model_id=LOCAL_MODEL, base_url=BASE_URL, skip_lemonade=True, silent_mode=True
    )
    assert agent._max_output_tokens() == LOCAL_MAX_OUTPUT_TOKENS
    agent.chat.config.model = CLOUD_MODEL
    assert agent._max_output_tokens() == CLOUD_MAX_OUTPUT_TOKENS


def test_catalog_metadata_marks_a_custom_provider_as_cloud(no_local_model_management):
    agent = _Agent(
        model_id="company.llama-4",
        base_url=BASE_URL,
        skip_lemonade=True,
        silent_mode=True,
    )
    assert agent._max_output_tokens() == LOCAL_MAX_OUTPUT_TOKENS
    agent.chat.llm_client._backend._model_metadata["company.llama-4"] = {
        "recipe": "cloud"
    }
    assert agent._max_output_tokens() == CLOUD_MAX_OUTPUT_TOKENS


@pytest.mark.parametrize("bad", [0, -1, 1.5, "32768", True])
def test_invalid_cap_fails_loudly(bad):
    with pytest.raises(ValueError, match="max_output_tokens"):
        _Agent(skip_lemonade=True, silent_mode=True, max_output_tokens=bad)


def _fake_anthropic(monkeypatch):
    mod = types.ModuleType("anthropic")

    class Anthropic:
        def __init__(self, **kwargs):
            self.messages = SimpleNamespace(create=MagicMock())

    mod.Anthropic = Anthropic
    for name in (
        "APIStatusError",
        "AuthenticationError",
        "NotFoundError",
        "RateLimitError",
        "APIConnectionError",
    ):
        setattr(mod, name, type(name, (Exception,), {}))
    monkeypatch.setattr("gaia.llm.providers.claude.anthropic", mod)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-unit-test")


@pytest.mark.parametrize("cap,expected", [(None, 8192), (20000, 20000)])
def test_claude_request_keeps_its_default_and_honors_an_explicit_cap(
    monkeypatch, cap, expected
):
    _fake_anthropic(monkeypatch)
    agent = _Agent(use_claude=True, silent_mode=True, max_output_tokens=cap)
    create = agent.chat.llm_client._client.messages.create
    create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"answer": "done"}')],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    agent.process_query("hi")
    assert create.call_args.kwargs["max_tokens"] == expected


@pytest.mark.parametrize(
    "cap,expected", [(None, LOCAL_MAX_OUTPUT_TOKENS), (20000, 20000)]
)
def test_openai_request_keeps_its_default_and_honors_an_explicit_cap(
    monkeypatch, cap, expected
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unit-test")
    agent = _Agent(use_chatgpt=True, silent_mode=True, max_output_tokens=cap)
    create = MagicMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"answer": "done"}'))
            ]
        )
    )
    agent.chat.llm_client._client.chat.completions.create = create
    agent.process_query("hi")
    assert create.call_args.kwargs["max_tokens"] == expected
