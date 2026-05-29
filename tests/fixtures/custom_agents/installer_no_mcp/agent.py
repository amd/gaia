# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Custom-agent fixture that exercises a code-only path with no MCP traffic."""

from __future__ import annotations

from typing import Any, Dict

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


class InstallerNoMCPAgent(Agent):
    AGENT_ID = "installer-no-mcp"
    AGENT_NAME = "Installer No MCP Fixture"
    AGENT_DESCRIPTION = "Installer harness fixture that uses only local code"
    CONVERSATION_STARTERS = ["Run the local calculation"]

    def __init__(self, **kwargs):
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("silent_mode", True)
        kwargs.setdefault("max_steps", 2)
        super().__init__(**kwargs)
        self._snapshot_tools()

    def _get_system_prompt(self) -> str:
        return "Use only local tools."

    def _register_tools(self) -> None:
        _TOOL_REGISTRY.clear()

        @tool
        def local_double(value: int) -> Dict[str, int]:
            """Double an integer locally."""
            return {"doubled": value * 2}

    def process_query(self, user_input: str, **kwargs) -> Dict[str, Any]:
        del user_input, kwargs
        return self._execute_tool("local_double", {"value": 21})
