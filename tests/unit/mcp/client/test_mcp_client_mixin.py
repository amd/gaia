"""Unit tests for MCPClientMixin."""

from unittest.mock import Mock, patch

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.mcp import MCPClientMixin
from gaia.mcp.client.mcp_client import MCPTool


class MockAgent(MCPClientMixin):
    """Mock agent for testing the mixin."""

    def __init__(self, debug=False):
        self.debug = debug
        super().__init__()


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
        """Test that connect_mcp_server adds server to manager."""
        mock_manager = Mock()
        mock_client = Mock()
        mock_client.is_connected.return_value = True
        mock_client.server_info = {"name": "Test Server"}
        mock_client.list_tools.return_value = []
        mock_manager.add_server.return_value = mock_client
        mock_manager_class.return_value = mock_manager

        agent = MockAgent()
        result = agent.connect_mcp_server("test", command="echo test")

        assert result is True
        mock_manager.add_server.assert_called_once_with(
            "test", command="echo test", config=None
        )

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
        agent.connect_mcp_server("testserver", command="echo test")

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
        agent.connect_mcp_server("testserver", command="echo test")

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
        result = agent.connect_mcp_server("test", command="echo test")

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
        agent.connect_mcp_server("server1", command="echo 1")
        agent.connect_mcp_server("server2", command="echo 2")

        # Both tools should be registered with different names
        assert "mcp_server1_read_file" in _TOOL_REGISTRY
        assert "mcp_server2_read_file" in _TOOL_REGISTRY

        # Descriptions should include server names
        assert "[MCP:server1]" in _TOOL_REGISTRY["mcp_server1_read_file"]["description"]
        assert "[MCP:server2]" in _TOOL_REGISTRY["mcp_server2_read_file"]["description"]


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
        agent.connect_mcp_server("testserver", command="echo test")

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
        agent.connect_mcp_server("testserver", command="echo test")

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
        agent.connect_mcp_server("testserver", command="echo test")

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
        agent.connect_mcp_server("testserver", command="echo test")

        # Get the registered wrapper and call it
        wrapper = _TOOL_REGISTRY["mcp_testserver_list_tool"]["function"]
        result = wrapper()

        # List should pass through unchanged
        assert result == ["item1", "item2", "item3"]
