# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-of-turn checks resolve relative paths in the chat's folder, not the process's.

The Agent UI backend and the daemon start in the home folder while the chat works
in its own folder. A request naming ``notes.csv`` must find the file the user
means, and every per-turn guard must share that same root.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base import agent as agent_module
from gaia.agents.base.tools import _TOOL_REGISTRY
from tests.unit.agents.agent_script import LookingAgent, answer, script


@pytest.fixture
def agent():
    with (
        patch("gaia.agents.base.agent.AgentSDK"),
        patch("gaia.agents.base.agent._LOOK_TOOLS", ("look_first_test_reader",)),
    ):
        a = LookingAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        yield a
    # The registry is process-wide; a leftover test tool breaks the flagship's label check.
    _TOOL_REGISTRY.pop("look_first_test_reader", None)


def test_turn_start_checks_use_the_chat_folder(agent, chat_and_process):
    chat, _process = chat_and_process
    agent.working_directory = str(chat)
    script(agent, answer("done"))

    agent.process_query("hello", max_steps=5)

    expected = str(chat.resolve())
    assert agent._denied_effects.cwd == expected
    assert agent._extraction_ledger.root == expected
    assert agent._turn_scope.root == expected
    assert agent._completion_evidence.root == expected


def test_grounding_checks_start_at_the_chat_folder(
    agent, chat_and_process, monkeypatch
):
    chat, _process = chat_and_process
    agent.working_directory = str(chat)
    agent._turn_tool_executions = []
    agent._original_user_input = "Summarize notes.csv"
    agent._current_query = "Summarize notes.csv"
    seen = []
    real_path_locator = agent_module.path_locator

    def spy(roots):
        seen.append(roots)
        return real_path_locator(roots)

    monkeypatch.setattr(agent_module, "path_locator", spy)
    agent._grounding_findings("I read notes.csv", "Summarize notes.csv", "")

    assert seen and seen[0][0] == str(chat.resolve())

    captured = {}
    monkeypatch.setattr(agent, "_grounding_findings", lambda *args: [])

    def capture(answer, query, history, roots):
        captured["roots"] = roots
        return []

    monkeypatch.setattr(agent, "_deliverable_findings", capture)
    agent._note_ungrounded("done", "Summarize notes.csv")

    assert captured["roots"][0] == str(chat.resolve())
