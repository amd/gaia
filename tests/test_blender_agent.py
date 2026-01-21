# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for Blender Agent.

Purpose: Verify that the Blender Agent correctly uses SIMPLE_TOOLS
to allow direct execution of scene management queries without requiring
multi-step plans.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.blender.agent import BlenderAgent


class TestBlenderAgent:
    """Test suite for Blender Agent."""

    @pytest.fixture
    def mock_mcp_client(self):
        """Mock MCP client for testing without real Blender connection."""
        mock_client = MagicMock()
        # Mock the list_tools response
        mock_client.list_tools.return_value = {
            "tools": [
                {
                    "name": "create_cube",
                    "description": "Create a cube",
                    "inputSchema": {},
                },
                {
                    "name": "get_scene_objects",
                    "description": "Get scene objects",
                    "inputSchema": {},
                },
            ]
        }
        return mock_client

    @pytest.fixture
    def agent(self, mock_mcp_client):
        """Create Blender Agent instance with mocked MCP client."""
        with patch(
            "gaia.agents.blender.agent.BlenderAgent._initialize_mcp_client"
        ) as mock_init:
            mock_init.return_value = mock_mcp_client
            agent = BlenderAgent(silent_mode=True)
            return agent

    def test_agent_initialization(self, agent):
        """Test agent initializes correctly."""
        assert agent is not None
        assert agent.max_steps == 50

    def test_simple_tools_defined(self, agent):
        """Test that SIMPLE_TOOLS is properly defined."""
        expected_tools = ["clear_scene", "get_scene_info"]
        assert agent.SIMPLE_TOOLS == expected_tools

    def test_simple_tools_are_registered(self, agent):
        """Test that tools in SIMPLE_TOOLS are actually registered."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        for tool_name in agent.SIMPLE_TOOLS:
            assert (
                tool_name in _TOOL_REGISTRY
            ), f"Tool {tool_name} in SIMPLE_TOOLS but not registered"

    def test_clear_scene_tool_exists(self, agent):
        """Test that clear_scene tool is registered and callable."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        assert "clear_scene" in _TOOL_REGISTRY
        tool_func = _TOOL_REGISTRY["clear_scene"]["function"]
        assert callable(tool_func)

    def test_get_scene_info_tool_exists(self, agent):
        """Test that get_scene_info tool is registered and callable."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        assert "get_scene_info" in _TOOL_REGISTRY
        tool_func = _TOOL_REGISTRY["get_scene_info"]["function"]
        assert callable(tool_func)


class TestBlenderAgentSimpleToolsBehavior:
    """Test that Blender Agent correctly uses SIMPLE_TOOLS."""

    @pytest.fixture
    def mock_mcp_client(self):
        """Mock MCP client."""
        mock_client = MagicMock()
        mock_client.list_tools.return_value = {"tools": []}
        return mock_client

    @pytest.fixture
    def agent(self, mock_mcp_client):
        """Create agent instance."""
        with patch(
            "gaia.agents.blender.agent.BlenderAgent._initialize_mcp_client"
        ) as mock_init:
            mock_init.return_value = mock_mcp_client
            return BlenderAgent(silent_mode=True)

    def test_simple_tool_does_not_require_plan(self, agent):
        """Test that simple tools can execute without creating a plan."""
        # Set agent to planning state
        agent.execution_state = agent.STATE_PLANNING
        agent.current_plan = None

        # Simulate parsed response with a simple tool
        parsed = {"tool": "clear_scene", "args": {}}

        # Validate - should not require plan
        agent._validate_plan_required(parsed, step=1)

        # Should NOT have needs_plan flag
        assert "needs_plan" not in parsed

    def test_all_simple_tools_execute_without_plan(self, agent):
        """Test that all tools in SIMPLE_TOOLS can execute without plan."""
        agent.execution_state = agent.STATE_PLANNING
        agent.current_plan = None

        for tool_name in agent.SIMPLE_TOOLS:
            parsed = {"tool": tool_name, "args": {}}
            agent._validate_plan_required(parsed, step=1)
            assert (
                "needs_plan" not in parsed
            ), f"Tool {tool_name} should not require plan"

    def test_non_simple_tool_requires_plan(self, agent):
        """Test that tools not in SIMPLE_TOOLS would require a plan."""
        agent.execution_state = agent.STATE_PLANNING
        agent.current_plan = None

        # Tool that doesn't exist in SIMPLE_TOOLS (e.g., a complex operation)
        parsed = {"tool": "create_complex_scene", "args": {}}
        agent._validate_plan_required(parsed, step=1)

        # Should require plan
        assert parsed.get("needs_plan") is True

    def test_get_scene_info_executes_without_plan(self, agent):
        """Test that get_scene_info specifically can execute without plan."""
        agent.execution_state = agent.STATE_PLANNING
        agent.current_plan = None

        parsed = {"tool": "get_scene_info", "args": {}}
        agent._validate_plan_required(parsed, step=1)

        assert "needs_plan" not in parsed


class TestBlenderAgentToolImplementation:
    """Test tool implementation details."""

    @pytest.fixture
    def mock_mcp_client(self):
        """Mock MCP client with scene information."""
        mock_client = MagicMock()
        mock_client.list_tools.return_value = {
            "tools": [
                {
                    "name": "get_scene_objects",
                    "description": "Get all objects in scene",
                    "inputSchema": {},
                }
            ]
        }
        mock_client.call_tool.return_value = {
            "content": [
                {
                    "type": "text",
                    "text": '{"objects": ["Cube", "Camera", "Light"], "count": 3}',
                }
            ]
        }
        return mock_client

    @pytest.fixture
    def agent(self, mock_mcp_client):
        """Create agent instance."""
        with patch(
            "gaia.agents.blender.agent.BlenderAgent._initialize_mcp_client"
        ) as mock_init:
            mock_init.return_value = mock_mcp_client
            return BlenderAgent(silent_mode=True)

    def test_clear_scene_tool_callable(self, agent):
        """Test clear_scene tool can be called."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        clear_scene = _TOOL_REGISTRY["clear_scene"]["function"]

        # Mock the MCP call
        agent.mcp_client.call_tool = MagicMock(
            return_value={
                "content": [{"type": "text", "text": '{"success": true, "cleared": 3}'}]
            }
        )

        result = clear_scene()

        # Should return result from MCP
        assert "success" in str(result) or "cleared" in str(result)

    def test_get_scene_info_tool_callable(self, agent):
        """Test get_scene_info tool can be called."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        get_scene_info = _TOOL_REGISTRY["get_scene_info"]["function"]

        # Mock the MCP call
        agent.mcp_client.call_tool = MagicMock(
            return_value={
                "content": [
                    {
                        "type": "text",
                        "text": '{"objects": 3, "cameras": 1, "lights": 1}',
                    }
                ]
            }
        )

        result = get_scene_info()

        # Should return scene information
        assert "objects" in str(result) or "scene" in str(result).lower()


class TestBlenderAgentAttributes:
    """Test Blender Agent class attributes."""

    @pytest.fixture
    def mock_mcp_client(self):
        """Mock MCP client."""
        mock_client = MagicMock()
        mock_client.list_tools.return_value = {"tools": []}
        return mock_client

    @pytest.fixture
    def agent(self, mock_mcp_client):
        """Create agent instance."""
        with patch(
            "gaia.agents.blender.agent.BlenderAgent._initialize_mcp_client"
        ) as mock_init:
            mock_init.return_value = mock_mcp_client
            return BlenderAgent(silent_mode=True)

    def test_simple_tools_is_list(self, agent):
        """Test that SIMPLE_TOOLS is a list."""
        assert isinstance(agent.SIMPLE_TOOLS, list)

    def test_simple_tools_contains_expected_tools(self, agent):
        """Test that SIMPLE_TOOLS contains the expected tools."""
        assert "clear_scene" in agent.SIMPLE_TOOLS
        assert "get_scene_info" in agent.SIMPLE_TOOLS

    def test_simple_tools_count(self, agent):
        """Test that SIMPLE_TOOLS has the expected number of tools."""
        # BlenderAgent should have exactly 2 simple tools
        assert len(agent.SIMPLE_TOOLS) == 2
