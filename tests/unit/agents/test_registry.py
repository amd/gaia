# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for AgentRegistry discovery, manifest validation, and model resolution."""

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.registry import (
    KNOWN_TOOLS,
    AgentManifest,
    AgentRegistration,
    AgentRegistry,
)

# ---------------------------------------------------------------------------
# AgentManifest validation
# ---------------------------------------------------------------------------


class TestAgentManifest:
    def test_valid_manifest(self):
        m = AgentManifest(id="test", name="Test Agent")
        assert m.id == "test"
        assert m.name == "Test Agent"
        assert m.tools == ["rag", "file_search"]
        assert m.models == []

    def test_empty_id_rejected(self):
        with pytest.raises(Exception):
            AgentManifest(id="", name="Test")

    def test_whitespace_id_rejected(self):
        with pytest.raises(Exception):
            AgentManifest(id="   ", name="Test")

    def test_unknown_tool_rejected(self):
        with pytest.raises(Exception):
            AgentManifest(id="test", name="Test", tools=["nonexistent_tool"])

    def test_valid_tools_accepted(self):
        m = AgentManifest(id="test", name="Test", tools=["rag"])
        assert m.tools == ["rag"]

    def test_all_known_tools_accepted(self):
        m = AgentManifest(id="test", name="Test", tools=list(KNOWN_TOOLS.keys()))
        assert set(m.tools) == set(KNOWN_TOOLS.keys())

    def test_conversation_starters(self):
        m = AgentManifest(
            id="test", name="Test", conversation_starters=["Hello!", "Help me"]
        )
        assert m.conversation_starters == ["Hello!", "Help me"]

    def test_mcp_servers(self):
        servers = {"my-server": {"command": "npx", "args": ["-y", "@test/server"]}}
        m = AgentManifest(id="test", name="Test", mcp_servers=servers)
        assert m.mcp_servers == servers


# ---------------------------------------------------------------------------
# Built-in agent registration
# ---------------------------------------------------------------------------


class TestBuiltinRegistration:
    def test_chat_always_registered(self):
        registry = AgentRegistry()
        registry.discover()
        assert registry.get("chat") is not None

    def test_chat_source_is_builtin(self):
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("chat")
        assert reg.source == "builtin"

    def test_chat_has_conversation_starters(self):
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("chat")
        assert len(reg.conversation_starters) > 0

    def test_list_returns_all_builtins(self):
        registry = AgentRegistry()
        registry.discover()
        ids = [r.id for r in registry.list()]
        assert "chat" in ids

    def test_unknown_agent_returns_none(self):
        registry = AgentRegistry()
        registry.discover()
        assert registry.get("nonexistent-agent-xyz") is None

    def test_create_unknown_agent_raises(self):
        registry = AgentRegistry()
        registry.discover()
        with pytest.raises(ValueError, match="Unknown agent ID"):
            registry.create_agent("nonexistent-agent-xyz")

    def test_builder_registered_as_hidden_builtin(self):
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("builder")
        assert reg is not None, "BuilderAgent should be registered"
        assert reg.hidden is True
        assert reg.source == "builtin"

    def test_builder_not_in_visible_list(self):
        registry = AgentRegistry()
        registry.discover()
        visible_ids = [r.id for r in registry.list() if not r.hidden]
        assert "builder" not in visible_ids


# ---------------------------------------------------------------------------
# YAML manifest agent loading
# ---------------------------------------------------------------------------


class TestManifestAgentLoading:
    def test_load_simple_manifest(self, tmp_path):
        agent_dir = tmp_path / "simple-bot"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(textwrap.dedent("""
            manifest_version: 1
            id: simple-bot
            name: Simple Bot
            description: A simple test bot
            instructions: You are a test assistant.
            tools:
              - rag
              - file_search
            conversation_starters:
              - Hello!
        """))

        registry = AgentRegistry()
        with patch.object(Path, "home", return_value=tmp_path):
            with patch("gaia.agents.registry.Path.home", return_value=tmp_path):
                registry._load_manifest_agent(agent_dir, agent_dir / "agent.yaml")

        reg = registry.get("simple-bot")
        assert reg is not None
        assert reg.name == "Simple Bot"
        assert reg.source == "custom_manifest"
        assert reg.conversation_starters == ["Hello!"]

    def test_manifest_with_models(self, tmp_path):
        agent_dir = tmp_path / "model-bot"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(textwrap.dedent("""
            id: model-bot
            name: Model Bot
            models:
              - Qwen3.5-35B-A3B-GGUF
              - Qwen3-0.6B-GGUF
        """))

        registry = AgentRegistry()
        registry._load_manifest_agent(agent_dir, agent_dir / "agent.yaml")

        reg = registry.get("model-bot")
        assert reg.models == ["Qwen3.5-35B-A3B-GGUF", "Qwen3-0.6B-GGUF"]

    def test_manifest_prompt_used_as_system_prompt(self, tmp_path):
        agent_dir = tmp_path / "prompt-bot"
        agent_dir.mkdir()
        manifest = AgentManifest(
            id="prompt-bot",
            name="Prompt Bot",
            instructions="You are a specialized test assistant with unique instructions.",
            tools=["rag"],
        )

        registry = AgentRegistry()
        klass = registry._create_manifest_agent_class(manifest, agent_dir)
        # Call the unbound _get_system_prompt with a dummy self
        instance = object.__new__(klass)
        prompt = klass._get_system_prompt(instance)
        assert (
            prompt == "You are a specialized test assistant with unique instructions."
        )

    def test_manifest_invalid_yaml_raises(self, tmp_path):
        agent_dir = tmp_path / "bad-bot"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(": invalid: yaml: content: [")

        registry = AgentRegistry()
        with pytest.raises(Exception):
            registry._load_manifest_agent(agent_dir, agent_dir / "agent.yaml")

    def test_manifest_unknown_tool_raises(self, tmp_path):
        agent_dir = tmp_path / "bad-tool-bot"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(textwrap.dedent("""
            id: bad-tool-bot
            name: Bad Tool Bot
            tools:
              - nonexistent_tool_xyz
        """))

        registry = AgentRegistry()
        with pytest.raises(Exception):
            registry._load_manifest_agent(agent_dir, agent_dir / "agent.yaml")


# ---------------------------------------------------------------------------
# Python agent loading
# ---------------------------------------------------------------------------


class TestPythonAgentLoading:
    def test_load_python_agent(self, tmp_path):
        agent_dir = tmp_path / "custom--agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(textwrap.dedent("""
            from gaia.agents.base.agent import Agent

            class MyCustomAgent(Agent):
                AGENT_ID = "custom/agent"
                AGENT_NAME = "Custom Agent"
                AGENT_DESCRIPTION = "A custom test agent"
                CONVERSATION_STARTERS = ["Hello from custom"]

                def _get_system_prompt(self):
                    return "Custom system prompt"

                def _register_tools(self):
                    from gaia.agents.base.tools import _TOOL_REGISTRY
                    _TOOL_REGISTRY.clear()
        """))

        registry = AgentRegistry()
        registry._load_python_agent(agent_dir, agent_dir / "agent.py", None)

        reg = registry.get("custom/agent")
        assert reg is not None
        assert reg.name == "Custom Agent"
        assert reg.source == "custom_python"
        assert reg.conversation_starters == ["Hello from custom"]

    def test_python_agent_without_required_attrs_raises(self, tmp_path):
        agent_dir = tmp_path / "missing-attrs"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(textwrap.dedent("""
            from gaia.agents.base.agent import Agent

            class IncompleteAgent(Agent):
                # Missing AGENT_ID and AGENT_NAME
                def _get_system_prompt(self):
                    return "test"
                def _register_tools(self):
                    pass
        """))

        registry = AgentRegistry()
        with pytest.raises(ValueError, match="No Agent subclass"):
            registry._load_python_agent(agent_dir, agent_dir / "agent.py", None)

    def test_python_agent_reads_companion_yaml_for_models(self, tmp_path):
        agent_dir = tmp_path / "python-with-yaml"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(textwrap.dedent("""
            from gaia.agents.base.agent import Agent

            class YamlCompanionAgent(Agent):
                AGENT_ID = "yaml-companion"
                AGENT_NAME = "YAML Companion"
                def _get_system_prompt(self):
                    return "test"
                def _register_tools(self):
                    pass
        """))
        (agent_dir / "agent.yaml").write_text(textwrap.dedent("""
            models:
              - Qwen3.5-35B-A3B-GGUF
        """))

        registry = AgentRegistry()
        registry._load_python_agent(
            agent_dir, agent_dir / "agent.py", agent_dir / "agent.yaml"
        )

        reg = registry.get("yaml-companion")
        assert reg.models == ["Qwen3.5-35B-A3B-GGUF"]


# ---------------------------------------------------------------------------
# Directory-based discovery
# ---------------------------------------------------------------------------


class TestDirectoryDiscovery:
    def test_python_takes_precedence_over_yaml(self, tmp_path):
        agent_dir = tmp_path / "dual-agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(textwrap.dedent("""
            from gaia.agents.base.agent import Agent

            class DualAgent(Agent):
                AGENT_ID = "dual/agent"
                AGENT_NAME = "Python Dual Agent"
                def _get_system_prompt(self):
                    return "From Python"
                def _register_tools(self):
                    pass
        """))
        (agent_dir / "agent.yaml").write_text(textwrap.dedent("""
            id: yaml-dual-agent
            name: YAML Dual Agent
        """))

        registry = AgentRegistry()
        registry._load_from_dir(agent_dir)

        # Python agent wins
        assert registry.get("dual/agent") is not None
        assert registry.get("dual/agent").source == "custom_python"
        # YAML agent not loaded
        assert registry.get("yaml-dual-agent") is None

    def test_no_agent_files_logs_warning(self, tmp_path):
        agent_dir = tmp_path / "empty-dir"
        agent_dir.mkdir()

        registry = AgentRegistry()
        # Should not raise, just skip
        registry._load_from_dir(agent_dir)
        assert len(registry.list()) == 0


# ---------------------------------------------------------------------------
# Model preference resolution
# ---------------------------------------------------------------------------


class TestModelResolution:
    def test_first_available_model_returned(self):
        registry = AgentRegistry()
        registry._register(
            AgentRegistration(
                id="test-agent",
                name="Test",
                description="",
                source="builtin",
                conversation_starters=[],
                factory=lambda **kw: None,
                agent_dir=None,
                models=["ModelA", "ModelB", "ModelC"],
            )
        )
        result = registry.resolve_model(
            "test-agent", available_models=["ModelB", "ModelC"]
        )
        assert result == "ModelB"

    def test_none_returned_when_no_models_match(self):
        registry = AgentRegistry()
        registry._register(
            AgentRegistration(
                id="test-agent",
                name="Test",
                description="",
                source="builtin",
                conversation_starters=[],
                factory=lambda **kw: None,
                agent_dir=None,
                models=["ModelX"],
            )
        )
        result = registry.resolve_model(
            "test-agent", available_models=["ModelA", "ModelB"]
        )
        assert result is None

    def test_none_returned_when_no_preferred_models(self):
        registry = AgentRegistry()
        registry._register(
            AgentRegistration(
                id="test-agent",
                name="Test",
                description="",
                source="builtin",
                conversation_starters=[],
                factory=lambda **kw: None,
                agent_dir=None,
                models=[],
            )
        )
        result = registry.resolve_model("test-agent", available_models=["ModelA"])
        assert result is None

    def test_unknown_agent_returns_none(self):
        registry = AgentRegistry()
        result = registry.resolve_model("nonexistent", available_models=["ModelA"])
        assert result is None
