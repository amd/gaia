# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OEM Experience Zone MCP Agent Example

Connects to a C# (.NET) MCP server and invokes the launch_experience_zone tool.

Run: uv run examples/mcp_oem_experience_zone_agent.py
"""

from pathlib import Path

from gaia.agents.base.agent import Agent
from gaia.mcp import MCPClientMixin

class OEMAgent(Agent, MCPClientMixin):
    def __init__(self):
        Agent.__init__(self, model_id="Qwen3-4B-GGUF")
        MCPClientMixin.__init__(self)

    def _get_system_prompt(self) -> str:
        return "You are an OEM experience zone assistant"

    def _register_tools(self) -> None:
        pass


if __name__ == "__main__":
    agent = OEMAgent()
    result = agent.process_query("Launch the Experience Zone.")
    print(result.get("result", "No result"))
