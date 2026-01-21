# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration tests for SIMPLE_TOOLS functionality.

Purpose: Test end-to-end behavior of agents with and without SIMPLE_TOOLS
defined. These tests verify that the planning validation flow works correctly
with real agent instances.

Tests verify:
- Agents with SIMPLE_TOOLS process queries without triggering plan requirements
- Agents without SIMPLE_TOOLS trigger planning flow for tool calls
- Tool execution works correctly for both simple and complex tools
"""

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool

# ============================================================================
# TEST AGENT IMPLEMENTATIONS
# ============================================================================


class QuickInfoAgent(Agent):
    """Test agent with SIMPLE_TOOLS for quick information retrieval."""

    SIMPLE_TOOLS = ["get_info", "get_status"]

    def __init__(self, **kwargs):
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("silent_mode", True)
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return """You provide quick system information.
        Use get_info or get_status tools to answer queries."""

    def _register_tools(self):
        @tool
        def get_info() -> dict:
            """Get quick system information."""
            return {"success": True, "info": "System is operational"}

        @tool
        def get_status() -> dict:
            """Get current system status."""
            return {"success": True, "status": "healthy"}


class ComplexAgent(Agent):
    """Test agent without SIMPLE_TOOLS - all operations require planning."""

    def __init__(self, **kwargs):
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("silent_mode", True)
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return """You perform complex multi-step operations.
        Always create a plan before executing tools."""

    def _register_tools(self):
        @tool
        def complex_operation(param: str) -> dict:
            """Perform a complex multi-step operation."""
            return {"success": True, "result": f"Processed: {param}"}


# ============================================================================
# SIMPLE_TOOLS INTEGRATION TESTS
# ============================================================================


class TestSimpleToolsAgentBehavior:
    """Test agent behavior with SIMPLE_TOOLS defined."""

    def test_agent_with_simple_tools_processes_without_plan_requirement(self):
        """Verify agent with SIMPLE_TOOLS can execute tools directly."""
        agent = QuickInfoAgent()

        # Verify SIMPLE_TOOLS is set
        assert agent.SIMPLE_TOOLS == ["get_info", "get_status"]

        # Simulate a parsed response with a simple tool call
        parsed = {"tool": "get_info", "args": {}}

        # Set agent to planning state
        agent.execution_state = agent.STATE_PLANNING
        agent.current_plan = None

        # Validate - should not require plan
        agent._validate_plan_required(parsed, step=1)

        # Should NOT have needs_plan flag
        assert "needs_plan" not in parsed

    def test_agent_without_simple_tools_requires_plan(self):
        """Verify agent without SIMPLE_TOOLS requires plan for all tools."""
        agent = ComplexAgent()

        # Verify SIMPLE_TOOLS is empty (default)
        assert agent.SIMPLE_TOOLS == []

        # Simulate a tool call
        parsed = {"tool": "complex_operation", "args": {"param": "test"}}

        # Set agent to planning state
        agent.execution_state = agent.STATE_PLANNING
        agent.current_plan = None

        # Validate - should require plan
        agent._validate_plan_required(parsed, step=1)

        # Should have needs_plan flag set
        assert parsed.get("needs_plan") is True

    def test_simple_tools_list_matches_registered_tools(self):
        """Verify SIMPLE_TOOLS contains names of actually registered tools."""
        agent = QuickInfoAgent()

        # Check that tools in SIMPLE_TOOLS are actually registered
        from gaia.agents.base.tools import _TOOL_REGISTRY

        for tool_name in agent.SIMPLE_TOOLS:
            assert (
                tool_name in _TOOL_REGISTRY
            ), f"Tool {tool_name} in SIMPLE_TOOLS but not registered"


class TestSimpleToolsExecution:
    """Test that simple tools execute correctly."""

    def test_simple_tool_can_be_called_directly(self):
        """Verify simple tools can be called through the registry."""
        _agent = QuickInfoAgent()  # Instantiation registers tools

        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Call get_info tool
        get_info_func = _TOOL_REGISTRY["get_info"]["function"]
        result = get_info_func()

        assert result["success"] is True
        assert "info" in result

    def test_simple_tool_execution_without_llm(self):
        """Verify simple tools work without LLM interaction."""
        _agent = QuickInfoAgent()  # Instantiation registers tools

        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Call both simple tools
        get_info_func = _TOOL_REGISTRY["get_info"]["function"]
        get_status_func = _TOOL_REGISTRY["get_status"]["function"]

        info_result = get_info_func()
        status_result = get_status_func()

        assert info_result["success"] is True
        assert status_result["success"] is True
        assert status_result["status"] == "healthy"


class TestPlanningStateBehavior:
    """Test planning state transitions with SIMPLE_TOOLS."""

    def test_simple_tool_in_planning_state_no_warning(self, caplog):
        """Verify no warning is logged for simple tools in planning state."""
        agent = QuickInfoAgent()
        agent.execution_state = agent.STATE_PLANNING
        agent.current_plan = None

        parsed = {"tool": "get_info", "args": {}}

        with caplog.at_level("WARNING"):
            agent._validate_plan_required(parsed, step=1)

        # No warning should be logged
        assert "No plan found" not in caplog.text

    def test_non_simple_tool_in_planning_state_warns(self, caplog):
        """Verify warning is logged for non-simple tools in planning state."""
        agent = ComplexAgent()
        agent.execution_state = agent.STATE_PLANNING
        agent.current_plan = None

        parsed = {"tool": "complex_operation", "args": {"param": "test"}}

        with caplog.at_level("WARNING"):
            agent._validate_plan_required(parsed, step=1)

        # Warning should be logged
        assert "No plan found in step 1 response" in caplog.text

    def test_executing_state_skips_validation(self):
        """Verify validation is skipped when not in planning state."""
        agent = QuickInfoAgent()
        agent.execution_state = agent.STATE_EXECUTING_PLAN  # Not planning
        agent.current_plan = None

        # Even a non-simple tool should not trigger warning
        parsed = {"tool": "unknown_tool", "args": {}}
        agent._validate_plan_required(parsed, step=1)

        # Should not require plan when not in planning state
        assert "needs_plan" not in parsed


class TestAgentInitialization:
    """Test that agents initialize correctly with SIMPLE_TOOLS."""

    def test_agent_with_simple_tools_initializes(self):
        """Verify agent with SIMPLE_TOOLS can be instantiated."""
        agent = QuickInfoAgent()
        assert agent is not None
        assert isinstance(agent, Agent)
        assert agent.SIMPLE_TOOLS == ["get_info", "get_status"]

    def test_agent_without_simple_tools_initializes(self):
        """Verify agent without SIMPLE_TOOLS can be instantiated."""
        agent = ComplexAgent()
        assert agent is not None
        assert isinstance(agent, Agent)
        assert agent.SIMPLE_TOOLS == []  # Default empty list

    def test_simple_tools_attribute_persists(self):
        """Verify SIMPLE_TOOLS attribute persists across instances."""
        agent1 = QuickInfoAgent()
        agent2 = QuickInfoAgent()

        # Both instances should have the same SIMPLE_TOOLS
        assert agent1.SIMPLE_TOOLS == agent2.SIMPLE_TOOLS
        assert agent1.SIMPLE_TOOLS == ["get_info", "get_status"]


# ============================================================================
# MIXED SCENARIO TESTS
# ============================================================================


class TestMixedToolScenarios:
    """Test scenarios with both simple and complex tools."""

    def test_agent_with_both_simple_and_complex_tools(self):
        """Verify agent can have both types of tools."""

        class MixedAgent(Agent):
            SIMPLE_TOOLS = ["quick_check"]

            def __init__(self, **kwargs):
                kwargs.setdefault("skip_lemonade", True)
                kwargs.setdefault("silent_mode", True)
                super().__init__(**kwargs)

            def _get_system_prompt(self) -> str:
                return "Agent with mixed tools"

            def _register_tools(self):
                @tool
                def quick_check() -> dict:
                    """Quick check operation."""
                    return {"success": True}

                @tool
                def complex_analysis() -> dict:
                    """Complex analysis operation."""
                    return {"success": True}

        agent = MixedAgent()
        agent.execution_state = agent.STATE_PLANNING
        agent.current_plan = None

        # Simple tool should not require plan
        parsed_simple = {"tool": "quick_check", "args": {}}
        agent._validate_plan_required(parsed_simple, step=1)
        assert "needs_plan" not in parsed_simple

        # Complex tool should require plan
        parsed_complex = {"tool": "complex_analysis", "args": {}}
        agent._validate_plan_required(parsed_complex, step=1)
        assert parsed_complex.get("needs_plan") is True
