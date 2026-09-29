# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An answer about named workspace files that never looked is sent back once.

From Qwen3 30B on the everyday benchmark: asked to review ``toybox/dates.py``
it answered "the file is missing" with no tool call, and asked what the file
does it guessed. Both are one look away from right.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.look_first import named_workspace_paths
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "toybox").mkdir()
    (tmp_path / "toybox" / "dates.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# toybox\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "request_text,expected",
    [
        ("What does toybox/dates.py do?", ["toybox/dates.py"]),
        ("Document it in README.md and keep the suite green.", ["README.md"]),
        # Not there, but its folder is: the agent should go and find it.
        ("Review toybox/parsers.py", ["toybox/parsers.py"]),
        ("What is 17 times 23?", []),
        ("How does Node.js handle this?", []),
        ("Write notes.md with a summary.", []),
    ],
)
def test_named_workspace_paths(workspace, request_text, expected):
    assert named_workspace_paths(request_text, str(workspace)) == expected


def test_the_working_folder_alone_is_not_something_to_look_at(workspace):
    request_text = f"You are working in {workspace}. What is 2 + 2?"
    assert named_workspace_paths(request_text, str(workspace)) == []


class _LookingAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def look_first_test_reader(file_path: str) -> dict:
            """Read a file."""
            return {"status": "success", "file_path": file_path, "content": "x = 1"}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with (
        patch("gaia.agents.base.agent.AgentSDK"),
        patch("gaia.agents.base.agent._LOOK_TOOLS", ("look_first_test_reader",)),
    ):
        a = _LookingAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        yield a
    # The registry is process-wide; a leftover test tool breaks the flagship's label check.
    _TOOL_REGISTRY.pop("look_first_test_reader", None)


def _script(agent_, *responses):
    queue = list(responses)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    agent_.chat = MagicMock()
    agent_.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def _answer(text):
    return json.dumps({"thought": "", "answer": text})


def _read(path):
    return json.dumps(
        {
            "thought": "",
            "tool": "look_first_test_reader",
            "tool_args": {"file_path": path},
        }
    )


GUESS = "The file is missing, so I can't review it."
REVIEW = "It assigns x = 1 and nothing else."


def test_an_unlooked_answer_is_sent_back_to_look(agent, workspace):
    sent = _script(agent, _answer(GUESS), _read("toybox/dates.py"), _answer(REVIEW))

    result = agent.process_query("Review toybox/dates.py", max_steps=10)

    assert "`toybox/dates.py`" in sent[1][-1]["content"]
    assert "without opening anything" in sent[1][-1]["content"]
    assert REVIEW in result["result"]


def test_it_asks_only_once(agent, workspace):
    sent = _script(agent, _answer(GUESS), _answer(GUESS))

    result = agent.process_query("Review toybox/dates.py", max_steps=10)

    assert len(sent) == 2
    assert GUESS in result["result"]


def test_an_answer_that_looked_is_left_alone(agent, workspace):
    sent = _script(agent, _read("toybox/dates.py"), _answer(REVIEW))

    agent.process_query("Review toybox/dates.py", max_steps=10)

    assert len(sent) == 2


def test_a_question_about_no_file_is_answered_directly(agent, workspace):
    sent = _script(agent, _answer("391"))

    result = agent.process_query("What is 17 times 23?", max_steps=10)

    assert len(sent) == 1
    assert "391" in result["result"]
