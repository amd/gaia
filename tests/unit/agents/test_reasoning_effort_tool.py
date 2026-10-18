# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent sets its own reasoning effort, step by step, on models that grade it.

gpt-oss reasons at low, medium or high effort. A fixed level either wastes time
on routine steps or under-thinks hard ones, so the model chooses: the level it
sets reaches every later model call of that turn, and the next user turn
starts again at the model's default.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.reasoning_tools import ReasoningEffortToolsMixin
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, GPT_OSS_20B_MODEL_NAME


class _EffortAgent(ReasoningEffortToolsMixin, Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        self.register_reasoning_effort_tools()

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.pop("set_reasoning_effort", None)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _build(model, streaming=False):
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _EffortAgent(silent_mode=True, skip_lemonade=True, model_id=model)
    built.streaming = streaming
    built.verification_scope_statement = lambda: None
    return built


def _script(agent, *replies):
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})

    def _send(*_, **__):
        return SimpleNamespace(text=queue.pop(0), stats={})

    def _stream(*_, **__):
        text = queue.pop(0)
        if text.startswith('{"__tool_calls__"'):
            yield SimpleNamespace(text=text, is_complete=True, stats={})
            return
        yield SimpleNamespace(text=text, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    chat.send_messages = MagicMock(side_effect=_send)
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat
    return chat


def _set(level, reason="hard step"):
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {
                        "name": "set_reasoning_effort",
                        "arguments": json.dumps({"level": level, "reason": reason}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
            "content": None,
        }
    )


def _efforts(chat, streaming):
    calls = (
        chat.send_messages_stream if streaming else chat.send_messages
    ).call_args_list
    return [
        (c.kwargs.get("chat_template_kwargs") or {}).get("reasoning_effort")
        for c in calls
    ]


@pytest.mark.parametrize("streaming", [False, True], ids=["non-streaming", "streaming"])
def test_chosen_level_reaches_the_next_calls_and_resets_next_turn(
    clean_registry, streaming
):
    agent = _build(GPT_OSS_20B_MODEL_NAME, streaming)
    chat = _script(agent, _set("high"), "391", "Done.")

    assert agent.process_query("multiply 17 by 23")["result"] == "391"
    assert agent.process_query("thanks")["result"] == "Done."

    # First call: the model's default (nothing sent); after the tool: high;
    # the next user turn: the default again.
    assert _efforts(chat, streaming) == [None, "high", None]


def test_tool_is_only_offered_on_models_that_grade_reasoning(clean_registry):
    _build(DEFAULT_MODEL_NAME)
    assert "set_reasoning_effort" not in _TOOL_REGISTRY
    _build(GPT_OSS_20B_MODEL_NAME)
    assert "set_reasoning_effort" in _TOOL_REGISTRY


def test_an_invalid_level_is_refused_and_changes_nothing(clean_registry):
    agent = _build(GPT_OSS_20B_MODEL_NAME)
    out = agent._set_reasoning_effort("maximum", "")
    assert out["status"] == "error" and "low, medium, high" in out["error"]
    assert agent.reasoning_effort_kwargs() == {}


def test_a_valid_level_reports_what_it_replaced(clean_registry, monkeypatch):
    monkeypatch.delenv("GAIA_REASONING_EFFORT", raising=False)
    agent = _build(GPT_OSS_20B_MODEL_NAME)
    assert agent._set_reasoning_effort(" Low ", "listing files") == {
        "status": "success",
        "previous": "medium",
        "level": "low",
    }
    assert agent.reasoning_effort_kwargs() == {
        "chat_template_kwargs": {"reasoning_effort": "low"}
    }


def test_agent_without_the_mixin_sends_nothing_extra():
    class _Plain(Agent):
        def _get_system_prompt(self):
            return "t"

        def _register_tools(self):
            pass

    with patch("gaia.agents.base.agent.AgentSDK"):
        plain = _Plain(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    assert plain._reasoning_request_kwargs() == {}
