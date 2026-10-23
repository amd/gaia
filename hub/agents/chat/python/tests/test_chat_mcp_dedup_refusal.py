# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A ChatAgent refuses only the MCP server with a bad dedup_key (#3746).

The servers are fakes injected where ``load_from_config`` would connect them;
nothing here starts a process or talks to Lemonade.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.mcp.client.mcp_client import MCPTool
from gaia.mcp.client.mcp_client_manager import MCPClientManager
from gaia.mcp.mixin import MCP_DEDUP_KEY_META, MCPClientMixin


def _client(name: str, dedup_key: list) -> SimpleNamespace:
    tool = MCPTool(
        name="read",
        description="d",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        meta={MCP_DEDUP_KEY_META: dedup_key},
    )
    return SimpleNamespace(
        name=name,
        prefix=name,
        list_tools=lambda: [tool],
        create_tool_wrapper=lambda t: lambda **kw: {"ok": True},
        disconnect=Mock(),
    )


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.setenv("GAIA_MEMORY_DB", str(tmp_path / "memory.db"))
    (tmp_path / "mcp_servers.json").write_text('{"mcpServers": {}}')
    saved = dict(_TOOL_REGISTRY)
    with (
        patch.object(MemoryMixin, "_get_embedder", MagicMock()),
        patch(
            "gaia.agents.base.memory._system_context_is_enabled",
            return_value=False,
        ),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ),
        patch("gaia.agents.base.agent.AgentSDK"),
        patch("gaia.rag.sdk.AgentSDK"),
        patch("gaia.llm.lemonade_client.LemonadeClient"),
    ):
        yield tmp_path
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def test_a_bad_server_first_does_not_stop_the_next_one(backend):
    bad = _client("bad", ["pth"])
    good = _client("good", ["path"])

    def _connect(manager):
        manager._clients.update({"bad": bad, "good": good})
        manager.config.get_servers = lambda: {"bad": {}, "good": {}}
        manager.config.load_report = {"mode": "explicit", "config_file": "x"}

    printed = []
    with (
        patch.object(MCPClientManager, "load_from_config", _connect),
        patch.object(
            ChatAgent,
            "_active_mcp_servers",
            lambda self, manager: list(manager.list_servers()),
        ),
        patch.object(
            MCPClientMixin,
            "_console_print",
            lambda self, method, message: printed.append((method, message)),
        ),
    ):
        agent = ChatAgent(
            ChatAgentConfig(
                silent_mode=True,
                max_steps=1,
                memory_incognito=True,
                allowed_paths=[str(backend)],
            )
        )
    agent.stop_watching()

    assert _TOOL_REGISTRY["mcp_good_read"]["dedup_key"] == ("path",)
    assert "mcp_bad_read" not in _TOOL_REGISTRY
    bad.disconnect.assert_called_once()
    assert agent._mcp_manager.list_servers() == ["good"]
    assert "'pth'" in agent._mcp_manager.failure("bad")
    errors = [m for kind, m in printed if kind == "print_error"]
    assert any("MCP server 'bad'" in m for m in errors)
    [summary] = [m for kind, m in printed if kind == "print_info" and "✓" in m]
    assert "✓ good" in summary and "✗ bad (MCP server 'bad'" in summary
