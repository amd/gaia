# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool name with incidental surrounding whitespace must still resolve.

Observed on a local model calling a long MCP tool name: it emitted
``"mcp_catalyst_memory_read_developmental_candidates "`` (one trailing
space). ``_execute_tool``'s normalization stripped a trailing ``()`` and
handled a hyphen/underscore mismatch, but never stripped whitespace, so the
exact-match lookup, ``_resolve_tool_name``'s suffix/exact checks, and the
prefix-candidate search all failed identically on the same stray space --
the model got the fully generic "Unknown tool name" error with no candidate
list, even though the intended tool was registered under (almost) the exact
name requested.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import check_was_executed


class _DummyAgent(Agent):
    """Minimal concrete Agent with one real registered tool."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def mcp_catalyst_memory_read_developmental_candidates(
            record_type: str,
        ) -> dict:
            """Stand-in for a real, long, underscore-heavy MCP tool name."""
            return {"status": "success", "record_type": record_type}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _DummyAgent(silent_mode=True, skip_lemonade=True)


class TestTrailingAndLeadingWhitespace:
    def test_trailing_space_resolves(self, agent):
        result = agent._execute_tool(
            "mcp_catalyst_memory_read_developmental_candidates ",
            {"record_type": "observations"},
        )
        assert result.get("status") != "error", result
        assert "Unknown tool name" not in str(result.get("error", ""))

    def test_leading_space_resolves(self, agent):
        result = agent._execute_tool(
            " mcp_catalyst_memory_read_developmental_candidates",
            {"record_type": "observations"},
        )
        assert result.get("status") != "error", result
        assert "Unknown tool name" not in str(result.get("error", ""))

    def test_trailing_whitespace_and_call_parens_both_stripped(self, agent):
        result = agent._execute_tool(
            "mcp_catalyst_memory_read_developmental_candidates() ",
            {"record_type": "observations"},
        )
        assert result.get("status") != "error", result
        assert "Unknown tool name" not in str(result.get("error", ""))

    def test_exact_name_without_whitespace_still_resolves(self, agent):
        """Guard against a fix that only handles the whitespace case.

        Asserts the name resolved to the real tool (not "Unknown tool
        name") rather than a full success, since whether the call is then
        confirmation-gated is a separate concern from name resolution.
        """
        result = agent._execute_tool(
            "mcp_catalyst_memory_read_developmental_candidates",
            {"record_type": "observations"},
        )
        assert "Unknown tool name" not in str(result.get("error", "")), result

    def test_space_before_call_parens_also_resolves(self, agent):
        """A stray space *before* "()" is a second, distinct gap: stripping
        once before removesuffix("()") leaves a trailing space behind for
        this ordering (name un-suffixed, then re-stripped), so the fix
        strips again after removesuffix too."""
        result = agent._execute_tool(
            "mcp_catalyst_memory_read_developmental_candidates ()",
            {"record_type": "observations"},
        )
        assert result.get("status") != "error", result
        assert "Unknown tool name" not in str(result.get("error", ""))

    def test_whitespace_only_name_reports_not_executed(self, agent):
        """A whitespace-only name strips down to empty and must still carry
        NOT_EXECUTED, the same as the pre-existing "()"-only case -- so the
        verification footer never reports a rejected call as one that ran
        and failed (#3677)."""
        result = agent._execute_tool("   ", {"record_type": "observations"})
        assert result.get("status") == "error", result
        assert check_was_executed(result) is False, result
