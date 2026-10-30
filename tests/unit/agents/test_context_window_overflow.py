# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An overflow on a deliberately small window is trimmed, not reloaded (#5284).

The overflow branches used to treat any error naming a 4K/8K/16K window as a
bad load and re-raise for a reload that cannot change the window. Only the
loaded-versus-requested comparison may decide that now.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, _loaded_context_is_too_small
from gaia.llm.lemonade_client import LemonadeClient

OWN_MODEL = "Qwen3-4B-GGUF"
REGISTRY_CTX = 65536


def test_intentionally_small_loaded_window_is_not_misclassified_when_requested():
    assert not _loaded_context_is_too_small(16384, 16384)
    assert not _loaded_context_is_too_small(8192, 8192)


def test_loaded_window_below_requested_target_is_still_a_bad_load():
    assert _loaded_context_is_too_small(8192, 16384)
    assert _loaded_context_is_too_small(16384, 32768)


def test_loaded_window_above_requested_target_is_not_a_bad_load():
    assert not _loaded_context_is_too_small(65536, 16384)


@pytest.mark.parametrize(
    "loaded,requested",
    [
        (0, 16384),
        (-1, 16384),
        (None, 16384),
        (True, 16384),
        (16384, True),
        ("16384", 16384),
        (8192.0, 16384),
        (8192, 0),
        (8192, None),
    ],
)
def test_context_window_comparison_rejects_untrusted_values(loaded, requested):
    """Malformed health/config values must not trigger a reload."""
    assert not _loaded_context_is_too_small(loaded, requested)


# --- the health probe honours an exact client pin -------------------------


def _probe(loaded_ctx, pin=None, backend_cls=LemonadeClient):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "all_models_loaded": [
            {
                "model_name": OWN_MODEL,
                "type": "llm",
                "recipe_options": {"ctx_size": loaded_ctx},
            }
        ]
    }
    backend = MagicMock(spec=backend_cls)
    backend.ctx_size_override = pin
    agent = SimpleNamespace(
        model_id=OWN_MODEL,
        device="gpu",
        chat=SimpleNamespace(llm_client=SimpleNamespace(_backend=backend)),
    )
    with (
        patch("httpx.get", return_value=resp),
        patch("gaia.llm.lemonade_client.resolve_ctx_size", return_value=REGISTRY_CTX),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://localhost:13305/api/v1",
        ),
    ):
        return Agent._is_loaded_ctx_too_small(agent)


def test_pinned_16k_window_loaded_at_16k_is_not_a_bad_load():
    # Without the pin the registry target (64K) would flag this load.
    assert _probe(16384, pin=16384) is False


def test_without_a_pin_the_registry_target_still_flags_an_undersized_load():
    assert _probe(16384, pin=None) is True


def test_load_below_the_pin_is_still_a_bad_load():
    assert _probe(8192, pin=16384) is True


@pytest.mark.parametrize("pin", [True, 0, -4096, "16384", 16384.0])
def test_malformed_pin_is_ignored_and_the_registry_target_applies(pin):
    assert _probe(16384, pin=pin) is True


def test_agent_without_a_chat_client_falls_back_to_the_registry_target():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "all_models_loaded": [
            {
                "model_name": OWN_MODEL,
                "type": "llm",
                "recipe_options": {"ctx_size": 16384},
            }
        ]
    }
    agent = SimpleNamespace(model_id=OWN_MODEL, device="gpu")
    with (
        patch("httpx.get", return_value=resp),
        patch("gaia.llm.lemonade_client.resolve_ctx_size", return_value=REGISTRY_CTX),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://localhost:13305/api/v1",
        ),
    ):
        assert Agent._is_loaded_ctx_too_small(agent) is True


# --- the agent loop trims instead of re-raising ---------------------------


class _DummyAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
    # A large old tool result, so the overflow shrink frees enough to retry.
    a.conversation_history = [
        {"role": "user", "content": "read the report"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "h1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "h1",
            "name": "read_file",
            "content": "lorem " * 4000,
        },
        {"role": "assistant", "content": "Read it."},
    ]
    return a


SMALL_WINDOW_ERRORS = [
    f"request (17000 tokens) exceeds the available context size ({n} tokens)"
    for n in (4096, 8192, 16384)
] + [
    "Error in chat completions (status 400): "
    "{'error': {'message': 'request exceeds the available context size', "
    f"'n_ctx': {n}}}}}"
    for n in (4096, 8192, 16384)
]


def _types(agent):
    return [e.get("type") for e in agent.error_history]


@pytest.mark.parametrize("error_text", SMALL_WINDOW_ERRORS)
def test_small_window_overflow_trims_and_retries_non_streaming(agent, error_text):
    agent.streaming = False
    agent._is_loaded_ctx_too_small = lambda: False
    responses = [
        RuntimeError(error_text),
        json.dumps({"thought": "ok", "answer": "Here you go."}),
    ]

    def _send(*_, **__):
        r = responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return SimpleNamespace(text=r, stats={})

    agent.chat.send_messages = MagicMock(side_effect=_send)

    agent.process_query("anything", max_steps=5)

    assert agent.chat.send_messages.call_count == 2
    assert "llm_context_overflow_trimmed" in _types(agent)
    assert "llm_wrong_ctx_loaded_reraise" not in _types(agent)


@pytest.mark.parametrize("error_text", SMALL_WINDOW_ERRORS)
def test_small_window_overflow_trims_and_retries_streaming(agent, error_text):
    agent.streaming = True
    agent._is_loaded_ctx_too_small = lambda: False
    calls = {"n": 0}

    def _send_stream(*_, **__):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(error_text)
        return iter([SimpleNamespace(text="Here you go.", is_complete=False, stats={})])

    agent.chat.send_messages_stream = MagicMock(side_effect=_send_stream)

    result = agent.process_query("anything", max_steps=5)

    assert calls["n"] == 2
    assert "llm_context_overflow_trimmed" in _types(agent)
    assert "llm_wrong_ctx_loaded_reraise" not in _types(agent)
    assert "Here you go." in result["result"]


@pytest.mark.parametrize("streaming", [False, True])
def test_undersized_load_still_reraises_for_a_reload(agent, streaming):
    agent.streaming = streaming
    agent._is_loaded_ctx_too_small = lambda: True
    err = RuntimeError(SMALL_WINDOW_ERRORS[0])
    agent.chat.send_messages = MagicMock(side_effect=err)
    agent.chat.send_messages_stream = MagicMock(side_effect=err)

    with pytest.raises(RuntimeError, match="exceeds the available context size"):
        agent.process_query("anything", max_steps=5)
    assert "llm_wrong_ctx_loaded_reraise" in _types(agent)
    assert "llm_context_overflow_trimmed" not in _types(agent)


@pytest.mark.parametrize("streaming", [False, True])
def test_non_overflow_error_never_consults_the_probe(agent, streaming):
    agent.streaming = streaming
    probe = MagicMock(return_value=True)
    agent._is_loaded_ctx_too_small = probe
    err = RuntimeError("context size (16384 tokens) is fine; tool crashed")
    agent.chat.send_messages = MagicMock(side_effect=err)
    agent.chat.send_messages_stream = MagicMock(side_effect=err)

    agent.process_query("anything", max_steps=2)

    probe.assert_not_called()
    assert "llm_wrong_ctx_loaded_reraise" not in _types(agent)
