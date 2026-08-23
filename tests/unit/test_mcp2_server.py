# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression tests for the mcp 2.x MCPServer API (#2940)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

try:
    from mcp.server import MCPServer
except ImportError:
    pytest.skip("mcp 2.x is required for these tests", allow_module_level=True)

from gaia import cli
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.mcp_agent import MCPAgent
from gaia.mcp.agent_mcp_server import AgentMCPServer
from gaia.mcp.servers import agent_ui_mcp, tui_mcp
from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp
from gaia.mcp.servers.tui_mcp import create_tui_mcp


class _EchoAgent(MCPAgent):
    """Minimal agent that does not need a model or external service."""

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
                "description": "Echo text.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ]

    def execute_mcp_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {"tool": tool_name, "arguments": arguments}


def test_mcp2_server_import_and_construction():
    """All GAIA MCP constructors use the mcp 2.x public MCPServer class."""
    server = AgentMCPServer(agent_class=_EchoAgent)
    assert isinstance(server.mcp, MCPServer)

    with patch("requests.get") as get:
        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = {"mcp_memory_enabled": False}
        assert isinstance(create_agent_ui_mcp(), MCPServer)

    assert isinstance(create_tui_mcp(), MCPServer)


def test_agent_server_http_passes_host_and_port_to_run():
    """mcp 2.x receives HTTP bind settings as run kwargs, not server settings."""
    server = AgentMCPServer(agent_class=_EchoAgent, host="0.0.0.0", port=9876)
    with (
        patch.object(server, "_print_startup_info"),
        patch.object(server.mcp, "run") as run,
    ):
        server.start()

    run.assert_called_once_with(transport="streamable-http", host="0.0.0.0", port=9876)


def test_agent_ui_main_http_passes_host_and_port_to_run():
    server = MCPServer(name="test")
    with (
        patch.object(agent_ui_mcp, "create_agent_ui_mcp", return_value=server),
        patch.object(server, "run") as run,
        patch.object(
            agent_ui_mcp.sys,
            "argv",
            ["agent_ui_mcp", "--host", "0.0.0.0", "--port", "9877"],
        ),
    ):
        agent_ui_mcp.main()

    run.assert_called_once_with(transport="streamable-http", host="0.0.0.0", port=9877)


def test_tui_main_http_passes_host_and_port_to_run():
    server = MCPServer(name="test")
    with (
        patch.object(tui_mcp, "create_tui_mcp", return_value=server),
        patch.object(server, "run") as run,
        patch.object(
            tui_mcp.sys,
            "argv",
            ["tui_mcp", "--host", "0.0.0.0", "--port", "9878"],
        ),
    ):
        tui_mcp.main()

    run.assert_called_once_with(transport="streamable-http", host="0.0.0.0", port=9878)


def test_cli_agent_ui_http_passes_host_and_port_to_run():
    server = MCPServer(name="test")
    args = SimpleNamespace(
        backend="http://backend", stdio=False, host="0.0.0.0", port=9879
    )
    with (
        patch(
            "gaia.mcp.servers.agent_ui_mcp.create_agent_ui_mcp",
            return_value=server,
        ),
        patch.object(server, "run") as run,
    ):
        cli.handle_mcp_serve(args)

    run.assert_called_once_with(transport="streamable-http", host="0.0.0.0", port=9879)


def test_cli_tui_http_passes_host_and_port_to_run():
    server = MCPServer(name="test")
    args = SimpleNamespace(stdio=False, host="0.0.0.0", port=9880)
    with (
        patch(
            "gaia.mcp.servers.tui_mcp.create_tui_mcp",
            return_value=server,
        ),
        patch.object(server, "run") as run,
    ):
        cli.handle_mcp_tui(args)

    run.assert_called_once_with(transport="streamable-http", host="0.0.0.0", port=9880)
