# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the loop sends when an in-text plan finishes, under GAIA_AGENT_PLAN_RECAP.

By default the completion prompt restates the task and the whole plan. With
``GAIA_AGENT_PLAN_RECAP=0`` it is one line: the plan is already in the history
as the model's own message, and its results as the tool messages after it.
Nothing else in the request changes, and a native tool-calling turn, which
never makes an in-text plan, sends the same request either way.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, plan_recap_enabled
from gaia.agents.base.tools import _TOOL_REGISTRY, tool

_LOOKUP = "lookup_for_plan_note_test"
_TASK = "Look up the alpha and beta settings and tell me both values."


class _Agent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def lookup_for_plan_note_test(key: str) -> dict:
            """Look a setting up."""
            return {"status": "success", "key": key, "value": f"{key}-value"}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        made = _Agent(silent_mode=True, skip_lemonade=True)
    made.streaming = False
    yield made
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _stub_chat(agent, *replies):
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.sent = []

    def _send(*_, **kwargs):
        chat.sent.append(
            {
                "messages": [dict(m) for m in kwargs.get("messages") or []],
                "tools": kwargs.get("tools"),
                "system_prompt": kwargs.get("system_prompt"),
            }
        )
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        return SimpleNamespace(text=queue.pop(0), stats={})

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return chat


def _plan() -> str:
    return json.dumps(
        {
            "thought": "Two lookups.",
            "goal": "Report both settings.",
            "plan": [
                {"tool": _LOOKUP, "tool_args": {"key": "alpha"}},
                {"tool": _LOOKUP, "tool_args": {"key": "beta"}},
            ],
        }
    )


def _answer() -> str:
    return json.dumps(
        {"thought": "", "goal": "", "answer": "alpha-value and beta-value."}
    )


def _scrub(value):
    """*value* with the per-run random tool-call ids blanked."""
    if isinstance(value, dict):
        return {
            k: "<id>" if k in ("tool_call_id", "id") else _scrub(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _completion_prompt(chat) -> str:
    """The user message the loop added after the plan ran."""
    return chat.sent[1]["messages"][-1]["content"]


def test_by_default_the_task_and_plan_are_restated(agent, monkeypatch):
    monkeypatch.delenv("GAIA_AGENT_PLAN_RECAP", raising=False)
    chat = _stub_chat(agent, _plan(), _answer())

    agent.process_query(_TASK)

    prompt = _completion_prompt(chat)
    assert prompt.startswith("You have successfully completed all steps in the plan.")
    assert f"Task: {_TASK}" in prompt
    assert '"completed_plan"' in prompt and '"beta"' in prompt


@pytest.mark.parametrize("value", ["0", "false", "off", "no", " OFF "])
def test_off_sends_one_line(agent, monkeypatch, value):
    monkeypatch.setenv("GAIA_AGENT_PLAN_RECAP", value)
    chat = _stub_chat(agent, _plan(), _answer())

    result = agent.process_query(_TASK)

    prompt = _completion_prompt(chat)
    assert "\n" not in prompt
    assert prompt.startswith("All 2 plan steps ran;")
    assert '"answer"' in prompt and "new plan" in prompt
    assert _TASK not in prompt and "completed_plan" not in prompt
    assert len(prompt) < 250
    assert "alpha-value" in result["result"]


def test_off_changes_only_the_completion_message(agent, monkeypatch):
    """Same script both ways: every other message, the tools and the prompt match."""
    runs = {}
    for value in ("1", "0"):
        monkeypatch.setenv("GAIA_AGENT_PLAN_RECAP", value)
        chat = _stub_chat(agent, _plan(), _answer())
        agent.process_query(_TASK)
        runs[value] = _scrub(chat.sent)

    on, off = runs["1"], runs["0"]
    assert len(on) == len(off) == 2
    assert on[0] == off[0]
    assert on[1]["tools"] == off[1]["tools"]
    assert on[1]["system_prompt"] == off[1]["system_prompt"]
    assert on[1]["messages"][:-1] == off[1]["messages"][:-1]
    assert on[1]["messages"][-1] != off[1]["messages"][-1]


def _native_call() -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": _LOOKUP,
                        "arguments": json.dumps({"key": "alpha"}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def test_a_native_tool_calling_turn_is_unchanged(agent, monkeypatch):
    runs = {}
    for value in ("1", "0"):
        monkeypatch.setenv("GAIA_AGENT_PLAN_RECAP", value)
        chat = _stub_chat(agent, _native_call(), _answer())
        agent.process_query(_TASK)
        runs[value] = chat.sent
    assert runs["1"] == runs["0"]


@pytest.mark.parametrize("value", ["maybe", "2", "yes please"])
def test_a_value_that_is_not_a_boolean_fails_loudly(monkeypatch, value):
    monkeypatch.setenv("GAIA_AGENT_PLAN_RECAP", value)
    with pytest.raises(ValueError, match="GAIA_AGENT_PLAN_RECAP"):
        plan_recap_enabled()


@pytest.mark.parametrize("value", [None, "", "1", "true", "on", "yes"])
def test_unset_or_on_keeps_the_recap(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("GAIA_AGENT_PLAN_RECAP", raising=False)
    else:
        monkeypatch.setenv("GAIA_AGENT_PLAN_RECAP", value)
    assert plan_recap_enabled() is True
