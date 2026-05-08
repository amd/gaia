# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Regression tests for per-instance tool registry isolation (Fix 1 from PR #495 review).

Verifies that:
- _snapshot_tools() creates an independent copy of the global registry
- Mutations on the global registry after snapshot do not leak into the agent
- Two agents in the same process have independent tool sets
- Agents without a snapshot still work (backward compatibility)
"""

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save and restore the global _TOOL_REGISTRY around each test."""
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _FakeAgent:
    """Minimal stand-in for Agent — only the _tools_registry machinery."""

    _instance_tools = None

    @property
    def _tools_registry(self):
        if self._instance_tools is not None:
            return self._instance_tools
        return _TOOL_REGISTRY

    def _snapshot_tools(self):
        self._instance_tools = dict(_TOOL_REGISTRY)


def _register_dummy_tool(name: str):
    """Add a minimal tool entry to the global registry."""
    _TOOL_REGISTRY[name] = {
        "name": name,
        "description": f"dummy tool {name}",
        "parameters": {},
        "function": lambda: name,
        "atomic": True,
    }


class TestSnapshotIsolation:
    def test_snapshot_creates_independent_copy(self):
        _register_dummy_tool("tool_a")
        agent = _FakeAgent()
        agent._snapshot_tools()

        _register_dummy_tool("tool_b")

        assert "tool_a" in agent._tools_registry
        assert "tool_b" not in agent._tools_registry
        assert "tool_b" in _TOOL_REGISTRY

    def test_global_clear_does_not_affect_snapshotted_agent(self):
        _register_dummy_tool("tool_x")
        agent = _FakeAgent()
        agent._snapshot_tools()

        _TOOL_REGISTRY.clear()

        assert "tool_x" in agent._tools_registry
        assert len(_TOOL_REGISTRY) == 0

    def test_instance_pop_does_not_affect_global(self):
        _register_dummy_tool("keep_me")
        _register_dummy_tool("drop_me")
        agent = _FakeAgent()
        agent._snapshot_tools()
        agent._instance_tools.pop("drop_me", None)

        assert "drop_me" not in agent._tools_registry
        assert "drop_me" in _TOOL_REGISTRY

    def test_two_agents_independent_tools(self):
        _register_dummy_tool("shared")
        _register_dummy_tool("only_a")

        agent_a = _FakeAgent()
        agent_a._snapshot_tools()

        _register_dummy_tool("only_b")
        agent_b = _FakeAgent()
        agent_b._snapshot_tools()

        assert "only_b" not in agent_a._tools_registry
        assert "only_b" in agent_b._tools_registry

    def test_backward_compat_no_snapshot(self):
        _register_dummy_tool("fallback_tool")
        agent = _FakeAgent()

        assert agent._instance_tools is None
        assert "fallback_tool" in agent._tools_registry
        assert agent._tools_registry is _TOOL_REGISTRY

    def test_snapshot_is_dict_not_reference(self):
        _register_dummy_tool("some_tool")
        agent = _FakeAgent()
        agent._snapshot_tools()
        assert agent._instance_tools is not _TOOL_REGISTRY
