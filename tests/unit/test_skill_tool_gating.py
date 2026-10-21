# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill's tools are gated by the rules a core tool of the same name meets.

Skill tools register as ``<skill>/<tool>``. The confirmation gate and the "did
this change a file" check both classify tools by name, and both compared the
namespaced key: ``office-docs/write_file`` matched neither the confirmation set
nor the ``write_`` prefix, so a skill tool skipped checks its core twin gets.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import bare_tool_name
from gaia.agents.base.turn_scope import TurnScopeGuard
from gaia.agents.base.verification import is_mutating_tool


class _GateAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _GateAgent(silent_mode=True, skip_lemonade=True)
    built._instance_tools = {}
    return built


@pytest.mark.parametrize(
    ("name", "bare"),
    [
        ("rss-digest/fetch_rss", "fetch_rss"),
        ("read_file", "read_file"),
        ("mcp_server_read_file", "mcp_server_read_file"),
        ("", ""),
    ],
)
def test_bare_tool_name_strips_only_the_skill_namespace(name, bare):
    assert bare_tool_name(name) == bare


def test_a_skill_tool_named_like_a_gated_core_tool_asks_first(agent):
    assert agent._tool_requires_confirmation("office-docs/write_file", {})


def test_no_grant_pre_authorizes_a_skill_tool(agent):
    # Grants describe what the CORE tool does with its arguments; a skill's
    # own run_shell_command could do anything with the same arguments.
    with patch.object(_GateAgent, "_call_is_pre_authorized", return_value=True):
        assert agent._tool_requires_confirmation("run_shell_command", {}) is False
        assert agent._tool_requires_confirmation("x/run_shell_command", {}) is True


def test_an_ordinary_skill_tool_is_not_gated(agent):
    assert agent._tool_requires_confirmation("rss-digest/fetch_rss", {}) is False


def test_a_skill_tool_still_honors_its_registry_flag(agent):
    agent._instance_tools["office-docs/render"] = {"requires_confirmation": True}
    assert agent._tool_requires_confirmation("office-docs/render", {})


@pytest.mark.parametrize(
    ("name", "mutating"),
    [
        ("office-docs/write_docx", True),
        ("office-docs/edit_file", True),
        ("rss-digest/fetch_rss", False),
        ("log-triage/summarize_log", False),
    ],
)
def test_a_skill_tool_that_writes_counts_as_a_change(name, mutating):
    assert is_mutating_tool(name) is mutating


@pytest.mark.parametrize(
    ("tool", "clears"),
    [("office-docs/write_docx", True), ("rss-digest/fetch_rss", False)],
)
def test_the_turn_scope_guard_treats_a_skill_write_as_a_change(tool, clears):
    """A write resets failure streaks ("the world changed"); a read does not."""
    guard = TurnScopeGuard(3)
    guard.begin_turn("make a report", root=".")
    guard.record("read_file", {"file_path": "a.txt"}, {"status": "error", "error": "x"})
    assert guard.failures

    guard.record(tool, {"file_path": "report.docx"}, {"status": "success"})

    assert (not guard.failures) is clears
