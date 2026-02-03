# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SDK Integration tests for MCPClientManager.

Tests MCPClientManager against real MCP servers - mocks nothing.

Run:
    uv run pytest tests/mcp/test_mcp_sdk_integration.py -xvs -m integration
"""

import pytest

from gaia.mcp import MCPClientManager
from gaia.mcp.client.config import MCPConfig

# MCP server configs (Anthropic format - no API keys required)
MCP_SERVERS = {
    "memory": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
    },
    "sequential-thinking": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
    },
    "time": {
        "command": "uvx",
        "args": ["mcp-server-time"],
    },
}

# Tool to call for each server (simple tools that verify server works)
MCP_TEST_TOOLS = {
    "memory": (
        "create_entities",
        {
            "entities": [
                {
                    "name": "TestEntity",
                    "entityType": "test",
                    "observations": ["Integration test"],
                }
            ]
        },
    ),
    "sequential-thinking": (
        "sequentialthinking",
        {
            "thought": "Integration test thought",
            "nextThoughtNeeded": False,
            "thoughtNumber": 1,
            "totalThoughts": 1,
        },
    ),
    "time": ("get_current_time", {"timezone": "UTC"}),
}


class TestMCPSDKIntegration:
    """Integration tests using real MCP servers via npx."""

    @pytest.mark.integration
    def test_sdk_connect_all_three_servers(self, npx_available, temp_config_file):
        """Connect to all three MCP servers and verify they're connected."""
        config = MCPConfig(temp_config_file)
        manager = MCPClientManager(config=config)
        connected = []

        try:
            for name, server_config in MCP_SERVERS.items():
                client = manager.add_server(name, server_config)
                assert client is not None, f"Failed to create client for {name}"
                assert client.is_connected(), f"{name} not connected"
                connected.append(name)

            assert len(connected) == 3
            assert set(manager.list_servers()) == set(MCP_SERVERS.keys())

        finally:
            manager.disconnect_all()

    @pytest.mark.integration
    def test_sdk_call_tool_from_each_server(self, npx_available, temp_config_file):
        """Call one tool from each server and verify response."""
        config = MCPConfig(temp_config_file)
        manager = MCPClientManager(config=config)
        results = {}

        try:
            for name, server_config in MCP_SERVERS.items():
                manager.add_server(name, server_config)

            for name, (tool_name, tool_args) in MCP_TEST_TOOLS.items():
                client = manager.get_client(name)
                result = client.call_tool(tool_name, tool_args)
                results[name] = result

                assert result is not None, f"No result from {name}.{tool_name}"
                assert "error" not in result or result.get("isError") is not True

            assert len(results) == 3

        finally:
            manager.disconnect_all()

    @pytest.mark.integration
    def test_sdk_list_tools_from_each_server(self, npx_available, temp_config_file):
        """List tools from each server and verify structure."""
        config = MCPConfig(temp_config_file)
        manager = MCPClientManager(config=config)

        try:
            for name, server_config in MCP_SERVERS.items():
                client = manager.add_server(name, server_config)
                tools = client.list_tools()

                assert len(tools) > 0, f"No tools from {name}"

                for tool in tools:
                    assert hasattr(tool, "name")
                    assert hasattr(tool, "description")
                    assert hasattr(tool, "input_schema")

        finally:
            manager.disconnect_all()

    @pytest.mark.integration
    def test_sdk_load_from_config_reconnects(self, npx_available, temp_config_file):
        """Save config, reload, verify reconnection works."""
        config = MCPConfig(temp_config_file)
        manager = MCPClientManager(config=config)

        try:
            # Add all servers (config auto-saves on add)
            for name, server_config in MCP_SERVERS.items():
                manager.add_server(name, server_config)

            manager.disconnect_all()

            # Create new manager from saved config and load servers
            config2 = MCPConfig(temp_config_file)
            manager2 = MCPClientManager(config=config2)
            manager2.load_from_config()

            # Verify all servers reconnected
            for name in MCP_SERVERS.keys():
                client = manager2.get_client(name)
                assert client is not None, f"Failed to load {name} from config"
                assert client.is_connected(), f"{name} not reconnected"

                tools = client.list_tools()
                assert len(tools) > 0, f"No tools from {name} after reconnect"

        finally:
            manager.disconnect_all()
            if "manager2" in locals():
                manager2.disconnect_all()
