# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Custom-agent fixture that loads MCP servers via the supported runtime path."""

from __future__ import annotations

import os
from typing import Any, Dict

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.mcp import MCPClientMixin


class InstallerMCPAgent(Agent, MCPClientMixin):
    AGENT_ID = "installer-mcp"
    AGENT_NAME = "Installer MCP Fixture"
    AGENT_DESCRIPTION = "Installer harness fixture that calls a configured MCP tool"
    CONVERSATION_STARTERS = ["Add two numbers through MCP"]

    def __init__(self, mcp_config_file: str | None = None, **kwargs):
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("silent_mode", True)
        kwargs.setdefault("max_steps", 2)
        Agent.__init__(self, **kwargs)

        config_file = mcp_config_file or os.environ.get("GAIA_TEST_MCP_CONFIG")
        if not config_file:
            raise ValueError(
                "InstallerMCPAgent requires mcp_config_file or GAIA_TEST_MCP_CONFIG"
            )
        MCPClientMixin.__init__(self, auto_load_config=False, config_file=config_file)
        self._snapshot_tools()

    def _get_system_prompt(self) -> str:
        return "Use the configured MCP tools exactly as requested."

    def _register_tools(self) -> None:
        _TOOL_REGISTRY.clear()

    def process_query(self, user_input: str, **kwargs) -> Dict[str, Any]:
        del user_input, kwargs
        return self._execute_tool("mcp_dummy_add_two_numbers", {"a": 7, "b": 35})
