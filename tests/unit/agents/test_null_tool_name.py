# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for null/empty tool name guard clauses in the base Agent class."""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    """Minimal concrete Agent for testing."""

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
        return _DummyAgent(silent_mode=True, skip_lemonade=True)


class TestExecuteToolNullName:
    def test_none_tool_name_returns_error_dict(self, agent):
        result = agent._execute_tool(None, {})
        assert isinstance(result, dict)
        assert result.get("status") == "error"
        assert "Tool name is missing" in result.get("error", "")

    def test_empty_string_tool_name_returns_error_dict(self, agent):
        result = agent._execute_tool("", {})
        assert isinstance(result, dict)
        assert result.get("status") == "error"

    def test_none_tool_name_does_not_raise(self, agent):
        # Should never raise AttributeError
        try:
            agent._execute_tool(None, {})
        except AttributeError:
            pytest.fail("_execute_tool raised AttributeError on None tool name")


class TestParseLlmResponseNullTool:
    def test_null_tool_not_treated_as_tool_call(self, agent):
        """LLM response with tool=null and an answer should return the answer."""
        response = '{"tool": null, "tool_args": {}, "answer": "Hello there!"}'
        result = agent._parse_llm_response(response)
        # With tool=null the parsed dict has "tool" key but value is None.
        # The caller guard (parsed.get("tool")) treats this as falsy,
        # so the answer path should be taken instead.
        # _parse_llm_response itself returns the parsed JSON as-is;
        # the guard lives in process_query. We verify the parsed result
        # contains the answer and that tool value is falsy.
        assert not result.get("tool")
        assert result.get("answer") == "Hello there!"

    def test_null_tool_only_response_is_not_a_tool_call(self, agent):
        """LLM response with only tool=null should not be treated as a tool call."""
        response = '{"tool": null, "tool_args": {}}'
        result = agent._parse_llm_response(response)
        assert not result.get("tool")
