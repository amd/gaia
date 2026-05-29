# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-agent activation gates MCP tool visibility (issue #1005).

Tests focus on the boundary between :class:`MCPClientManager` and the
:class:`Agent` base class — specifically that ``servers_for_agent`` and
``Agent._active_mcp_servers`` honour the activations ledger.

End-to-end behaviour (ChatAgent registering tools to ``_TOOL_REGISTRY``)
is covered by the multi-caller equivalence test in
``tests/unit/connectors/test_e2e_smoke.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gaia.connectors.activations import activate_agent, deactivate_agent
from gaia.mcp.client.mcp_client_manager import MCPClientManager


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connectors.activations.Path.home", lambda: tmp_path)
    return tmp_path


def _stub_manager_with_servers(*names: str) -> MCPClientManager:
    """Build an MCPClientManager whose ``_clients`` map points at stubbed clients.

    Bypasses the actual connect path because the activation filter is a
    purely-in-memory concern — the only thing being tested here is which
    server names cross the filter.
    """
    manager = MCPClientManager()
    for name in names:
        client = MagicMock()
        client.list_tools.return_value = [MagicMock(name=f"tool-from-{name}")]
        manager._clients[name] = client
    return manager


class TestServersForAgent:
    def test_no_activations_yields_empty_list(self, fake_home):
        manager = _stub_manager_with_servers("github", "filesystem")
        # No activation entries yet — least-privilege default.
        assert manager.servers_for_agent("builtin:chat") == []

    def test_only_activated_servers_returned(self, fake_home):
        manager = _stub_manager_with_servers("github", "filesystem", "fetch")
        activate_agent("github", "builtin:chat")
        activate_agent("fetch", "builtin:chat")
        assert sorted(manager.servers_for_agent("builtin:chat")) == [
            "fetch",
            "github",
        ]

    def test_activation_for_different_agent_does_not_leak(self, fake_home):
        manager = _stub_manager_with_servers("github")
        activate_agent("github", "builtin:code")
        # builtin:chat must NOT see github tools just because builtin:code
        # is activated — activations are per-agent.
        assert manager.servers_for_agent("builtin:chat") == []
        assert manager.servers_for_agent("builtin:code") == ["github"]

    def test_deactivate_removes_visibility(self, fake_home):
        manager = _stub_manager_with_servers("github")
        activate_agent("github", "builtin:chat")
        assert manager.servers_for_agent("builtin:chat") == ["github"]
        deactivate_agent("github", "builtin:chat")
        assert manager.servers_for_agent("builtin:chat") == []

    def test_none_agent_id_bypasses_filter(self, fake_home):
        # CLI / debug callers with no agent context still see everything.
        manager = _stub_manager_with_servers("github", "filesystem")
        assert sorted(manager.servers_for_agent(None)) == [
            "filesystem",
            "github",
        ]

    def test_servers_not_in_manager_are_not_returned(self, fake_home):
        # Activating an agent for a server that isn't connected is allowed
        # (the user may activate before the connector is up); the filter
        # only returns servers actually known to the manager.
        manager = _stub_manager_with_servers("github")
        activate_agent("filesystem", "builtin:chat")
        activate_agent("github", "builtin:chat")
        assert manager.servers_for_agent("builtin:chat") == ["github"]


class TestToolsForAgent:
    def test_tools_for_agent_returns_per_server_lists(self, fake_home):
        manager = _stub_manager_with_servers("github", "filesystem")
        activate_agent("github", "builtin:chat")
        result = manager.tools_for_agent("builtin:chat")
        assert set(result.keys()) == {"github"}
        assert len(result["github"]) == 1

    def test_tools_for_agent_empty_when_no_activation(self, fake_home):
        manager = _stub_manager_with_servers("github")
        assert manager.tools_for_agent("builtin:chat") == {}

    def test_tools_for_agent_with_none_returns_all(self, fake_home):
        manager = _stub_manager_with_servers("github", "filesystem")
        result = manager.tools_for_agent(None)
        assert set(result.keys()) == {"github", "filesystem"}


class TestAgentBaseHelper:
    """``Agent._active_mcp_servers`` is the integration point that ChatAgent
    (and any future MCP-consuming agent) calls during ``_register_tools``."""

    def test_none_manager_returns_empty(self, fake_home):
        from gaia.agents.base.agent import Agent

        # We don't instantiate Agent (too heavy); call the unbound method
        # against a minimal SimpleNamespace stand-in that has the resolver.
        class _Stub:
            _gaia_namespaced_agent_id = "builtin:chat"
            AGENT_ID = "chat"

            _namespaced_agent_id = Agent._namespaced_agent_id
            _active_mcp_servers = Agent._active_mcp_servers

        assert _Stub()._active_mcp_servers(None) == []

    def test_with_namespaced_id_applies_filter(self, fake_home):
        from gaia.agents.base.agent import Agent

        class _Stub:
            _gaia_namespaced_agent_id = "builtin:chat"
            AGENT_ID = "chat"

            _namespaced_agent_id = Agent._namespaced_agent_id
            _active_mcp_servers = Agent._active_mcp_servers

        manager = _stub_manager_with_servers("github", "filesystem")
        activate_agent("github", "builtin:chat")
        servers = _Stub()._active_mcp_servers(manager)
        assert servers == ["github"]

    def test_no_namespaced_id_returns_all_servers(self, fake_home):
        # Fallback path for ad-hoc agents constructed outside the registry.
        from gaia.agents.base.agent import Agent

        class _Stub:
            _gaia_namespaced_agent_id = None
            AGENT_ID = None

            _namespaced_agent_id = Agent._namespaced_agent_id
            _active_mcp_servers = Agent._active_mcp_servers

        manager = _stub_manager_with_servers("github", "filesystem")
        # No activations, but no agent id either — must NOT filter.
        servers = _Stub()._active_mcp_servers(manager)
        assert sorted(servers) == ["filesystem", "github"]

    def test_falls_back_to_bare_agent_id_when_no_namespaced(self, fake_home):
        from gaia.agents.base.agent import Agent

        class _Stub:
            _gaia_namespaced_agent_id = None
            AGENT_ID = "chat"  # bare id — used as the activation key

            _namespaced_agent_id = Agent._namespaced_agent_id
            _active_mcp_servers = Agent._active_mcp_servers

        manager = _stub_manager_with_servers("github")
        activate_agent("github", "chat")
        servers = _Stub()._active_mcp_servers(manager)
        assert servers == ["github"]
