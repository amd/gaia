# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for AgentRegistry discovery and model resolution.

YAML manifest agent support was removed in v0.17.5 (#912); the registry
now loads only Python agents (``agent.py``).  Tests for ``AgentManifest``
and ``_load_manifest_agent`` were removed alongside that change.
"""

import platform
import sys
import textwrap
import warnings
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.agents import registry as registry_module
from gaia.agents.registry import AgentRegistration, AgentRegistry

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
    # chat/doc/file ship as the standalone gaia-agent-chat wheel (#1102),
    # discovered via the gaia.agent entry point. The chat-specific tests below
    # importorskip the wheel so a framework-only env tolerates its absence.
    def test_chat_registered_when_wheel_installed(self):
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        assert registry.get("chat") is not None

    def test_chat_source_is_installed(self):
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("chat")
        assert reg.source == "installed"

    def test_chat_has_conversation_starters(self):
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("chat")
        assert len(reg.conversation_starters) > 0

    def test_list_returns_all_builtins(self):
        pytest.importorskip("gaia_agent_chat")
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

    # ---- Consolidated model tiers (#1162) ----
    # The former per-agent "-lite" registrations (chat-lite, doc-lite, …) and
    # gaia-lite were collapsed into a "lite" model TIER of the single base
    # agent. The old IDs survive only as legacy aliases.

    # chat/doc/file (ChatAgent profiles), data (AnalystAgent) and web
    # (BrowserAgent) all now ship as standalone hub wheels (#1102); their
    # full+lite tiers are verified in those packages' own tests. The
    # framework-only suite asserts tiers on chat/doc/file only when the
    # gaia-agent-chat wheel is installed (importorskip below).
    _BASE_AGENTS = ["chat", "doc", "file"]
    _LEGACY_LITE_IDS = [
        "chat-lite",
        "doc-lite",
        "file-lite",
        "data-lite",
        "web-lite",
        "gaia-lite",
    ]

    def test_no_separate_lite_registrations(self):
        """Each agent appears ONCE — no duplicate ``-lite`` cards (#1162)."""
        registry = AgentRegistry()
        registry.discover()
        ids = {r.id for r in registry.list()}
        for legacy in self._LEGACY_LITE_IDS:
            assert legacy not in ids, f"{legacy} must no longer be its own card"

    def test_base_agents_declare_full_and_lite_tiers(self):
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        for agent_id in self._BASE_AGENTS:
            reg = registry.get(agent_id)
            assert reg is not None, agent_id
            tier_names = [t.name for t in reg.model_tiers]
            assert tier_names == ["full", "lite"], (agent_id, tier_names)
            full, lite = reg.model_tiers
            # Full tier carries no model list — the agent's own default wins.
            assert full.models == []
            assert full.default is True
            # Lite tier pins the ~4B preset and a memory floor.
            assert lite.models[0] == _EXPECTED_PRIMARY
            assert all("4b" in m.lower() for m in lite.models), lite.models
            assert lite.min_memory_gb == 5.0

    def test_legacy_lite_ids_resolve_to_base_agent(self):
        registry = AgentRegistry()
        registry.discover()
        expected = {
            "chat-lite": "chat",
            "doc-lite": "doc",
            "file-lite": "file",
            "data-lite": "data",
            "web-lite": "web",
            # gaia-lite historically aliased doc-lite.
            "gaia-lite": "doc",
        }
        for legacy, base in expected.items():
            # canonical_id is a pure alias mapping — holds whether or not the
            # base agent's wheel is installed.
            assert registry.canonical_id(legacy) == base
            # data/web ship as standalone wheels (#1102); only assert the
            # registration resolves when the base agent is actually registered.
            if base in {r.id for r in registry.list()}:
                reg = registry.get(legacy)
                assert reg is not None and reg.id == base

    def test_legacy_chat_lite_create_agent_uses_lite_tier(self):
        """``create_agent('chat-lite')`` builds chat with the ~4B preset."""
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        with patch("gaia_agent_chat.agent.ChatAgent") as mock_agent:
            registry.create_agent("chat-lite")
        mock_agent.assert_called_once()
        config = mock_agent.call_args.kwargs["config"]
        assert config.model_id == _EXPECTED_PRIMARY
        assert config.prompt_profile == "chat"

    def test_legacy_gaia_lite_create_agent_uses_doc_profile(self):
        """gaia-lite resolves to the doc agent on the lite tier."""
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        with patch("gaia_agent_chat.agent.ChatAgent") as mock_agent:
            registry.create_agent("gaia-lite")
        config = mock_agent.call_args.kwargs["config"]
        assert config.model_id == _EXPECTED_PRIMARY
        assert config.prompt_profile == "doc"

    def test_explicit_model_id_overrides_lite_tier(self):
        """A caller-pinned ``model_id`` wins over the alias's lite preset."""
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        with patch("gaia_agent_chat.agent.ChatAgent") as mock_agent:
            registry.create_agent("chat-lite", model_id="Custom-Model-Override")
        config = mock_agent.call_args.kwargs["config"]
        assert config.model_id == "Custom-Model-Override"

    def test_explicit_model_tier_selects_lite_on_base_id(self):
        """The base id + ``model_tier='lite'`` selects the ~4B preset (#1162)."""
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        with patch("gaia_agent_chat.agent.ChatAgent") as mock_agent:
            registry.create_agent("chat", model_tier="lite")
        config = mock_agent.call_args.kwargs["config"]
        assert config.model_id == _EXPECTED_PRIMARY

    def test_base_chat_create_agent_uses_default_model(self):
        """Plain ``chat`` (full tier) leaves model_id unset for the agent default."""
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        with patch("gaia_agent_chat.agent.ChatAgent") as mock_agent:
            registry.create_agent("chat")
        config = mock_agent.call_args.kwargs["config"]
        assert config.model_id is None

    def test_legacy_chat_lite_resolve_model_returns_4b(self):
        """``resolve_model('chat-lite')`` must honour the alias's lite tier.

        Regression: a session stored with ``agent_type='chat-lite'`` must
        still resolve to the 4B preset even though the base ``chat`` agent
        carries no top-level ``models`` preference (#1162).
        """
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        resolved = registry.resolve_model(
            "chat-lite",
            available_models=[_EXPECTED_PRIMARY, "Something-Else"],
        )
        assert resolved == _EXPECTED_PRIMARY

    def test_legacy_gaia_lite_resolve_model_returns_4b(self):
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        resolved = registry.resolve_model(
            "gaia-lite",
            available_models=[_EXPECTED_PRIMARY, "Something-Else"],
        )
        assert resolved == _EXPECTED_PRIMARY

    def test_base_chat_resolve_model_returns_none(self):
        """The full tier defers to the agent default — resolve_model is None."""
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        resolved = registry.resolve_model("chat", available_models=[_EXPECTED_PRIMARY])
        assert resolved is None

    def test_canonical_id_maps_aliases_and_passes_through_known_ids(self):
        registry = AgentRegistry()
        registry.discover()
        # Known IDs pass through unchanged.
        assert registry.canonical_id("chat") == "chat"
        # Legacy lite IDs resolve to their base agent.
        assert registry.canonical_id("chat-lite") == "chat"
        assert registry.canonical_id("gaia-lite") == "doc"
        # Unknown IDs pass through so callers can surface the miss themselves
        # (``get`` returns ``None``, ``create_agent`` raises ValueError).
        assert registry.canonical_id("unknown-agent") == "unknown-agent"

    def test_chat_has_no_memory_requirement_by_default(self):
        """Chat (full tier) leaves min_memory_gb unset — existing behaviour."""
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("chat")
        assert reg.min_memory_gb is None


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
# Installed wheel entry point discovery
# ---------------------------------------------------------------------------


class TestEntryPointDiscovery:
    def _entry_point(self, name, loaded):
        return SimpleNamespace(
            name=name, value=f"{name}:build_registration", load=lambda: loaded
        )

    def _entry_points(self, entry_point):
        return lambda group: (
            [entry_point] if group == registry_module.AGENT_ENTRY_POINT_GROUP else []
        )

    def test_discovers_agent_registration_entry_point(self, monkeypatch):
        registration = AgentRegistration(
            id="hub-chat",
            name="Hub Chat",
            description="Standalone hub agent",
            source="installed",
            conversation_starters=["Hello"],
            factory=lambda **kw: SimpleNamespace(kind="created"),
            agent_dir=None,
            models=["Qwen3.5-35B-A3B-GGUF"],
            category="conversation",
            tags=["hub"],
            icon="message-circle",
            tools_count=1,
        )
        entry_point = self._entry_point("hub-chat", lambda: registration)
        monkeypatch.setattr(
            registry_module.importlib.metadata,
            "entry_points",
            self._entry_points(entry_point),
        )

        registry = AgentRegistry()
        registry._discover_installed_agents()

        reg = registry.get("hub-chat")
        assert reg is not None
        assert reg.name == "Hub Chat"
        assert reg.source == "installed"
        assert reg.namespaced_agent_id == "installed:hub-chat"
        agent = registry.create_agent("hub-chat")
        assert agent.kind == "created"
        assert agent._gaia_namespaced_agent_id == "installed:hub-chat"

    def test_entry_point_does_not_override_existing_agent(self, monkeypatch):
        existing = AgentRegistration(
            id="chat",
            name="Existing Chat",
            description="Existing registration",
            source="builtin",
            conversation_starters=[],
            factory=lambda **kw: "existing",
            agent_dir=None,
            models=[],
            namespaced_agent_id="builtin:chat",
        )
        replacement = AgentRegistration(
            id="chat",
            name="Replacement Chat",
            description="Should not replace existing registrations",
            source="installed",
            conversation_starters=[],
            factory=lambda **kw: "replacement",
            agent_dir=None,
            models=[],
        )
        entry_point = self._entry_point("chat", lambda: replacement)
        monkeypatch.setattr(
            registry_module.importlib.metadata,
            "entry_points",
            self._entry_points(entry_point),
        )

        registry = AgentRegistry()
        registry._register(existing)
        registry._discover_installed_agents()

        assert registry.get("chat").name == "Existing Chat"
        assert registry.create_agent("chat") == "existing"

    def test_bad_entry_point_is_skipped_with_warning(self, monkeypatch, caplog):
        entry_point = self._entry_point("broken-agent", object())
        monkeypatch.setattr(
            registry_module.importlib.metadata,
            "entry_points",
            self._entry_points(entry_point),
        )

        registry = AgentRegistry()
        with caplog.at_level("WARNING", logger="gaia.agents.registry"):
            registry._discover_installed_agents()

        assert registry.get("broken-agent") is None
        assert "Failed to load agent entry point broken-agent" in caplog.text

    def test_entry_point_namespaced_agent_id_is_coerced_to_installed(self, monkeypatch):
        registration = AgentRegistration(
            id="hub-chat",
            name="Hub Chat",
            description="Standalone hub agent",
            source="installed",
            conversation_starters=[],
            factory=lambda **kw: "created",
            agent_dir=None,
            models=[],
            namespaced_agent_id="builtin:chat",
        )
        entry_point = self._entry_point("hub-chat", lambda: registration)
        monkeypatch.setattr(
            registry_module.importlib.metadata,
            "entry_points",
            self._entry_points(entry_point),
        )

        registry = AgentRegistry()
        registry._discover_installed_agents()

        assert registry.get("hub-chat").namespaced_agent_id == "installed:hub-chat"

    def test_entry_point_source_is_coerced_to_installed(self, monkeypatch):
        registration = AgentRegistration(
            id="hub-chat",
            name="Hub Chat",
            description="Standalone hub agent",
            source="builtin",
            conversation_starters=[],
            factory=lambda **kw: "created",
            agent_dir=None,
            models=[],
        )
        entry_point = self._entry_point("hub-chat", lambda: registration)
        monkeypatch.setattr(
            registry_module.importlib.metadata,
            "entry_points",
            self._entry_points(entry_point),
        )

        registry = AgentRegistry()
        registry._discover_installed_agents()

        assert registry.get("hub-chat").source == "installed"

    def test_discovers_direct_agent_registration_entry_point(self, monkeypatch):
        registration = AgentRegistration(
            id="direct-agent",
            name="Direct",
            description="Loaded directly, not via callable",
            source="installed",
            conversation_starters=[],
            factory=lambda **kw: SimpleNamespace(kind="direct"),
            agent_dir=None,
            models=[],
        )
        entry_point = self._entry_point("direct-agent", registration)
        monkeypatch.setattr(
            registry_module.importlib.metadata,
            "entry_points",
            self._entry_points(entry_point),
        )

        registry = AgentRegistry()
        registry._discover_installed_agents()

        assert registry.get("direct-agent") is not None


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


# ---------------------------------------------------------------------------
# Builder model-preference contract (#2243)
#
# BuilderAgent currently hardcodes model_id="Qwen3.5-35B-A3B-GGUF" and fails
# outright on any machine that hasn't installed that specific 35B model.
# These tests are written against the accepted interface contract for the
# fix, which is not yet implemented — they are expected to fail red
# (ImportError/AttributeError on missing names), not to pass.
# ---------------------------------------------------------------------------


class TestBuilderModelPreferences:
    def test_resolve_preferred_model_returns_first_match_in_order(self):
        from gaia.agents.registry import resolve_preferred_model

        preferred = ["Qwen3.5-35B-A3B-GGUF", "Gemma-4-E4B-it-GGUF", "gemma4-it-e2b-FLM"]
        result = resolve_preferred_model(
            preferred, available_models=["gemma4-it-e2b-FLM", "Gemma-4-E4B-it-GGUF"]
        )
        assert result == "Gemma-4-E4B-it-GGUF"

    def test_resolve_preferred_model_returns_none_when_no_match(self):
        from gaia.agents.registry import resolve_preferred_model

        result = resolve_preferred_model(
            ["Qwen3.5-35B-A3B-GGUF"], available_models=["Something-Else"]
        )
        assert result is None

    def test_resolve_preferred_model_returns_none_for_empty_available(self):
        from gaia.agents.registry import resolve_preferred_model

        result = resolve_preferred_model(
            ["Qwen3.5-35B-A3B-GGUF", "gemma4-it-e2b-FLM"], available_models=[]
        )
        assert result is None

    def test_builder_preferred_models_constant_order(self):
        from gaia.agents.registry import BUILDER_PREFERRED_MODELS

        assert BUILDER_PREFERRED_MODELS == [
            "Qwen3.5-35B-A3B-GGUF",
            "Gemma-4-E4B-it-GGUF",
            "gemma4-it-e2b-FLM",
        ]

    def test_get_lemonade_models_none_means_unreachable_not_empty(self):
        """None (unreachable) and [] (reachable, zero models) are distinct —
        callers must be able to tell them apart."""
        from gaia.agents.registry import get_lemonade_models

        mock_response = SimpleNamespace(
            status_code=200, json=lambda: {"data": []}
        )
        with patch("requests.get", return_value=mock_response):
            reachable_empty = get_lemonade_models("http://localhost:13305/api/v1")

        with patch("requests.get", side_effect=ConnectionError("refused")):
            unreachable = get_lemonade_models("http://localhost:13305/api/v1")

        assert reachable_empty == []
        assert unreachable is None
        assert reachable_empty is not unreachable

    def test_builder_registration_models_is_builder_preferred_models(self):
        """The real builtin 'builder' registration must carry the new
        preference list, not the old empty models=[] placeholder."""
        from gaia.agents.registry import BUILDER_PREFERRED_MODELS

        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("builder")
        assert reg is not None
        assert reg.models == BUILDER_PREFERRED_MODELS
        assert reg.models != []

    def test_builder_registration_resolves_to_installed_fallback_model(self):
        """resolve_model('builder', ...) picks the FLM fallback when that's
        the only preferred model actually installed."""
        registry = AgentRegistry()
        registry.discover()
        resolved = registry.resolve_model(
            "builder", available_models=["gemma4-it-e2b-FLM"]
        )
        assert resolved == "gemma4-it-e2b-FLM"
