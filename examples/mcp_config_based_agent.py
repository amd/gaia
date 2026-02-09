# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
MCP Agent Example - Config-Based Server Loading

Run: uv run examples/mcp_config_based_agent.py
"""

from pathlib import Path

from gaia.agents.base.agent import Agent
from gaia.mcp import MCPClientMixin

# Config file next to this script
CONFIG_FILE = str(Path(__file__).parent / "mcp_servers.json")


class MCPAgent(Agent, MCPClientMixin):
    """Agent that loads MCP servers from config file."""

    def __init__(self):
        Agent.__init__(self, max_steps=20)
        MCPClientMixin.__init__(self, debug=True, config_file=CONFIG_FILE)

    def _get_system_prompt(self) -> str:
        return "You are a helpful assistant. Use the available tools when needed."

    def _register_tools(self) -> None:
        pass  # MCP tools auto-registered


if __name__ == "__main__":
    agent = MCPAgent()

    servers = agent.list_mcp_servers()
    print(f"Connected to MCP servers: {', '.join(servers)}")
    print("Try: 'What time is it in Tokyo?' | Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if user_input:
            result = agent.process_query(user_input)
            if result.get("result"):
                print(f"\nAgent: {result['result']}\n")
