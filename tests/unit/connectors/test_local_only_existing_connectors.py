# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Local only also covers connectors added earlier.

A search connector set up while cloud was allowed must not start, answer a
tool call, or be turned back on once the user picks Local only.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.errors import ConfigurationError
from gaia.connectors.mcp_server import McpServerHandler, _read_mcp_servers_json
from gaia.llm import local_only
from gaia.mcp.client.mcp_client import MCPClient
from gaia.mcp.client.mcp_client_manager import MCPClientManager
from gaia.mcp.client.transports.base import MCPTransport
from gaia.mcp.protocol import MCP_PROTOCOL_VERSION

OFFSITE_IDS = ("mcp-keenable", "mcp-tavily", "mcp-brave-search")


class FakeTransport(MCPTransport):
    """Records every request; answers like a healthy server."""

    def __init__(self) -> None:
        self.started = False
        self.requests: List[str] = []

    def connect(self) -> bool:
        self.started = True
        return True

    def disconnect(self) -> None:
        self.started = False

    def send_request(self, method: str, params: Dict[str, Any] | None = None):
        self.requests.append(method)
        if method == "initialize":
            return {"result": {"protocolVersion": MCP_PROTOCOL_VERSION}}
        if method == "tools/list":
            return {
                "result": {"tools": [{"name": "search_web_pages", "inputSchema": {}}]}
            }
        return {"result": {"content": [{"type": "text", "text": "ok"}]}}

    def send_notification(self, method: str, params=None) -> None:
        return None

    def is_connected(self) -> bool:
        return self.started


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    local_only.set_policy("cloud_allowed")
    return tmp_path


def _connected(name: str) -> tuple[MCPClient, FakeTransport]:
    transport = FakeTransport()
    client = MCPClient(name, transport)
    assert client.connect()
    return client, transport


@pytest.mark.parametrize("server", OFFSITE_IDS)
def test_connected_before_local_only_then_refused(server):
    client, transport = _connected(server)
    assert "error" not in client.call_tool("search_web_pages", {"query": "x"})

    local_only.set_policy("local_ai_only")
    result = client.call_tool("search_web_pages", {"query": "secret plans"})
    assert "Local only" in result["error"]
    assert transport.requests.count("tools/call") == 1


def test_tool_wrapper_path_is_refused():
    client, transport = _connected("mcp-keenable")
    tool = client.list_tools()[0]
    wrapper = client.create_tool_wrapper(tool)
    local_only.set_policy("local_ai_only")
    assert "Local only" in wrapper(query="x")["error"]
    assert "tools/call" not in transport.requests


def test_local_only_off_restores_calls():
    client, transport = _connected("mcp-keenable")
    local_only.set_policy("local_ai_only")
    assert "error" in client.call_tool("search_web_pages", {"query": "x"})
    local_only.set_policy("cloud_allowed")
    assert "error" not in client.call_tool("search_web_pages", {"query": "x"})
    assert transport.requests.count("tools/call") == 1


@pytest.mark.parametrize("server", OFFSITE_IDS)
def test_server_never_starts_under_local_only(server):
    local_only.set_policy("local_ai_only")
    transport = FakeTransport()
    client = MCPClient(server, transport)
    assert client.connect() is False
    assert "Local only" in client.last_error
    assert transport.started is False
    assert transport.requests == []


@pytest.mark.parametrize("server", ["mcp-git", "mcp-memory", "my-own-server"])
def test_local_servers_unaffected(server):
    local_only.set_policy("local_ai_only")
    client, transport = _connected(server)
    assert "error" not in client.call_tool("search_web_pages", {"query": "x"})
    assert "tools/call" in transport.requests


class _Config:
    def __init__(self, servers):
        self._servers = servers

    def get_servers(self):
        return dict(self._servers)

    def _load(self):
        return None


def test_manager_skips_saved_offsite_server_and_reload_restores(monkeypatch):
    transports: Dict[str, FakeTransport] = {}

    def from_config(cls, name, config, timeout=30, debug=False):
        transports[name] = FakeTransport()
        return cls(name, transports[name], debug=debug)

    monkeypatch.setattr(MCPClient, "from_config", classmethod(from_config))
    servers = {
        "mcp-keenable": {"command": "npx", "args": ["-y", "@keenable/mcp@0.1.1"]},
        "mcp-git": {"command": "uvx", "args": ["mcp-server-git"]},
    }
    manager = MCPClientManager(config=_Config(servers))

    local_only.set_policy("local_ai_only")
    manager.load_from_config()
    assert manager.get_client("mcp-keenable") is None
    assert transports["mcp-keenable"].started is False
    assert "Local only" in manager._failed["mcp-keenable"]
    assert manager.get_client("mcp-git") is not None

    local_only.set_policy("cloud_allowed")
    manager.reload()
    assert manager.get_client("mcp-keenable") is not None
    manager.disconnect_all()


def _save(handler: McpServerHandler, connector_id: str) -> None:
    from gaia.connectors.registry import REGISTRY

    spec = REGISTRY.get(connector_id)
    asyncio.run(handler.configure(spec, {k: "k" for k in spec.mcp_env_keys}))


def test_reenable_toggle_refused_under_local_only(monkeypatch):
    monkeypatch.setattr(
        "gaia.connectors.mcp_server.keyring.set_password", lambda *a, **k: None
    )
    handler = McpServerHandler()
    _save(handler, "mcp-keenable")
    asyncio.run(handler.set_enabled("mcp-keenable", False))

    local_only.set_policy("local_ai_only")
    with pytest.raises(ConfigurationError, match="Local only"):
        asyncio.run(handler.set_enabled("mcp-keenable", True))
    assert _read_mcp_servers_json()["mcp-keenable"]["disabled"] is True

    # Turning it off is always allowed.
    asyncio.run(handler.set_enabled("mcp-keenable", False))

    local_only.set_policy("cloud_allowed")
    asyncio.run(handler.set_enabled("mcp-keenable", True))
    assert _read_mcp_servers_json()["mcp-keenable"]["disabled"] is False


def test_reenable_local_connector_under_local_only(monkeypatch):
    monkeypatch.setattr(
        "gaia.connectors.mcp_server.keyring.set_password", lambda *a, **k: None
    )
    handler = McpServerHandler()
    _save(handler, "mcp-git")
    asyncio.run(handler.set_enabled("mcp-git", False))
    local_only.set_policy("local_ai_only")
    asyncio.run(handler.set_enabled("mcp-git", True))
    assert _read_mcp_servers_json()["mcp-git"]["disabled"] is False
