# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A plan step's goal line quotes the user's words, not the memory context."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_ASK = "list the work folder and read notes.txt"
_AUGMENTED = (
    "[GAIA Memory Context]\nCurrent time: 2026-10-07T17:13:33-0700 (Wednesday)"
    f"\n\n{_ASK}"
)


class _GoalConsole(AgentConsole):
    def __init__(self):
        super().__init__()
        self.goals = []

    def print_goal(self, goal):
        self.goals.append(goal)


class _PlanAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def list_plan_goal_test(path: str) -> str:
            """List a folder."""
            del path
            return "notes.txt"

        @tool
        def read_plan_goal_test(path: str) -> str:
            """Read a file."""
            del path
            return "hello"

    def _create_console(self):
        return _GoalConsole()


@pytest.fixture
def agent():
    snapshot = dict(_TOOL_REGISTRY)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _PlanAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    built.streaming = False
    built.verification_scope_statement = lambda: None
    yield built
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _script(agent, *replies):
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(
        side_effect=lambda *_, **__: SimpleNamespace(text=queue.pop(0), stats={})
    )
    agent.chat = chat


def test_plan_step_goal_quotes_the_user_not_the_memory_context(agent):
    plan = [
        {"tool": "list_plan_goal_test", "tool_args": {"path": "work"}},
        {"tool": "read_plan_goal_test", "tool_args": {"path": "work/notes.txt"}},
    ]
    first = json.dumps(
        {
            "thought": "List, then read.",
            "goal": "Read the notes",
            "plan": plan,
            "tool": plan[0]["tool"],
            "tool_args": plan[0]["tool_args"],
        }
    )
    _script(agent, first, json.dumps({"answer": "It says hello."}))
    # What MemoryMixin.process_query hands the base loop.
    agent._original_user_input = _ASK

    agent.process_query(_AUGMENTED)

    plan_goals = [g for g in agent.console.goals if g.startswith("Following the plan")]
    assert plan_goals
    assert set(plan_goals) == {f"Following the plan to {_ASK}"}
