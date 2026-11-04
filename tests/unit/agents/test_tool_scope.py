# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Execution-enforced per-agent tool scopes."""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import check_was_executed


class _ScopedAgent(Agent):
    calls = []

    def _get_system_prompt(self) -> str:
        return "Use the tools."

    def _register_tools(self) -> None:
        @tool
        def scope_allowed(value: str) -> dict:
            """A tool permitted by the test scope."""
            self.calls.append(("allowed", value))
            return {"status": "success", "value": value}

        @tool
        def scope_hidden(value: str) -> dict:
            """A tool deliberately outside the test scope."""
            self.calls.append(("hidden", value))
            return {"status": "success", "value": value}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def scoped_agent():
    _ScopedAgent.calls = []
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _ScopedAgent(
            silent_mode=True,
            skip_lemonade=True,
            tool_scope=["scope_allowed"],
        )


@pytest.fixture
def unrestricted_agent():
    _ScopedAgent.calls = []
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _ScopedAgent(silent_mode=True, skip_lemonade=True)


def test_scope_allows_registered_tool(scoped_agent):
    result = scoped_agent._execute_tool("scope_allowed", {"value": "ok"})

    assert result["status"] == "success"
    assert _ScopedAgent.calls == [("allowed", "ok")]


def test_scope_blocks_direct_hidden_tool_before_execution(scoped_agent):
    result = scoped_agent._execute_tool("scope_hidden", {"value": "no"})

    assert result["status"] == "error"
    assert check_was_executed(result) is False
    assert _ScopedAgent.calls == []
    assert "scope_hidden" not in result["error"]


def test_scope_keeps_hidden_names_out_of_resolution_and_candidates(scoped_agent):
    # The unprefixed and hyphenated forms must resolve only against the scope.
    for name in ("hidden", "scope-hidden"):
        result = scoped_agent._execute_tool(name, {"value": "no"})
        assert result["status"] == "error"
        assert check_was_executed(result) is False
        assert "scope_hidden" not in result["error"]

    # A bare prefix may list permitted candidates, but never the hidden tool.
    result = scoped_agent._execute_tool("scope", {"value": "no"})
    assert result["status"] == "error"
    assert "scope_allowed" in result["error"]
    assert "scope_hidden" not in result["error"]
    assert _ScopedAgent.calls == []


def test_scope_filters_prompt_and_native_schema(scoped_agent):
    prompt = scoped_agent._format_tools_for_prompt()
    schemas = scoped_agent._build_openai_tool_schemas()
    schema_names = {item["function"]["name"] for item in schemas}

    assert "scope_allowed" in prompt
    assert "scope_hidden" not in prompt
    assert schema_names == {"scope_allowed"}
    assert set(scoped_agent.get_tools_info()) == {"scope_allowed"}
    assert len(scoped_agent.get_tools()) == 1


def test_none_scope_preserves_unrestricted_execution(unrestricted_agent):
    result = unrestricted_agent._execute_tool("scope_hidden", {"value": "yes"})

    assert result["status"] == "success"
    assert _ScopedAgent.calls == [("hidden", "yes")]
    assert "scope_hidden" in unrestricted_agent.get_tools_info()


def test_empty_scope_blocks_all_tools():
    _ScopedAgent.calls = []
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _ScopedAgent(silent_mode=True, skip_lemonade=True, tool_scope=[])

    result = agent._execute_tool("scope_allowed", {"value": "no"})

    assert result["status"] == "error"
    assert check_was_executed(result) is False
    assert _ScopedAgent.calls == []
    assert agent.get_tools_info() == {}


def test_scope_rejects_string_argument():
    with (
        patch("gaia.agents.base.agent.AgentSDK"),
        pytest.raises(TypeError, match="sequence"),
    ):
        _ScopedAgent(silent_mode=True, skip_lemonade=True, tool_scope="scope_allowed")
