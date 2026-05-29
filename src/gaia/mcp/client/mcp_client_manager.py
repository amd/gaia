# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Manager for multiple MCP client connections."""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeoutError
from concurrent.futures import as_completed
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

        # Store client (in-memory only).
        # Persistence to ~/.gaia/mcp_servers.json goes through the connectors
        # framework (see gaia.connectors.mcp_server.McpServerHandler.configure)
        # — this manager is a runtime registry, not a writer (#976).
        self._clients[name] = client

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

        # Removal is in-memory only — persistence (clearing the
        # mcp_servers.json entry + keyring + grants) goes through
        # gaia.connectors.mcp_server.McpServerHandler.disconnect (#976).

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

    def servers_for_agent(self, agent_id: Optional[str]) -> List[str]:
        """Return server names whose tools are visible to *agent_id*.

        Per issue #1005 (per-agent activation), an MCP server's tools are
        only visible to an agent when ``is_agent_active(server_id, agent_id)``
        is True. With ``agent_id=None`` (CLI/debug context with no agent
        identity), the unfiltered list is returned — activations only
        gate the agent-tool path.

        Activation lookups are file-backed (``activations.json``) and
        cached by the OS page cache; this method is safe to call in tight
        loops at tool-registration time.
        """
        if agent_id is None:
            return self.list_servers()
        # Local import keeps the manager free of a hard dependency on the
        # connectors package — callers that never use activations don't
        # pay the import cost.
        from gaia.connectors.activations import is_agent_active

        return [name for name in self._clients if is_agent_active(name, agent_id)]

    def tools_for_agent(self, agent_id: Optional[str]) -> Dict[str, List]:
        """Return ``{server_name: [MCPTool, ...]}`` filtered by activation.

        Returns every connected server's tool list when ``agent_id`` is
        None (CLI/debug callers) so unrelated tooling that walks the full
        registry keeps working. With a real ``agent_id`` the result is
        filtered to servers that have been explicitly activated for the
        pair via ``gaia.connectors.activations``.
        """
        names = self.servers_for_agent(agent_id)
        result: Dict[str, List] = {}
        for name in names:
            client = self._clients.get(name)
            if client is None:
                continue
            result[name] = list(client.list_tools())
        return result

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

    def reload(self) -> None:
        """Hot-reload server config without restarting GAIA.

        Disconnects all currently running servers, re-reads
        ``mcp_servers.json``, and reconnects all enabled servers.
        Called by ``McpServerHandler`` after ``configure`` / ``disconnect``
        (plan amendment A5).
        """
        logger.debug("MCPClientManager: reloading server config")
        self.disconnect_all()
        self.config._load()
        self.load_from_config()

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
        # A hard timeout prevents a hanging stdio server from blocking agent
        # construction indefinitely — the underlying readline() has no OS-level
        # timeout, so we impose one here at the futures layer.
        _CONNECT_TIMEOUT = 10.0  # seconds; generous for legitimate servers

        def _connect_one(name, server_config):
            try:
                client = MCPClient.from_config(name, server_config, debug=self.debug)
                if client.connect():
                    return name, client, None
                return name, None, client.last_error
            except Exception as e:
                return name, None, str(e)

        pool = ThreadPoolExecutor(max_workers=len(to_connect))
        futures = {
            pool.submit(_connect_one, name, cfg): name
            for name, cfg in to_connect.items()
        }
        try:
            for future in as_completed(futures, timeout=_CONNECT_TIMEOUT):
                name, client, error = future.result()
                if client is not None:
                    self._clients[name] = client
                    self._failed.pop(name, None)
                    logger.debug(f"Loaded MCP server: {name}")
                else:
                    self._failed[name] = error or "Unknown error"
                    logger.debug(f"Failed to connect to server '{name}': {error}")
        except _FutureTimeoutError:
            for future, server_name in futures.items():
                if not future.done():
                    self._failed[server_name] = (
                        f"Connection timed out after {_CONNECT_TIMEOUT:.0f}s"
                    )
                    logger.warning(
                        "MCP server '%s' did not respond within %.0fs; skipping",
                        server_name,
                        _CONNECT_TIMEOUT,
                    )
        finally:
            # wait=False: don't block on threads that are stuck in readline().
            # cancel_futures=True: stop any pending (not-yet-started) futures.
            # Already-running threads become daemon threads and exit with the process.
            pool.shutdown(wait=False, cancel_futures=True)
