# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
MCP Agent Example - Config-Based Server Loading

Run: python examples/mcp_config_based_agent.py
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
        return """You are a helpful assistant with MCP tools.
Use tools prefixed with 'mcp_<server>_<tool>' when needed."""

    def _register_tools(self) -> None:
        pass  # MCP tools auto-registered


def main():
    MCPAgent().process_query("What time is it in Tokyo?")

if __name__ == "__main__":
    main()
