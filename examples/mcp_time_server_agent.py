# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
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

    def __init__(self, **kwargs):
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("max_steps", 10)
        kwargs.setdefault("silent_mode", True)

        Agent.__init__(self, **kwargs)
        MCPClientMixin.__init__(self)

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
    agent = TimeAgent()

    # Show available tools
    client = agent.get_mcp_client("time")
    tools = client.list_tools()
    print(f"Available tools: {[t.name for t in tools]}")

    # Demo: Get current time
    result = agent.process_query("What time is it in New York?")
    print(f"\nAgent: {result.get('result', 'No response')}")


if __name__ == "__main__":
    main()
