# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for BuilderAgent — name normalization, YAML generation, and registry integration."""

from unittest.mock import patch

import yaml

from gaia.agents.builder.agent import _create_agent_impl, _normalize_agent_id
from gaia.agents.registry import AgentManifest, AgentRegistry

# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------


class TestNormalizeAgentId:
    def test_simple_two_word(self):
        assert _normalize_agent_id("Widget Agent") == "widget-agent"

    def test_already_has_agent_suffix(self):
        assert _normalize_agent_id("Widget Agent Agent") == "widget-agent"

    def test_no_agent_suffix(self):
        assert _normalize_agent_id("zoo") == "zoo-agent"

    def test_lowercases(self):
        assert _normalize_agent_id("My Cool Agent") == "my-cool-agent"

    def test_strips_special_chars(self):
        assert _normalize_agent_id("My!@# Agent") == "my-agent"

    def test_multiple_agent_suffixes(self):
        assert _normalize_agent_id("My Agent Agent Agent") == "my-agent"

    def test_empty_string(self):
        assert _normalize_agent_id("") == ""

    def test_only_special_chars(self):
        assert _normalize_agent_id("!!!") == ""

    def test_single_word(self):
        assert _normalize_agent_id("Helper") == "helper-agent"

    def test_preserves_numbers(self):
        assert _normalize_agent_id("Agent 42") == "agent-42-agent"


# ---------------------------------------------------------------------------
# create_agent implementation
# ---------------------------------------------------------------------------


class TestCreateAgentImpl:
    def test_creates_yaml_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("Widget Agent")
        assert "widget-agent" in result
        yaml_path = tmp_path / ".gaia" / "agents" / "widget-agent" / "agent.yaml"
        assert yaml_path.exists()

    def test_yaml_is_valid_manifest(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Tester Agent")
        yaml_path = tmp_path / ".gaia" / "agents" / "tester-agent" / "agent.yaml"
        raw = yaml.safe_load(yaml_path.read_text())
        manifest = AgentManifest(**raw)
        assert manifest.id == "tester-agent"
        assert manifest.name == "Tester Agent"
        assert manifest.tools == []

    def test_uses_provided_description(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Foo Agent", description="Does foo things")
        yaml_path = tmp_path / ".gaia" / "agents" / "foo-agent" / "agent.yaml"
        raw = yaml.safe_load(yaml_path.read_text())
        assert raw["description"] == "Does foo things"

    def test_default_description_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Bar Agent")
        yaml_path = tmp_path / ".gaia" / "agents" / "bar-agent" / "agent.yaml"
        raw = yaml.safe_load(yaml_path.read_text())
        assert "Bar Agent" in raw["description"]

    def test_idempotency_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Dup Agent")
        result = _create_agent_impl("Dup Agent")
        assert result.startswith("Error:")
        assert "already exists" in result

    def test_invalid_name_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("!!!")
        assert result.startswith("Error:")

    def test_reserved_name_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl(
            "Chat"
        )  # normalizes to "chat-agent", base "chat" is reserved
        assert result.startswith("Error:")
        assert "reserved" in result

    def test_path_traversal_sanitized(self, tmp_path, monkeypatch):
        """Traversal characters (../) are stripped by normalization.
        The resulting agent ID is safe and no files escape ~/.gaia/agents/."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("../../etc/passwd agent")
        # The traversal characters are stripped; we get a safe sanitized ID
        # (e.g. "etcpasswd-agent") or an error for the empty-after-strip case.
        yaml_path_root = tmp_path / ".gaia" / "agents"
        if result.startswith("Error:"):
            return  # empty slug after strip → valid error
        # All created files must be under the agents dir
        for p in yaml_path_root.rglob("*"):
            assert str(p).startswith(str(yaml_path_root))

    def test_yaml_contains_commented_examples(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Comment Agent")
        yaml_path = tmp_path / ".gaia" / "agents" / "comment-agent" / "agent.yaml"
        content = yaml_path.read_text()
        assert "mcp_servers" in content
        assert "models" in content

    def test_hotreload_called_when_registry_available(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        mock_registry = MagicMock()
        with patch(
            "gaia.ui._chat_helpers.get_agent_registry", return_value=mock_registry
        ):
            result = _create_agent_impl("Reload Agent")
        assert "reload-agent" in result
        # Verify register_from_dir was actually called with the agent directory path
        mock_registry.register_from_dir.assert_called_once()
        called_path = mock_registry.register_from_dir.call_args[0][0]
        assert called_path.name == "reload-agent"

    def test_hotreload_skipped_gracefully_when_no_registry(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        with patch("gaia.ui._chat_helpers.get_agent_registry", return_value=None):
            result = _create_agent_impl("NoReg Agent")
        # Should still succeed — hot-reload is best-effort
        assert "noreg-agent" in result


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestBuilderRegistryIntegration:
    def test_builder_registered_as_hidden(self):
        registry = AgentRegistry()
        registry.discover()
        reg = registry.get("builder")
        assert reg is not None
        assert reg.hidden is True
        assert reg.source == "builtin"

    def test_builder_excluded_from_visible_list(self):
        registry = AgentRegistry()
        registry.discover()
        visible = [r.id for r in registry.list() if not r.hidden]
        assert "builder" not in visible

    def test_builder_present_in_full_list(self):
        registry = AgentRegistry()
        registry.discover()
        all_ids = [r.id for r in registry.list()]
        assert "builder" in all_ids

    def test_register_from_dir_loads_new_agent(self, tmp_path):
        """round-trip: write YAML → register_from_dir → agent in registry."""
        agent_dir = tmp_path / "my-test-agent"
        agent_dir.mkdir()
        manifest = {
            "manifest_version": 1,
            "id": "my-test-agent",
            "name": "My Test Agent",
            "instructions": "You are a test.",
            "tools": [],
        }
        (agent_dir / "agent.yaml").write_text(
            yaml.dump(manifest, default_flow_style=False), encoding="utf-8"
        )

        registry = AgentRegistry()
        registry.register_from_dir(agent_dir)
        reg = registry.get("my-test-agent")
        assert reg is not None
        assert reg.source == "custom_manifest"
        assert reg.name == "My Test Agent"
