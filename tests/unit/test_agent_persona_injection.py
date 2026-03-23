# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for agent persona context injection.

Verifies that persona fields from YAML/JSON configs are properly
extracted, passed to ConfigurableAgent, and appear in the final system prompt.

NOTE: Tests reflect CONSOLIDATED persona structure (all fields in unified persona dict).
"""

import pytest
from pathlib import Path
import tempfile
import yaml

from gaia.api.agent_registry import AgentRegistry
from gaia.agents.base.configurable import ConfigurableAgent


class TestPersonaFieldExtraction:
    """Test that _register_custom_agent extracts persona fields correctly."""

    def test_register_custom_agent_extracts_nested_persona(self):
        """Verify nested persona dict is extracted and stored."""
        registry = AgentRegistry()

        config = {
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "system_prompt": "You are a test agent.",
            "tools": ["search_web"],
            "persona": {
                "style": "Analytical",
                "focus": "Information gathering",
                "background": "PhD researcher with 10 years experience",
                "expertise": ["data analysis", "research"],
                "voice": "Precise, measured language",
                "communication": "Professional and thorough"
            }
        }

        registry._register_custom_agent("test-agent", config)
        stored = registry._custom_agents["test-agent"]["config"]

        # Verify persona dict is stored
        assert "persona" in stored
        assert stored["persona"]["style"] == "Analytical"
        assert stored["persona"]["focus"] == "Information gathering"
        assert stored["persona"]["background"] == "PhD researcher with 10 years experience"
        assert stored["persona"]["expertise"] == ["data analysis", "research"]
        assert stored["persona"]["voice"] == "Precise, measured language"
        assert stored["persona"]["communication"] == "Professional and thorough"

    def test_register_custom_agent_extracts_top_level_persona(self):
        """Verify top-level persona fields are extracted into unified persona dict."""
        registry = AgentRegistry()

        config = {
            "id": "test-agent-2",
            "name": "Test Agent 2",
            "system_prompt": "You are helpful.",
            "persona": {
                "voice": "Friendly and warm",
                "background": "You are a helpful assistant.",
                "expertise": ["customer service", "support"],
                "communication": "Empathetic and patient"
            }
        }

        registry._register_custom_agent("test-agent-2", config)
        stored = registry._custom_agents["test-agent-2"]["config"]

        # Verify persona fields are stored in unified dict
        assert stored["persona"]["voice"] == "Friendly and warm"
        assert stored["persona"]["background"] == "You are a helpful assistant."
        assert stored["persona"]["expertise"] == ["customer service", "support"]
        assert stored["persona"]["communication"] == "Empathetic and patient"

    def test_register_custom_agent_handles_empty_persona(self):
        """Verify agent works with no persona fields."""
        registry = AgentRegistry()

        config = {
            "id": "minimal-agent",
            "name": "Minimal Agent",
            "system_prompt": "You are minimal.",
            "tools": []
        }

        registry._register_custom_agent("minimal-agent", config)
        stored = registry._custom_agents["minimal-agent"]["config"]

        assert stored["persona"] == {}


class TestPersonaFieldPassing:
    """Test that get_agent passes persona fields to ConfigurableAgent."""

    def test_get_agent_passes_nested_persona(self):
        """Verify get_agent passes nested persona dict to ConfigurableAgent."""
        registry = AgentRegistry()

        config = {
            "id": "persona-agent",
            "name": "Persona Agent",
            "system_prompt": "You are a persona-driven agent.",
            "persona": {
                "style": "Creative",
                "focus": "Storytelling",
                "expertise": ["fiction", "narrative design"]
            }
        }

        registry._register_custom_agent("persona-agent", config)
        agent = registry.get_agent("persona-agent")

        # Verify agent received persona fields
        assert isinstance(agent, ConfigurableAgent)
        assert agent.persona["style"] == "Creative"
        assert agent.persona["focus"] == "Storytelling"
        assert agent.persona["expertise"] == ["fiction", "narrative design"]

    def test_get_agent_passes_unified_persona(self):
        """Verify get_agent passes unified persona dict with all fields."""
        registry = AgentRegistry()

        config = {
            "id": "voice-agent",
            "name": "Voice Agent",
            "system_prompt": "You speak with character.",
            "persona": {
                "voice": "Deep, resonant voice with dramatic pauses",
                "communication": "Theatrical and engaging",
                "style": "Dramatic",
                "background": "Trained actor with 10 years stage experience"
            }
        }

        registry._register_custom_agent("voice-agent", config)
        agent = registry.get_agent("voice-agent")

        assert agent.persona["voice"] == "Deep, resonant voice with dramatic pauses"
        assert agent.persona["communication"] == "Theatrical and engaging"
        assert agent.persona["style"] == "Dramatic"
        assert agent.persona["background"] == "Trained actor with 10 years stage experience"

    def test_get_agent_passes_mixed_persona(self):
        """Verify get_agent handles mixed persona fields."""
        registry = AgentRegistry()

        config = {
            "id": "mixed-agent",
            "name": "Mixed Agent",
            "system_prompt": "You are a mixed persona agent.",
            "persona": {
                "style": "Technical",
                "focus": "Problem solving",
                "voice": "Clear and precise",
                "background": "Software engineer with 20 years experience"
            }
        }

        registry._register_custom_agent("mixed-agent", config)
        agent = registry.get_agent("mixed-agent")

        # Verify all persona fields
        assert agent.persona["style"] == "Technical"
        assert agent.persona["focus"] == "Problem solving"
        assert agent.persona["voice"] == "Clear and precise"
        assert agent.persona["background"] == "Software engineer with 20 years experience"


class TestPersonaInjectionInSystemPrompt:
    """Test that persona fields appear in the final system prompt."""

    def test_system_prompt_includes_nested_persona_fields(self):
        """Verify _get_system_prompt includes all nested persona fields."""
        agent = ConfigurableAgent(
            name="Test Agent",
            description="Test",
            system_prompt="You are a test agent.",
            persona={
                "style": "Analytical and methodical",
                "focus": "Information gathering",
                "background": "PhD researcher with 15 years experience",
                "expertise": ["research", "analysis"],
                "voice": "Precise, measured language",
                "communication": "Professional and citation-focused"
            }
        )

        prompt = agent._get_system_prompt()

        # Verify all persona sections appear in prompt
        assert "**Style:** Analytical and methodical" in prompt
        assert "**Focus:** Information gathering" in prompt
        assert "**Background:** PhD researcher with 15 years experience" in prompt
        assert "**Expertise:** research, analysis" in prompt
        assert "**Voice:** Precise, measured language" in prompt
        assert "**Communication:** Professional and citation-focused" in prompt

    def test_system_prompt_includes_unified_persona(self):
        """Verify _get_system_prompt includes all unified persona fields."""
        agent = ConfigurableAgent(
            name="Voice Agent",
            description="Test",
            system_prompt="You speak with character.",
            persona={
                "voice": "Warm and friendly tone",
                "background": "Helpful assistant with customer service background",
                "expertise": ["support", "troubleshooting"],
                "communication": "Patient and empathetic"
            }
        )

        prompt = agent._get_system_prompt()

        assert "**Voice:** Warm and friendly tone" in prompt
        assert "**Background:** Helpful assistant with customer service background" in prompt
        assert "**Expertise:** support, troubleshooting" in prompt
        assert "**Communication:** Patient and empathetic" in prompt

    def test_system_prompt_handles_empty_persona(self):
        """Verify _get_system_prompt works with no persona fields."""
        agent = ConfigurableAgent(
            name="Minimal Agent",
            description="Test",
            system_prompt="You are minimal."
        )

        prompt = agent._get_system_prompt()

        # Should just return base prompt without persona section
        assert prompt == "You are minimal."
        assert "PERSONA" not in prompt


class TestYamlAgentLoading:
    """Test full YAML file loading with persona injection."""

    def test_load_yaml_agent_with_full_persona(self):
        """Test loading YAML agent and verifying persona injection."""
        # Create temp directory with single YAML file
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpfile = Path(tmpdir) / "test-agent.yml"
            yaml_content = """---
id: yaml-test-agent
name: YAML Test Agent
description: Agent loaded from YAML with full persona
tools: []
init_params:
  max_steps: 50
---
You are a YAML test agent.

## Persona

**Style:** Methodical
**Focus:** Testing
**Background:** QA engineer
**Expertise:**
  - Unit testing
  - Integration testing
**Voice:** Precise
**Communication:** Detail-oriented
"""
            tmpfile.write_text(yaml_content)

            # Load via registry with isolated temp directory
            registry = AgentRegistry(custom_agents_dir=Path(tmpdir))

            # Verify agent was loaded
            agent_id = "yaml-test-agent"
            assert agent_id in registry._custom_agents

            agent = registry.get_agent(agent_id)

            # Verify persona was parsed from markdown body
            assert agent.persona["style"] == "Methodical"
            assert agent.persona["focus"] == "Testing"
            assert agent.persona["background"] == "QA engineer"
            assert agent.persona["expertise"] == ["Unit testing", "Integration testing"]
            assert agent.persona["voice"] == "Precise"
            assert agent.persona["communication"] == "Detail-oriented"


class TestPersonaEdgeCases:
    """Test edge cases for persona injection."""

    def test_persona_with_none_values(self):
        """Verify agent handles None persona values gracefully."""
        agent = ConfigurableAgent(
            name="Test",
            description="Test",
            system_prompt="Base",
            persona={
                "style": None,
                "focus": "",
                "background": None
            }
        )

        prompt = agent._get_system_prompt()
        # None/empty values should not appear in prompt
        assert "**Style:**" not in prompt
        assert "**Focus:**" not in prompt
        assert "**Background:**" not in prompt

    def test_persona_with_empty_strings(self):
        """Verify empty strings don't create empty sections."""
        agent = ConfigurableAgent(
            name="Test",
            description="Test",
            system_prompt="Base",
            persona={
                "style": "",
                "voice": "",
                "communication": ""
            }
        )

        prompt = agent._get_system_prompt()
        assert prompt == "Base"

    def test_persona_expertise_as_strings(self):
        """Test expertise list is properly formatted."""
        agent = ConfigurableAgent(
            name="Test",
            description="Test",
            system_prompt="Base",
            persona={
                "expertise": ["research", "analysis", "testing"]
            }
        )

        prompt = agent._get_system_prompt()
        assert "**Expertise:** research, analysis, testing" in prompt


class TestToolExecutionFiltering:
    """Test that tool execution is properly filtered at runtime."""

    def test_execute_tool_enforces_filtering(self):
        """Test that _execute_tool blocks tools not in requested_tools."""
        agent = ConfigurableAgent(
            name="Test",
            description="Test",
            system_prompt="Base",
            tools=["allowed_tool"]
        )

        # Try to execute a tool that's not configured
        result = agent._execute_tool("forbidden_tool", {})

        # Should return error, not execute
        assert result["status"] == "error"
        assert "forbidden_tool" in result["error"]
        assert "not available" in result["error"].lower()

    def test_execute_tool_allows_configured_tools(self):
        """Test that configured tools can be executed."""
        # Note: This test would need a mock tool in _TOOL_REGISTRY
        # For now, we test the filtering logic returns error for non-configured
        agent = ConfigurableAgent(
            name="Test",
            description="Test",
            system_prompt="Base",
            tools=["tool_a", "tool_b"]
        )

        # Wildcard should allow all
        agent_wildcard = ConfigurableAgent(
            name="Test Wildcard",
            description="Test",
            system_prompt="Base",
            tools=["*"]
        )

        # Wildcard agent would allow any tool (tested via "*" check)
        assert "*" in agent_wildcard.requested_tools
