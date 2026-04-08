# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for BuilderAgent — name normalization, Python agent generation,
and registry integration."""

import ast
import importlib.util
from pathlib import Path
from unittest.mock import patch

from gaia.agents.builder.agent import (
    _create_agent_impl,
    _name_to_class_name,
    _normalize_agent_id,
    _normalize_display_name,
    _split_camel_case,
)
from gaia.agents.registry import AgentRegistry

# ---------------------------------------------------------------------------
# CamelCase splitting
# ---------------------------------------------------------------------------


class TestSplitCamelCase:
    def test_pascal_case(self):
        assert _split_camel_case("AlphaAgent") == "Alpha Agent"

    def test_acronym(self):
        assert _split_camel_case("MCPAgent") == "MCP Agent"

    def test_acronym_mid_word(self):
        assert _split_camel_case("RAGDocAgent") == "RAG Doc Agent"

    def test_already_spaced(self):
        assert _split_camel_case("Alpha Agent") == "Alpha Agent"

    def test_all_lowercase(self):
        assert _split_camel_case("zoo") == "zoo"

    def test_empty(self):
        assert _split_camel_case("") == ""

    def test_single_word(self):
        assert _split_camel_case("Widget") == "Widget"

    def test_numbers(self):
        assert _split_camel_case("Agent42Bot") == "Agent42 Bot"


# ---------------------------------------------------------------------------
# Display name normalization
# ---------------------------------------------------------------------------


class TestNormalizeDisplayName:
    def test_adds_agent_suffix(self):
        assert _normalize_display_name("Beta") == "Beta Agent"

    def test_no_duplicate(self):
        assert _normalize_display_name("Alpha Agent") == "Alpha Agent"

    def test_strips_multiple_agent_suffixes(self):
        assert _normalize_display_name("My Agent Agent") == "My Agent"

    def test_case_insensitive(self):
        assert _normalize_display_name("beta agent") == "beta Agent"

    def test_multi_word(self):
        assert _normalize_display_name("My Cool") == "My Cool Agent"

    def test_just_agent(self):
        assert _normalize_display_name("Agent") == "Agent"

    def test_empty(self):
        assert _normalize_display_name("") == "Agent"


# ---------------------------------------------------------------------------
# Name normalization (agent ID)
# ---------------------------------------------------------------------------


class TestNormalizeAgentId:
    def test_simple_two_word(self):
        assert _normalize_agent_id("Widget Agent") == "widget"

    def test_already_has_agent_suffix(self):
        assert _normalize_agent_id("Widget Agent Agent") == "widget"

    def test_no_agent_suffix(self):
        assert _normalize_agent_id("zoo") == "zoo"

    def test_lowercases(self):
        assert _normalize_agent_id("My Cool Agent") == "my-cool"

    def test_strips_special_chars(self):
        assert _normalize_agent_id("My!@# Agent") == "my"

    def test_multiple_agent_suffixes(self):
        assert _normalize_agent_id("My Agent Agent Agent") == "my"

    def test_empty_string(self):
        assert _normalize_agent_id("") == ""

    def test_only_special_chars(self):
        assert _normalize_agent_id("!!!") == ""

    def test_single_word(self):
        assert _normalize_agent_id("Helper") == "helper"

    def test_preserves_numbers(self):
        assert _normalize_agent_id("Agent 42") == "agent-42"

    def test_reagent_not_corrupted(self):
        """'Reagent' should NOT have 'agent' stripped — no hyphen boundary."""
        assert _normalize_agent_id("Reagent") == "reagent"

    def test_just_agent(self):
        """'Agent' alone → 'agent' (will be caught by reserved check)."""
        assert _normalize_agent_id("Agent") == "agent"


# ---------------------------------------------------------------------------
# Name to class name conversion
# ---------------------------------------------------------------------------


class TestNameToClassName:
    def test_simple_two_word(self):
        assert _name_to_class_name("Widget Agent") == "WidgetAgent"

    def test_single_word(self):
        assert _name_to_class_name("zoo") == "ZooAgent"

    def test_deduplicates_agent_suffix(self):
        assert _name_to_class_name("My Agent Agent") == "MyAgent"

    def test_digit_starting_name(self):
        assert _name_to_class_name("42 Things") == "Gaia42ThingsAgent"

    def test_agent_name_produces_custom_agent(self):
        assert _name_to_class_name("Agent") == "CustomAgent"

    def test_agent_agent_produces_custom_agent(self):
        assert _name_to_class_name("Agent Agent") == "CustomAgent"

    def test_empty_string(self):
        assert _name_to_class_name("") == ""

    def test_only_special_chars(self):
        assert _name_to_class_name("!!!") == ""

    def test_multi_word(self):
        assert _name_to_class_name("My Cool Helper") == "MyCoolHelperAgent"

    def test_result_is_valid_identifier(self):
        names = ["Widget Agent", "zoo", "42 Things", "Agent", "My Cool Agent"]
        for name in names:
            result = _name_to_class_name(name)
            if result:
                assert result.isidentifier(), f"{name!r} → {result!r} is not valid"


# ---------------------------------------------------------------------------
# create_agent implementation (Python generation)
# ---------------------------------------------------------------------------


class TestCreateAgentImpl:
    def test_creates_agent_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("Widget Agent")
        assert "widget" in result
        py_path = tmp_path / ".gaia" / "agents" / "widget" / "agent.py"
        assert py_path.exists()

    def test_no_yaml_file_created(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent")
        yaml_path = tmp_path / ".gaia" / "agents" / "widget" / "agent.yaml"
        assert not yaml_path.exists()

    def test_python_file_syntax_valid(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Tester Agent")
        py_path = tmp_path / ".gaia" / "agents" / "tester" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        ast.parse(source)  # raises SyntaxError if invalid

    def test_python_file_has_correct_class(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent")
        py_path = tmp_path / ".gaia" / "agents" / "widget" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        assert "class WidgetAgent(Agent):" in source
        assert "AGENT_ID = 'widget'" in source
        assert "AGENT_NAME = 'Widget Agent'" in source
        assert "from gaia.agents.base.agent import Agent" in source

    def test_uses_provided_description(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Foo Agent", description="Does foo things")
        py_path = tmp_path / ".gaia" / "agents" / "foo" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        assert "Does foo things" in source

    def test_default_description_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Bar Agent")
        py_path = tmp_path / ".gaia" / "agents" / "bar" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        assert "Custom agent: Bar Agent" in source

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
        result = _create_agent_impl("Chat")
        assert result.startswith("Error:")
        assert "reserved" in result

    def test_path_traversal_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("../../etc/passwd agent")
        yaml_path_root = tmp_path / ".gaia" / "agents"
        if result.startswith("Error:"):
            return
        for p in yaml_path_root.rglob("*"):
            assert str(p).startswith(str(yaml_path_root))

    def test_python_has_customization_comments(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Comment Agent")
        py_path = tmp_path / ".gaia" / "agents" / "comment" / "agent.py"
        content = py_path.read_text(encoding="utf-8")
        assert "# -- Tools" in content
        assert "# -- Advanced" in content
        assert "@tool" in content

    def test_special_chars_in_name(self, tmp_path, monkeypatch):
        """Names with special characters produce valid Python."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl('He said "hello" agent')
        if result.startswith("Error:"):
            return  # name sanitized to empty → valid error
        agent_dir = tmp_path / ".gaia" / "agents"
        for py_file in agent_dir.rglob("agent.py"):
            source = py_file.read_text(encoding="utf-8")
            ast.parse(source)  # must not raise

    def test_special_chars_in_description(self, tmp_path, monkeypatch):
        """Descriptions with {, }, quotes produce valid Python via repr()."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Safe Agent", description='Has {curly} and "quotes"')
        py_path = tmp_path / ".gaia" / "agents" / "safe" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        ast.parse(source)
        assert "curly" in source
        assert "quotes" in source

    def test_mcp_comments_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Mcp Agent")
        py_path = tmp_path / ".gaia" / "agents" / "mcp" / "agent.py"
        content = py_path.read_text(encoding="utf-8")
        assert "mcp_servers.json" in content
        assert "MCPClientMixin" in content
        assert "MCPConfig" in content
        assert "_mcp_manager" in content

    def test_mcp_imports_valid(self):
        """MCP import paths referenced in generated comments actually exist."""
        assert importlib.util.find_spec("gaia.mcp.mixin") is not None
        assert importlib.util.find_spec("gaia.mcp.client.config") is not None
        assert (
            importlib.util.find_spec("gaia.mcp.client.mcp_client_manager") is not None
        )

    def test_generated_agent_importable(self, tmp_path, monkeypatch):
        """Generated agent.py can be imported and contains a valid Agent subclass."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Import Test Agent")
        py_path = tmp_path / ".gaia" / "agents" / "import-test" / "agent.py"

        spec = importlib.util.spec_from_file_location("test_import_agent", py_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Find Agent subclass with required attributes
        from gaia.agents.base.agent import Agent as BaseAgent

        found = False
        for _name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseAgent)
                and obj is not BaseAgent
                and hasattr(obj, "AGENT_ID")
                and hasattr(obj, "AGENT_NAME")
            ):
                assert obj.AGENT_ID == "import-test"
                assert obj.AGENT_NAME == "Import Test Agent"
                found = True
                break
        assert found, "No valid Agent subclass found in generated agent.py"

    def test_cleanup_on_failure(self, tmp_path, monkeypatch):
        """If writing fails, the directory is cleaned up."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)

        # Make the write fail by making target dir read-only after mkdir
        original_write = Path.write_text

        def failing_write(self_path, *args, **kwargs):
            if self_path.name == "agent.py":
                raise OSError("Simulated write failure")
            return original_write(self_path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", failing_write)
        result = _create_agent_impl("Fail Agent")
        assert result.startswith("Error:")
        # The directory should have been cleaned up
        target = tmp_path / ".gaia" / "agents" / "fail"
        assert not target.exists()

    def test_hotreload_called_when_registry_available(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        mock_registry = MagicMock()
        with patch(
            "gaia.ui._chat_helpers.get_agent_registry", return_value=mock_registry
        ):
            result = _create_agent_impl("Reload Agent")
        assert "reload" in result
        mock_registry.register_from_dir.assert_called_once()
        called_path = mock_registry.register_from_dir.call_args[0][0]
        assert called_path.name == "reload"

    def test_hotreload_skipped_gracefully_when_no_registry(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        with patch("gaia.ui._chat_helpers.get_agent_registry", return_value=None):
            result = _create_agent_impl("NoReg Agent")
        assert "no-reg" in result

    def test_reserved_name_gaia_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("Gaia")
        assert result.startswith("Error:")
        assert "reserved" in result

    def test_reserved_name_builder_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("Builder")
        assert result.startswith("Error:")
        assert "reserved" in result

    def test_reserved_name_agent_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("Agent")
        assert result.startswith("Error:")
        assert "reserved" in result

    def test_hotreload_exception_still_returns_success(self, tmp_path, monkeypatch):
        """If hot-reload raises, the function still returns success (agent was written)."""
        from unittest.mock import MagicMock

        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        mock_registry = MagicMock()
        mock_registry.register_from_dir.side_effect = RuntimeError("reload failed")
        with patch(
            "gaia.ui._chat_helpers.get_agent_registry", return_value=mock_registry
        ):
            result = _create_agent_impl("ExcAgent Agent")
        assert not result.startswith("Error:")
        assert "exc" in result

    def test_camel_case_input(self, tmp_path, monkeypatch):
        """CamelCase input like 'AlphaAgent' is split and handled correctly."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("AlphaAgent")
        assert not result.startswith("Error:")
        py_path = tmp_path / ".gaia" / "agents" / "alpha" / "agent.py"
        assert py_path.exists()
        source = py_path.read_text(encoding="utf-8")
        assert "class AlphaAgent(Agent):" in source
        assert "AGENT_ID = 'alpha'" in source
        assert "AGENT_NAME = 'Alpha Agent'" in source

    def test_acronym_camel_case(self, tmp_path, monkeypatch):
        """Acronym CamelCase like 'MCPAgent' splits correctly."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl("MCPAgent")
        py_path = tmp_path / ".gaia" / "agents" / "mcp" / "agent.py"
        assert py_path.exists(), f"Expected mcp/ directory, got: {result}"
        source = py_path.read_text(encoding="utf-8")
        assert "AGENT_ID = 'mcp'" in source

    def test_display_name_always_has_agent(self, tmp_path, monkeypatch):
        """A name without 'Agent' gets it appended in the generated source."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Beta")
        py_path = tmp_path / ".gaia" / "agents" / "beta" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        assert "AGENT_NAME = 'Beta Agent'" in source


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

    def test_register_from_dir_loads_python_agent(self, tmp_path, monkeypatch):
        """Round-trip: _create_agent_impl → register_from_dir → custom_python."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("My Test Agent")
        agent_dir = tmp_path / ".gaia" / "agents" / "my-test"

        registry = AgentRegistry()
        registry.register_from_dir(agent_dir)
        reg = registry.get("my-test")
        assert reg is not None
        assert reg.source == "custom_python"
        assert reg.name == "My Test Agent"
