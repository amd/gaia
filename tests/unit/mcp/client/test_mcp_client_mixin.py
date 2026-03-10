# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for MCPClientMixin."""

from unittest.mock import Mock, patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY, get_tool_display_name
from gaia.mcp import MCPClientMixin
from gaia.mcp.client.mcp_client import MCPTool


class MockAgent(MCPClientMixin):
    """Mock agent for testing the mixin."""

    def __init__(self, debug=False, auto_load_config=False):
        self.debug = debug
        super().__init__(auto_load_config=auto_load_config)


class TestMCPClientMixin:
    """Test MCPClientMixin functionality."""

    def setup_method(self):
        """Clear tool registry before each test."""
        _TOOL_REGISTRY.clear()

    def teardown_method(self):
        """Clear tool registry after each test."""
        _TOOL_REGISTRY.clear()

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_connect_mcp_server_adds_server(self, mock_manager_class):
        """Test that connect_mcp_server adds server to manager using config dict."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.server_info = {"name": "Test Server"}
        mock_client.list_tools.return_value = []
        mock_manager.add_server.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {"command": "npx", "args": ["-y", "server"]}
        result = agent.connect_mcp_server("test", config)

        assert result is True
        mock_manager.add_server.assert_called_once_with("test", config)

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_connect_mcp_server_registers_tools(self, mock_manager_class):
        """Test that connect_mcp_server registers tools in _TOOL_REGISTRY."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.name = "testserver"
        mock_client.server_info = {"name": "Test Server"}

        # Mock tool
        mock_tool = MCPTool(
            name="test_tool",
            description="Test tool",
            input_schema={
                "type": "object",
                "properties": {"param": {"type": "string"}},
                "required": ["param"],
            },
        )
        mock_client.list_tools.return_value = [mock_tool]

        # Mock wrapper function
        mock_wrapper = Mock(return_value={"result": "ok"})
        mock_client.create_tool_wrapper.return_value = mock_wrapper

        mock_manager.add_server.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {"command": "npx", "args": ["-y", "server"]}
        agent.connect_mcp_server("testserver", config)

        # Check tool was registered
        tool_name = "mcp_testserver_test_tool"
        assert tool_name in _TOOL_REGISTRY

        # Check tool format
        registered_tool = _TOOL_REGISTRY[tool_name]
        assert registered_tool["name"] == tool_name
        assert "[MCP:testserver]" in registered_tool["description"]
        # Function is wrapped, not the same as mock_wrapper
        assert callable(registered_tool["function"])
        assert registered_tool["parameters"]["param"]["required"] is True

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_tool_display_name_via_full_registration_path(self, mock_manager_class):
        """get_tool_display_name resolves MCP namespacing after full registration."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.name = "oem"
        mock_client.server_info = {"name": "OEM Experience Zone"}

        mock_tool = MCPTool(
            name="launch_experience_zone",
            description="Launch the experience zone",
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        mock_client.list_tools.return_value = [mock_tool]
        mock_client.create_tool_wrapper.return_value = Mock(return_value="ok")

        mock_manager.add_server.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        agent.connect_mcp_server("oem", {"command": "dotnet", "args": ["run"]})

        display = get_tool_display_name("mcp_oem_launch_experience_zone")
        assert display == "launch_experience_zone (oem)"

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_disconnect_mcp_server_unregisters_tools(self, mock_manager_class):
        """Test that disconnect_mcp_server removes tools from registry."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.name = "testserver"
        mock_client.server_info = {"name": "Test Server"}

        # Mock tool
        mock_tool = MCPTool(
            name="test_tool",
            description="Test tool",
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        mock_client.list_tools.return_value = [mock_tool]
        mock_client.create_tool_wrapper.return_value = Mock()

        mock_manager.add_server.return_value = mock_client
        mock_manager.get_client.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {"command": "npx", "args": ["-y", "server"]}
        agent.connect_mcp_server("testserver", config)

        # Tool should be registered
        tool_name = "mcp_testserver_test_tool"
        assert tool_name in _TOOL_REGISTRY

        # Disconnect
        agent.disconnect_mcp_server("testserver")

        # Tool should be unregistered
        assert tool_name not in _TOOL_REGISTRY
        mock_manager.remove_server.assert_called_once_with("testserver")

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_list_mcp_servers_returns_server_names(self, mock_manager_class):
        """Test that list_mcp_servers returns server names."""
        mock_manager = Mock()
        mock_manager.list_servers.return_value = ["server1", "server2"]
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        servers = agent.list_mcp_servers()

        assert servers == ["server1", "server2"]

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_get_mcp_client_returns_client(self, mock_manager_class):
        """Test that get_mcp_client returns the correct client."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_manager.get_client.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        client = agent.get_mcp_client("test")

        assert client == mock_client
        mock_manager.get_client.assert_called_once_with("test")

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_connect_returns_false_on_error(self, mock_manager_class):
        """Test that connect_mcp_server returns False on error."""
        mock_manager = Mock()
        mock_manager.add_server.side_effect = Exception("Connection failed")
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {"command": "npx", "args": ["-y", "server"]}
        result = agent.connect_mcp_server("test", config)

        assert result is False

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_multiple_servers_namespace_tools_correctly(self, mock_manager_class):
        """Test that tools from multiple servers get correctly namespaced."""
        mock_manager = Mock()

        # First server
        mock_client1 = Mock()
        mock_client1.is_connected.return_value = True
        mock_client1.name = "server1"
        mock_client1.server_info = {"name": "Server 1"}
        mock_tool1 = MCPTool(
            "read_file", "Read file", {"type": "object", "properties": {}}
        )
        mock_client1.list_tools.return_value = [mock_tool1]
        mock_client1.create_tool_wrapper.return_value = Mock()

        # Second server (same tool name)
        mock_client2 = Mock()
        mock_client2.is_connected.return_value = True
        mock_client2.name = "server2"
        mock_client2.server_info = {"name": "Server 2"}
        mock_tool2 = MCPTool(
            "read_file", "Read file", {"type": "object", "properties": {}}
        )
        mock_client2.list_tools.return_value = [mock_tool2]
        mock_client2.create_tool_wrapper.return_value = Mock()

        mock_manager.add_server.side_effect = [mock_client1, mock_client2]
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config1 = {"command": "npx", "args": ["-y", "s1"]}
        config2 = {"command": "npx", "args": ["-y", "s2"]}
        agent.connect_mcp_server("server1", config1)
        agent.connect_mcp_server("server2", config2)

        # Both tools should be registered with different names
        assert "mcp_server1_read_file" in _TOOL_REGISTRY
        assert "mcp_server2_read_file" in _TOOL_REGISTRY

        # Descriptions should include server names
        assert "[MCP:server1]" in _TOOL_REGISTRY["mcp_server1_read_file"]["description"]
        assert "[MCP:server2]" in _TOOL_REGISTRY["mcp_server2_read_file"]["description"]

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_connect_mcp_server_requires_config_dict(self, mock_manager_class):
        """Test that connect_mcp_server requires a config dict."""
        mock_manager = Mock()
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()

        # Should raise when passing a string instead of dict
        with pytest.raises(ValueError, match="config dict"):
            agent.connect_mcp_server("test", "echo test")

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_connect_mcp_server_raises_without_command(self, mock_manager_class):
        """Test that connect_mcp_server raises if config missing command."""
        mock_manager = Mock()
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {"args": ["-y", "server"]}  # Missing 'command'

        with pytest.raises(ValueError, match="command"):
            agent.connect_mcp_server("test", config)

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_connect_mcp_server_passes_env(self, mock_manager_class):
        """Test that connect_mcp_server passes env to manager."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.server_info = {"name": "Test Server"}
        mock_client.list_tools.return_value = []
        mock_manager.add_server.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_xxx"},
        }
        agent.connect_mcp_server("github", config)

        # Verify the full config was passed
        mock_manager.add_server.assert_called_once_with("github", config)

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_connect_mcp_server_rejects_http_type(self, mock_manager_class):
        """Test that connect_mcp_server raises for non-stdio types."""
        mock_manager = Mock()
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {
            "type": "sse",
            "url": "http://localhost:8080/sse",
        }

        with pytest.raises(ValueError, match="only supports stdio"):
            agent.connect_mcp_server("http_server", config)


class TestMCPClientMixinConfigFile:
    """Tests for config_file behavior in MCPClientMixin."""

    def setup_method(self):
        _TOOL_REGISTRY.clear()

    def teardown_method(self):
        _TOOL_REGISTRY.clear()

    @patch("gaia.mcp.mixin.MCPClientManager")
    @patch("gaia.mcp.mixin.MCPConfig")
    def test_config_file_forces_loading_even_when_auto_load_false(
        self, mock_config_class, mock_manager_class
    ):
        """config_file triggers load_mcp_servers_from_config even if auto_load_config=False."""
        mock_config = mock_config_class.return_value
        mock_config.load_report = {
            "mode": "explicit",
            "config_file": "/path/to/mcp_servers.json",
            "servers": [],
        }
        mock_manager = mock_manager_class.return_value
        mock_manager.config = mock_config
        mock_manager.list_servers.return_value = []
        mock_manager.config.get_servers.return_value = {}

        class ConfigFileAgent(MCPClientMixin):
            def __init__(self):
                self.debug = False
                super().__init__(auto_load_config=False, config_file="/path/to/mcp_servers.json")

        agent = ConfigFileAgent()
        mock_manager.load_from_config.assert_called_once()

    @patch("gaia.mcp.mixin.MCPClientManager")
    @patch("gaia.mcp.mixin.MCPConfig")
    def test_auto_load_false_and_no_config_file_skips_loading(
        self, mock_config_class, mock_manager_class
    ):
        """auto_load_config=False with no config_file skips load_mcp_servers_from_config."""
        mock_config = mock_config_class.return_value
        mock_manager = mock_manager_class.return_value
        mock_manager.config = mock_config

        agent = MockAgent(auto_load_config=False)
        mock_manager.load_from_config.assert_not_called()


class TestMCPToolResponseWrapper:
    """Test that MCP tool responses are wrapped in GAIA-style format."""

    def setup_method(self):
        """Clear tool registry before each test."""
        _TOOL_REGISTRY.clear()

    def teardown_method(self):
        """Clear tool registry after each test."""
        _TOOL_REGISTRY.clear()

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_json_response_wrapped_with_gaia_format(self, mock_manager_class):
        """JSON dict responses should be wrapped with status/message/data/instruction."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.name = "testserver"
        mock_client.server_info = {"name": "Test Server"}

        # Mock tool
        mock_tool = MCPTool(
            name="get_stats",
            description="Get stats",
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        mock_client.list_tools.return_value = [mock_tool]

        # Mock wrapper returns raw JSON
        mock_wrapper = Mock(return_value={"cpu": 45.2, "memory": 78.5})
        mock_client.create_tool_wrapper.return_value = mock_wrapper

        mock_manager.add_server.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {"command": "npx", "args": ["-y", "server"]}
        agent.connect_mcp_server("testserver", config)

        # Get the registered wrapper and call it
        tool_name = "mcp_testserver_get_stats"
        wrapper = _TOOL_REGISTRY[tool_name]["function"]
        result = wrapper()

        # Verify GAIA-style format
        assert result["status"] == "success"
        assert "message" in result
        assert result["data"] == {"cpu": 45.2, "memory": 78.5}
        assert "instruction" in result
        assert "plain text string" in result["instruction"]

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_error_response_wrapped_with_error_status(self, mock_manager_class):
        """Error responses should be wrapped with status='error'."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.name = "testserver"
        mock_client.server_info = {"name": "Test Server"}

        mock_tool = MCPTool(
            name="failing_tool",
            description="Failing tool",
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        mock_client.list_tools.return_value = [mock_tool]

        # Mock wrapper returns error
        mock_wrapper = Mock(return_value={"error": "Command failed: access denied"})
        mock_client.create_tool_wrapper.return_value = mock_wrapper

        mock_manager.add_server.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {"command": "npx", "args": ["-y", "server"]}
        agent.connect_mcp_server("testserver", config)

        # Get the registered wrapper and call it
        wrapper = _TOOL_REGISTRY["mcp_testserver_failing_tool"]["function"]
        result = wrapper()

        # Verify error format
        assert result["status"] == "error"
        assert result["error"] == "Command failed: access denied"
        assert "data" in result

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_string_response_passed_through(self, mock_manager_class):
        """Non-dict responses (strings) should pass through unchanged."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.name = "testserver"
        mock_client.server_info = {"name": "Test Server"}

        mock_tool = MCPTool(
            name="string_tool",
            description="String tool",
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        mock_client.list_tools.return_value = [mock_tool]

        # Mock wrapper returns plain string
        mock_wrapper = Mock(return_value="Plain text output from tool")
        mock_client.create_tool_wrapper.return_value = mock_wrapper

        mock_manager.add_server.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {"command": "npx", "args": ["-y", "server"]}
        agent.connect_mcp_server("testserver", config)

        # Get the registered wrapper and call it
        wrapper = _TOOL_REGISTRY["mcp_testserver_string_tool"]["function"]
        result = wrapper()

        # String should pass through unchanged
        assert result == "Plain text output from tool"

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_list_response_passed_through(self, mock_manager_class):
        """List responses should pass through unchanged (not a dict)."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.name = "testserver"
        mock_client.server_info = {"name": "Test Server"}

        mock_tool = MCPTool(
            name="list_tool",
            description="List tool",
            input_schema={"type": "object", "properties": {}, "required": []},
        )
        mock_client.list_tools.return_value = [mock_tool]

        # Mock wrapper returns list
        mock_wrapper = Mock(return_value=["item1", "item2", "item3"])
        mock_client.create_tool_wrapper.return_value = mock_wrapper

        mock_manager.add_server.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        config = {"command": "npx", "args": ["-y", "server"]}
        agent.connect_mcp_server("testserver", config)

        # Get the registered wrapper and call it
        wrapper = _TOOL_REGISTRY["mcp_testserver_list_tool"]["function"]
        result = wrapper()

        # List should pass through unchanged
        assert result == ["item1", "item2", "item3"]


class TestMCPLoadSummary:
    """Tests for _print_mcp_load_summary output."""

    def setup_method(self):
        _TOOL_REGISTRY.clear()

    def teardown_method(self):
        _TOOL_REGISTRY.clear()

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_explicit_config_shows_connected_servers(self, mock_manager_class):
        """Explicit config: connected server shown with ✓ and tool count."""
        mock_client = Mock()
        mock_client.list_tools.return_value = [Mock(), Mock()]

        mock_manager = Mock()
        mock_manager.config.load_report = {
            "mode": "explicit",
            "config_file": "/path/to/mcp_servers.json",
            "servers": ["oem"],
        }
        mock_manager.config.get_servers.return_value = {"oem": {}}
        mock_manager.list_servers.return_value = ["oem"]
        mock_manager.get_client.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        console = Mock()
        agent = MockAgent()
        agent.console = console

        agent._print_mcp_load_summary()

        console.print_info.assert_called_once()
        message = console.print_info.call_args[0][0]
        assert "✓" in message
        assert "oem" in message
        assert "2 tools" in message

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_failed_server_shown_with_cross(self, mock_manager_class):
        """Server that failed to connect is shown with ✗."""
        mock_manager = Mock()
        mock_manager.config.load_report = {
            "mode": "explicit",
            "config_file": "/path/to/mcp_servers.json",
            "servers": ["windows"],
        }
        mock_manager.config.get_servers.return_value = {"windows": {}}
        mock_manager.list_servers.return_value = []  # none connected
        mock_manager_class.return_value = mock_manager

        console = Mock()
        agent = MockAgent()
        agent.console = console

        agent._print_mcp_load_summary()

        message = console.print_info.call_args[0][0]
        assert "✗" in message
        assert "windows" in message
        assert "failed to connect" in message

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_auto_config_shows_both_paths(self, mock_manager_class):
        """Auto-load: both global and local paths appear in the summary."""
        from pathlib import Path

        mock_client = Mock()
        mock_client.list_tools.return_value = []

        mock_manager = Mock()
        mock_manager.config.load_report = {
            "mode": "auto",
            "config_file": Path("/tmp/local/mcp_servers.json"),
            "global": {
                "path": Path("/tmp/global/.gaia/mcp_servers.json"),
                "exists": True,
                "servers": ["github"],
            },
            "local": {
                "path": Path("/tmp/local/mcp_servers.json"),
                "exists": True,
                "servers": ["oem"],
            },
            "overrides": [],
        }
        mock_manager.config.get_servers.return_value = {"github": {}, "oem": {}}
        mock_manager.list_servers.return_value = ["github", "oem"]
        mock_manager.get_client.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        console = Mock()
        agent = MockAgent()
        agent.console = console

        agent._print_mcp_load_summary()

        message = console.print_info.call_args[0][0]
        assert "local" in message
        assert "global" in message
        assert "github" in message
        assert "oem" in message

    @patch("gaia.mcp.mixin.MCPClientManager")
    def test_auto_config_shows_overrides(self, mock_manager_class):
        """Auto-load report highlights servers that local overrides from global."""
        from pathlib import Path

        mock_client = Mock()
        mock_client.list_tools.return_value = []

        mock_manager = Mock()
        mock_manager.config.load_report = {
            "mode": "auto",
            "config_file": Path("/tmp/local/mcp_servers.json"),
            "global": {
                "path": Path("/tmp/global/.gaia/mcp_servers.json"),
                "exists": True,
                "servers": ["shared"],
            },
            "local": {
                "path": Path("/tmp/local/mcp_servers.json"),
                "exists": True,
                "servers": ["shared"],
            },
            "overrides": ["shared"],
        }
        mock_manager.config.get_servers.return_value = {"shared": {}}
        mock_manager.list_servers.return_value = ["shared"]
        mock_manager.get_client.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        console = Mock()
        agent = MockAgent()
        agent.console = console

        agent._print_mcp_load_summary()

        message = console.print_info.call_args[0][0]
        assert "overrides" in message
        assert "shared" in message


class TestToolDisplayName:
    """Tests for get_tool_display_name utility."""

    def setup_method(self):
        _TOOL_REGISTRY.clear()

    def teardown_method(self):
        _TOOL_REGISTRY.clear()

    def test_tool_display_name_for_mcp_tools(self):
        """MCP tools resolve namespaced registry key to '{tool} ({server})' display name."""
        _TOOL_REGISTRY["mcp_oem_launch_experience_zone"] = {
            "name": "mcp_oem_launch_experience_zone",
            "display_name": "launch_experience_zone (oem)",
            "description": "[MCP:oem] Launch the experience zone",
            "parameters": {},
            "_mcp_server": "oem",
        }

        display = get_tool_display_name("mcp_oem_launch_experience_zone")
        assert display == "launch_experience_zone (oem)"

    def test_tool_display_name_for_native_tools(self):
        """Native tools return their name unchanged."""
        _TOOL_REGISTRY["read_file"] = {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {},
        }

        assert get_tool_display_name("read_file") == "read_file"

    def test_tool_display_name_unknown_tool(self):
        """Unknown tool names (not in registry) are returned unchanged."""
        assert get_tool_display_name("nonexistent_tool") == "nonexistent_tool"
