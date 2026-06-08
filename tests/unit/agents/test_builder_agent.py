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

    def test_spaced_input_preserves_internal_caps(self):
        """When the name already has spaces, internal caps are left intact."""
        assert _split_camel_case("Daily arXiv Summary") == "Daily arXiv Summary"

    def test_spaced_input_preserves_leading_lowercase_acronym(self):
        assert _split_camel_case("iOS Helper") == "iOS Helper"

    def test_spaced_input_preserves_proper_noun(self):
        assert _split_camel_case("McKinsey Advisor") == "McKinsey Advisor"


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

    def test_mcp_docs_link_present(self, tmp_path, monkeypatch):
        """Non-MCP generated agent.py has an MCP docs link (not a comment block)."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Mcp Agent")
        py_path = tmp_path / ".gaia" / "agents" / "mcp" / "agent.py"
        content = py_path.read_text(encoding="utf-8")
        assert "amd-gaia.ai/docs/sdk/infrastructure/mcp" in content
        # The MCP code should NOT be in the non-MCP template
        assert "MCPClientMixin" not in content

    def test_mcp_imports_valid(self):
        """MCP import paths used in the MCP-enabled template actually exist."""
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

    def test_built_agent_runs_with_authored_persona(self, tmp_path, monkeypatch):
        """A Builder-produced agent instantiates and runs through the real agent
        loop on ITS authored persona — fully offline (mocked LLM, no Lemonade).

        Closes the coverage gap: other tests prove the Builder *writes* a correct
        agent.py; this one *runs* a built agent end-to-end.
        """
        from unittest.mock import MagicMock

        authored_prompt = (
            "You are the Daily arXiv Summary Agent. You find recent arXiv papers "
            "and deliver concise digests (title, authors, summary, why it matters)."
        )
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl(
            "Daily arXiv Summary",
            description="Finds and summarizes new arXiv papers each day.",
            system_prompt=authored_prompt,
            conversation_starters=["Summarize today's top papers on diffusion models"],
        )

        # Dynamically import the generated agent.py (mirrors
        # test_generated_agent_importable) and grab the built class.
        py_path = tmp_path / ".gaia" / "agents" / "daily-arxiv-summary" / "agent.py"
        spec = importlib.util.spec_from_file_location("built_arxiv_agent", py_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        agent_cls = module.DailyArxivSummaryAgent

        # Construct with skip_lemonade=True so __init__ makes no network call,
        # and a mocked LLM so the loop never hits a real server (CI-safe).
        # A tool-calling model_id keeps _compose_system_prompt from appending the
        # embedded-JSON response-format template; with no registered tools the
        # composed prompt is then exactly the authored persona.
        agent = agent_cls(
            model_id="Qwen3.5-35B-A3B-GGUF",
            skip_lemonade=True,
            silent_mode=True,
            max_steps=1,
        )

        mocked_answer = (
            "I summarize recent arXiv papers — e.g. Denoising Diffusion "
            "Probabilistic Models (arXiv:2006.11239)."
        )
        # The base loop calls self.chat.send_messages(...) and reads .text /
        # .stats (agent.py). A JSON {"answer": ...} response is parsed as a
        # planning-mode final answer the loop accepts and returns as result.
        mock_resp = MagicMock()
        mock_resp.text = '{"answer": "' + mocked_answer + '"}'
        mock_resp.stats = {}
        mock_resp.tool_calls = []

        with patch.object(agent.chat, "send_messages", return_value=mock_resp):
            result = agent.process_query("What do you do?")

        assert result["status"] == "success"
        assert mocked_answer in result["result"]
        # The built agent ran on ITS authored persona, not a placeholder.
        assert result["system_prompt"] == authored_prompt
        assert agent.system_prompt == authored_prompt
        assert result.get("error_count", 0) == 0
        assert "zoo" not in result["result"].lower()

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
        # The input "NoReg Agent" already contains a space, so _split_camel_case
        # short-circuits and leaves "NoReg" intact, producing agent-id "noreg"
        # (not the old "no-reg").
        assert "noreg" in result

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
# Persona authoring (system prompt + conversation starters, no zoo default)
# ---------------------------------------------------------------------------


class TestCreateAgentPersona:
    def test_passed_system_prompt_is_used(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        prompt = "You summarize arXiv papers into concise digests."
        _create_agent_impl("Daily arXiv Summary", system_prompt=prompt)
        py_path = tmp_path / ".gaia" / "agents" / "daily-arxiv-summary" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        assert prompt in source

    def test_passed_starters_are_used(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        starters = ["Summarize today's arXiv papers", "Find papers on diffusion models"]
        _create_agent_impl(
            "Daily arXiv Summary",
            system_prompt="Summarize arXiv papers.",
            conversation_starters=starters,
        )
        py_path = tmp_path / ".gaia" / "agents" / "daily-arxiv-summary" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        for s in starters:
            assert s in source

    def test_no_zoo_persona_when_prompt_provided(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl(
            "Daily arXiv Summary",
            description="Summarizes arXiv papers daily.",
            system_prompt="You are an arXiv summarizer.",
            conversation_starters=["Summarize today's papers"],
        )
        py_path = tmp_path / ".gaia" / "agents" / "daily-arxiv-summary" / "agent.py"
        source = py_path.read_text(encoding="utf-8").lower()
        assert "zoo" not in source
        assert "zookeeper" not in source

    def test_no_zoo_persona_in_fallback(self, tmp_path, monkeypatch):
        """Omitting system_prompt yields a description-based prompt, never the zoo."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl(
            "Daily arXiv Summary", description="Summarizes arXiv papers daily."
        )
        py_path = tmp_path / ".gaia" / "agents" / "daily-arxiv-summary" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        lowered = source.lower()
        assert "zoo" not in lowered
        assert "zookeeper" not in lowered
        # Fallback derives the persona from name + description.
        assert "Daily arXiv Summary Agent" in source
        assert "Summarizes arXiv papers daily." in source

    def test_no_todo_docstring(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl(
            "Daily arXiv Summary", description="Summarizes arXiv papers daily."
        )
        py_path = tmp_path / ".gaia" / "agents" / "daily-arxiv-summary" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        assert "TODO: Replace this docstring" not in source
        assert "Summarizes arXiv papers daily." in source

    def test_agent_name_preserves_capitalization(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Daily arXiv Summary")
        py_path = tmp_path / ".gaia" / "agents" / "daily-arxiv-summary" / "agent.py"
        source = py_path.read_text(encoding="utf-8")
        assert "AGENT_NAME = 'Daily arXiv Summary Agent'" in source
        assert "ar Xiv" not in source

    def test_acceptance_scenario_with_tools_and_mcp(self, tmp_path, monkeypatch):
        """End-to-end acceptance: arXiv agent via generate_agent_source (tools + MCP)."""
        from gaia.agents.builder.template import generate_agent_source

        source = generate_agent_source(
            agent_id="daily-arxiv-summary",
            agent_name="Daily arXiv Summary Agent",
            description="Summarizes the day's arXiv papers.",
            class_name="DailyArxivSummaryAgent",
            starters=["Summarize today's arXiv papers"],
            system_prompt="You are an arXiv summarizer that finds and digests papers.",
            tools=["rag", "browser", "shell", "file_search"],
            enable_mcp=True,
        )
        ast.parse(source)
        lowered = source.lower()
        assert "zoo" not in lowered and "zookeeper" not in lowered
        assert "TODO: Replace this docstring" not in source
        assert "arXiv summarizer" in source
        assert "AGENT_NAME = 'Daily arXiv Summary Agent'" in source


# ---------------------------------------------------------------------------
# MCP-enabled agent creation
# ---------------------------------------------------------------------------


class TestCreateAgentImplMCP:
    def test_mcp_enabled_creates_json_file(self, tmp_path, monkeypatch):
        """mcp_servers.json is created alongside agent.py when enable_mcp=True."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=True)
        mcp_path = tmp_path / ".gaia" / "agents" / "widget" / "mcp_servers.json"
        assert mcp_path.exists()

    def test_mcp_enabled_json_is_empty_skeleton(self, tmp_path, monkeypatch):
        """mcp_servers.json contains an empty mcpServers dict."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=True)
        import json

        mcp_path = tmp_path / ".gaia" / "agents" / "widget" / "mcp_servers.json"
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "mcpServers" in data
        assert data["mcpServers"] == {}

    def test_mcp_enabled_agent_has_mixin_in_class(self, tmp_path, monkeypatch):
        """Generated source has MCPClientMixin in the class declaration (Agent first)."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=True)
        source = (tmp_path / ".gaia" / "agents" / "widget" / "agent.py").read_text(
            encoding="utf-8"
        )
        assert "class WidgetAgent(Agent, MCPClientMixin):" in source

    def test_mcp_enabled_agent_has_init_with_mcp_manager(self, tmp_path, monkeypatch):
        """Generated source has __init__ setting _mcp_manager before super().__init__()."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=True)
        source = (tmp_path / ".gaia" / "agents" / "widget" / "agent.py").read_text(
            encoding="utf-8"
        )
        assert "self._mcp_manager = MCPClientManager(" in source
        assert "super().__init__(**kwargs)" in source
        # _mcp_manager must appear before super().__init__(**kwargs) in the __init__ body
        mcp_mgr_pos = source.index("self._mcp_manager = MCPClientManager(")
        # Find the super().__init__(**kwargs) that follows _mcp_manager (not the comment)
        super_pos = source.index("super().__init__(**kwargs)", mcp_mgr_pos)
        assert mcp_mgr_pos < super_pos

    def test_mcp_enabled_register_tools_loads_mcp(self, tmp_path, monkeypatch):
        """_register_tools calls self.load_mcp_servers_from_config()."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=True)
        source = (tmp_path / ".gaia" / "agents" / "widget" / "agent.py").read_text(
            encoding="utf-8"
        )
        assert "self.load_mcp_servers_from_config()" in source

    def test_mcp_enabled_imports_present(self, tmp_path, monkeypatch):
        """All four MCP imports are present in the generated source."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=True)
        source = (tmp_path / ".gaia" / "agents" / "widget" / "agent.py").read_text(
            encoding="utf-8"
        )
        assert "from pathlib import Path" in source
        assert "from gaia.mcp.mixin import MCPClientMixin" in source
        assert "from gaia.mcp.client.config import MCPConfig" in source
        assert (
            "from gaia.mcp.client.mcp_client_manager import MCPClientManager" in source
        )

    def test_mcp_enabled_syntax_valid(self, tmp_path, monkeypatch):
        """Generated MCP-enabled source passes ast.parse()."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=True)
        source = (tmp_path / ".gaia" / "agents" / "widget" / "agent.py").read_text(
            encoding="utf-8"
        )
        ast.parse(source)  # raises SyntaxError on failure

    def test_mcp_enabled_importable(self, tmp_path, monkeypatch):
        """Generated MCP-enabled agent.py can be imported (class definition only)."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=True)
        py_path = tmp_path / ".gaia" / "agents" / "widget" / "agent.py"
        spec = importlib.util.spec_from_file_location("widget_mcp_test", py_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert hasattr(module, "WidgetAgent")

    def test_mcp_disabled_no_json_file(self, tmp_path, monkeypatch):
        """mcp_servers.json is NOT created when enable_mcp=False."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=False)
        mcp_path = tmp_path / ".gaia" / "agents" / "widget" / "mcp_servers.json"
        assert not mcp_path.exists()

    def test_mcp_disabled_no_mixin_in_class(self, tmp_path, monkeypatch):
        """Generated source does NOT include MCPClientMixin when enable_mcp=False."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=False)
        source = (tmp_path / ".gaia" / "agents" / "widget" / "agent.py").read_text(
            encoding="utf-8"
        )
        assert "MCPClientMixin" not in source
        assert "class WidgetAgent(Agent):" in source

    def test_mcp_disabled_has_docs_link(self, tmp_path, monkeypatch):
        """Non-MCP template contains a 1-line MCP docs link instead of 40-line block."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        _create_agent_impl("Widget Agent", enable_mcp=False)
        source = (tmp_path / ".gaia" / "agents" / "widget" / "agent.py").read_text(
            encoding="utf-8"
        )
        assert "amd-gaia.ai/docs/sdk/infrastructure/mcp" in source
        # The verbose 40-line comment block should NOT be present
        assert "Add MCP server support" not in source

    def test_register_tools_clears_global_registry(self, tmp_path, monkeypatch):
        """_register_tools() clears _TOOL_REGISTRY to prevent tool pollution from other agents."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        for enable_mcp in (False, True):
            _create_agent_impl("Widget Agent", enable_mcp=enable_mcp)
            source = (tmp_path / ".gaia" / "agents" / "widget" / "agent.py").read_text(
                encoding="utf-8"
            )
            assert (
                "_TOOL_REGISTRY.clear()" in source
            ), f"_TOOL_REGISTRY.clear() missing for enable_mcp={enable_mcp}"
            import shutil

            shutil.rmtree(tmp_path / ".gaia" / "agents" / "widget")

    def test_mcp_json_write_failure_cleans_up_and_returns_error(
        self, tmp_path, monkeypatch
    ):
        """If mcp_servers.json write fails, directory is removed and Error: is returned."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        original_write = Path.write_text

        def failing_write(self_path, *args, **kwargs):
            if self_path.name == "mcp_servers.json":
                raise OSError("disk full")
            return original_write(self_path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", failing_write)
        result = _create_agent_impl("Widget Agent", enable_mcp=True)
        assert result.startswith("Error:")
        target = tmp_path / ".gaia" / "agents" / "widget"
        assert not target.exists()


# ---------------------------------------------------------------------------
# tools=[...] parameter — tested via generate_agent_source directly
# (tools is no longer on the Builder's surface; _create_agent_impl no longer
#  accepts a tools= arg; tool-mixin composition lives only at the generator layer)
# ---------------------------------------------------------------------------


class TestCreateAgentImplTools:
    """Tool-mixin composition tests exercising generate_agent_source directly."""

    def test_single_tool_rag_generates_mixin(self):
        from gaia.agents.builder.template import generate_agent_source

        src = generate_agent_source(
            agent_id="research-bot",
            agent_name="Research Bot Agent",
            description="Answers questions from local docs.",
            class_name="ResearchBotAgent",
            starters=["Ask me anything"],
            system_prompt="You are Research Bot.",
            tools=["rag"],
        )
        ast.parse(src)
        assert "from gaia.agents.tools.rag_tools import RAGToolsMixin" in src
        # Agent must come first in the base list (GAIA convention).
        assert "class ResearchBotAgent(Agent, RAGToolsMixin):" in src
        assert "self.register_rag_tools()" in src
        assert "_TOOL_REGISTRY.clear()" in src

    def test_multiple_tools_in_mro_order(self):
        from gaia.agents.builder.template import generate_agent_source

        src = generate_agent_source(
            agent_id="doc-editor",
            agent_name="Doc Editor Agent",
            description="Edits documents.",
            class_name="DocEditorAgent",
            starters=["Edit my doc"],
            system_prompt="You are Doc Editor.",
            tools=["rag", "file_io"],
        )
        ast.parse(src)
        assert "class DocEditorAgent(Agent, RAGToolsMixin, FileIOToolsMixin):" in src
        assert "self.register_rag_tools()" in src
        assert "self.register_file_io_tools()" in src

    def test_tools_combined_with_mcp(self):
        from gaia.agents.builder.template import generate_agent_source

        src = generate_agent_source(
            agent_id="ops-bot",
            agent_name="Ops Bot Agent",
            description="Runs tasks via MCP.",
            class_name="OpsBotAgent",
            starters=["Organize my files"],
            system_prompt="You are Ops Bot.",
            tools=["file_io"],
            enable_mcp=True,
        )
        ast.parse(src)
        # MCPClientMixin must come LAST (after other mixins, after Agent).
        assert "class OpsBotAgent(Agent, FileIOToolsMixin, MCPClientMixin):" in src
        assert "self.register_file_io_tools()" in src
        assert "self.load_mcp_servers_from_config()" in src

    def test_invalid_tool_returns_error(self):
        import pytest

        from gaia.agents.builder.template import generate_agent_source

        with pytest.raises(ValueError, match="definitely-not-a-tool"):
            generate_agent_source(
                agent_id="bad-bot",
                agent_name="Bad Bot Agent",
                description="Bad.",
                class_name="BadBotAgent",
                starters=["Go"],
                system_prompt="You are Bad Bot.",
                tools=["definitely-not-a-tool"],
            )

    def test_all_tools_importable(self):
        """Every KNOWN_TOOLS entry can be composed into a generated agent."""
        from gaia.agents.builder.template import generate_agent_source
        from gaia.agents.registry import KNOWN_TOOLS

        for i, tool_name in enumerate(sorted(KNOWN_TOOLS.keys())):
            src = generate_agent_source(
                agent_id=f"tool-test-{i}",
                agent_name=f"Tool Test {i} Agent",
                description=f"Tests tool {tool_name}.",
                class_name=f"ToolTest{i}Agent",
                starters=["Go"],
                system_prompt=f"You test {tool_name}.",
                tools=[tool_name],
            )
            ast.parse(src)

    def test_no_tools_same_as_basic(self):
        """tools=None or tools=[] produces the same output as omitting the arg."""
        from gaia.agents.builder.template import generate_agent_source

        kwargs = dict(
            agent_id="plain",
            agent_name="Plain Agent",
            description="A plain agent.",
            class_name="PlainAgent",
            starters=["Hello"],
            system_prompt="You are Plain.",
        )
        src_none = generate_agent_source(**kwargs, tools=None)
        src_empty = generate_agent_source(**kwargs, tools=[])
        assert src_none == src_empty


# ---------------------------------------------------------------------------
# Builder surface: tools param removed, dict-error detection, docs link
# ---------------------------------------------------------------------------


class TestBuilderSurface:
    def test_create_agent_tool_has_no_tools_param(self):
        """The registered create_agent tool must NOT have a 'tools' parameter."""
        import inspect
        from unittest.mock import patch

        from gaia.agents.builder.agent import BuilderAgent, BuilderAgentConfig

        config = BuilderAgentConfig(
            base_url="http://localhost:9999/api/v1",
            model_id="test-model",
            silent_mode=True,
        )
        with patch("os.path.expanduser", return_value="/tmp/gaia-test"):
            agent = BuilderAgent(config)

        # _TOOL_REGISTRY stores either the raw callable or a dict with a
        # 'function' key (the @tool decorator wraps the function).  Check both.
        from gaia.agents.base.tools import _TOOL_REGISTRY

        tool_entry = _TOOL_REGISTRY.get("create_agent")
        assert tool_entry is not None, "create_agent tool not found in registry"

        if isinstance(tool_entry, dict):
            # Check the schema's parameters dict (built by the @tool decorator)
            params = tool_entry.get("parameters", {})
            assert "tools" not in params, (
                f"create_agent tool schema must not have a 'tools' parameter; "
                f"found params: {list(params)}"
            )
            # Also check the underlying function's signature
            fn = tool_entry.get("function", tool_entry)
        else:
            fn = tool_entry

        if callable(fn):
            sig = inspect.signature(fn)
            assert "tools" not in sig.parameters, (
                f"create_agent function must not have a 'tools' parameter; "
                f"found params: {list(sig.parameters)}"
            )

    def test_stray_tools_kwarg_surfaces_error(self, tmp_path):
        """If the LLM passes tools=[...] in the tool call, an honest error is returned."""
        from unittest.mock import MagicMock, patch

        from gaia.agents.builder.agent import BuilderAgent, BuilderAgentConfig

        config = BuilderAgentConfig(
            base_url="http://localhost:9999/api/v1",
            model_id="test-model",
            max_steps=5,
            streaming=False,
            silent_mode=True,
        )
        with patch("os.path.expanduser", return_value=str(tmp_path)):
            agent = BuilderAgent(config)
        agent.console = MagicMock()

        # LLM emits a call with a stray tools kwarg (old schema)
        stray_call = (
            '{"tool": "create_agent", "tool_args": {"name": "Stray", "tools": ["rag"]}}'
        )
        follow_up = "Here is your agent!"  # never reached

        mock_resp = MagicMock()
        mock_resp.text = stray_call
        agent.chat = MagicMock()
        agent.chat.send_messages.return_value = mock_resp

        # The stray 'tools' kwarg trips a TypeError inside the tool, but the base
        # Agent._execute_tool catches it and returns a {"status": "error", ...} dict.
        # The dict-error check in _process_query_impl then surfaces it as an honest
        # failure, not a crash.
        result = agent._process_query_impl("create a Stray agent with RAG")

        answer = result["answer"]
        # Must not fabricate success
        assert "✅" not in answer
        assert "Agent Created" not in answer
        assert "File location" not in answer
        # Must indicate failure or inability
        assert any(
            kw in answer.lower()
            for kw in ("error", "unable", "fail", "could not", "unexpected")
        ), f"Expected failure indicator in answer, got: {answer!r}"

    def test_confirmation_mentions_starter_and_docs_link(self, tmp_path, monkeypatch):
        """Successful _create_agent_impl result contains starter framing + docs link."""
        monkeypatch.setattr("gaia.agents.builder.agent.Path.home", lambda: tmp_path)
        result = _create_agent_impl(
            "Docs Test Agent",
            description="Tests the confirmation message.",
        )
        assert not result.startswith("Error:"), result
        assert "starter" in result.lower()
        assert "amd-gaia.ai/docs/guides/custom-agent" in result


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
