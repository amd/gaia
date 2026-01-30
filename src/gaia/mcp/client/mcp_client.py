"""MCP Client for interacting with MCP servers."""

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from gaia.logger import get_logger

from .transports.base import MCPTransport
from .transports.stdio import StdioTransport

logger = get_logger(__name__)


@dataclass
class MCPTool:
    """Represents an MCP tool with its schema.

    Attributes:
        name: Tool name from MCP server
        description: Tool description
        input_schema: MCP inputSchema (JSON Schema format)
    """

    name: str
    description: str
    input_schema: Dict[str, Any]

    def to_gaia_format(self, server_name: str) -> Dict[str, Any]:
        """Convert MCP tool schema to GAIA _TOOL_REGISTRY format.

        Args:
            server_name: Name of the MCP server providing this tool

        Returns:
            dict: GAIA tool registry entry (without function field)
        """
        properties = self.input_schema.get("properties", {})
        required_list = self.input_schema.get("required", [])

        # Convert MCP parameters to GAIA format
        gaia_params = {}
        for param_name, param_schema in properties.items():
            gaia_params[param_name] = {
                "type": param_schema.get("type", "string"),
                "required": param_name in required_list,
                "description": param_schema.get("description", ""),
            }

        return {
            "name": f"mcp_{server_name}_{self.name}",
            "description": f"[MCP:{server_name}] {self.description}",
            "parameters": gaia_params,
            "atomic": True,
            # Metadata for debugging/routing
            "_mcp_server": server_name,
            "_mcp_tool_name": self.name,
        }


class MCPClient:
    """Client for interacting with an MCP server.

    Args:
        name: Friendly name for this server
        transport: Transport implementation to use
        debug: Enable debug logging
    """

    def __init__(self, name: str, transport: MCPTransport, debug: bool = False):
        self.name = name
        self.transport = transport
        self.debug = debug
        self.server_info: Dict[str, Any] = {}
        self._tools: Optional[List[MCPTool]] = None

    @classmethod
    def from_command(
        cls, name: str, command: str, timeout: int = 30, debug: bool = False
    ) -> "MCPClient":
        """Create an MCP client using stdio transport.

        Args:
            name: Friendly name for this server
            command: Shell command to start the server
            timeout: Request timeout in seconds
            debug: Enable debug logging

        Returns:
            MCPClient: Configured client instance
        """
        transport = StdioTransport(command, timeout=timeout, debug=debug)
        return cls(name, transport, debug=debug)

    def connect(self) -> bool:
        """Connect to the MCP server and initialize.

        Returns:
            bool: True if connection and initialization successful
        """
        logger.debug(f"Connecting to MCP server '{self.name}'...")

        if not self.transport.connect():
            logger.error(f"Failed to establish transport connection to '{self.name}'")
            return False

        try:
            # Send initialize request with proper MCP format
            response = self.transport.send_request(
                "initialize",
                {
                    "protocolVersion": "1.0.0",
                    "clientInfo": {
                        "name": "GAIA MCP Client",
                        "version": "0.15.2",
                    },
                    "capabilities": {},
                },
            )

            if "error" in response:
                error = response["error"]
                logger.error(
                    f"Initialization failed: {error.get('message', 'Unknown error')}"
                )
                return False

            result = response.get("result", {})
            self.server_info = result.get("serverInfo", {})

            logger.debug(
                f"Connected to '{self.name}' - {self.server_info.get('name', 'Unknown')}"
            )
            return True

        except Exception as e:
            logger.error(f"Error during initialization: {e}")
            self.disconnect()
            return False

    def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        logger.debug(f"Disconnecting from MCP server '{self.name}'")
        self.transport.disconnect()
        self._tools = None

    def is_connected(self) -> bool:
        """Check if connected to server.

        Returns:
            bool: True if connected
        """
        return self.transport.is_connected()

    def list_tools(self, refresh: bool = False) -> List[MCPTool]:
        """List all available tools from the server.

        Args:
            refresh: Force refresh from server (default: cache result)

        Returns:
            list[MCPTool]: Available tools
        """
        if self._tools is not None and not refresh:
            return self._tools

        logger.debug(f"Fetching tools from MCP server '{self.name}'")

        response = self.transport.send_request("tools/list")

        if "error" in response:
            error = response["error"]
            logger.error(f"Failed to list tools: {error.get('message', 'Unknown')}")
            return []

        result = response.get("result", {})
        tools_data = result.get("tools", [])

        self._tools = [
            MCPTool(
                name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
            )
            for tool in tools_data
        ]

        logger.debug(f"Found {len(self._tools)} tools from '{self.name}'")
        return self._tools

    def _format_validation_error(
        self, tool_name: str, error_msg: str, arguments: Dict[str, Any]
    ) -> str:
        """Format MCP validation errors with helpful context.

        Args:
            tool_name: Name of the tool that failed
            error_msg: Raw error message from MCP server
            arguments: The arguments that were passed

        Returns:
            Enhanced error message with schema and provided args
        """
        # Get the tool schema for reference
        tool_def = next((t for t in self.list_tools() if t.name == tool_name), None)

        if not tool_def:
            return error_msg

        # Build enhanced error message
        lines = [
            f"MCP tool '{tool_name}' input validation failed.",
            "",
            "Error details:",
            error_msg,
            "",
            "Expected tool schema:",
            json.dumps(tool_def.input_schema, indent=2),
            "",
            "Your arguments:",
            json.dumps(arguments, indent=2),
        ]

        return "\n".join(lines)

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            dict: Tool response

        Raises:
            RuntimeError: If not connected
        """
        if not self.is_connected():
            raise RuntimeError(f"Not connected to MCP server '{self.name}'")

        logger.debug(f"Invoking MCP tool: {tool_name} from server '{self.name}'")

        if self.debug:
            logger.debug(f"  Arguments: {json.dumps(arguments, indent=2)}")

        response = self.transport.send_request(
            "tools/call", {"name": tool_name, "arguments": arguments}
        )

        if "error" in response:
            error = response["error"]
            error_msg = error.get("message", "Unknown error")

            # Enhance validation errors with schema context
            if error.get("code") == -32602:  # JSON-RPC invalid params error
                error_msg = self._format_validation_error(
                    tool_name, error_msg, arguments
                )

            logger.error(f"Tool execution failed: {error_msg}")
            return {"error": error_msg}

        result = response.get("result", {})

        if self.debug:
            logger.debug(f"Tool {tool_name} completed successfully")
            logger.debug(f"  Response: {json.dumps(result, indent=2)}")

        logger.debug(f"Tool {tool_name} completed successfully")
        return result

    def create_tool_wrapper(self, tool: MCPTool) -> Callable[..., Dict[str, Any]]:
        """Create a callable wrapper for an MCP tool.

        The wrapper accepts **kwargs to be compatible with GAIA's
        agent._execute_tool() which calls tool(**tool_args).

        Args:
            tool: MCPTool to wrap

        Returns:
            callable: Function that calls the MCP tool
        """

        def wrapper(**kwargs: Any) -> Dict[str, Any]:
            return self.call_tool(tool.name, kwargs)

        return wrapper
