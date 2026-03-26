# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Manager for multiple MCP client connections."""

from concurrent.futures import ThreadPoolExecutor, as_completed
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
        self._failed: Dict[str, str] = {}  # name -> last connection error

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
        self._failed.pop(name, None)

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

    def get_status_report(self) -> List[Dict]:
        """Return runtime connection status for all known servers.

        Returns:
            List of dicts with keys: name, connected, tool_count, error
        """
        report = []
        for name, client in self._clients.items():
            tools = client.list_tools()  # uses cached list — no network call
            report.append(
                {
                    "name": name,
                    "connected": True,
                    "tool_count": len(tools),
                    "error": None,
                }
            )
        for name, error in self._failed.items():
            report.append(
                {"name": name, "connected": False, "tool_count": 0, "error": error}
            )
        return report

    def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        logger.debug("Disconnecting from all MCP servers")

        for name, client in list(self._clients.items()):
            try:
                client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting from '{name}': {e}")

        self._clients.clear()
        self._failed.clear()

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

        # Filter to servers that are eligible to connect
        to_connect = {}
        for name, server_config in servers.items():
            if name in self._clients:
                logger.debug(f"Skipping already-connected server: {name}")
                continue
            if server_config.get("disabled", False):
                logger.debug(f"Skipping disabled server: {name}")
                continue
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
            to_connect[name] = server_config

        if not to_connect:
            return

        # Connect to all servers in parallel so slow/failing servers don't
        # block each other (each connection can take 1-3s on failure).
        def _connect_one(name, server_config):
            try:
                client = MCPClient.from_config(name, server_config, debug=self.debug)
                if client.connect():
                    return name, client, None
                return name, None, client.last_error
            except Exception as e:
                return name, None, str(e)

        with ThreadPoolExecutor(max_workers=len(to_connect)) as pool:
            futures = {
                pool.submit(_connect_one, name, cfg): name
                for name, cfg in to_connect.items()
            }
            for future in as_completed(futures):
                name, client, error = future.result()
                if client is not None:
                    self._clients[name] = client
                    self._failed.pop(name, None)
                    logger.debug(f"Loaded MCP server: {name}")
                else:
                    self._failed[name] = error or "Unknown error"
                    logger.debug(f"Failed to connect to server '{name}': {error}")
