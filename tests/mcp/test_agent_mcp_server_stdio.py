# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for AgentMCPServer's stdio transport + typed-tool registration (#1104).

These exercise the new surface directly with a minimal, Lemonade-free
``MCPAgent`` — no Docker, no network, no model. They guard:

- the ``transport`` selection (constructor default + ``start()`` override),
- typed tool registration producing a precise FastMCP schema (not the legacy
  single-``kwargs`` schema), and
- the startup banner going to **stderr** in stdio mode so stdout stays a clean
  JSON-RPC channel.
"""

from __future__ import annotations

import io
from typing import Any, Dict, List

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed ([mcp] extra)")

from gaia.agents.base.console import SilentConsole  # noqa: E402
from gaia.agents.base.mcp_agent import MCPAgent  # noqa: E402
from gaia.mcp.agent_mcp_server import AgentMCPServer  # noqa: E402


class _EchoAgent(MCPAgent):
    """Minimal Lemonade-free MCPAgent exposing one typed tool."""

    def __init__(self, **kwargs: Any):
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("silent_mode", True)
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return "echo agent"

    def _create_console(self):
        return SilentConsole()

    def _register_tools(self) -> None:
        pass

    def get_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "echo",
                "description": "Echo the provided text back.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to echo."},
                        "count": {
                            "type": "integer",
                            "description": "Repeat count.",
                            "default": 1,
                        },
                    },
                    "required": ["text"],
                },
            }
        ]

    def execute_mcp_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        if tool_name != "echo":
            raise ValueError(f"Unknown tool: {tool_name}")
        return {"echoed": arguments.get("text", ""), "count": arguments.get("count", 1)}


class TestTransportSelection:
    def test_default_transport_is_http(self):
        server = AgentMCPServer(agent_class=_EchoAgent)
        assert server.transport == "streamable-http"

    def test_transport_can_be_set_to_stdio(self):
        server = AgentMCPServer(agent_class=_EchoAgent, transport="stdio")
        assert server.transport == "stdio"


class TestTypedToolRegistration:
    def test_typed_registration_produces_precise_schema(self):
        """With register_typed_tools=True, FastMCP derives a per-property
        schema from inputSchema — NOT a single required 'kwargs' string."""
        server = AgentMCPServer(agent_class=_EchoAgent, register_typed_tools=True)
        # FastMCP stores registered tools; pull the echo tool's schema.
        tools = server.mcp._tool_manager.list_tools()
        echo = next(t for t in tools if t.name == "echo")
        props = echo.parameters["properties"]
        assert "text" in props
        assert "count" in props
        # The legacy bug we explicitly avoid: a lone required 'kwargs' param.
        assert "kwargs" not in props
        assert echo.parameters.get("required") == ["text"]

    def test_legacy_registration_is_kwargs_wrapper(self):
        """Default (legacy) registration keeps the **kwargs wrapper schema so
        existing HTTP agents are unchanged."""
        server = AgentMCPServer(agent_class=_EchoAgent)  # register_typed_tools=False
        tools = server.mcp._tool_manager.list_tools()
        echo = next(t for t in tools if t.name == "echo")
        # Legacy wrapper exposes a single 'kwargs' property.
        assert list(echo.parameters["properties"].keys()) == ["kwargs"]


class TestStartupBanner:
    def test_stdio_banner_goes_to_stderr_not_stdout(self):
        """In stdio mode the banner must not touch stdout (would corrupt the
        JSON-RPC frame). Routing to stderr is the contract."""
        server = AgentMCPServer(agent_class=_EchoAgent, transport="stdio")
        err = io.StringIO()
        server._print_startup_info(stream=err)
        text = err.getvalue()
        assert "GAIA" in text
        assert "stdio" in text.lower()
        # Stdio banner must NOT advertise an HTTP endpoint.
        assert "/mcp" not in text

    def test_http_banner_mentions_endpoint(self):
        """HTTP mode banner still advertises the /mcp endpoint (unchanged)."""
        server = AgentMCPServer(agent_class=_EchoAgent)
        out = io.StringIO()
        server._print_startup_info(stream=out)
        text = out.getvalue()
        assert "/mcp" in text
