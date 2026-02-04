# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
MCP Agent Example - Config-Based Server Loading

Run: python examples/mcp_config_based_agent.py
"""

from pathlib import Path

from gaia.agents.base.agent import Agent
from gaia.mcp import MCPClientMixin
from gaia.mcp.client.config import MCPConfig


class MCPAgent(Agent, MCPClientMixin):
    """Agent that loads MCP servers from config file."""

    def __init__(self, **kwargs):
        kwargs.setdefault('skip_lemonade', True)
        kwargs.setdefault('max_steps', 20)
        kwargs.setdefault('silent_mode', True)  # Suppress console output

        Agent.__init__(self, **kwargs)
        MCPClientMixin.__init__(self)

        # Load config from same directory as script
        config_path = Path(__file__).parent / "mcp_servers.json"
        if config_path.exists():
            self._mcp_manager.config = MCPConfig(str(config_path))

        # Load MCP servers (system prompt auto-updated with MCP tools)
        self.load_mcp_servers_from_config()

    def _get_system_prompt(self) -> str:
        return """You are a helpful assistant with MCP tools.
Use tools prefixed with 'mcp_<server>_<tool>' when needed."""

    def _register_tools(self) -> None:
        pass  # MCP tools auto-registered


def main():
    agent = MCPAgent()

    servers = agent.list_mcp_servers()
    print(f"Connected: {', '.join(servers)}")

    # Demo: Use the time tool
    result = agent.process_query("What time is it in Tokyo?")
    print(f"\nAgent: {result.get('result', 'No response')}")


if __name__ == "__main__":
    main()
