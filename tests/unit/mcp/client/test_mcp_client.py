"""Unit tests for MCPClient."""

from unittest.mock import Mock

import pytest

from gaia.mcp.client.mcp_client import MCPClient, MCPTool


class TestMCPToolConversion:
    """Test MCP → GAIA format conversion."""

    def test_converts_required_array_to_per_param(self):
        """Test that MCP required array becomes per-param boolean."""
        mcp_tool = MCPTool(
            name="read_file",
            description="Read a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "encoding": {"type": "string", "description": "File encoding"},
                },
                "required": ["path"],  # Array format
            },
        )

        gaia_format = mcp_tool.to_gaia_format("filesystem")

        # GAIA uses per-param boolean
        assert gaia_format["parameters"]["path"]["required"] is True
        assert gaia_format["parameters"]["encoding"]["required"] is False

    def test_adds_server_namespace_to_name(self):
        """Test that tool names get namespaced with server name."""
        mcp_tool = MCPTool(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object", "properties": {}, "required": []},
        )

        gaia_format = mcp_tool.to_gaia_format("filesystem")

        assert gaia_format["name"] == "mcp_filesystem_read_file"

    def test_adds_server_prefix_to_description(self):
        """Test that descriptions get server prefix."""
        mcp_tool = MCPTool(
            name="search",
            description="Search for content",
            input_schema={"type": "object", "properties": {}, "required": []},
        )

        gaia_format = mcp_tool.to_gaia_format("github")

        assert gaia_format["description"] == "[MCP:github] Search for content"

    def test_includes_metadata_fields(self):
        """Test that GAIA format includes metadata for routing."""
        mcp_tool = MCPTool(
            name="test_tool",
            description="Test",
            input_schema={"type": "object", "properties": {}, "required": []},
        )

        gaia_format = mcp_tool.to_gaia_format("testserver")

        assert gaia_format["_mcp_server"] == "testserver"
        assert gaia_format["_mcp_tool_name"] == "test_tool"
        assert gaia_format["atomic"] is True

    def test_preserves_parameter_types(self):
        """Test that parameter types are preserved."""
        mcp_tool = MCPTool(
            name="calc",
            description="Calculate",
            input_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Count"},
                    "flag": {"type": "boolean", "description": "Flag"},
                    "name": {"type": "string", "description": "Name"},
                },
                "required": [],
            },
        )

        gaia_format = mcp_tool.to_gaia_format("math")

        assert gaia_format["parameters"]["count"]["type"] == "integer"
        assert gaia_format["parameters"]["flag"]["type"] == "boolean"
        assert gaia_format["parameters"]["name"]["type"] == "string"

    def test_handles_empty_properties(self):
        """Test that tools with no parameters work correctly."""
        mcp_tool = MCPTool(
            name="ping",
            description="Ping server",
            input_schema={"type": "object", "properties": {}, "required": []},
        )

        gaia_format = mcp_tool.to_gaia_format("monitor")

        assert gaia_format["parameters"] == {}
        assert gaia_format["name"] == "mcp_monitor_ping"


class TestMCPClient:
    """Test MCPClient functionality."""

    def test_from_command_creates_stdio_transport(self):
        """Test that from_command factory creates stdio transport."""
        client = MCPClient.from_command("test", "echo test")

        assert client.name == "test"
        assert client.transport is not None

    def test_connect_sends_initialize_request(self):
        """Test that connect sends initialize request."""
        mock_transport = Mock()
        mock_transport.connect.return_value = True
        mock_transport.send_request.return_value = {
            "jsonrpc": "2.0",
            "id": 0,
            "result": {
                "protocolVersion": "1.0.0",
                "serverInfo": {"name": "Test Server", "version": "1.0"},
                "capabilities": {"tools": True},
            },
        }

        client = MCPClient("test", mock_transport)
        result = client.connect()

        assert result is True
        mock_transport.connect.assert_called_once()
        # Verify initialize was called with proper MCP format
        call_args = mock_transport.send_request.call_args
        assert call_args[0][0] == "initialize"
        assert call_args[0][1]["protocolVersion"] == "1.0.0"
        assert "clientInfo" in call_args[0][1]
        assert "capabilities" in call_args[0][1]
        assert client.server_info["name"] == "Test Server"

    def test_connect_returns_false_on_error(self):
        """Test that connect returns False on error response."""
        mock_transport = Mock()
        mock_transport.connect.return_value = True
        mock_transport.send_request.return_value = {
            "jsonrpc": "2.0",
            "id": 0,
            "error": {"code": -1, "message": "Server error"},
        }

        client = MCPClient("test", mock_transport)
        result = client.connect()

        assert result is False

    def test_list_tools_fetches_from_server(self):
        """Test that list_tools fetches and parses tools."""
        mock_transport = Mock()
        mock_transport.is_connected.return_value = True
        mock_transport.send_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo back",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string", "description": "Message"}
                            },
                            "required": ["message"],
                        },
                    }
                ]
            },
        }

        client = MCPClient("test", mock_transport)
        tools = client.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "echo"
        assert tools[0].description == "Echo back"

    def test_list_tools_caches_result(self):
        """Test that list_tools caches results."""
        mock_transport = Mock()
        mock_transport.is_connected.return_value = True
        mock_transport.send_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": []},
        }

        client = MCPClient("test", mock_transport)
        client.list_tools()
        client.list_tools()  # Second call

        # Should only call transport once
        assert mock_transport.send_request.call_count == 1

    def test_call_tool_sends_correct_request(self):
        """Test that call_tool sends correct JSON-RPC request."""
        mock_transport = Mock()
        mock_transport.is_connected.return_value = True
        mock_transport.send_request.return_value = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "Success"}]},
        }

        client = MCPClient("test", mock_transport)
        result = client.call_tool("echo", {"message": "hello"})

        mock_transport.send_request.assert_called_once_with(
            "tools/call", {"name": "echo", "arguments": {"message": "hello"}}
        )
        assert result["content"][0]["text"] == "Success"

    def test_call_tool_raises_when_not_connected(self):
        """Test that call_tool raises error when not connected."""
        mock_transport = Mock()
        mock_transport.is_connected.return_value = False

        client = MCPClient("test", mock_transport)

        with pytest.raises(RuntimeError, match="Not connected"):
            client.call_tool("echo", {})

    def test_create_tool_wrapper_returns_callable(self):
        """Test that create_tool_wrapper creates a callable."""
        mock_transport = Mock()
        mock_transport.is_connected.return_value = True
        mock_transport.send_request.return_value = {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {"status": "ok"},
        }

        client = MCPClient("test", mock_transport)
        tool = MCPTool("test_tool", "Test", {"type": "object", "properties": {}})

        wrapper = client.create_tool_wrapper(tool)
        result = wrapper(arg="value")

        assert result["status"] == "ok"
        mock_transport.send_request.assert_called_once()

    def test_wrapper_compatible_with_agent_execute_tool(self):
        """Test wrapper works with **kwargs (how Agent._execute_tool calls it)."""
        mock_transport = Mock()
        mock_transport.is_connected.return_value = True
        mock_transport.send_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }

        client = MCPClient("test", mock_transport)
        tool = MCPTool(
            "test",
            "Test",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
        wrapper = client.create_tool_wrapper(tool)

        # Simulate Agent._execute_tool() calling tool(**tool_args)
        result = wrapper(path="/tmp/foo")  # kwargs, not dict

        mock_transport.send_request.assert_called_with(
            "tools/call", {"name": "test", "arguments": {"path": "/tmp/foo"}}
        )


class TestEnhancedErrorMessages:
    """Test enhanced error message formatting for validation errors."""

    def test_format_validation_error_includes_schema(self):
        """Test that validation error includes tool schema."""
        mock_transport = Mock()
        mock_transport.is_connected.return_value = True

        client = MCPClient("test", mock_transport)

        # Setup tool with schema
        tool = MCPTool(
            "create_entities",
            "Create entities",
            {
                "type": "object",
                "properties": {
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "entityType": {"type": "string"},
                                "observations": {"type": "array"},
                            },
                            "required": ["name", "entityType", "observations"],
                        },
                    }
                },
                "required": ["entities"],
            },
        )

        # Make tool available to client (use correct cache attribute)
        client._tools = [tool]

        error_msg = "Invalid input: expected string, received undefined"
        arguments = {
            "entities": [{"name": "Test", "type": "timestamp"}]  # Wrong key "type"
        }

        result = client._format_validation_error("create_entities", error_msg, arguments)

        # Should include original error
        assert error_msg in result

        # Should include schema
        assert "Expected tool schema:" in result
        assert '"entityType"' in result
        assert '"observations"' in result

        # Should include provided arguments
        assert "Your arguments:" in result
        assert '"type": "timestamp"' in result

    def test_format_validation_error_includes_arguments(self):
        """Test that validation error includes the arguments that were passed."""
        mock_transport = Mock()
        client = MCPClient("test", mock_transport)

        tool = MCPTool(
            "test_tool",
            "Test",
            {
                "type": "object",
                "properties": {"required_field": {"type": "string"}},
                "required": ["required_field"],
            },
        )
        client._tools = [tool]

        arguments = {"wrong_field": "value"}
        result = client._format_validation_error(
            "test_tool", "Missing required field", arguments
        )

        assert "Your arguments:" in result
        assert '"wrong_field": "value"' in result

    def test_format_validation_error_fallback_for_unknown_tool(self):
        """Test that unknown tool name returns original error message."""
        mock_transport = Mock()
        client = MCPClient("test", mock_transport)
        client._tools = []

        error_msg = "Some error"
        result = client._format_validation_error("unknown_tool", error_msg, {})

        # Should return original message when tool not found
        assert result == error_msg

    def test_call_tool_enhances_validation_errors(self):
        """Test that call_tool detects and enhances validation errors."""
        mock_transport = Mock()
        mock_transport.is_connected.return_value = True
        mock_transport.send_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32602,  # JSON-RPC invalid params error
                "message": "Invalid arguments for tool create_entities: [...]",
            },
        }

        client = MCPClient("test", mock_transport)

        # Setup tool schema
        tool = MCPTool(
            "create_entities",
            "Create entities",
            {
                "type": "object",
                "properties": {
                    "entities": {
                        "type": "array",
                        "items": {
                            "properties": {
                                "name": {"type": "string"},
                                "entityType": {"type": "string"},
                            },
                            "required": ["name", "entityType"],
                        },
                    }
                },
            },
        )
        client._tools = [tool]

        result = client.call_tool(
            "create_entities", {"entities": [{"name": "Test"}]}  # Missing entityType
        )

        # Should return error with enhanced message
        assert "error" in result
        error_msg = result["error"]

        # Enhanced error should include schema
        assert "Expected tool schema:" in error_msg
        assert "Your arguments:" in error_msg
        assert '"entityType"' in error_msg

    def test_call_tool_preserves_non_validation_errors(self):
        """Test that non-validation errors are not enhanced."""
        mock_transport = Mock()
        mock_transport.is_connected.return_value = True
        mock_transport.send_request.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32600,  # Different error code (invalid request)
                "message": "Invalid request format",
            },
        }

        client = MCPClient("test", mock_transport)

        result = client.call_tool("some_tool", {})

        # Should return error but NOT enhanced
        assert "error" in result
        assert result["error"] == "Invalid request format"
        assert "Expected tool schema:" not in result["error"]
