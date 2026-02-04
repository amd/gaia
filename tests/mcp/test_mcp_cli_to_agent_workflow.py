# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration test for the complete MCP workflow: CLI → Config → Agent.

This tests the ACTUAL user workflow:
1. User runs CLI commands: gaia mcp add <name> <command>
2. Config is saved to ~/.gaia/mcp_servers.json
3. Agent loads servers from config
4. Agent can use all MCP tools

Run:
    uv run pytest tests/mcp/test_mcp_cli_to_agent_workflow.py -xvs -m integration
"""

import json

import pytest

from gaia.agents.base.agent import Agent
from gaia.mcp import MCPClientMixin
from gaia.mcp.client.config import MCPConfig

# MCP server configs (Anthropic format - same as in other tests)
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


class MCPTestAgent(Agent, MCPClientMixin):
    """Test agent that loads MCP servers from config."""

    def __init__(self, config_path: str, **kwargs):
        # Skip Lemonade to avoid subprocess issues
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("max_steps", 10)

        Agent.__init__(self, **kwargs)
        MCPClientMixin.__init__(self)

        # Override config path for testing
        self._mcp_manager.config = MCPConfig(config_path)

        # Load servers from config (system prompt auto-updated with MCP tools)
        self.load_mcp_servers_from_config()

    def _get_system_prompt(self) -> str:
        """Simple system prompt for testing."""
        return "You are a test agent with access to MCP servers."

    def _register_tools(self) -> None:
        """No additional tools needed."""
        pass


@pytest.mark.integration
class TestMCPCLIToAgentWorkflow:
    """Test the complete CLI → Config → Agent workflow."""

    def test_full_workflow_cli_to_agent(self, npx_available, tmp_path):
        """Test the complete user workflow from CLI to Agent usage.

        Workflow:
        1. Simulate CLI: gaia mcp add <name> <command>
        2. Verify config file is created with correct servers
        3. Create agent that loads from config
        4. Verify agent can list and use MCP tools
        """
        config_file = tmp_path / "mcp_servers.json"

        # Step 1: Simulate CLI commands (gaia mcp add)
        # In real usage: gaia mcp add memory "npx -y @modelcontextprotocol/server-memory"
        # For testing, we'll directly write to config to simulate this
        config_data = {"mcpServers": MCP_SERVERS}

        config_file.write_text(json.dumps(config_data, indent=2))

        # Step 2: Verify config file was created correctly
        assert config_file.exists(), "Config file should exist"
        loaded_config = json.loads(config_file.read_text())
        assert "mcpServers" in loaded_config
        assert len(loaded_config["mcpServers"]) == 3
        assert "memory" in loaded_config["mcpServers"]
        assert "sequential-thinking" in loaded_config["mcpServers"]
        assert "time" in loaded_config["mcpServers"]

        # Step 3: Create agent that loads from config
        agent = MCPTestAgent(config_path=str(config_file))

        # Step 4: Verify agent loaded all servers
        servers = agent.list_mcp_servers()
        assert len(servers) == 3, "Should have loaded 3 servers"
        assert "memory" in servers
        assert "sequential-thinking" in servers
        assert "time" in servers

        # Step 5: Verify tools are registered and accessible
        # Memory server should have ~9 tools
        memory_client = agent.get_mcp_client("memory")
        memory_tools = memory_client.list_tools()
        assert len(memory_tools) > 0, "Memory server should have tools"

        # Sequential thinking should have 1 tool
        thinking_client = agent.get_mcp_client("sequential-thinking")
        thinking_tools = thinking_client.list_tools()
        assert len(thinking_tools) == 1, "Sequential thinking should have 1 tool"

        # Time server should have 2 tools
        time_client = agent.get_mcp_client("time")
        time_tools = time_client.list_tools()
        assert len(time_tools) == 2, "Time server should have 2 tools"

        # Step 6: Verify tools are in the agent's tool registry
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Check for namespaced MCP tools
        mcp_tools = [name for name in _TOOL_REGISTRY.keys() if name.startswith("mcp_")]
        assert (
            len(mcp_tools) >= 12
        ), f"Should have at least 12 MCP tools, found {len(mcp_tools)}"

        # Check specific tools exist (note: server name from config)
        assert "mcp_memory_create_entities" in _TOOL_REGISTRY
        assert "mcp_sequential-thinking_sequentialthinking" in _TOOL_REGISTRY
        assert "mcp_time_get_current_time" in _TOOL_REGISTRY

        # Step 7: Verify system prompt includes tools
        assert "AVAILABLE TOOLS" in agent.system_prompt
        assert "mcp_memory_create_entities" in agent.system_prompt
        assert "mcp_time_get_current_time" in agent.system_prompt
        assert "mcp_sequential-thinking_sequentialthinking" in agent.system_prompt

        # Cleanup
        agent._mcp_manager.disconnect_all()

    def test_cli_add_command_simulation(self, npx_available, tmp_path):
        """Test simulating the actual 'gaia mcp add' CLI command.

        This test simulates what happens when users run:
        gaia mcp add memory "npx -y @modelcontextprotocol/server-memory"
        """
        config_file = tmp_path / "mcp_servers.json"

        # Simulate the CLI command behavior
        from gaia.mcp import MCPClientManager
        from gaia.mcp.client.config import MCPConfig

        config = MCPConfig(str(config_file))
        manager = MCPClientManager(config=config)

        try:
            # This is what 'gaia mcp add' does internally (with config dicts)
            for name, server_config in MCP_SERVERS.items():
                client = manager.add_server(name, server_config)
                assert client is not None, f"Failed to add {name}"
                assert client.is_connected(), f"{name} not connected"

            # Verify config file was created
            assert config_file.exists(), "Config file should exist after adding servers"

            # Verify config contents (uses mcpServers key)
            config_data = json.loads(config_file.read_text())
            assert len(config_data["mcpServers"]) == 3

            # Now test that a NEW manager can load from this config
            manager2 = MCPClientManager(config=MCPConfig(str(config_file)))
            manager2.load_from_config()

            servers = manager2.list_servers()
            assert len(servers) == 3
            assert set(servers) == set(MCP_SERVERS.keys())

            # Cleanup
            manager2.disconnect_all()

        finally:
            manager.disconnect_all()

    def test_agent_can_call_mcp_tools(self, npx_available, tmp_path):
        """Test that agent can actually call MCP tools loaded from config.

        This validates that tools work end-to-end through the Agent class.
        """
        config_file = tmp_path / "mcp_servers.json"

        # Setup config (using mcpServers format)
        config_data = {
            "mcpServers": {
                "time": MCP_SERVERS["time"],
            }
        }
        config_file.write_text(json.dumps(config_data, indent=2))

        # Create agent
        agent = MCPTestAgent(config_path=str(config_file))

        try:
            # Verify time server loaded
            assert "time" in agent.list_mcp_servers()

            # Get the MCP client directly and test tool call
            time_client = agent.get_mcp_client("time")
            result = time_client.call_tool("get_current_time", {"timezone": "UTC"})

            # Verify result
            assert result is not None
            assert "content" in result
            assert len(result["content"]) > 0

            # Parse the JSON response
            response_text = result["content"][0]["text"]
            response_data = json.loads(response_text)
            assert "timezone" in response_data
            assert response_data["timezone"] == "UTC"
            assert "datetime" in response_data

        finally:
            agent._mcp_manager.disconnect_all()

    def test_config_persistence_across_sessions(self, npx_available, tmp_path):
        """Test that MCP server config persists across sessions.

        Simulates:
        Session 1: User adds servers via CLI
        Session 2: New agent loads them automatically
        """
        config_file = tmp_path / "mcp_servers.json"

        # Session 1: Add servers
        from gaia.mcp import MCPClientManager
        from gaia.mcp.client.config import MCPConfig

        session1_manager = MCPClientManager(config=MCPConfig(str(config_file)))

        try:
            # Add just one server in session 1
            client = session1_manager.add_server("time", MCP_SERVERS["time"])
            assert client is not None

            # Disconnect session 1
            session1_manager.disconnect_all()

            # Session 2: New agent loads from config
            agent = MCPTestAgent(config_path=str(config_file))

            assert (
                "time" in agent.list_mcp_servers()
            ), "Time server should be loaded from config"

            # Verify tool is accessible
            time_client = agent.get_mcp_client("time")
            tools = time_client.list_tools()
            assert len(tools) == 2

            # Cleanup
            agent._mcp_manager.disconnect_all()

        finally:
            if session1_manager:
                session1_manager.disconnect_all()
