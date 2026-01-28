"""Mixin for adding MCP client support to agents."""

from typing import Any, Dict, List

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.logger import get_logger
from gaia.mcp.client.mcp_client import MCPClient
from gaia.mcp.client.mcp_client_manager import MCPClientManager

logger = get_logger(__name__)


class MCPClientMixin:
    """Mixin to add MCP client capabilities to agents.

    This mixin allows any agent to connect to MCP servers and use their tools.
    MCP tools are automatically registered in the agent's _TOOL_REGISTRY.

    Usage:
        class MyAgent(Agent, MCPClientMixin):
            def __init__(self, ...):
                super().__init__(...)
                self.connect_mcp_server("filesystem", "npx @modelcontextprotocol/server-filesystem")
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mcp_manager = MCPClientManager(debug=getattr(self, "debug", False))

    def connect_mcp_server(
        self, name: str, command: str = None, config: Dict = None
    ) -> bool:
        """Connect to an MCP server and register its tools.

        Args:
            name: Friendly name for the server
            command: Shell command to start server (for stdio transport)
            config: Optional server configuration dict

        Returns:
            bool: True if connection and tool registration successful
        """
        logger.debug(f"Connecting to MCP server '{name}'...")

        if getattr(self, "debug", False):
            print(f"[DEBUG] Command: {command}")

        try:
            client = self._mcp_manager.add_server(name, command=command, config=config)

            if client.is_connected():
                # Register tools
                self._register_mcp_tools(client)

                logger.debug(
                    f"Connected to '{name}' - {client.server_info.get('name', 'Unknown')}"
                )
                return True
            else:
                logger.warning(f"Failed to connect to MCP server '{name}'")
                return False

        except Exception as e:
            logger.error(f"Error connecting to '{name}': {e}")
            return False

    def load_mcp_servers_from_config(self) -> int:
        """Load and register MCP servers from configuration file.

        This is a convenience method that:
        1. Loads servers from ~/.gaia/mcp_servers.json via the manager
        2. Registers all tools from loaded servers

        Returns:
            int: Number of servers loaded and registered

        Example:
            >>> agent = MyAgent()
            >>> count = agent.load_mcp_servers_from_config()
            >>> print(f"Loaded {count} MCP servers")
        """
        # Load servers from config
        self._mcp_manager.load_from_config()

        # Register tools from all loaded servers
        server_count = 0
        for server_name in self._mcp_manager.list_servers():
            client = self._mcp_manager.get_client(server_name)
            self._register_mcp_tools(client)
            server_count += 1

        return server_count

    def disconnect_mcp_server(self, name: str) -> None:
        """Disconnect from an MCP server and unregister its tools.

        Args:
            name: Server name
        """
        client = self._mcp_manager.get_client(name)
        if not client:
            logger.warning(f"MCP server '{name}' not found")
            return

        # Unregister tools
        self._unregister_mcp_tools(client)

        # Disconnect
        self._mcp_manager.remove_server(name)

        logger.debug(f"Disconnected from MCP server '{name}'")

    def list_mcp_servers(self) -> List[str]:
        """List all connected MCP servers.

        Returns:
            list[str]: Server names
        """
        return self._mcp_manager.list_servers()

    def get_mcp_client(self, name: str) -> MCPClient:
        """Get an MCP client by name.

        Args:
            name: Server name

        Returns:
            MCPClient or None: Client instance if exists
        """
        return self._mcp_manager.get_client(name)

    def _register_mcp_tools(self, client: MCPClient) -> None:
        """Register all tools from an MCP server into _TOOL_REGISTRY.

        Args:
            client: MCPClient instance
        """
        tools = client.list_tools()

        for tool in tools:
            # Convert to GAIA format
            gaia_tool = tool.to_gaia_format(client.name)

            # Create wrapper function
            wrapper = client.create_tool_wrapper(tool)
            gaia_tool["function"] = wrapper

            # Register in global registry
            gaia_name = gaia_tool["name"]
            _TOOL_REGISTRY[gaia_name] = gaia_tool

            logger.debug(f"Registered MCP tool: {gaia_name}")

        logger.debug(
            f"Registered {len(tools)} tools from MCP server '{client.name}'"
        )

    def _unregister_mcp_tools(self, client: MCPClient) -> None:
        """Unregister all tools from an MCP server.

        Args:
            client: MCPClient instance
        """
        tools = client.list_tools()

        for tool in tools:
            gaia_name = f"mcp_{client.name}_{tool.name}"
            if gaia_name in _TOOL_REGISTRY:
                del _TOOL_REGISTRY[gaia_name]
                logger.debug(f"Unregistered MCP tool: {gaia_name}")

        logger.debug(
            f"Unregistered {len(tools)} tools from MCP server '{client.name}'"
        )

    def __del__(self):
        """Cleanup: disconnect from all MCP servers."""
        if hasattr(self, "_mcp_manager"):
            self._mcp_manager.disconnect_all()
