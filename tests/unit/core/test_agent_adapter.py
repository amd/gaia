# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for AgentAdapter.

This test suite validates:
- AgentAdapter creation and profile extraction
- Attribute delegation to legacy agents
- LegacyAgentWrapper functionality
- Backward compatibility

Quality Gate 4 Criteria Covered:
- BC-001: AgentAdapter 100% backward compatibility
"""

import pytest

from gaia.core.adapter import AgentAdapter, LegacyAgentWrapper, extract_profile
from gaia.core.profile import AgentProfile


# =============================================================================
# Mock Legacy Agent Classes
# =============================================================================

class MockLegacyAgent:
    """Mock legacy agent for testing."""

    def __init__(
        self,
        agent_id="mock-agent",
        name="Mock Agent",
        model_id="Qwen3.5-35B",
        max_steps=10,
        debug=False,
    ):
        self.agent_id = agent_id
        self.name = name
        self.model_id = model_id
        self.max_steps = max_steps
        self.debug = debug
        self.system_prompt = "You are a mock agent"
        self.allowed_tools = ["tool1", "tool2"]
        self.supports_code_execution = True
        self.temperature = 0.7

    async def run_step(self, topic, context=None):
        """Mock run_step method."""
        return {"response": f"Mock response to {topic}", "topic": topic}

    async def run(self, topic, context=None):
        """Mock run method."""
        return await self.run_step(topic, context)


class MockLegacyAgentWithRunOnly:
    """Mock legacy agent with only run method."""

    def __init__(self):
        self.id = "run-only-agent"
        self.name = "Run Only Agent"
        self.model = "Qwen3-0.6B"

    async def run(self, topic, context=None):
        """Mock run method."""
        return {"response": f"Run-only response to {topic}"}


class MockLegacyAgentNoMethods:
    """Mock legacy agent with no run methods (for error testing)."""

    def __init__(self):
        self.id = "no-methods-agent"


# =============================================================================
# AgentAdapter Creation Tests
# =============================================================================

class TestAgentAdapterCreation:
    """Tests for AgentAdapter creation and initialization."""

    def test_create_adapter_with_valid_agent(self):
        """Test creating adapter with valid legacy agent."""
        legacy = MockLegacyAgent()
        adapter = AgentAdapter(legacy)

        assert adapter.legacy_agent is legacy
        assert adapter.profile is not None
        assert adapter.profile.id == "mock-agent"
        assert adapter.profile.name == "Mock Agent"

    def test_create_adapter_with_none_raises(self):
        """Test that creating adapter with None raises ValueError."""
        with pytest.raises(ValueError, match="legacy_agent cannot be None"):
            AgentAdapter(None)

    def test_adapter_repr(self):
        """Test adapter string representation."""
        legacy = MockLegacyAgent()
        adapter = AgentAdapter(legacy)

        repr_str = repr(adapter)
        assert "AgentAdapter" in repr_str
        assert "MockLegacyAgent" in repr_str
        assert "mock-agent" in repr_str


# =============================================================================
# Profile Extraction Tests
# =============================================================================

class TestProfileExtraction:
    """Tests for profile extraction from legacy agents."""

    def test_extract_profile_id(self):
        """Test extracting agent_id field."""
        legacy = MockLegacyAgent(agent_id="custom-id")
        adapter = AgentAdapter(legacy)

        assert adapter.profile.id == "custom-id"

    def test_extract_profile_id_fallback_to_id(self):
        """Test fallback to 'id' attribute if agent_id not present."""
        legacy = MockLegacyAgentWithRunOnly()
        adapter = AgentAdapter(legacy)

        assert adapter.profile.id == "run-only-agent"

    def test_extract_profile_name(self):
        """Test extracting name field."""
        legacy = MockLegacyAgent(name="Custom Name")
        adapter = AgentAdapter(legacy)

        assert adapter.profile.name == "Custom Name"

    def test_extract_profile_role(self):
        """Test extracting role/description."""
        legacy = MockLegacyAgent()
        adapter = AgentAdapter(legacy)

        # Should fall back to description or generated name
        assert adapter.profile.role != ""

    def test_extract_profile_model(self):
        """Test extracting model_id."""
        legacy = MockLegacyAgent(model_id="Custom-Model-123")
        adapter = AgentAdapter(legacy)

        assert adapter.profile.model_config["model_id"] == "Custom-Model-123"

    def test_extract_profile_model_fallback(self):
        """Test model fallback to default if not specified."""
        legacy = MockLegacyAgent()
        delattr(legacy, "model_id")

        adapter = AgentAdapter(legacy)
        assert "model_id" in adapter.profile.model_config

    def test_extract_profile_tools(self):
        """Test extracting allowed_tools."""
        legacy = MockLegacyAgent()
        adapter = AgentAdapter(legacy)

        assert "tool1" in adapter.profile.tools
        assert "tool2" in adapter.profile.tools

    def test_extract_profile_max_steps(self):
        """Test extracting max_steps."""
        legacy = MockLegacyAgent(max_steps=25)
        adapter = AgentAdapter(legacy)

        assert adapter.profile.metadata.get("max_steps") == 25

    def test_extract_profile_temperature(self):
        """Test extracting temperature model setting."""
        legacy = MockLegacyAgent()
        legacy.temperature = 0.9
        adapter = AgentAdapter(legacy)

        assert adapter.profile.model_config.get("temperature") == 0.9

    def test_extract_profile_capabilities(self):
        """Test extracting capability flags."""
        legacy = MockLegacyAgent()
        legacy.supports_code_execution = True
        legacy.supports_vision = False

        adapter = AgentAdapter(legacy)

        assert adapter.profile.capabilities.supports_code_execution is True

    def test_get_profile_method(self):
        """Test get_profile() method."""
        legacy = MockLegacyAgent()
        adapter = AgentAdapter(legacy)

        profile = adapter.get_profile()
        assert profile is adapter.profile

    def test_get_legacy_agent_method(self):
        """Test get_legacy_agent() method."""
        legacy = MockLegacyAgent()
        adapter = AgentAdapter(legacy)

        retrieved = adapter.get_legacy_agent()
        assert retrieved is legacy


# =============================================================================
# Attribute Delegation Tests
# =============================================================================

class TestAttributeDelegation:
    """Tests for __getattr__ delegation to legacy agent."""

    def test_delegate_known_attribute(self):
        """Test delegating known attribute to legacy."""
        legacy = MockLegacyAgent(debug=True)
        adapter = AgentAdapter(legacy)

        assert adapter.debug is True

    def test_delegate_method_call(self):
        """Test delegating method call to legacy."""
        legacy = MockLegacyAgent()
        adapter = AgentAdapter(legacy)

        # Access method from legacy through adapter
        assert hasattr(adapter, "run_step")
        assert hasattr(adapter, "run")

    def test_delegate_custom_attribute(self):
        """Test delegating custom attribute."""
        legacy = MockLegacyAgent()
        legacy.custom_attr = "custom_value"
        adapter = AgentAdapter(legacy)

        assert adapter.custom_attr == "custom_value"


# =============================================================================
# Run Step Delegation Tests
# =============================================================================

class TestRunStepDelegation:
    """Tests for run_step delegation to legacy agent."""

    @pytest.mark.asyncio
    async def test_run_step_delegation(self):
        """Test delegating run_step to legacy agent."""
        legacy = MockLegacyAgent()
        adapter = AgentAdapter(legacy)

        result = await adapter.run_step("Test topic", {"key": "value"})

        assert "response" in result
        assert result["topic"] == "Test topic"

    @pytest.mark.asyncio
    async def test_run_delegation(self):
        """Test delegating run to legacy agent (alias for run_step)."""
        legacy = MockLegacyAgent()
        adapter = AgentAdapter(legacy)

        result = await adapter.run("Test topic")

        assert "response" in result

    @pytest.mark.asyncio
    async def test_run_step_fallback_to_run(self):
        """Test fallback to run method if run_step not available."""
        legacy = MockLegacyAgentWithRunOnly()
        adapter = AgentAdapter(legacy)

        result = await adapter.run_step("Test topic")

        assert "response" in result

    @pytest.mark.asyncio
    async def test_run_step_no_methods_raises(self):
        """Test that adapter raises if legacy has no run methods."""
        legacy = MockLegacyAgentNoMethods()
        adapter = AgentAdapter(legacy)

        with pytest.raises(AttributeError, match="run_step.*run"):
            await adapter.run_step("Test topic")


# =============================================================================
# Convenience Function Tests
# =============================================================================

class TestExtractProfileFunction:
    """Tests for extract_profile() convenience function."""

    def test_extract_profile_returns_profile(self):
        """Test that extract_profile returns AgentProfile."""
        legacy = MockLegacyAgent()
        profile = extract_profile(legacy)

        assert isinstance(profile, AgentProfile)
        assert profile.id == "mock-agent"

    def test_extract_profile_creates_adapter_internally(self):
        """Test that extract_profile creates adapter internally."""
        legacy = MockLegacyAgent()
        profile = extract_profile(legacy)

        assert profile.name == "Mock Agent"


# =============================================================================
# LegacyAgentWrapper Tests
# =============================================================================

class TestLegacyAgentWrapper:
    """Tests for LegacyAgentWrapper class."""

    def test_create_wrapper(self):
        """Test creating empty wrapper."""
        wrapper = LegacyAgentWrapper()
        assert wrapper.list_agents() == []
        assert wrapper.get_agent_count() == 0

    def test_add_agent(self):
        """Test adding agent to wrapper."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("test", MockLegacyAgent())

        assert "test" in wrapper.list_agents()
        assert wrapper.get_agent_count() == 1

    def test_add_agent_returns_self(self):
        """Test that add_agent returns self for chaining."""
        wrapper = LegacyAgentWrapper()
        result = wrapper.add_agent("test", MockLegacyAgent())

        assert result is wrapper

    def test_add_agent_method_chaining(self):
        """Test method chaining with add_agent."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("agent1", MockLegacyAgent()).add_agent("agent2", MockLegacyAgent())

        assert wrapper.get_agent_count() == 2
        assert "agent1" in wrapper.list_agents()
        assert "agent2" in wrapper.list_agents()

    def test_add_agent_none_raises(self):
        """Test that adding None agent raises ValueError."""
        wrapper = LegacyAgentWrapper()

        with pytest.raises(ValueError, match="cannot be None"):
            wrapper.add_agent("none", None)

    def test_add_agent_sets_default(self):
        """Test that first agent becomes default automatically."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("first", MockLegacyAgent(agent_id="first"))

        default = wrapper.get_default_agent()
        assert default is not None
        assert default.profile.id == "first"

    def test_add_agent_set_default_explicit(self):
        """Test explicitly setting default agent."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("agent1", MockLegacyAgent(agent_id="agent1"))
        wrapper.add_agent("agent2", MockLegacyAgent(agent_id="agent2"), set_default=True)

        default = wrapper.get_default_agent()
        assert default.profile.id == "agent2"

    def test_get_agent_by_name(self):
        """Test getting agent by name."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("test", MockLegacyAgent(agent_id="test-id"))

        agent = wrapper.get_agent("test")
        assert agent.profile.id == "test-id"

    def test_get_agent_unknown_fallback_to_default(self):
        """Test getting unknown agent falls back to default."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("default", MockLegacyAgent(agent_id="default-id"))

        # Get unknown agent - should return default
        agent = wrapper.get_agent("unknown")
        assert agent.profile.id == "default-id"

    def test_get_agent_no_default_raises(self):
        """Test getting unknown agent with no default raises KeyError."""
        wrapper = LegacyAgentWrapper()

        with pytest.raises(KeyError, match="not found"):
            wrapper.get_agent("unknown")

    @pytest.mark.asyncio
    async def test_run_agent(self):
        """Test running specific agent."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("test", MockLegacyAgent())

        result = await wrapper.run_agent("test", "Test topic")

        assert "response" in result

    def test_set_default_agent(self):
        """Test setting default agent."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("agent1", MockLegacyAgent(agent_id="agent1"))
        wrapper.add_agent("agent2", MockLegacyAgent(agent_id="agent2"))

        result = wrapper.set_default_agent("agent2")
        assert result is True

        default = wrapper.get_default_agent()
        assert default.profile.id == "agent2"

    def test_set_default_agent_unknown_returns_false(self):
        """Test setting unknown default returns False."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("test", MockLegacyAgent())

        result = wrapper.set_default_agent("unknown")
        assert result is False

    def test_remove_agent(self):
        """Test removing agent."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("agent1", MockLegacyAgent())
        wrapper.add_agent("agent2", MockLegacyAgent())

        result = wrapper.remove_agent("agent1")
        assert result is True
        assert "agent1" not in wrapper.list_agents()
        assert wrapper.get_agent_count() == 1

    def test_remove_agent_unknown_returns_false(self):
        """Test removing unknown agent returns False."""
        wrapper = LegacyAgentWrapper()

        result = wrapper.remove_agent("unknown")
        assert result is False

    def test_remove_default_agent_updates_default(self):
        """Test removing default agent updates default."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("agent1", MockLegacyAgent(agent_id="agent1"))
        wrapper.add_agent("agent2", MockLegacyAgent(agent_id="agent2"))
        wrapper.set_default_agent("agent1")

        wrapper.remove_agent("agent1")

        # Default should now be the remaining agent
        default = wrapper.get_default_agent()
        assert default.profile.id == "agent2"

    def test_wrapper_repr(self):
        """Test wrapper string representation."""
        wrapper = LegacyAgentWrapper()
        wrapper.add_agent("test", MockLegacyAgent())

        repr_str = repr(wrapper)
        assert "LegacyAgentWrapper" in repr_str
        assert "test" in repr_str


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Integration tests for AgentAdapter with real patterns."""

    def test_adapter_with_code_agent_pattern(self):
        """Test adapter pattern simulating CodeAgent."""
        class CodeAgentLike:
            def __init__(self):
                self.agent_id = "code-agent"
                self.name = "Code Assistant"
                self.model_id = "Qwen3.5-35B"
                self.max_steps = 15
                self.system_prompt = "You are a coding expert"
                self.allowed_tools = ["read_file", "write_file", "run_command"]
                self.supports_code_execution = True
                self.temperature = 0.2

            async def run_step(self, topic, context=None):
                return {"code_response": f"Generated code for {topic}"}

        legacy = CodeAgentLike()
        adapter = AgentAdapter(legacy)

        assert adapter.profile.id == "code-agent"
        assert adapter.profile.name == "Code Assistant"
        assert "read_file" in adapter.profile.tools
        assert adapter.profile.capabilities.supports_code_execution is True
        assert adapter.profile.model_config["model_id"] == "Qwen3.5-35B"

    def test_wrapper_multi_agent_routing(self):
        """Test multi-agent routing pattern."""
        wrapper = LegacyAgentWrapper()

        class Agent1:
            agent_id = "agent-1"
            name = "Agent One"

            async def run_step(self, topic, ctx=None):
                return {"from": "agent-1"}

        class Agent2:
            agent_id = "agent-2"
            name = "Agent Two"

            async def run_step(self, topic, ctx=None):
                return {"from": "agent-2"}

        wrapper.add_agent("one", Agent1())
        wrapper.add_agent("two", Agent2())

        # Verify routing works
        assert wrapper.get_agent("one").profile.id == "agent-1"
        assert wrapper.get_agent("two").profile.id == "agent-2"


# Run tests with: pytest tests/unit/core/test_agent_adapter.py -v
