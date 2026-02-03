# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mixin for adding MCP client support to agents."""

from typing import Dict, List

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
                self.connect_mcp_server("github", {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "ghp_xxx"}
                })
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mcp_manager = MCPClientManager(debug=getattr(self, "debug", False))

    def connect_mcp_server(self, name: str, config: Dict) -> bool:
        """Connect to an MCP server and register its tools.

        Args:
            name: Friendly name for the server
            config: Server configuration dict with:
                - command (required): Base command to run
                - args (optional): List of arguments
                - env (optional): Environment variables dict

        Returns:
            bool: True if connection and tool registration successful

        Raises:
            ValueError: If config is not a dict or missing 'command' field
        """
        # Validate config format
        if not isinstance(config, dict):
            raise ValueError(
                "connect_mcp_server requires a config dict, not a command string. "
                "Use format: {'command': 'npx', 'args': ['-y', 'server']}"
            )

        # Check transport type - only stdio is supported
        transport_type = config.get("type", "stdio")
        if transport_type != "stdio":
            raise ValueError(
                f"GAIA MCP client only supports stdio transport at this time. "
                f"Server uses '{transport_type}' transport which is not supported."
            )

        if "command" not in config:
            raise ValueError("Config must include 'command' field")

        logger.debug(f"Connecting to MCP server '{name}'...")

        if getattr(self, "debug", False):
            print(f"[DEBUG] Config: {config}")

        try:
            client = self._mcp_manager.add_server(name, config)

            if client.is_connected():
                # Register tools
                self._register_mcp_tools(client)

                # Update system prompt with new tools
                if hasattr(self, "rebuild_system_prompt"):
                    self.rebuild_system_prompt()

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

        # Update system prompt with all newly registered tools
        if server_count > 0 and hasattr(self, "rebuild_system_prompt"):
            self.rebuild_system_prompt()

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

        MCP tool responses are wrapped in GAIA-style format with
        status/message/data/instruction fields to help the LLM
        understand how to interpret and respond to the data.

        Args:
            client: MCPClient instance
        """
        tools = client.list_tools()

        for tool in tools:
            # Convert to GAIA format
            gaia_tool = tool.to_gaia_format(client.name)

            # Create base wrapper function
            base_wrapper = client.create_tool_wrapper(tool)
            tool_name = tool.name

            # Create enhanced wrapper that formats responses in GAIA style
            def create_enhanced_wrapper(base_wrapper=base_wrapper, tool_name=tool_name):
                def enhanced_wrapper(**kwargs):
                    result = base_wrapper(**kwargs)

                    # Wrap successful dict responses in GAIA-style format
                    if isinstance(result, dict) and "error" not in result:
                        return {
                            "status": "success",
                            "message": f"Tool '{tool_name}' returned data",
                            "data": result,
                            "instruction": (
                                "Parse this JSON data and provide a human-readable summary. "
                                "Your answer must be a plain text string, not a JSON object."
                            ),
                        }
                    # Wrap error responses with error status
                    elif isinstance(result, dict) and "error" in result:
                        return {
                            "status": "error",
                            "error": result.get("error", "Unknown error"),
                            "data": result,
                        }
                    # Pass through non-dict responses (strings, etc.) unchanged
                    return result

                return enhanced_wrapper

            gaia_tool["function"] = create_enhanced_wrapper()

            # Register in global registry
            gaia_name = gaia_tool["name"]
            _TOOL_REGISTRY[gaia_name] = gaia_tool

            logger.debug(f"Registered MCP tool: {gaia_name}")

        logger.debug(f"Registered {len(tools)} tools from MCP server '{client.name}'")

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

        logger.debug(f"Unregistered {len(tools)} tools from MCP server '{client.name}'")

    def __del__(self):
        """Cleanup: disconnect from all MCP servers."""
        if hasattr(self, "_mcp_manager"):
            self._mcp_manager.disconnect_all()
