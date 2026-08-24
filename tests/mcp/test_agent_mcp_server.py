#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for MCPAgent and AgentMCPServer.

Pure Python class testing - no external services (Docker/Lemonade/network)
needed. This file used to also cover a DockerAgent-backed integration suite
(real Docker CLI + real LLM orchestration), but DockerAgent was removed in
the agent-collapse (#1102-follow-on); those tests always skipped once the
gaia_agent_docker package was gone, so they were deleted rather than kept
as permanent no-ops.

Usage:
    pytest tests/mcp/test_agent_mcp_server.py -v
"""

from typing import Any, Dict, List

import pytest

from gaia.agents.base.mcp_agent import MCPAgent
from gaia.mcp.agent_mcp_server import AgentMCPServer

# ============================================================================
# TEST: MCPAgent Abstract Interface
# ============================================================================


class TestMCPAgentContract:
    """
    Test MCPAgent abstract base class interface enforcement.

    These tests verify that the abstract class pattern works correctly
    and that subclasses must implement required methods.

    No external services needed - pure Python class testing.
    """

    def test_cannot_instantiate_mcp_agent_directly(self):
        """MCPAgent is abstract and cannot be instantiated"""
        with pytest.raises(TypeError, match="abstract"):
            MCPAgent()

    def test_incomplete_subclass_missing_tool_definitions(self):
        """Subclass without get_mcp_tool_definitions() cannot be instantiated"""

        class IncompleteAgent(MCPAgent):
            """Missing get_mcp_tool_definitions()"""

            def execute_mcp_tool(
                self, tool_name: str, arguments: Dict[str, Any]
            ) -> Dict[str, Any]:
                return {}

        with pytest.raises(TypeError, match="abstract"):
            IncompleteAgent()

    def test_incomplete_subclass_missing_execute_tool(self):
        """Subclass without execute_mcp_tool() cannot be instantiated"""

        class IncompleteAgent(MCPAgent):
            """Missing execute_mcp_tool()"""

            def get_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
                return []

        with pytest.raises(TypeError, match="abstract"):
            IncompleteAgent()

    def test_minimal_valid_mcp_agent(self):
        """Valid MCPAgent subclass with both abstract methods can be instantiated"""

        class MinimalAgent(MCPAgent):
            """Minimal valid MCPAgent implementation"""

            def get_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
                return [
                    {
                        "name": "test-tool",
                        "description": "A test tool",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    }
                ]

            def execute_mcp_tool(
                self, tool_name: str, arguments: Dict[str, Any]
            ) -> Dict[str, Any]:
                if tool_name != "test-tool":
                    raise ValueError(f"Unknown tool: {tool_name}")
                return {"success": True}

            # Implement base Agent abstract methods
            def _get_system_prompt(self) -> str:
                return "Minimal test agent"

            def _create_console(self):
                from gaia.agents.base.console import SilentConsole

                return SilentConsole()

            def _register_tools(self):
                pass  # No tools to register

        # Should not raise
        agent = MinimalAgent(silent_mode=True)
        assert isinstance(agent, MCPAgent)

        # Test methods work
        tools = agent.get_mcp_tool_definitions()
        assert len(tools) == 1
        assert tools[0]["name"] == "test-tool"

        result = agent.execute_mcp_tool("test-tool", {})
        assert result["success"] is True


# ============================================================================
# TEST: AgentMCPServer Contract
# ============================================================================


class TestAgentMCPServerContract:
    """
    Test AgentMCPServer's own validation, independent of any concrete agent.

    No external services needed - pure Python class testing.
    """

    def test_server_requires_mcp_agent_subclass(self):
        """AgentMCPServer rejects non-MCPAgent classes"""

        class NotAnAgent:
            pass

        with pytest.raises(TypeError, match="must inherit from MCPAgent"):
            AgentMCPServer(agent_class=NotAnAgent, agent_params={})
