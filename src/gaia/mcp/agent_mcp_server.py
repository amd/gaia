# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Generic MCP Server for MCPAgent Subclasses
Wraps any MCPAgent and exposes it via the MCP Python SDK
"""

import inspect
import io
import json
import sys
from typing import Any, Dict, Literal, Type

from mcp.server.fastmcp import FastMCP

from gaia.agents.base.mcp_agent import MCPAgent
from gaia.logger import get_logger

logger = get_logger(__name__)

# Default MCP server configuration
MCP_DEFAULT_PORT = 8080
MCP_DEFAULT_HOST = "localhost"

# Selectable MCP transports. ``streamable-http`` is the long-standing default
# (binds a port, serves /mcp). ``stdio`` speaks JSON-RPC over the process's
# stdin/stdout — the transport desktop MCP clients (VSCode, Copilot, Claude
# Desktop) launch by default. See ``AgentMCPServer.start``.
Transport = Literal["streamable-http", "stdio"]

# JSON-Schema -> python annotation for typed tool registration. Anything not
# listed maps to ``Any`` (FastMCP then emits an open schema for that param).
_JSON_TYPE_TO_PY = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


class AgentMCPServer:
    """Generic MCP server that wraps any MCPAgent subclass using the MCP SDK"""

    def __init__(
        self,
        agent_class: Type[MCPAgent],
        name: str = None,
        port: int = None,
        host: str = None,
        verbose: bool = False,
        agent_params: Dict[str, Any] = None,
        transport: Transport = "streamable-http",
        register_typed_tools: bool = False,
    ):
        """
        Initialize MCP server for an agent.

        Args:
            agent_class: MCPAgent subclass to wrap
            name: Display name for the server
            port: Port to listen on (default: 8080). Ignored for stdio.
            host: Host to bind to (default: localhost). Ignored for stdio.
            verbose: Enable verbose logging
            agent_params: Parameters to pass to agent __init__
            transport: Default transport for ``start()`` — ``"streamable-http"``
                (binds a port) or ``"stdio"`` (JSON-RPC over stdin/stdout).
                ``start()`` accepts an override.
            register_typed_tools: When True, register each tool with an
                explicit typed signature derived from its ``inputSchema`` so a
                standard MCP client gets a precise parameter schema and
                ``structuredContent`` back. When False (default), use the
                legacy ``**kwargs`` wrapper that tolerates VSCode/Copilot's
                ``{"kwargs": ...}`` envelope. New agents that want clean
                schemas opt in; existing HTTP agents keep the legacy behavior.
        """
        # Verify agent_class is MCPAgent subclass
        if not issubclass(agent_class, MCPAgent):
            raise TypeError(f"{agent_class.__name__} must inherit from MCPAgent")

        # Initialize agent
        self.agent = agent_class(**(agent_params or {}))
        self.agent_class = agent_class

        # Server configuration
        self.name = name or f"GAIA {agent_class.__name__} MCP"
        self.port = port or MCP_DEFAULT_PORT
        self.host = host or MCP_DEFAULT_HOST
        self.verbose = verbose
        self.transport: Transport = transport
        self.register_typed_tools = register_typed_tools

        # Create FastMCP server
        server_info = self.agent.get_mcp_server_info()
        self.mcp = FastMCP(name=server_info.get("name", self.name))

        # Configure server settings (host, port)
        self.mcp.settings.host = self.host
        self.mcp.settings.port = self.port

        # Register tools dynamically from agent
        self._register_agent_tools()

    def _register_agent_tools(self):
        """Dynamically register agent tools with FastMCP"""
        tools = self.agent.get_mcp_tool_definitions()

        if self.register_typed_tools:
            for tool_def in tools:
                self._register_typed_tool(tool_def)
            return

        for tool_def in tools:
            tool_name = tool_def["name"]
            tool_description = tool_def.get("description", "")
            _input_schema = tool_def.get("inputSchema", {})

            # Create a wrapper function for this tool
            # We need to capture tool_name in the closure properly
            # NOTE: Using **kwargs means FastMCP won't validate parameters,
            # allowing us to handle both standard and VSCode's kwargs format
            def create_tool_wrapper(name: str, description: str, verbose: bool):
                async def tool_wrapper(**kwargs) -> Dict[str, Any]:
                    """Dynamically generated tool wrapper"""
                    if verbose:
                        logger.info("=" * 80)
                        logger.info(f"[MCP TOOL] Tool call: {name}")
                        logger.info(f"[MCP TOOL] Raw kwargs type: {type(kwargs)}")
                        logger.info(f"[MCP TOOL] Raw kwargs: {kwargs}")
                        try:
                            pretty_kwargs = json.dumps(kwargs, indent=2)
                            logger.info(
                                f"[MCP TOOL] Raw kwargs (pretty):\n{pretty_kwargs}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"[MCP TOOL] Could not JSON format kwargs: {e}"
                            )

                    try:
                        import time

                        start_time = time.time()

                        # Handle VSCode/Copilot kwargs wrapper format
                        # VSCode sends parameters wrapped in a "kwargs" field
                        # Can be either a dict object or stringified JSON:
                        # - {"kwargs": {"param": "value"}}  <- dict format
                        # - {"kwargs": "{\"param\": \"value\"}"} <- string format
                        if "kwargs" in kwargs:
                            kwargs_value = kwargs["kwargs"]

                            if isinstance(kwargs_value, dict):
                                # Already a dict, just unwrap it
                                if verbose:
                                    logger.info(
                                        f"[MCP] Unwrapped kwargs dict: {kwargs_value}"
                                    )
                                kwargs = kwargs_value
                            elif isinstance(kwargs_value, str):
                                # Stringified JSON, try to parse it
                                try:
                                    parsed = json.loads(kwargs_value)
                                    if verbose:
                                        logger.info(
                                            f"[MCP] Parsed stringified kwargs: {parsed}"
                                        )
                                    kwargs = parsed
                                except json.JSONDecodeError as parse_error:
                                    logger.warning(
                                        f"[MCP] Failed to parse kwargs string: {kwargs_value}, error: {parse_error}"
                                    )
                                    # Keep original kwargs if parsing fails

                        # Map common parameter variations to what agent expects
                        # VSCode may send different param names than agent expects

                        # Map VSCode's app_dir to Docker agent's appPath
                        if "app_dir" in kwargs and "appPath" not in kwargs:
                            kwargs["appPath"] = kwargs.pop("app_dir")
                            if verbose:
                                logger.info("[MCP] Mapped app_dir to appPath")

                        # Map other common variations
                        if "directory" in kwargs and "appPath" not in kwargs:
                            kwargs["appPath"] = kwargs.pop("directory")
                            if verbose:
                                logger.info("[MCP] Mapped directory to appPath")

                        if "project_path" in kwargs and "appPath" not in kwargs:
                            kwargs["appPath"] = kwargs.pop("project_path")
                            if verbose:
                                logger.info("[MCP] Mapped project_path to appPath")

                        if verbose:
                            logger.info("[MCP TOOL] Final args to agent:")
                            try:
                                pretty_final = json.dumps(kwargs, indent=2)
                                logger.info(f"{pretty_final}")
                            except Exception:
                                logger.info(f"{kwargs}")
                            logger.info("=" * 80)

                        result = self.agent.execute_mcp_tool(name, kwargs)

                        elapsed = time.time() - start_time
                        if verbose:
                            logger.info(
                                f"[MCP TOOL] Tool {name} completed in {elapsed:.2f}s"
                            )

                        return result
                    except Exception as e:
                        logger.error(f"[MCP] Error executing tool {name}: {e}")
                        if verbose:
                            import traceback

                            logger.error(f"[MCP] Traceback: {traceback.format_exc()}")
                        return {"error": str(e), "success": False}

                # Set proper metadata
                tool_wrapper.__name__ = name
                tool_wrapper.__doc__ = description

                return tool_wrapper

            # Create the tool function
            tool_func = create_tool_wrapper(tool_name, tool_description, self.verbose)

            # Register using FastMCP's decorator API
            # This ensures proper registration using the public API
            self.mcp.tool()(tool_func)

            if self.verbose:
                logger.info(f"Registered tool: {tool_name}")

    def _register_typed_tool(self, tool_def: Dict[str, Any]) -> None:
        """Register one tool with an explicit, typed signature.

        FastMCP derives a tool's JSON schema from the wrapper function's
        signature + annotations. A bare ``**kwargs`` wrapper makes FastMCP emit
        a single required ``kwargs`` property — which standard MCP clients
        cannot satisfy. So when ``register_typed_tools`` is set we synthesize a
        signature whose keyword-only parameters mirror the tool's
        ``inputSchema`` properties, giving the client a precise schema and the
        agent a clean kwargs dict. The agent's ``execute_mcp_tool`` is the sole
        executor — parity with REST comes from there, not from this glue.
        """
        name = tool_def["name"]
        description = tool_def.get("description", "")
        schema = tool_def.get("inputSchema", {}) or {}
        properties: Dict[str, Any] = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])

        parameters = []
        annotations: Dict[str, Any] = {}
        for prop_name, prop_schema in properties.items():
            py_type = _JSON_TYPE_TO_PY.get(
                (prop_schema or {}).get("type", "string"), Any
            )
            annotations[prop_name] = py_type
            if prop_name in required:
                parameters.append(
                    inspect.Parameter(
                        prop_name,
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=py_type,
                    )
                )
            else:
                parameters.append(
                    inspect.Parameter(
                        prop_name,
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=py_type,
                        default=(prop_schema or {}).get("default", None),
                    )
                )
        annotations["return"] = Dict[str, Any]

        agent = self.agent
        verbose = self.verbose

        async def tool_wrapper(**kwargs) -> Dict[str, Any]:
            # Drop keys the client omitted that we defaulted to None so the
            # agent sees only the arguments actually supplied.
            args = {k: v for k, v in kwargs.items() if v is not None}
            try:
                return agent.execute_mcp_tool(name, args)
            except Exception as e:  # noqa: BLE001 - boundary: structured error out
                logger.error(f"[MCP] Error executing tool {name}: {e}")
                if verbose:
                    import traceback

                    logger.error(f"[MCP] Traceback: {traceback.format_exc()}")
                return {"error": str(e), "success": False}

        tool_wrapper.__name__ = name
        tool_wrapper.__doc__ = description
        tool_wrapper.__signature__ = inspect.Signature(parameters)
        tool_wrapper.__annotations__ = annotations

        self.mcp.tool()(tool_wrapper)

        if self.verbose:
            logger.info(f"Registered typed tool: {name}")

    def start(self, transport: Transport = None):
        """Start the MCP server.

        Args:
            transport: Override the instance default. ``"streamable-http"``
                binds ``host:port`` and serves ``/mcp`` (HTTP POST + SSE).
                ``"stdio"`` speaks JSON-RPC over stdin/stdout — the transport
                desktop MCP clients launch by default.

        In stdio mode the startup banner is routed to **stderr**, never
        stdout: stdio framing requires stdout to carry *only* JSON-RPC bytes,
        and a single stray line corrupts the client's message parser.
        """
        transport = transport or self.transport

        if transport == "stdio":
            self._print_startup_info(stream=sys.stderr, transport="stdio")
            try:
                self.mcp.run(transport="stdio")
            except KeyboardInterrupt:
                pass
            return

        self._print_startup_info()
        try:
            # Run with streamable-http transport (industry standard)
            # This automatically serves at /mcp endpoint
            # Supports both HTTP POST and SSE streaming
            # Host and port are configured via mcp.settings
            self.mcp.run(transport="streamable-http")
        except KeyboardInterrupt:
            print("\n✅ Server stopped")

    def stop(self):
        """Stop the MCP server.

        Note: With uvicorn, stopping is handled by KeyboardInterrupt.
        This method is kept for API compatibility.
        """

    def _print_startup_info(self, stream=None, transport: Transport = None):
        """Print the startup banner.

        Args:
            stream: Where to write. Defaults to stdout (HTTP mode). Stdio mode
                passes ``sys.stderr`` so the banner never pollutes the
                JSON-RPC channel on stdout.
            transport: Which transport's banner to render. Defaults to the
                instance transport. Drives the banner *content* (HTTP endpoint
                vs. stdio note) independently of the output stream, so the
                format is correct even when ``stream`` is a test buffer.
        """
        stream = stream or sys.stdout
        transport = transport or self.transport
        # Fix Windows Unicode only on the real stdout (don't rewrap stderr).
        if sys.platform == "win32" and stream is sys.stdout:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
            stream = sys.stdout

        tools = self.agent.get_mcp_tool_definitions()
        is_stdio = transport == "stdio"

        print("=" * 60, file=stream)
        print(f"🚀 {self.name}", file=stream)
        print("=" * 60, file=stream)
        if is_stdio:
            print("Transport: stdio (JSON-RPC over stdin/stdout)", file=stream)
        else:
            print(f"Server: http://{self.host}:{self.port}", file=stream)
        print(f"Agent: {self.agent_class.__name__}", file=stream)
        print(f"Tools: {len(tools)}", file=stream)
        for tool in tools:
            print(
                f"  - {tool['name']}: {tool.get('description', 'No description')}",
                file=stream,
            )
        if self.verbose:
            print("\n🔍 Verbose Mode: ENABLED", file=stream)
        if not is_stdio:
            print("\n📍 MCP Endpoint:", file=stream)
            print(f"  http://{self.host}:{self.port}/mcp", file=stream)
            print("\n  Supports:", file=stream)
            print("    - HTTP POST for requests", file=stream)
            print("    - SSE streaming for real-time responses", file=stream)
        print("=" * 60, file=stream)
        print("\nPress Ctrl+C to stop\n", file=stream)
