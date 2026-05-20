# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MCP Client for interacting with MCP servers."""

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from gaia.logger import get_logger

from .transports.base import MCPTransport
from .transports.stdio import StdioTransport

logger = get_logger(__name__)


_MCP_TOKEN_RE = re.compile(r"(?:^mcp_|_mcp_|_mcp$|^mcp$)", re.IGNORECASE)
_logged_sanitisations: set = set()


def sanitise_server_name(raw: str) -> str:
    """Strip redundant ``mcp`` tokens + normalise to snake_case (idempotent)."""
    baseline = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    n = baseline
    while True:
        new = _MCP_TOKEN_RE.sub("_", n).strip("_")
        if new == n:
            break
        n = new
    if not n:
        n = "server"
    # Only warn when a real ``mcp``-token was stripped — pure case/separator
    # normalisation (``Filesystem`` -> ``filesystem``) shouldn't nag the user.
    if n != baseline and raw not in _logged_sanitisations:
        logger.warning(
            "MCP server name %r normalised to %r for tool-name prefix. "
            "Consider renaming the server in mcp_servers.json.",
            raw,
            n,
        )
        _logged_sanitisations.add(raw)
    return n


def _resolve_keyring_refs(env: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """
    Resolve ``{"$keyring": "<service>:<connector_id>:<env_key>"}`` references in *env*.

    The keyring lookup uses ``service`` and the two-part username
    ``<connector_id>:<env_key>`` (first ``:`` splits service from username, so
    ``ref.partition(":")`` gives the correct ``service`` / ``username`` pair
    that ``McpServerHandler.configure`` writes).

    Security invariants:

    * ``service`` MUST equal ``gaia.connections``. Any other value would let a
      corrupted ``mcp_servers.json`` (e.g. a malicious entry pointing at
      ``"Chrome Safe Storage:Chrome:..."``) exfiltrate other applications'
      keyring entries into the spawned MCP subprocess env. We refuse to
      spawn instead.
    * Missing keyring entries fail closed (raise ``ConnectorsError``) — the
      MCP server is never spawned with empty env in place of a secret.

    Plain string values pass through unchanged.
    """
    if not env:
        return {}

    # Imports (keyring + connectors) are deferred to the branch that
    # actually needs them. ``keyring`` is an optional dependency — if
    # the env contains no ``$keyring`` references, plain values must
    # pass through without forcing a keyring install.
    keyring = None  # populated on first $keyring reference
    SERVICE_NAME = None
    ConnectorsError: type = Exception  # type: ignore[assignment]

    resolved: Dict[str, str] = {}
    missing: list[str] = []
    for key, value in env.items():
        if isinstance(value, dict) and "$keyring" in value:
            if keyring is None:
                # pylint: disable=import-outside-toplevel
                import keyring as _keyring  # noqa: I001

                from gaia.connectors.errors import ConnectorsError as _ConnectorsError
                from gaia.connectors.store import SERVICE_NAME as _SERVICE_NAME

                keyring = _keyring
                ConnectorsError = _ConnectorsError
                SERVICE_NAME = _SERVICE_NAME
            ref = value["$keyring"]
            service, _, username = ref.partition(":")
            if service != SERVICE_NAME:
                raise ConnectorsError(
                    f"MCPClient: refusing to spawn — $keyring reference "
                    f"{ref!r} points outside the gaia namespace "
                    f"(service={service!r}). Only {SERVICE_NAME!r} is allowed."
                )
            password = keyring.get_password(service, username)
            if password is None:
                missing.append(ref)
            else:
                resolved[key] = password
        else:
            resolved[key] = str(value)
    if missing:
        # Diagnostic parse — split each ref into (service, connector_id,
        # env_key) for an actionable error pointing at `gaia connectors
        # configure <id>`. Keep the raw refs in the message too for grep.
        details = []
        for ref in missing:
            parts = ref.split(":", 2)
            if len(parts) == 3:
                _, connector_id, env_key = parts
                details.append(
                    f"connector_id={connector_id!r} env_key={env_key!r} ref={ref!r}"
                )
            else:
                details.append(f"ref={ref!r}")
        joined = "; ".join(details)
        # Pull a connector_id from the first parseable ref for the recovery
        # hint; if none parses, fall back to a generic message.
        recovery_hint = ""
        for ref in missing:
            parts = ref.split(":", 2)
            if len(parts) == 3:
                recovery_hint = (
                    f" Run `gaia connectors configure {parts[1]}` to "
                    "re-supply the secret."
                )
                break
        raise ConnectorsError(
            f"MCPClient: refusing to spawn — missing keyring entries: "
            f"{joined}.{recovery_hint}"
        )
    return resolved


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

    def to_gaia_format(
        self, prefix: str, raw_server_name: str = None
    ) -> Dict[str, Any]:
        """Convert MCP tool schema to GAIA _TOOL_REGISTRY format.

        Args:
            prefix: Sanitised server-name prefix (from ``MCPClient.prefix``).
                Used to build the registry name and description tag.
            raw_server_name: Original server name from ``mcp_servers.json``,
                stored in ``_mcp_server`` for routing/lookup. Defaults to
                ``prefix`` for backwards compatibility with tests that pass
                a single sanitised value.

        Returns:
            dict: GAIA tool registry entry (without function field)
        """
        properties = self.input_schema.get("properties", {})
        required_list = self.input_schema.get("required", [])
        if raw_server_name is None:
            raw_server_name = prefix

        # Convert MCP parameters to GAIA format
        gaia_params = {}
        for param_name, param_schema in properties.items():
            gaia_params[param_name] = {
                "type": param_schema.get("type", "string"),
                "required": param_name in required_list,
                "description": param_schema.get("description", ""),
            }

        return {
            "name": f"mcp_{prefix}_{self.name}",
            "display_name": f"{self.name} ({prefix})",
            "description": f"[MCP:{prefix}] {self.description}",
            "parameters": gaia_params,
            "atomic": True,
            # Metadata for debugging/routing — raw name preserves the
            # mcp_servers.json key so routing back to a client still
            # works after sanitisation changes the prefix.
            "_mcp_server": raw_server_name,
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
        # Sanitised prefix used for tool-name namespacing. Computed once
        # here so every consumer (registration, unregistration, prompt
        # fragments) sees the same value. See ``sanitise_server_name``
        # docstring for the rationale.
        self.prefix = sanitise_server_name(name)
        self.transport = transport
        self.debug = debug
        self.server_info: Dict[str, Any] = {}
        self._tools: Optional[List[MCPTool]] = None
        self.last_error: Optional[str] = None

    @classmethod
    def from_command(
        cls, name: str, command: str, timeout: int = 30, debug: bool = False
    ) -> "MCPClient":
        """Create an MCP client using stdio transport (legacy method).

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

    @classmethod
    def from_config(
        cls, name: str, config: Dict[str, Any], timeout: int = 30, debug: bool = False
    ) -> "MCPClient":
        """Create an MCP client from a config dict (Anthropic format).

        Args:
            name: Friendly name for this server
            config: Server configuration dict with:
                - command (required): Base command to run
                - args (optional): List of arguments
                - env (optional): Environment variables dict
            timeout: Request timeout in seconds
            debug: Enable debug logging

        Returns:
            MCPClient: Configured client instance

        Raises:
            ValueError: If config is missing required 'command' field
        """
        if "command" not in config:
            raise ValueError("Config must include 'command' field")

        # Resolve any $keyring references before spawning; raises RuntimeError
        # if a reference is dangling (fail-closed per plan amendment A5b).
        resolved_env = _resolve_keyring_refs(config.get("env"))

        transport = StdioTransport(
            command=config["command"],
            args=config.get("args"),
            env=resolved_env or None,
            timeout=timeout,
            debug=debug,
        )
        return cls(name, transport, debug=debug)

    def connect(self) -> bool:
        """Connect to the MCP server and initialize.

        On failure, stores the error message in ``self.last_error``.

        Returns:
            bool: True if connection and initialization successful
        """
        self.last_error = None
        logger.debug(f"Connecting to MCP server '{self.name}'...")

        try:
            if not self.transport.connect():
                self.last_error = (
                    f"Failed to establish transport connection to '{self.name}'"
                )
                logger.debug(self.last_error)
                return False
        except Exception as e:
            self.last_error = f"Transport error for '{self.name}': {e}"
            logger.debug(self.last_error)
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
                self.last_error = (
                    f"Initialization failed: {error.get('message', 'Unknown error')}"
                )
                logger.debug(f"MCP server '{self.name}': {self.last_error}")
                return False

            result = response.get("result", {})
            self.server_info = result.get("serverInfo", {})

            logger.debug(
                f"Connected to '{self.name}' - {self.server_info.get('name', 'Unknown')}"
            )
            return True

        except Exception as e:
            self.last_error = f"Error during initialization: {e}"
            logger.debug(f"MCP server '{self.name}': {self.last_error}")
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
