# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration tests for agent tool scoping.

Tests verify that:
1. Agent.base creates and uses _tool_scope correctly
2. ConfigurableAgent uses YAML tools as allowlist
3. Tool execution goes through scope
4. Access denied errors are handled properly
"""

import pytest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import Mock, MagicMock, patch

from gaia.agents.base.agent import Agent
from gaia.agents.configurable import ConfigurableAgent
from gaia.agents.base.context import AgentDefinition, AgentCapabilities, AgentConstraints
from gaia.agents.base.tools import (
    ToolRegistry,
    ToolAccessDeniedError,
    ToolNotFoundError,
    tool,
)


class MockTestAgent(Agent):
    """Concrete test agent subclass for testing (not collected by pytest)."""

    def _register_tools(self):
        """No-op tool registration for testing."""
        pass


class TestAgentToolScoping:
    """Tests for Agent tool scoping integration."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def teardown_method(self):
        """Clean up after each test."""
        ToolRegistry._instance = None

    def test_agent_with_allowed_tools(self):
        """Test agent creation with allowed_tools parameter."""
        # Register test tools
        @tool
        def tool_a() -> str:
            """Tool A."""
            return "a"

        @tool
        def tool_b() -> str:
            """Tool B."""
            return "b"

        # Create agent with limited tools
        agent = MockTestAgent(allowed_tools=["tool_a"], skip_lemonade=True)

        # Verify scope created
        assert hasattr(agent, "_tool_scope")
        assert agent._tool_scope is not None
        assert agent._tool_scope.has_tool("tool_a")
        assert not agent._tool_scope.has_tool("tool_b")

        # Clean up
        agent.cleanup()

    def test_agent_without_allowed_tools(self):
        """Test agent creation without allowed_tools (all tools accessible)."""
        @tool
        def tool_c() -> str:
            """Tool C."""
            return "c"

        # Create agent without tool restrictions
        agent = MockTestAgent(allowed_tools=None, skip_lemonade=True)

        # Verify scope created with no restrictions
        assert hasattr(agent, "_tool_scope")
        assert agent._tool_scope.has_tool("tool_c")

        # Clean up
        agent.cleanup()

    def test_agent_execute_tool_through_scope(self):
        """Test agent executes tools through scope."""
        @tool
        def add(x: int, y: int) -> int:
            """Add two numbers."""
            return x + y

        agent = MockTestAgent(allowed_tools=["add"], skip_lemonade=True)
        result = agent._execute_tool("add", {"x": 2, "y": 3})
        assert result == 5

        # Clean up
        agent.cleanup()

    def test_agent_tool_access_denied(self):
        """Test agent denied access to tools outside allowlist."""
        @tool
        def secret_tool() -> str:
            """Secret tool."""
            return "secret"

        @tool
        def other_tool() -> str:
            """Other tool."""
            return "other"

        agent = MockTestAgent(allowed_tools=["other_tool"], skip_lemonade=True)
        result = agent._execute_tool("secret_tool", {})

        assert result["status"] == "error"
        assert "denied" in result["error"].lower() or "access denied" in result["error"].lower()

        # Clean up
        agent.cleanup()

    def test_agent_format_tools_for_prompt_uses_scope(self):
        """Test _format_tools_for_prompt uses scoped tools."""
        @tool
        def visible_tool() -> None:
            """Visible tool description."""
            pass

        @tool
        def hidden_tool() -> None:
            """Hidden tool description."""
            pass

        agent = MockTestAgent(allowed_tools=["visible_tool"], skip_lemonade=True)
        prompt = agent._format_tools_for_prompt()

        assert "visible_tool" in prompt
        assert "hidden_tool" not in prompt

        # Clean up
        agent.cleanup()

    def test_agent_cleanup_releases_scope(self):
        """Test agent.cleanup() releases tool scope."""
        agent = MockTestAgent(allowed_tools=["tool"], skip_lemonade=True)
        assert hasattr(agent, "_tool_scope")
        assert agent._tool_scope is not None

        agent.cleanup()

        assert agent._tool_scope is None

    def test_agent_format_tools_backward_compat(self):
        """Test _format_tools_for_prompt works without scope (backward compat)."""
        @tool
        def test_tool() -> None:
            """Test tool."""
            pass

        agent = MockTestAgent(skip_lemonade=True)

        # Remove scope to test backward compat
        agent._tool_scope = None

        # Should still work with fallback
        prompt = agent._format_tools_for_prompt()
        assert "test_tool" in prompt

        # Clean up
        agent.cleanup()

    def test_agent_execute_tool_backward_compat(self):
        """Test _execute_tool works without scope (backward compat)."""
        @tool
        def compat_tool(x: int) -> int:
            """Compatibility test tool."""
            return x * 2

        agent = MockTestAgent(skip_lemonade=True)
        agent._tool_scope = None  # Remove scope

        result = agent._execute_tool("compat_tool", {"x": 5})
        assert result == 10

        # Clean up
        agent.cleanup()

    def test_agent_tool_not_found_in_scope(self):
        """Test executing tool not in scope returns error."""
        @tool
        def available_tool() -> str:
            """Available tool."""
            return "available"

        agent = MockTestAgent(allowed_tools=["available_tool"], skip_lemonade=True)

        # Try to execute tool not in registry
        result = agent._execute_tool("nonexistent_tool", {})
        assert result["status"] == "error"

        # Clean up
        agent.cleanup()


class TestConfigurableAgentToolScoping:
    """Tests for ConfigurableAgent tool scoping."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def teardown_method(self):
        """Clean up after each test."""
        ToolRegistry._instance = None

    def _create_agent_definition(self, tools: List[str]) -> AgentDefinition:
        """Helper to create AgentDefinition with required fields."""
        return AgentDefinition(
            id="test-agent",
            name="Test Agent",
            version="1.0.0",
            category="test",
            description="Test agent for scoping",
            tools=tools,
            capabilities=AgentCapabilities(capabilities=["test"]),
            constraints=AgentConstraints(max_steps=50),
        )

    def _create_and_initialize_agent(self, definition: AgentDefinition) -> ConfigurableAgent:
        """Helper to create and synchronously initialize a ConfigurableAgent."""
        import asyncio
        agent = ConfigurableAgent(definition=definition, skip_lemonade=True)
        # Run initialize synchronously since it's a simple method
        asyncio.get_event_loop().run_until_complete(agent.initialize())
        return agent

    def test_configurable_agent_uses_yaml_allowlist(self):
        """Test ConfigurableAgent uses YAML tools as allowlist."""
        # Register test tools first
        @tool
        def yaml_tool_a() -> str:
            """YAML tool A."""
            return "a"

        @tool
        def yaml_tool_b() -> str:
            """YAML tool B."""
            return "b"

        @tool
        def yaml_tool_c() -> str:
            """YAML tool C."""
            return "c"

        # Create agent definition with specific tools
        definition = self._create_agent_definition(["yaml_tool_a", "yaml_tool_b"])

        # Create and initialize configurable agent
        agent = self._create_and_initialize_agent(definition)

        # Verify scope created with YAML tools
        assert hasattr(agent, "_tool_scope")
        assert agent._tool_scope.has_tool("yaml_tool_a")
        assert agent._tool_scope.has_tool("yaml_tool_b")
        assert not agent._tool_scope.has_tool("yaml_tool_c")

        # Clean up
        agent.cleanup()

    def test_configurable_agent_execute_allowed_tool(self):
        """Test ConfigurableAgent executes allowed tool."""
        @tool
        def allowed_yaml_tool(x: int) -> int:
            """Allowed YAML tool."""
            return x * 3

        definition = self._create_agent_definition(["allowed_yaml_tool"])
        definition.id = "test-agent-2"
        definition.name = "Test Agent 2"

        agent = self._create_and_initialize_agent(definition)

        result = agent._execute_tool("allowed_yaml_tool", {"x": 4})
        assert result == 12

        # Clean up
        agent.cleanup()

    def test_configurable_agent_execute_denied_tool(self):
        """Test ConfigurableAgent denies tool not in YAML."""
        @tool
        def permitted_tool() -> str:
            """Permitted tool."""
            return "permitted"

        @tool
        def forbidden_tool() -> str:
            """Forbidden tool."""
            return "forbidden"

        definition = self._create_agent_definition(["permitted_tool"])
        definition.id = "test-agent-3"
        definition.name = "Test Agent 3"

        agent = self._create_and_initialize_agent(definition)

        # Try to execute tool not in YAML allowlist
        result = agent._execute_tool("forbidden_tool", {})
        assert result["status"] == "error"
        assert result.get("security_violation") is True

        # Clean up
        agent.cleanup()

    def test_configurable_agent_format_tools_for_prompt(self):
        """Test ConfigurableAgent formats only allowed tools."""
        @tool
        def shown_tool() -> None:
            """This tool should be shown."""
            pass

        @tool
        def masked_tool() -> None:
            """This tool should be maskeded."""
            pass

        definition = self._create_agent_definition(["shown_tool"])
        definition.id = "test-agent-4"
        definition.name = "Test Agent 4"

        agent = self._create_and_initialize_agent(definition)

        prompt = agent._format_tools_for_prompt()
        assert "shown_tool" in prompt
        assert "masked_tool" not in prompt

        # Clean up
        agent.cleanup()

    def test_configurable_agent_get_available_tools(self):
        """Test ConfigurableAgent.get_available_tools() returns YAML tools."""
        @tool
        def listed_tool() -> None:
            """Listed tool."""
            pass

        @tool
        def unlisted_tool() -> None:
            """Unlisted tool."""
            pass

        definition = self._create_agent_definition(["listed_tool"])
        definition.id = "test-agent-5"
        definition.name = "Test Agent 5"

        agent = self._create_and_initialize_agent(definition)

        available = agent.get_available_tools()
        assert "listed_tool" in available
        assert "unlisted_tool" not in available

        # Clean up
        agent.cleanup()

    def test_configurable_agent_cleanup(self):
        """Test ConfigurableAgent.cleanup() releases scope."""
        @tool
        def cleanup_test_tool() -> None:
            """Cleanup test tool."""
            pass

        definition = self._create_agent_definition(["cleanup_test_tool"])
        definition.id = "test-agent-6"
        definition.name = "Test Agent 6"

        agent = self._create_and_initialize_agent(definition)

        assert hasattr(agent, "_tool_scope")
        assert agent._tool_scope is not None

        agent.cleanup()

        assert agent._tool_scope is None

    def test_configurable_agent_security_violation_logging(self):
        """Test security violations are logged."""
        @tool
        def safe_tool() -> str:
            """Safe tool."""
            return "safe"

        @tool
        def dangerous_tool() -> str:
            """Dangerous tool."""
            return "dangerous"

        definition = self._create_agent_definition(["safe_tool"])
        definition.id = "test-agent-7"
        definition.name = "Test Agent 7"

        agent = self._create_and_initialize_agent(definition)

        # Execute denied tool
        result = agent._execute_tool("dangerous_tool", {})

        # Verify security response
        assert result["status"] == "error"
        assert result.get("security_violation") is True

        # Clean up
        agent.cleanup()


class TestMultiAgentIsolation:
    """Tests for multi-agent tool isolation."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def teardown_method(self):
        """Clean up after each test."""
        ToolRegistry._instance = None

    def test_two_agents_isolated_scopes(self):
        """Test two agents have isolated tool access."""
        @tool
        def agent1_exclusive() -> str:
            """Agent 1 exclusive tool."""
            return "agent1"

        @tool
        def agent2_exclusive() -> str:
            """Agent 2 exclusive tool."""
            return "agent2"

        @tool
        def shared_tool() -> str:
            """Shared tool."""
            return "shared"

        # Create two agents with different allowlists
        agent1 = MockTestAgent(allowed_tools=["agent1_exclusive", "shared_tool"], skip_lemonade=True)
        agent2 = MockTestAgent(allowed_tools=["agent2_exclusive", "shared_tool"], skip_lemonade=True)

        # Agent 1 can access its tools
        assert agent1._tool_scope.has_tool("agent1_exclusive")
        assert agent1._tool_scope.has_tool("shared_tool")
        assert not agent1._tool_scope.has_tool("agent2_exclusive")

        # Agent 2 can access its tools
        assert agent2._tool_scope.has_tool("agent2_exclusive")
        assert agent2._tool_scope.has_tool("shared_tool")
        assert not agent2._tool_scope.has_tool("agent1_exclusive")

        # Execute tools through scopes
        result1 = agent1._execute_tool("agent1_exclusive", {})
        assert result1 == "agent1"

        result2 = agent2._execute_tool("agent2_exclusive", {})
        assert result2 == "agent2"

        result_shared1 = agent1._execute_tool("shared_tool", {})
        assert result_shared1 == "shared"

        result_shared2 = agent2._execute_tool("shared_tool", {})
        assert result_shared2 == "shared"

        # Clean up
        agent1.cleanup()
        agent2.cleanup()

    def test_agent_cannot_access_other_agent_tools(self):
        """Test agent cannot execute tools outside its scope."""
        @tool
        def private_tool() -> str:
            """Private tool."""
            return "private"

        agent = MockTestAgent(allowed_tools=["private_tool"], skip_lemonade=True)

        # Create another scope to simulate another agent
        registry = ToolRegistry.get_instance()
        other_scope = registry.create_scope("other_agent", allowed_tools=["other_tool"])

        # Agent should not have access to tools not in its scope
        result = agent._execute_tool("other_tool", {})
        assert result["status"] == "error"

        # Clean up
        agent.cleanup()
        other_scope.cleanup()


class TestToolScopingEdgeCases:
    """Tests for edge cases in tool scoping."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def teardown_method(self):
        """Clean up after each test."""
        ToolRegistry._instance = None

    def test_empty_allowlist(self):
        """Test agent with empty allowlist denies all tools."""
        @tool
        def any_tool() -> str:
            """Any tool."""
            return "any"

        agent = MockTestAgent(allowed_tools=[], skip_lemonade=True)

        assert not agent._tool_scope.has_tool("any_tool")
        result = agent._execute_tool("any_tool", {})
        assert result["status"] == "error"

        # Clean up
        agent.cleanup()

    def test_case_sensitive_tool_names(self):
        """Test tool name matching is case-sensitive."""
        @tool
        def File_Read() -> str:
            """File read with capital F and R."""
            return "read"

        @tool
        def file_read() -> str:
            """File read lowercase."""
            return "read_lower"

        # Agent with exact case match
        agent = MockTestAgent(allowed_tools=["file_read"], skip_lemonade=True)

        assert agent._tool_scope.has_tool("file_read")
        assert not agent._tool_scope.has_tool("File_Read")

        # Clean up
        agent.cleanup()

    def test_tool_with_kwargs_through_scope(self):
        """Test executing tool with kwargs through scope."""
        @tool
        def flexible_tool(name: str, value: int = 10) -> str:
            """Tool with default kwarg."""
            return f"{name}: {value}"

        agent = MockTestAgent(allowed_tools=["flexible_tool"], skip_lemonade=True)

        # Test with default
        result1 = agent._execute_tool("flexible_tool", {"name": "test"})
        assert result1 == "test: 10"

        # Test with override
        result2 = agent._execute_tool("flexible_tool", {"name": "test", "value": 42})
        assert result2 == "test: 42"

        # Clean up
        agent.cleanup()

    def test_tool_exception_through_scope(self):
        """Test tool exceptions are properly propagated through scope."""
        @tool
        def failing_tool() -> None:
            """Tool that always fails."""
            raise ValueError("Intentional failure")

        agent = MockTestAgent(allowed_tools=["failing_tool"], skip_lemonade=True)

        result = agent._execute_tool("failing_tool", {})
        assert result["status"] == "error"
        assert "Intentional failure" in result["error"]

        # Clean up
        agent.cleanup()


class TestBackwardCompatibility:
    """Tests for backward compatibility with existing code."""

    def setup_method(self):
        """Reset registry before each test."""
        ToolRegistry._instance = None

    def teardown_method(self):
        """Clean up after each test."""
        ToolRegistry._instance = None

    def _create_agent_definition(self, tools: List[str]) -> AgentDefinition:
        """Helper to create AgentDefinition with required fields."""
        return AgentDefinition(
            id="test-agent",
            name="Test Agent",
            version="1.0.0",
            category="test",
            description="Test agent for scoping",
            tools=tools,
            capabilities=AgentCapabilities(capabilities=["test"]),
            constraints=AgentConstraints(max_steps=50),
        )

    def _create_and_initialize_agent(self, definition: AgentDefinition) -> ConfigurableAgent:
        """Helper to create and synchronously initialize a ConfigurableAgent."""
        import asyncio
        agent = ConfigurableAgent(definition=definition, skip_lemonade=True)
        asyncio.get_event_loop().run_until_complete(agent.initialize())
        return agent

    def test_agent_without_allowed_tools_param(self):
        """Test existing agents without allowed_tools param still work."""
        @tool
        def legacy_tool() -> str:
            """Legacy tool."""
            return "legacy"

        # Create agent without allowed_tools (old style)
        agent = MockTestAgent(skip_lemonade=True)

        # Should work with all tools accessible
        assert hasattr(agent, "_tool_scope")
        result = agent._execute_tool("legacy_tool", {})
        assert result == "legacy"

        # Clean up
        agent.cleanup()

    def test_configurable_agent_backward_compat(self):
        """Test ConfigurableAgent maintains backward compatibility."""
        @tool
        def config_tool() -> str:
            """Config tool."""
            return "config"

        definition = self._create_agent_definition(["config_tool"])
        definition.id = "legacy-agent"
        definition.name = "Legacy Agent"

        # Create agent the old way
        agent = self._create_and_initialize_agent(definition)

        # Should work
        assert hasattr(agent, "_tool_scope")
        result = agent._execute_tool("config_tool", {})
        assert result == "config"

        # Clean up
        agent.cleanup()
