# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MCP Agent Example - Python Server (uvx)

Demonstrates connecting to a Python-based MCP server using uvx.
This is the simplest way to add MCP tools to a GAIA agent.

Run: python examples/mcp_time_server_agent.py
"""

from gaia.agents.base.agent import Agent
from gaia.mcp import MCPClientMixin


class TimeAgent(Agent, MCPClientMixin):
    """Agent with time tools from mcp-server-time."""

    def __init__(self):
        Agent.__init__(self, max_steps=10)
        MCPClientMixin.__init__(self, auto_load_config=False)

        # Connect to Python-based MCP server via uvx
        self.connect_mcp_server("time", {
            "command": "uvx",
            "args": ["mcp-server-time"]
        })

    def _get_system_prompt(self) -> str:
        return "You are a helpful assistant with access to time tools."

    def _register_tools(self) -> None:
        pass  # MCP tools auto-registered


def main():
    TimeAgent().process_query("What time is it in New York?")

if __name__ == "__main__":
    main()
