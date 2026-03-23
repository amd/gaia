# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for Agent Registry with YAML support.

Tests verify that custom agents can be loaded from:
- JSON files (.json)
- YAML files (.yml, .yaml)
- Markdown files (.md with YAML frontmatter)
"""

import tempfile
from pathlib import Path

from gaia.api.agent_registry import AgentRegistry


class TestYamlAgentLoading:
    """Test YAML agent configuration loading."""

    def test_load_yaml_agent_basic(self):
        """Test loading a basic YAML agent configuration."""
        yaml_content = """
name: TestAgent
description: A test agent
tools:
  - list_dir
  - view_file
system_prompt: |
  You are a test agent.
  Follow these steps:
  1. Analyze the request
  2. Use tools to find information
  3. Provide a clear response
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_file = Path(tmpdir) / "test_agent.yml"
            yaml_file.write_text(yaml_content)

            registry = AgentRegistry(custom_agents_dir=Path(tmpdir))

            assert "test_agent" in registry._custom_agents
            agent_config = registry._custom_agents["test_agent"]
            assert agent_config["config"]["name"] == "TestAgent"
            assert agent_config["config"]["description"] == "A test agent"
            assert "list_dir" in agent_config["config"]["tools"]
            assert "view_file" in agent_config["config"]["tools"]

    def test_load_yaml_agent_with_nested_structures(self):
        """Test loading YAML agent with nested persona and init_params."""
        yaml_content = """
name: AdvancedAgent
description: Agent with nested configuration
id: gaia-advanced
tools:
  - search_web
  - list_dir
system_prompt: |
  You are an advanced agent.
persona:
  style: Analytical and thorough
  focus: Complex problem solving
  tone: Professional
init_params:
  max_steps: 50
  silent_mode: false
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_file = Path(tmpdir) / "advanced.yml"
            yaml_file.write_text(yaml_content)

            registry = AgentRegistry(custom_agents_dir=Path(tmpdir))

            assert "gaia-advanced" in registry._custom_agents
            agent_config = registry._custom_agents["gaia-advanced"]
            assert agent_config["config"]["name"] == "AdvancedAgent"
            assert (
                agent_config["config"]["persona"]["style"] == "Analytical and thorough"
            )
            assert agent_config["config"]["init_params"]["max_steps"] == 50

    def test_load_yaml_agent_with_comments(self):
        """Test that YAML comments are properly ignored."""
        yaml_content = """
# This is a comment
name: CommentedAgent
description: Agent with comments
# Another comment
tools:
  - list_dir  # inline comment
system_prompt: |
  You are an agent.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_file = Path(tmpdir) / "commented.yml"
            yaml_file.write_text(yaml_content)

            registry = AgentRegistry(custom_agents_dir=Path(tmpdir))

            assert "commented" in registry._custom_agents

    def test_load_yaml_agent_yaml_error(self):
        """Test handling of invalid YAML syntax."""
        yaml_content = """
name: InvalidAgent
description: Invalid YAML
  tools:
    - list_dir  # Bad indentation
system_prompt: Test
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_file = Path(tmpdir) / "invalid.yml"
            yaml_file.write_text(yaml_content)

            # Should not raise, but should log error and skip agent
            registry = AgentRegistry(custom_agents_dir=Path(tmpdir))

            # Invalid YAML should be skipped
            assert "invalid" not in registry._custom_agents

    def test_load_yaml_agent_empty_file(self):
        """Test handling of empty YAML file."""
        yaml_content = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_file = Path(tmpdir) / "empty.yml"
            yaml_file.write_text(yaml_content)

            registry = AgentRegistry(custom_agents_dir=Path(tmpdir))

            # Empty file should be skipped
            assert "empty" not in registry._custom_agents

    def test_load_yaml_vs_json_equivalence(self):
        """Test that YAML and JSON produce equivalent agent configs."""
        yaml_content = """
name: EquivalentAgent
description: Same config in YAML
tools:
  - tool1
  - tool2
system_prompt: |
  You are an agent.
init_params:
  max_steps: 25
"""
        json_content = """{
  "name": "EquivalentAgent",
  "description": "Same config in JSON",
  "tools": ["tool1", "tool2"],
  "system_prompt": "You are an agent.",
  "init_params": {"max_steps": 25}
}"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            (tmpdir / "yaml_agent.yml").write_text(yaml_content)
            (tmpdir / "json_agent.json").write_text(json_content)

            registry = AgentRegistry(custom_agents_dir=tmpdir)

            yaml_config = registry._custom_agents["yaml_agent"]["config"]
            json_config = registry._custom_agents["json_agent"]["config"]

            assert yaml_config["name"] == json_config["name"]
            assert yaml_config["description"] == json_config["description"]
            assert yaml_config["tools"] == json_config["tools"]

    def test_load_yaml_agent_file_extension_variants(self):
        """Test that both .yml and .yaml extensions are supported."""
        yaml_content = """
name: YmlAgent
description: Agent with .yml extension
tools: []
system_prompt: Test
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Test .yml extension
            (tmpdir / "agent1.yml").write_text(yaml_content)
            registry1 = AgentRegistry(custom_agents_dir=tmpdir)
            assert "agent1" in registry1._custom_agents

            # Test .yaml extension
            (tmpdir / "agent2.yaml").write_text(yaml_content)
            registry2 = AgentRegistry(custom_agents_dir=tmpdir)
            assert "agent2" in registry2._custom_agents


class TestMarkdownAgentLoading:
    """Test Markdown agent configuration loading (existing functionality)."""

    def test_load_markdown_agent_frontmatter(self):
        """Test loading Markdown agent with YAML frontmatter."""
        md_content = """---
name: MarkdownAgent
description: Agent in Markdown format
tools: list_dir, view_file
---

You are a Markdown agent.
Your system prompt is in the content area.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_file = Path(tmpdir) / "markdown_agent.md"
            md_file.write_text(md_content)

            registry = AgentRegistry(custom_agents_dir=Path(tmpdir))

            assert "markdown_agent" in registry._custom_agents
            agent_config = registry._custom_agents["markdown_agent"]
            assert agent_config["config"]["name"] == "MarkdownAgent"
            # Tools should be parsed as list from comma-separated string
            assert "list_dir" in agent_config["config"]["tools"]


class TestAgentRegistryScanning:
    """Test the agent scanning functionality."""

    def test_scan_mixed_formats(self):
        """Test scanning directory with mixed agent formats."""
        yaml_content = """
name: YamlAgent
description: YAML agent
tools: []
system_prompt: YAML
"""
        json_content = """{
  "name": "JsonAgent",
  "description": "JSON agent",
  "tools": [],
  "system_prompt": "JSON"
}"""
        md_content = """---
name: MdAgent
description: Markdown agent
tools: []
---

Markdown content
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            (tmpdir / "yaml.yml").write_text(yaml_content)
            (tmpdir / "json.json").write_text(json_content)
            (tmpdir / "markdown.md").write_text(md_content)

            registry = AgentRegistry(custom_agents_dir=tmpdir)

            # All three formats should be loaded
            assert "yaml" in registry._custom_agents
            assert "json" in registry._custom_agents
            assert "markdown" in registry._custom_agents
