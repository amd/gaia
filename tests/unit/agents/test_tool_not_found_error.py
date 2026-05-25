# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the candidate-list error message in ``Agent._execute_tool``.

When the model emits a bad tool name, the framework's error message
must guide it toward a valid name WITHOUT echoing the bad name back —
re-quoting reinforces the failure token in the model's context and
keeps it stuck in the same loop.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


def _make_agent_with_tools(tool_names):
    """Build a minimal agent and pre-populate its tools snapshot.

    ``_tools_registry`` is a read-only property; the snapshot mechanism
    is the canonical override point (see ``Agent._snapshot_tools``).
    """
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _DummyAgent(silent_mode=True, skip_lemonade=True)
    agent._instance_tools = {
        name: {
            "name": name,
            "description": "stub",
            "parameters": {},
            "function": lambda **kwargs: {"status": "success"},
        }
        for name in tool_names
    }
    return agent


# ---------------------------------------------------------------------------
# Bare-prefix branch
# ---------------------------------------------------------------------------


def test_bare_prefix_emits_incomplete_name_error():
    agent = _make_agent_with_tools(
        [
            "mcp_tool_tool_displaylens_on",
            "mcp_tool_tool_battery_check",
        ]
    )
    result = agent._execute_tool("mcp_tool", {})
    assert result["status"] == "error"
    err = result["error"]
    assert "Incomplete tool name" in err
    # Bad name MUST NOT be quoted back at the model
    assert "'mcp_tool'" not in err
    # Candidates ARE listed
    assert "mcp_tool_tool_displaylens_on" in err
    assert "mcp_tool_tool_battery_check" in err


def test_bare_prefix_works_for_non_mcp_tools_too():
    """The predicate is generic — works for any hierarchical naming."""
    agent = _make_agent_with_tools(["query_documents", "query_specific_file"])
    result = agent._execute_tool("query", {})
    assert "Incomplete tool name" in result["error"]


# ---------------------------------------------------------------------------
# Single-candidate branch (NOT bare-prefix — _resolve_tool_name resolves it)
# ---------------------------------------------------------------------------


def test_single_candidate_is_auto_resolved_not_an_error():
    """A single suffix-match is auto-resolved by ``_resolve_tool_name``
    (not the bare-prefix branch). Tool call should succeed, not error."""
    agent = _make_agent_with_tools(["mcp_filesystem_read_file"])
    # _resolve_tool_name should map "read_file" -> "mcp_filesystem_read_file"
    result = agent._execute_tool("read_file", {})
    assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Unknown-without-candidates branch
# ---------------------------------------------------------------------------


def test_unknown_name_with_no_candidates_uses_generic_message():
    agent = _make_agent_with_tools(["mcp_tool_tool_displaylens_on"])
    result = agent._execute_tool("totally_unrelated", {})
    err = result["error"]
    assert "AVAILABLE TOOLS" in err
    # Bad name still NOT quoted
    assert "'totally_unrelated'" not in err


# ---------------------------------------------------------------------------
# No-echo invariant across all branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name,registry",
    [
        ("mcp_tool", ["mcp_tool_a_tool", "mcp_tool_b_tool"]),  # bare-prefix
        ("nothing_like_this", ["mcp_foo_bar"]),  # unknown-no-candidates
    ],
)
def test_bad_name_never_quoted_in_error(bad_name, registry):
    agent = _make_agent_with_tools(registry)
    err = agent._execute_tool(bad_name, {})["error"]
    assert f"'{bad_name}'" not in err, f"bad name leaked into error: {err}"
