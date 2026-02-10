# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Manager for multiple MCP client connections."""

from typing import Dict, List, Optional

from gaia.logger import get_logger

from .config import MCPConfig
from .mcp_client import MCPClient

logger = get_logger(__name__)


class MCPClientManager:
    """Manages multiple MCP client connections.

    Handles configuration loading/saving, connection management, and
    routing tool calls to the appropriate server.

    Args:
        config: Optional MCPConfig instance (creates default if not provided)
        debug: Enable debug logging
    """

    def __init__(self, config: Optional[MCPConfig] = None, debug: bool = False):
        self.config = config or MCPConfig()
        self.debug = debug
        self._clients: Dict[str, MCPClient] = {}

    def add_server(self, name: str, config: Dict) -> MCPClient:
        """Add and connect to an MCP server.

        Args:
            name: Friendly name for the server
            config: Server configuration dict with:
                - command (required): Base command to run
                - args (optional): List of arguments
                - env (optional): Environment variables dict
                - type (optional): Transport type, defaults to "stdio"

        Returns:
            MCPClient: Connected client instance

        Raises:
            ValueError: If server with this name already exists or config is invalid
            RuntimeError: If connection fails
        """
        # Validate config is a dict, not a string
        if not isinstance(config, dict):
            raise ValueError(
                "add_server requires a config dict, not a command string. "
                "Use format: {'command': 'npx', 'args': ['-y', 'server']}"
            )

        # Check transport type - only stdio is supported
        transport_type = config.get("type", "stdio")
        if transport_type != "stdio":
            raise ValueError(
                f"GAIA MCP client only supports stdio transport at this time. "
                f"Server '{name}' uses '{transport_type}' transport which is not supported."
            )

        if name in self._clients:
            raise ValueError(f"MCP server '{name}' already exists")

        logger.debug(f"Adding MCP server: {name}")

        # Create client from config
        client = MCPClient.from_config(name, config, debug=self.debug)

        # Connect
        if not client.connect():
            detail = f": {client.last_error}" if client.last_error else ""
            raise RuntimeError(f"Failed to connect to MCP server '{name}'{detail}")

        # Store client
        self._clients[name] = client

        # Save to config
        self.config.add_server(name, config)

        logger.debug(f"Successfully added MCP server: {name}")
        return client

    def remove_server(self, name: str) -> None:
        """Remove and disconnect from an MCP server.

        Args:
            name: Name of the server to remove
        """
        if name not in self._clients:
            logger.warning(f"MCP server '{name}' not found")
            return

        logger.debug(f"Removing MCP server: {name}")

        client = self._clients[name]
        client.disconnect()
        del self._clients[name]

        self.config.remove_server(name)

        logger.debug(f"Successfully removed MCP server: {name}")

    def get_client(self, name: str) -> Optional[MCPClient]:
        """Get a client by name.

        Args:
            name: Server name

        Returns:
            MCPClient or None: Client instance if exists
        """
        return self._clients.get(name)

    def list_servers(self) -> List[str]:
        """List all registered server names.

        Returns:
            list[str]: Server names
        """
        return list(self._clients.keys())

    def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        logger.debug("Disconnecting from all MCP servers")

        for name, client in list(self._clients.items()):
            try:
                client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting from '{name}': {e}")

        self._clients.clear()

    def load_from_config(self) -> None:
        """Load and connect to all servers from configuration.

        Skips servers that fail to connect but logs errors.
        Only stdio transport is supported - other types are skipped with a warning.
        """
        servers = self.config.get_servers()

        if not servers:
            logger.debug("No MCP servers configured")
            return

        logger.debug(f"Loading {len(servers)} MCP servers from configuration")

        for name, server_config in servers.items():
            if name in self._clients:
                logger.debug(f"Skipping already-connected server: {name}")
                continue

            try:
                # Check transport type - only stdio is supported
                transport_type = server_config.get("type", "stdio")
                if transport_type != "stdio":
                    logger.warning(
                        f"Skipping server '{name}': GAIA MCP client only supports stdio "
                        f"transport at this time (found '{transport_type}')"
                    )
                    continue

                if "command" not in server_config:
                    logger.warning(f"No command specified for server: {name}")
                    continue

                client = MCPClient.from_config(name, server_config, debug=self.debug)
                if client.connect():
                    self._clients[name] = client
                    logger.debug(f"Loaded MCP server: {name}")
                else:
                    detail = f": {client.last_error}" if client.last_error else ""
                    logger.warning(
                        f"Failed to connect to configured server: {name}{detail}"
                    )

            except Exception as e:
                logger.error(f"Error loading server '{name}': {e}")
