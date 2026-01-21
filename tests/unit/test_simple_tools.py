# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for SIMPLE_TOOLS functionality in the base Agent class.

Purpose: Validate that agents can define simple tools that execute directly
without requiring a multi-step plan. This tests the _validate_plan_required()
method in the base Agent class.

Tests verify:
- Tools in SIMPLE_TOOLS execute without plan warnings
- Tools not in SIMPLE_TOOLS trigger plan requirement
- Planning validation is skipped when a plan already exists
- Planning validation is skipped when not in STATE_PLANNING
"""

import pytest

from gaia.agents.base.agent import Agent

# ============================================================================
# TEST AGENT FIXTURES
# ============================================================================


class SimpleToolAgent(Agent):
    """Test agent with SIMPLE_TOOLS defined."""

    SIMPLE_TOOLS = ["quick_info", "get_status"]

    def _get_system_prompt(self) -> str:
        return "Test agent with simple tools"

    def _register_tools(self):
        pass


class NoSimpleToolAgent(Agent):
    """Test agent without SIMPLE_TOOLS defined."""

    def _get_system_prompt(self) -> str:
        return "Test agent without simple tools"

    def _register_tools(self):
        pass


@pytest.fixture
def simple_tool_agent():
    """Agent with SIMPLE_TOOLS defined."""
    agent = SimpleToolAgent(silent_mode=True, skip_lemonade=True)
    # Set execution state to planning
    agent.execution_state = agent.STATE_PLANNING
    agent.current_plan = None
    return agent


@pytest.fixture
def no_simple_tool_agent():
    """Agent without SIMPLE_TOOLS defined."""
    agent = NoSimpleToolAgent(silent_mode=True, skip_lemonade=True)
    # Set execution state to planning
    agent.execution_state = agent.STATE_PLANNING
    agent.current_plan = None
    return agent


# ============================================================================
# SIMPLE_TOOLS VALIDATION TESTS
# ============================================================================


class TestSimpleToolsAllowsDirectExecution:
    """Test that tools in SIMPLE_TOOLS execute without plan warnings."""

    def test_simple_tool_executes_without_warning(self, simple_tool_agent):
        """Verify tool in SIMPLE_TOOLS doesn't trigger plan requirement."""
        # Parsed response with a tool call that's in SIMPLE_TOOLS
        parsed = {"tool": "quick_info", "args": {}}

        # Should not set needs_plan flag
        simple_tool_agent._validate_plan_required(parsed, step=1)

        assert "needs_plan" not in parsed

    def test_simple_tool_console_no_warning(self, simple_tool_agent, caplog):
        """Verify no warning printed to console for simple tools."""
        parsed = {"tool": "get_status", "args": {}}

        with caplog.at_level("WARNING"):
            simple_tool_agent._validate_plan_required(parsed, step=1)

        # No warning messages should be logged
        assert "No plan found" not in caplog.text

    def test_multiple_simple_tools_allowed(self, simple_tool_agent):
        """Verify all tools in SIMPLE_TOOLS list are allowed."""
        for tool_name in simple_tool_agent.SIMPLE_TOOLS:
            parsed = {"tool": tool_name, "args": {}}
            simple_tool_agent._validate_plan_required(parsed, step=1)
            assert "needs_plan" not in parsed, f"Tool {tool_name} should be allowed"


class TestNonSimpleToolsRequirePlan:
    """Test that tools NOT in SIMPLE_TOOLS trigger plan requirement."""

    def test_non_simple_tool_triggers_plan_requirement(self, simple_tool_agent):
        """Verify tool not in SIMPLE_TOOLS sets needs_plan flag."""
        # Tool call for a tool NOT in SIMPLE_TOOLS
        parsed = {"tool": "complex_operation", "args": {}}

        simple_tool_agent._validate_plan_required(parsed, step=1)

        # Should set needs_plan flag
        assert parsed.get("needs_plan") is True

    def test_non_simple_tool_prints_warning(self, simple_tool_agent, caplog):
        """Verify warning is logged for non-simple tools."""
        parsed = {"tool": "complex_operation", "args": {}}

        with caplog.at_level("WARNING"):
            simple_tool_agent._validate_plan_required(parsed, step=1)

        # Warning should be logged
        assert "No plan found in step 1 response" in caplog.text

    def test_agent_without_simple_tools_requires_plan(self, no_simple_tool_agent):
        """Verify agent without SIMPLE_TOOLS defined requires plan for all tools."""
        # Agent has empty SIMPLE_TOOLS list by default
        assert no_simple_tool_agent.SIMPLE_TOOLS == []

        parsed = {"tool": "any_tool", "args": {}}
        no_simple_tool_agent._validate_plan_required(parsed, step=1)

        # Should require plan
        assert parsed.get("needs_plan") is True


class TestPlanValidationSkipConditions:
    """Test conditions where plan validation is skipped."""

    def test_skip_validation_when_plan_exists(self, simple_tool_agent):
        """Verify validation is skipped when current_plan is set."""
        # Set a current plan
        simple_tool_agent.current_plan = {"steps": ["step1", "step2"]}

        # Tool that would normally require a plan
        parsed = {"tool": "complex_operation", "args": {}}

        simple_tool_agent._validate_plan_required(parsed, step=1)

        # Should NOT set needs_plan because plan already exists
        assert "needs_plan" not in parsed

    def test_skip_validation_when_not_in_planning_state(self, simple_tool_agent):
        """Verify validation is skipped when not in STATE_PLANNING."""
        # Set execution state to executing
        simple_tool_agent.execution_state = simple_tool_agent.STATE_EXECUTING_PLAN
        simple_tool_agent.current_plan = None

        parsed = {"tool": "complex_operation", "args": {}}

        simple_tool_agent._validate_plan_required(parsed, step=1)

        # Should NOT set needs_plan when not in planning state
        assert "needs_plan" not in parsed

    def test_skip_validation_after_first_step(self, simple_tool_agent):
        """Verify validation only applies to step 1."""
        # Reset to planning state with no plan
        simple_tool_agent.execution_state = simple_tool_agent.STATE_PLANNING
        simple_tool_agent.current_plan = None

        parsed = {"tool": "complex_operation", "args": {}}

        # Step 2 should not trigger plan requirement
        simple_tool_agent._validate_plan_required(parsed, step=2)

        assert "needs_plan" not in parsed


class TestDirectAnswerAllowed:
    """Test that direct answers are allowed without plans."""

    def test_direct_answer_without_plan_allowed(self, simple_tool_agent):
        """Verify agents can provide direct answers without creating a plan."""
        # Simple conversational response with answer but no plan
        parsed = {"answer": "The system is running normally."}

        simple_tool_agent._validate_plan_required(parsed, step=1)

        # Should NOT require plan for direct answers
        assert "needs_plan" not in parsed

    def test_response_with_plan_is_valid(self, simple_tool_agent):
        """Verify responses with plans are always valid."""
        parsed = {"plan": ["Step 1: Analyze", "Step 2: Report"], "tool": "complex_op"}

        simple_tool_agent._validate_plan_required(parsed, step=1)

        # Should NOT require plan when plan is present
        assert "needs_plan" not in parsed


# ============================================================================
# SIMPLE_TOOLS ATTRIBUTE TESTS
# ============================================================================


class TestSimpleToolsAttribute:
    """Test SIMPLE_TOOLS class attribute handling."""

    def test_base_agent_has_empty_simple_tools(self):
        """Verify base Agent class has empty SIMPLE_TOOLS list."""
        assert Agent.SIMPLE_TOOLS == []

    def test_simple_tools_is_list(self, simple_tool_agent):
        """Verify SIMPLE_TOOLS is a list type."""
        assert isinstance(simple_tool_agent.SIMPLE_TOOLS, list)

    def test_simple_tools_contains_strings(self, simple_tool_agent):
        """Verify SIMPLE_TOOLS contains only strings."""
        for tool in simple_tool_agent.SIMPLE_TOOLS:
            assert isinstance(tool, str)

    def test_agent_can_override_simple_tools(self):
        """Verify subclasses can define their own SIMPLE_TOOLS."""
        assert SimpleToolAgent.SIMPLE_TOOLS == ["quick_info", "get_status"]
        assert NoSimpleToolAgent.SIMPLE_TOOLS == []  # Inherits from base
