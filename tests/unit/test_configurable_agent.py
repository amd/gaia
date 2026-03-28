# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
from unittest.mock import MagicMock, patch

from gaia.agents.base.configurable import ConfigurableAgent
from gaia.api.agent_registry import AgentRegistry


def test_configurable_agent_init():
    """Test that ConfigurableAgent initializes correctly with config."""
    agent = ConfigurableAgent(
        name="Test Agent",
        description="A test agent",
        system_prompt="You are a test agent.",
        tools=["list_dir"],
        skip_lemonade=True,
    )

    assert agent.agent_name == "Test Agent"
    assert agent.agent_description == "A test agent"
    assert agent.base_system_prompt == "You are a test agent."
    assert "list_dir" in agent.requested_tools
    assert agent._get_system_prompt() == "You are a test agent."


def test_configurable_agent_format_tools():
    """Test that ConfigurableAgent only includes requested tools."""
    # Mock _TOOL_REGISTRY
    mock_tools = {
        "tool1": {"description": "desc1", "parameters": {}},
        "tool2": {"description": "desc2", "parameters": {}},
    }

    with patch("gaia.agents.base.configurable._TOOL_REGISTRY", mock_tools):
        agent = ConfigurableAgent(
            name="Test",
            description="Test",
            system_prompt="Test",
            tools=["tool1"],
            skip_lemonade=True,
        )

        tools_prompt = agent._format_tools_for_prompt()
        assert "- tool1" in tools_prompt
        assert "- tool2" not in tools_prompt

        # Test all tools
        agent_all = ConfigurableAgent(
            name="Test",
            description="Test",
            system_prompt="Test",
            tools=["*"],
            skip_lemonade=True,
        )
        tools_prompt_all = agent_all._format_tools_for_prompt()
        assert "- tool1" in tools_prompt_all
        assert "- tool2" in tools_prompt_all


def test_registry_scan_custom_agents(tmp_path):
    """Test that AgentRegistry correctly scans and loads custom agents from a specified directory."""
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()

    # Create a JSON agent
    json_agent = {
        "id": "json-agent",
        "name": "JSON Agent",
        "description": "A JSON agent",
        "system_prompt": "JSON Prompt",
        "tools": ["tool1"],
    }
    with open(custom_dir / "agent1.json", "w") as f:
        json.dump(json_agent, f)

    # Create a Markdown agent
    md_content = """---
name: MD Agent
description: An MD agent
tools: tool2
---
MD Prompt
"""
    with open(custom_dir / "agent2.md", "w") as f:
        f.write(md_content)

    # Initialize registry with our custom temp directory
    registry = AgentRegistry(custom_agents_dir=custom_dir)

    assert "json-agent" in registry._custom_agents
    assert registry._custom_agents["json-agent"]["config"]["name"] == "JSON Agent"

    assert "agent2" in registry._custom_agents
    assert registry._custom_agents["agent2"]["config"]["name"] == "MD Agent"
    assert registry._custom_agents["agent2"]["config"]["system_prompt"] == "MD Prompt"
    assert registry._custom_agents["agent2"]["config"]["tools"] == ["tool2"]


def test_registry_get_agent_configurable():
    """Test that the registry can instantiate a ConfigurableAgent."""
    registry = AgentRegistry()
    registry._custom_agents["test-dynamic"] = {
        "type": "configurable",
        "config": {
            "name": "Dynamic Agent",
            "description": "Desc",
            "system_prompt": "Prompt",
            "tools": ["tool1"],
            "init_params": {"skip_lemonade": True},
        },
    }

    agent = registry.get_agent("test-dynamic")
    assert isinstance(agent, ConfigurableAgent)
    assert agent.agent_name == "Dynamic Agent"
    assert agent.base_system_prompt == "Prompt"
