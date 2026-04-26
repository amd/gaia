# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for AgentRegistry discovery, manifest validation, and model resolution."""

import platform
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

# gaia-lite's primary model is platform-conditional:
# - macOS hits a Lemonade llama.cpp pin (lemonade-sdk/lemonade#1741) that lacks
#   gemma4 arch support, so it can't run Gemma 4 E4B. Gemma 3 4B is loadable
#   but emits Gemini-style tool_code text blocks that the agent runtime can't
#   parse — see eval run eval/results/eval-20260426-061705 (1/3 PASS, both
#   failures attributed to the tool-call format mismatch). macOS therefore
#   uses Qwen3.5-4B-GGUF, which carries the catalog "tool-calling" label.
# - Linux/Windows ship a llama.cpp bundle with gemma4 support, so Gemma 4
#   E4B (also tool-calling-labelled) remains the primary there.
_EXPECTED_PRIMARY = (
    "Qwen3.5-4B-GGUF" if platform.system() == "Darwin" else "Gemma-4-E4B-it-GGUF"
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

    # ---- gaia-lite (Mac/low-memory ChatAgent with 4B model) ----

    def test_gaia_lite_registered(self):
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("gaia-lite")
        assert reg is not None, "gaia-lite should be registered as a built-in"
        assert reg.source == "builtin"
        assert reg.hidden is False
        assert reg.name == "Gaia Lite"

    def test_gaia_lite_visible_in_list(self):
        registry = AgentRegistry()
        registry.discover()
        visible_ids = [r.id for r in registry.list() if not r.hidden]
        assert "gaia-lite" in visible_ids

    def test_gaia_lite_prefers_4b_model(self):
        """The UI reads ``models`` to show/validate the preferred checkpoint."""
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("gaia-lite")
        assert reg.models, "gaia-lite should list preferred models"
        # The primary preference must be a ~4B GGUF checkpoint — that's the
        # whole reason this agent exists alongside ChatAgent. We currently
        # ship Gemma 4 E4B as primary, with Gemma 3 4B as the fallback for
        # catalogs that haven't picked up the Gemma 4 drop yet.
        assert reg.models[0] == _EXPECTED_PRIMARY
        # Case-insensitive "4B" check — Gemma 3 uses lowercase "4b" in its
        # checkpoint name, Gemma 4 E4B uses uppercase. Both are ~4B models.
        assert all("4b" in m.lower() for m in reg.models), reg.models

    def test_gaia_lite_factory_presets_model_id(self):
        """Factory must preset ``model_id`` so ChatAgent skips the 35B default."""
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("gaia-lite")

        # Mock ChatAgent to avoid needing a live LLM, but let ChatAgentConfig
        # construct normally so we can read the resolved model_id off it.
        with patch("gaia.agents.chat.agent.ChatAgent") as mock_agent:
            reg.factory()  # no kwargs — factory must still set model_id
        mock_agent.assert_called_once()
        config = mock_agent.call_args.kwargs["config"]
        assert config.model_id == _EXPECTED_PRIMARY

    def test_gaia_lite_factory_respects_caller_override(self):
        """Explicit ``model_id`` from the caller wins over the preset default."""
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("gaia-lite")

        with patch("gaia.agents.chat.agent.ChatAgent") as mock_agent:
            reg.factory(model_id="Custom-Model-Override")
        config = mock_agent.call_args.kwargs["config"]
        assert config.model_id == "Custom-Model-Override"

    def test_chat_and_gaia_lite_coexist(self):
        """Creating gaia-lite must not perturb the default Chat Agent."""
        registry = AgentRegistry()
        registry.discover()
        assert registry.get("chat") is not None
        assert registry.get("gaia-lite") is not None
        # Distinct registrations — not aliases.
        assert registry.get("chat") is not registry.get("gaia-lite")
        # Chat must keep an empty models preference list (unchanged default).
        assert registry.get("chat").models == []

    def test_gaia_lite_declares_memory_requirement(self):
        """gaia-lite should declare min_memory_gb so the UI can warn."""
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("gaia-lite")
        # Gemma 4 E4B Q4 GGUF is ~2.7 GB on disk; 5 GB free is the
        # comfortable load floor (weights + KV cache + runtime overhead).
        assert reg.min_memory_gb == 5.0

    def test_legacy_chat_lite_id_resolves_to_gaia_lite(self):
        """Persisted sessions with the old ``chat-lite`` agent_type must still work.

        The registry should resolve the legacy ID to the renamed ``gaia-lite``
        registration so we don't need a database migration for existing UI
        sessions.
        """
        registry = AgentRegistry()
        registry.discover()
        aliased = registry.get("chat-lite")
        canonical = registry.get("gaia-lite")
        assert aliased is not None, "chat-lite alias should resolve"
        assert (
            aliased is canonical
        ), "chat-lite should alias to the same registration as gaia-lite"

    def test_legacy_chat_lite_create_agent_routes_to_gaia_lite(self):
        """``create_agent('chat-lite')`` should build the renamed agent."""
        registry = AgentRegistry()
        registry.discover()
        with patch("gaia.agents.chat.agent.ChatAgent") as mock_agent:
            registry.create_agent("chat-lite")
        mock_agent.assert_called_once()
        config = mock_agent.call_args.kwargs["config"]
        # Same ~4B preset as gaia-lite — confirming the alias hit.
        assert config.model_id == _EXPECTED_PRIMARY

    def test_legacy_chat_lite_resolve_model_returns_4b(self):
        """``resolve_model('chat-lite')`` must honour the alias.

        Regression: before routing ``resolve_model`` through ``get()``, a
        session stored with ``agent_type='chat-lite'`` would bypass the
        registration lookup, return ``None``, and fall through to whatever
        35B default was loaded — silently defeating the 4B preset that's
        the whole reason gaia-lite exists.
        """
        registry = AgentRegistry()
        registry.discover()
        # Supply an explicit model list to keep the test hermetic (no
        # Lemonade HTTP call).
        resolved = registry.resolve_model(
            "chat-lite",
            available_models=[_EXPECTED_PRIMARY, "Something-Else"],
        )
        assert resolved == _EXPECTED_PRIMARY

    def test_canonical_id_maps_aliases_and_passes_through_known_ids(self):
        """``canonical_id`` is the single source of alias truth."""
        registry = AgentRegistry()
        registry.discover()
        assert registry.canonical_id("chat-lite") == "gaia-lite"
        # Known IDs pass through unchanged.
        assert registry.canonical_id("gaia-lite") == "gaia-lite"
        assert registry.canonical_id("chat") == "chat"
        # Unknown IDs pass through so callers can surface the miss themselves
        # (``get`` returns ``None``, ``create_agent`` raises ValueError).
        assert registry.canonical_id("unknown-agent") == "unknown-agent"

    def test_chat_has_no_memory_requirement_by_default(self):
        """Chat (35B default) leaves min_memory_gb unset — existing behaviour."""
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("chat")
        assert reg.min_memory_gb is None


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

    def test_manifest_min_memory_gb_propagates(self, tmp_path):
        """min_memory_gb in YAML must reach the registration so the UI can warn."""
        agent_dir = tmp_path / "hungry-bot"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(textwrap.dedent("""
            id: hungry-bot
            name: Hungry Bot
            min_memory_gb: 12.5
        """))

        registry = AgentRegistry()
        registry._load_manifest_agent(agent_dir, agent_dir / "agent.yaml")

        reg = registry.get("hungry-bot")
        assert reg.min_memory_gb == 12.5

    def test_manifest_min_memory_gb_defaults_to_none(self, tmp_path):
        """Manifests without min_memory_gb leave the registration's value as None."""
        agent_dir = tmp_path / "lazy-bot"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(textwrap.dedent("""
            id: lazy-bot
            name: Lazy Bot
        """))

        registry = AgentRegistry()
        registry._load_manifest_agent(agent_dir, agent_dir / "agent.yaml")

        reg = registry.get("lazy-bot")
        assert reg.min_memory_gb is None

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
