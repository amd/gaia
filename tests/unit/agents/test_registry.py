# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for AgentRegistry discovery and model resolution.

YAML manifest agent support was removed in v0.17.5 (#912); the registry
now loads only Python agents (``agent.py``).  Tests for ``AgentManifest``
and ``_load_manifest_agent`` were removed alongside that change.
"""

import sys
import textwrap
import warnings

import pytest

from gaia.agents import registry as registry_module
from gaia.agents.registry import AgentRegistration, AgentRegistry

# ---------------------------------------------------------------------------
# Test isolation: clear sys.modules cache for dynamically loaded agent
# modules so reordering tests cannot leak state through ``importlib``.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _purge_custom_agent_modules():
    before = {k for k in sys.modules if k.startswith("gaia_custom_agent_")}
    yield
    after = {k for k in sys.modules if k.startswith("gaia_custom_agent_")}
    for name in after - before:
        sys.modules.pop(name, None)


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

    def test_companion_yaml_models_scalar_is_ignored(self, tmp_path):
        """A scalar `models:` value must not silently leak into the
        registration as a string (which would later be iterated as
        individual characters by `resolve_model`)."""
        agent_dir = tmp_path / "scalar-models"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(textwrap.dedent("""
            from gaia.agents.base.agent import Agent

            class ScalarAgent(Agent):
                AGENT_ID = "scalar-models"
                AGENT_NAME = "Scalar Models"
                def _get_system_prompt(self):
                    return "test"
                def _register_tools(self):
                    pass
        """))
        (agent_dir / "agent.yaml").write_text("models: Qwen3-0.6B-GGUF\n")

        registry = AgentRegistry()
        registry._load_python_agent(
            agent_dir, agent_dir / "agent.py", agent_dir / "agent.yaml"
        )

        reg = registry.get("scalar-models")
        assert reg is not None
        # Scalar models value must be rejected, leaving an empty list.
        assert reg.models == []

    def test_companion_yaml_models_non_string_entries_warn(self, tmp_path, caplog):
        """Non-string entries inside a list `models:` must be filtered with a
        visible warning so users notice their YAML is malformed, instead of
        silently dropping the bad values."""
        agent_dir = tmp_path / "mixed-models"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(textwrap.dedent("""
            from gaia.agents.base.agent import Agent

            class MixedAgent(Agent):
                AGENT_ID = "mixed-models"
                AGENT_NAME = "Mixed Models"
                def _get_system_prompt(self):
                    return "test"
                def _register_tools(self):
                    pass
        """))
        (agent_dir / "agent.yaml").write_text(
            "models:\n  - 123\n  - Qwen3-0.6B-GGUF\n  - null\n"
        )

        registry = AgentRegistry()
        with caplog.at_level("WARNING", logger="gaia.agents.registry"):
            registry._load_python_agent(
                agent_dir, agent_dir / "agent.py", agent_dir / "agent.yaml"
            )

        reg = registry.get("mixed-models")
        assert reg is not None
        # String entries are kept; ints / None are filtered.
        assert reg.models == ["Qwen3-0.6B-GGUF"]
        # And there's an explicit warning naming the dropped entries.
        assert any(
            "non-string entries" in rec.getMessage() for rec in caplog.records
        ), "expected a warning about non-string `models:` entries"


# ---------------------------------------------------------------------------
# Directory-based discovery
# ---------------------------------------------------------------------------


class TestDirectoryDiscovery:
    def test_no_agent_files_logs_warning(self, tmp_path):
        agent_dir = tmp_path / "empty-dir"
        agent_dir.mkdir()

        registry = AgentRegistry()
        # Should not raise, just skip
        registry._load_from_dir(agent_dir)
        assert len(registry.list()) == 0


# ---------------------------------------------------------------------------
# YAML-only directories: deprecation behavior (#912)
# ---------------------------------------------------------------------------


class TestYamlOnlyDeprecation:
    def _write_legacy_manifest(self, agent_dir, agent_id="legacy-bot"):
        (agent_dir / "agent.yaml").write_text(textwrap.dedent(f"""
            manifest_version: 1
            id: {agent_id}
            name: Legacy Bot
            tools:
              - rag
            instructions: You are a legacy assistant.
        """))

    def test_yaml_only_dir_emits_deprecation_warning_and_skips(self, tmp_path):
        agent_dir = tmp_path / "legacy-bot"
        agent_dir.mkdir()
        self._write_legacy_manifest(agent_dir)

        registry = AgentRegistry()
        with pytest.warns(DeprecationWarning, match="no longer supported"):
            registry._load_from_dir(agent_dir)

        # Registry must remain empty — the agent was skipped.
        assert registry.get("legacy-bot") is None
        assert registry.list() == []

    def test_yaml_only_dir_with_malformed_yaml_still_warns(self, tmp_path):
        agent_dir = tmp_path / "broken-bot"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text(": invalid: yaml: content: [")

        registry = AgentRegistry()
        with pytest.warns(DeprecationWarning, match="no longer supported"):
            # _load_from_dir checks existence only and never parses YAML in
            # the YAML-only branch, so malformed content must not raise.
            registry._load_from_dir(agent_dir)

        assert registry.list() == []


# ---------------------------------------------------------------------------
# Companion YAML guard: legacy manifest keys alongside agent.py (#912)
# ---------------------------------------------------------------------------


class TestCompanionYamlGuard:
    PYTHON_AGENT_SRC = textwrap.dedent("""
        from gaia.agents.base.agent import Agent

        class CompanionAgent(Agent):
            AGENT_ID = "companion-bot"
            AGENT_NAME = "Companion Bot"
            def _get_system_prompt(self):
                return "test"
            def _register_tools(self):
                pass
    """)

    def test_python_with_pure_models_yaml_does_not_warn(self, tmp_path):
        agent_dir = tmp_path / "pure-companion"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(self.PYTHON_AGENT_SRC)
        (agent_dir / "agent.yaml").write_text("models:\n  - Qwen3-0.6B-GGUF\n")

        registry = AgentRegistry()
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            registry._load_from_dir(agent_dir)

        deprecations = [
            w for w in recorded if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecations == [], (
            "pure models-only sidecar must not raise DeprecationWarning; "
            f"got: {[str(w.message) for w in deprecations]}"
        )
        reg = registry.get("companion-bot")
        assert reg is not None
        assert reg.source == "custom_python"
        assert reg.models == ["Qwen3-0.6B-GGUF"]

    def test_python_with_manifest_keys_in_yaml_warns(self, tmp_path):
        agent_dir = tmp_path / "manifest-keys-companion"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(self.PYTHON_AGENT_SRC)
        (agent_dir / "agent.yaml").write_text(textwrap.dedent("""
            models:
              - Qwen3-0.6B-GGUF
            tools:
              - rag
            instructions: You are ignored.
        """))

        registry = AgentRegistry()
        with pytest.warns(DeprecationWarning, match="manifest-style keys"):
            registry._load_from_dir(agent_dir)

        # Python class still wins; only `models:` is read from the YAML.
        reg = registry.get("companion-bot")
        assert reg is not None
        assert reg.source == "custom_python"
        assert reg.models == ["Qwen3-0.6B-GGUF"]


# ---------------------------------------------------------------------------
# Removed-symbol regression guard (#912)
# ---------------------------------------------------------------------------


class TestRemovedSymbols:
    @pytest.mark.parametrize(
        "name",
        [
            "AgentManifest",
            "_load_manifest_agent",
            "_create_manifest_agent_class",
            "_write_merged_mcp_config",
        ],
    )
    def test_manifest_symbol_removed_from_module(self, name):
        assert not hasattr(registry_module, name), (
            f"{name} was deleted in #912 and must not be re-added to the "
            "registry module"
        )

    @pytest.mark.parametrize(
        "name",
        [
            "_load_manifest_agent",
            "_create_manifest_agent_class",
            "_write_merged_mcp_config",
        ],
    )
    def test_manifest_method_removed_from_class(self, name):
        assert not hasattr(
            AgentRegistry, name
        ), f"AgentRegistry.{name} was deleted in #912"


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
