# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent.deny_tools: withheld tools are never offered or run."""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import check_was_executed


class _DenyAgent(Agent):
    calls = []

    def _get_system_prompt(self) -> str:
        return "Use the tools."

    def _register_tools(self) -> None:
        @tool
        def deny_keep(value: str) -> dict:
            """A tool the tests leave on."""
            self.calls.append(("keep", value))
            return {"status": "success", "value": value}

        @tool
        def deny_web_search(value: str) -> dict:
            """A tool the tests turn off."""
            self.calls.append(("web_search", value))
            return {"status": "success", "value": value}

        @tool
        def deny_web_fetch(value: str) -> dict:
            """A second tool the glob turns off."""
            self.calls.append(("web_fetch", value))
            return {"status": "success", "value": value}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


def _build(**kwargs):
    _DenyAgent.calls = []
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _DenyAgent(silent_mode=True, skip_lemonade=True, **kwargs)
    agent._snapshot_tools()
    return agent


@pytest.fixture
def agent():
    return _build()


def test_glob_withholds_every_match_and_returns_their_names(agent):
    assert agent.deny_tools(["deny_web_*"]) == ["deny_web_fetch", "deny_web_search"]
    tools = agent.get_tools_info()
    assert "deny_keep" in tools
    assert "deny_web_search" not in tools
    assert "deny_web_fetch" not in tools


def test_denied_tools_leave_the_prompt(agent):
    assert "deny_web_search" in agent._format_tools_for_prompt()
    agent.deny_tools(["deny_web_search"])
    prompt = agent._format_tools_for_prompt()
    assert "deny_web_search" not in prompt
    assert "deny_keep" in prompt


def test_a_denied_tool_is_refused_before_it_runs(agent):
    agent.deny_tools(["deny_web_search"])
    result = agent._execute_tool("deny_web_search", {"value": "no"})
    assert result["status"] == "error"
    assert check_was_executed(result) is False
    assert _DenyAgent.calls == []
    assert agent._execute_tool("deny_keep", {"value": "ok"})["status"] == "success"


def test_a_tool_registered_after_the_deny_is_withheld_too(agent):
    # A skill's tools land in the registry mid-session, namespaced by skill.
    agent.deny_tools(["deny_web_*"])
    entry = dict(agent._tools_registry["deny_keep"])
    agent._instance_tools["some-skill/deny_web_later"] = entry
    assert "some-skill/deny_web_later" not in agent.get_tools_info()
    assert "deny_web_later" not in agent._format_tools_for_prompt()


def test_a_pattern_matching_nothing_is_an_error_and_denies_nothing(agent):
    with pytest.raises(ValueError, match="No tool matches deny_typo"):
        agent.deny_tools(["deny_web_search", "deny_typo"])
    assert "deny_web_search" in agent.get_tools_info()


def test_a_bare_string_is_refused(agent):
    with pytest.raises(TypeError):
        agent.deny_tools("deny_web_search")


def test_deny_narrows_an_existing_scope():
    agent = _build(tool_scope=["deny_keep", "deny_web_search"])
    agent.deny_tools(["deny_web_search"])
    assert set(agent.get_tools_info()) == {"deny_keep"}


def test_no_deny_leaves_every_tool_on(agent):
    assert {"deny_keep", "deny_web_search", "deny_web_fetch"} <= set(
        agent.get_tools_info()
    )
    assert agent._execution_tool_registry() is agent._tools_registry
