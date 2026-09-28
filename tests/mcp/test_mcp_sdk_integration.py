# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MCPClientManager against a real stdio MCP server — no mocks.

The server is the local fixture in ``tests/mcp/fixtures/`` (built on the
official MCP Python SDK), so these tests speak real JSON-RPC over stdio
without fetching anything at test time.
"""

import json

import pytest

from gaia.mcp import MCPClientManager
from gaia.mcp.client.config import MCPConfig

FIXTURE_TOOLS = {"echo", "add", "read_env"}


@pytest.fixture
def manager(tmp_path):
    mgr = MCPClientManager(config=MCPConfig(str(tmp_path / "mcp_servers.json")))
    yield mgr
    mgr.disconnect_all()


def _write_config(path, servers):
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


class TestMCPClientManagerAgainstRealServer:
    def test_add_server_connects_and_lists_tool_schemas(
        self, manager, fixture_server_config
    ):
        client = manager.add_server("fixture", fixture_server_config)

        assert client.is_connected()
        assert manager.list_servers() == ["fixture"]

        tools = {t.name: t for t in client.list_tools()}
        assert set(tools) == FIXTURE_TOOLS
        assert tools["add"].description == "Add two integers."
        assert set(tools["add"].input_schema["properties"]) == {"a", "b"}
        assert tools["add"].input_schema["required"] == ["a", "b"]

    def test_call_tool_returns_server_result(self, manager, fixture_server_config):
        client = manager.add_server("fixture", fixture_server_config)

        result = client.call_tool("add", {"a": 2, "b": 3})

        assert not result.get("isError")
        assert result["content"][0]["text"] == "5"

    def test_tool_failure_is_reported_not_swallowed(
        self, manager, fixture_server_config
    ):
        client = manager.add_server("fixture", fixture_server_config)

        result = client.call_tool("read_env", {"name": "GAIA_FIXTURE_UNSET_VAR"})

        assert result["isError"] is True
        assert "read_env" in result["content"][0]["text"]

    def test_env_from_config_reaches_server(self, manager, fixture_server_config):
        config = {**fixture_server_config, "env": {"GAIA_FIXTURE_VAR": "from-config"}}
        client = manager.add_server("fixture", config)

        result = client.call_tool("read_env", {"name": "GAIA_FIXTURE_VAR"})

        assert result["content"][0]["text"] == "from-config"

    def test_add_server_raises_when_server_cannot_start(self, manager, tmp_path):
        missing = {"command": str(tmp_path / "no-such-server")}

        with pytest.raises(RuntimeError, match="broken"):
            manager.add_server("broken", missing)
        assert manager.list_servers() == []

    def test_load_from_config_and_reload_follow_the_file(
        self, tmp_path, fixture_server_config
    ):
        config_path = tmp_path / "mcp_servers.json"
        _write_config(config_path, {"first": fixture_server_config})
        mgr = MCPClientManager(config=MCPConfig(str(config_path)))
        try:
            mgr.load_from_config()
            assert mgr.list_servers() == ["first"]
            assert mgr.get_client("first").is_connected()

            _write_config(
                config_path,
                {
                    "first": {**fixture_server_config, "disabled": True},
                    "second": fixture_server_config,
                },
            )
            mgr.reload()

            assert mgr.list_servers() == ["second"]
            tools = {t.name for t in mgr.get_client("second").list_tools()}
            assert tools == FIXTURE_TOOLS
        finally:
            mgr.disconnect_all()
