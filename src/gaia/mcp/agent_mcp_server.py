# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Generic MCP Server for MCPAgent Subclasses
Wraps any MCPAgent and exposes it via the MCP Python SDK
"""

import io
import json
import sys
from typing import Any, Dict, Type

from mcp.server.fastmcp import FastMCP

from gaia.agents.base.mcp_agent import MCPAgent
from gaia.logger import get_logger

logger = get_logger(__name__)

# Default MCP server configuration
MCP_DEFAULT_PORT = 8080
MCP_DEFAULT_HOST = "localhost"


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
    ):
        """
        Initialize MCP server for an agent.

        Args:
            agent_class: MCPAgent subclass to wrap
            name: Display name for the server
            port: Port to listen on (default: 8080)
            host: Host to bind to (default: localhost)
            verbose: Enable verbose logging
            agent_params: Parameters to pass to agent __init__
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

        for tool_def in tools:
            tool_name = tool_def["name"]
            tool_description = tool_def.get("description", "")
            input_schema = tool_def.get("inputSchema", {})

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
                                logger.info(f"[MCP] Mapped app_dir to appPath")

                        # Map other common variations
                        if "directory" in kwargs and "appPath" not in kwargs:
                            kwargs["appPath"] = kwargs.pop("directory")
                            if verbose:
                                logger.info(f"[MCP] Mapped directory to appPath")

                        if "project_path" in kwargs and "appPath" not in kwargs:
                            kwargs["appPath"] = kwargs.pop("project_path")
                            if verbose:
                                logger.info(f"[MCP] Mapped project_path to appPath")

                        if verbose:
                            logger.info(f"[MCP TOOL] Final args to agent:")
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

    def start(self):
        """Start the MCP server with Streamable HTTP transport"""
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
        """Stop the MCP server"""
        # Note: With uvicorn, stopping is handled by KeyboardInterrupt
        # This method is kept for API compatibility
        pass

    def _print_startup_info(self):
        """Print startup banner"""
        # Fix Windows Unicode
        if sys.platform == "win32":
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

        tools = self.agent.get_mcp_tool_definitions()

        print("=" * 60)
        print(f"🚀 {self.name}")
        print("=" * 60)
        print(f"Server: http://{self.host}:{self.port}")
        print(f"Agent: {self.agent_class.__name__}")
        print(f"Tools: {len(tools)}")
        for tool in tools:
            print(f"  - {tool['name']}: {tool.get('description', 'No description')}")
        if self.verbose:
            print(f"\n🔍 Verbose Mode: ENABLED")
        print("\n📍 MCP Endpoint:")
        print(f"  http://{self.host}:{self.port}/mcp")
        print("\n  Supports:")
        print("    - HTTP POST for requests")
        print("    - SSE streaming for real-time responses")
        print("=" * 60)
        print("\nPress Ctrl+C to stop\n")
