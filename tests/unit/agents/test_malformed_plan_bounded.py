# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A malformed reply can never run the model past the step budget."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.base.verification import strip_verification_scope

_QUERY = "Return exactly this JSON object and nothing else."
_NOT_A_PLAN = '{"plan": "buy milk", "tool": "hammer"}'


class _NoToolAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAIA_AGENT_VERIFY_EDITS", "0")
    with patch("gaia.agents.base.agent.AgentSDK"):
        made = _NoToolAgent(silent_mode=True, skip_lemonade=True)
    made.streaming = False
    yield made
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


class _TooManyCalls(Exception):
    pass


def _always(agent, reply, ceiling=60):
    """Answer every model call with *reply*; raise past *ceiling* calls."""
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.calls = 0

    def _send(*_, **__):
        chat.calls += 1
        if chat.calls > ceiling:
            raise _TooManyCalls(f"the model was called {chat.calls} times")
        return SimpleNamespace(text=reply, stats={})

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return chat


def test_a_plan_that_is_not_a_list_is_the_answer(agent):
    chat = _always(agent, _NOT_A_PLAN)

    result = agent.process_query(_QUERY, max_steps=8)

    assert chat.calls <= 3
    assert result["status"] == "success"
    assert strip_verification_scope(result["result"]).startswith(_NOT_A_PLAN)


@pytest.mark.parametrize(
    "reply",
    [
        _NOT_A_PLAN,
        '{"plan": "buy milk"}',
        '{"plan": {"step": "buy milk"}}',
        '{"plan": ["buy milk"]}',
        '{"plan": [{"description": "buy milk"}]}',
        '{"plan": [{"tool": "hammer", "tool_args": {}}], "tool": "hammer"}',
        '{"tool": "hammer", "tool_args": {}}',
        '{"thought": "thinking"}',
        '{"needs_plan": true}',
        '{"tool_args": {"a": 1}}',
        '{"tool_calls": "nope"}',
        '{"answer": ""}',
        "{not json",
        "",
    ],
)
@pytest.mark.parametrize("max_steps", [1, 4])
def test_the_step_budget_bounds_model_calls(agent, reply, max_steps):
    # Past the budget only the closing ask and its one no-tools retry run.
    chat = _always(agent, reply, ceiling=max_steps + 2)

    result = agent.process_query(_QUERY, max_steps=max_steps)

    assert chat.calls <= max_steps + 2
    assert isinstance(result.get("result"), str)


@pytest.mark.parametrize(
    "reply",
    [
        '{"thought": "x", "plan": "look it up"}',
        '{"goal": "y", "plan": "search, then summarize"}',
    ],
)
def test_an_agent_reply_with_a_sentence_plan_is_corrected_not_shown(agent, reply):
    chat = _always(agent, reply)

    result = agent.process_query(_QUERY, max_steps=50)

    assert chat.calls <= 3
    assert reply not in result["result"]
    assert "trouble formatting my plan" in result["result"]


def test_a_plan_with_bad_steps_is_corrected_a_bounded_number_of_times(agent):
    chat = _always(agent, '{"plan": [{"description": "buy milk"}]}')

    result = agent.process_query(_QUERY, max_steps=50)

    assert chat.calls <= 3
    assert "trouble formatting my plan" in result["result"]


def test_a_real_plan_is_still_run(agent):
    from gaia.agents.base.tools import tool

    ran = []

    @tool
    def note_it(text: str) -> dict:
        """Record a note."""
        ran.append(text)
        return {"status": "success"}

    agent._tools_registry["note_it"] = _TOOL_REGISTRY["note_it"]
    replies = iter(
        [
            '{"plan": [{"tool": "note_it", "tool_args": {"text": "milk"}}], '
            '"tool": "note_it", "tool_args": {"text": "milk"}}',
            '{"answer": "Noted milk."}',
        ]
    )
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(
        side_effect=lambda *_, **__: SimpleNamespace(text=next(replies), stats={})
    )
    agent.chat = chat

    result = agent.process_query(_QUERY, max_steps=8)

    assert ran == ["milk"]
    assert "Noted milk." in result["result"]
