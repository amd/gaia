# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for centralized response format templates in the base Agent class."""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    """Minimal concrete Agent for testing (planning mode by default)."""

    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


class _ConversationalDummyAgent(Agent):
    """Minimal concrete Agent using conversational response mode."""

    def __init__(self, **kwargs):
        self.response_mode = "conversational"
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return "You are a conversational test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def planning_agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _DummyAgent(silent_mode=True, skip_lemonade=True)


@pytest.fixture
def conversational_agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _ConversationalDummyAgent(silent_mode=True, skip_lemonade=True)


# ---------------------------------------------------------------------------
# Class-level constants exist
# ---------------------------------------------------------------------------


class TestFormatConstants:
    def test_planning_format_exists(self):
        assert hasattr(Agent, "_PLANNING_FORMAT")
        assert "RESPONSE FORMAT" in Agent._PLANNING_FORMAT

    def test_conversational_format_exists(self):
        assert hasattr(Agent, "_CONVERSATIONAL_FORMAT")
        assert "RESPONSE FORMAT" in Agent._CONVERSATIONAL_FORMAT

    def test_format_templates_dict(self):
        assert "planning" in Agent._FORMAT_TEMPLATES
        assert "conversational" in Agent._FORMAT_TEMPLATES

    def test_planning_format_has_prev_docs(self):
        assert "$PREV.field" in Agent._PLANNING_FORMAT
        assert "$STEP_N.field" in Agent._PLANNING_FORMAT

    def test_planning_format_has_full_tool_name_rule(self):
        """Phase 4 supplement: tool names must be used in full, not as
        a bare server prefix. Guards Class A truncation."""
        assert "Use the full tool name exactly as registered" in Agent._PLANNING_FORMAT
        assert "ending in `_mcp`" in Agent._PLANNING_FORMAT

    def test_conversational_format_has_full_tool_name_rule(self):
        assert (
            "Use the full tool name exactly as registered"
            in Agent._CONVERSATIONAL_FORMAT
        )

    def test_json_envelope_suppressed_for_tool_calling_models(self):
        """Regression: ``self.model_id`` must be set BEFORE _register_tools
        so the system-prompt cache built during MCP registration sees the
        correct ``is_tool_calling_model`` result. Previously the model_id
        was set later, suppression check ran with model_id=None, returned
        False, and the JSON envelope template leaked into the prompt for
        every tool-calling agent."""
        from unittest.mock import patch

        with patch("gaia.agents.base.agent.AgentSDK"):
            # Gemma-4 is a tool-calling model — the JSON envelope should
            # NOT appear in its system prompt.
            agent = _DummyAgent(
                silent_mode=True,
                skip_lemonade=True,
                model_id="Gemma-4-E4B-it-GGUF",
            )
        assert "==== RESPONSE FORMAT ====" not in agent.system_prompt, (
            "JSON envelope template should be suppressed for tool-calling "
            "models — model_id must be set before _register_tools()"
        )


# ---------------------------------------------------------------------------
# MCPClientMixin contributes the MCP TOOL NAMES block via auto-discovery
# ---------------------------------------------------------------------------


class TestMCPMixinPrompt:
    """``get_mcp_client_system_prompt`` is auto-discovered by
    ``_compose_system_prompt`` and must emit only when ≥2 MCP tools
    are registered. Action-only agents (``single_tool_per_turn=True``)
    suppress the plain-text escape clause."""

    @pytest.fixture(autouse=True)
    def _restore_registry(self):
        """Snapshot ``_TOOL_REGISTRY`` so mutations don't leak into
        other test files (which depend on the registry's prior state).
        """
        from gaia.agents.base.tools import _TOOL_REGISTRY

        before = dict(_TOOL_REGISTRY)
        yield
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)

    def _build_mcp_mixin(self, tool_names, single_tool_per_turn=False):
        """Build a bare MCPClientMixin instance with a stub
        ``_mcp_manager.list_servers()`` and a curated ``_TOOL_REGISTRY``.
        """
        from unittest.mock import MagicMock

        from gaia.agents.base.tools import _TOOL_REGISTRY
        from gaia.mcp.mixin import MCPClientMixin

        class _Bare(MCPClientMixin):
            def __init__(self):
                self._mcp_manager = MagicMock()
                self._mcp_manager.list_servers.return_value = ["tool"]

        _TOOL_REGISTRY.clear()
        for n in tool_names:
            # Include ``parameters`` so other tests that read the
            # registry via ``_format_tools_for_prompt`` don't KeyError
            # if cleanup somehow fails.
            _TOOL_REGISTRY[n] = {
                "name": n,
                "description": "stub",
                "parameters": {},
            }

        mixin = _Bare()
        if single_tool_per_turn:
            mixin.single_tool_per_turn = True
        return mixin

    def test_empty_string_when_no_mcp_tools(self):
        mixin = self._build_mcp_mixin([])
        assert mixin.get_mcp_client_system_prompt() == ""

    def test_empty_string_when_only_one_mcp_tool(self):
        """Single MCP tool doesn't show the failure mode; save prompt mass."""
        mixin = self._build_mcp_mixin(["mcp_foo_bar"])
        assert mixin.get_mcp_client_system_prompt() == ""

    def test_emits_block_with_concrete_example(self):
        mixin = self._build_mcp_mixin(
            [
                "mcp_tool_displaylens_on",
                "mcp_tool_battery_check",
            ]
        )
        fragment = mixin.get_mcp_client_system_prompt()
        assert "==== MCP TOOL NAMES ====" in fragment
        # Concrete example from the registry, not abstract placeholder
        assert (
            "mcp_tool_battery_check" in fragment
            or "mcp_tool_displaylens_on" in fragment
        )
        # No broken "listed above" anchor
        assert "listed above" not in fragment

    def test_single_tool_per_turn_suppresses_plain_text_escape(self):
        """For action-only agents, plain-text refusal is always wrong on
        a verbatim eval scenario."""
        mixin = self._build_mcp_mixin(
            ["mcp_tool_a_tool", "mcp_tool_b_tool"],
            single_tool_per_turn=True,
        )
        fragment = mixin.get_mcp_client_system_prompt()
        assert "answer in plain text" not in fragment

    def test_normal_agent_keeps_plain_text_escape(self):
        mixin = self._build_mcp_mixin(
            ["mcp_tool_a_tool", "mcp_tool_b_tool"],
            single_tool_per_turn=False,
        )
        fragment = mixin.get_mcp_client_system_prompt()
        assert "answer in plain text" in fragment


# ---------------------------------------------------------------------------
# response_mode selects the correct template
# ---------------------------------------------------------------------------


class TestResponseModeSelection:
    def test_default_is_planning(self, planning_agent):
        assert planning_agent.response_mode == "planning"
        assert planning_agent._response_format_template is Agent._PLANNING_FORMAT

    def test_conversational_mode(self, conversational_agent):
        assert conversational_agent.response_mode == "conversational"
        assert (
            conversational_agent._response_format_template
            is Agent._CONVERSATIONAL_FORMAT
        )

    def test_planning_format_in_system_prompt(self, planning_agent):
        prompt = planning_agent.system_prompt
        assert "You must respond ONLY in valid JSON" in prompt

    def test_conversational_format_in_system_prompt(self, conversational_agent):
        prompt = conversational_agent.system_prompt
        assert "Respond in plain text for normal conversation" in prompt

    def test_planning_format_NOT_in_conversational_prompt(self, conversational_agent):
        prompt = conversational_agent.system_prompt
        assert "You must respond ONLY in valid JSON" not in prompt

    def test_conversational_format_NOT_in_planning_prompt(self, planning_agent):
        prompt = planning_agent.system_prompt
        assert "Respond in plain text for normal conversation" not in prompt

    def test_tools_section_present(self, planning_agent):
        # Even with no tools registered, the section should not error.
        # With tools, it should appear.
        prompt = planning_agent.system_prompt
        # Agent prompt + format template should both be present
        assert "You are a test agent." in prompt
        assert "RESPONSE FORMAT" in prompt

    def test_unknown_mode_falls_back_to_planning(self):
        class _BadModeAgent(Agent):
            def __init__(self, **kwargs):
                self.response_mode = "typo_mode"
                super().__init__(**kwargs)

            def _get_system_prompt(self):
                return "test"

            def _register_tools(self):
                pass

            def _create_console(self):
                from gaia.agents.base.console import AgentConsole

                return AgentConsole()

        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _BadModeAgent(silent_mode=True, skip_lemonade=True)
        assert agent._response_format_template is Agent._PLANNING_FORMAT


# ---------------------------------------------------------------------------
# BuilderAgent uses conversational mode
# ---------------------------------------------------------------------------


class TestBuilderAgentFormat:
    """Construction hits Lemonade for model selection (#2243), so the model
    list is mocked — same patch target as test_builder_model_selection.py."""

    @staticmethod
    def _build_agent():
        from gaia.agents.builder.agent import BuilderAgent

        with (
            patch("gaia.agents.base.agent.AgentSDK"),
            patch(
                "gaia.agents.builder.agent.get_lemonade_models",
                return_value=["Gemma-4-E4B-it-GGUF"],
            ),
        ):
            return BuilderAgent()

    def test_builder_uses_conversational_mode(self):
        agent = self._build_agent()
        assert agent.response_mode == "conversational"

    def test_builder_prompt_has_conversational_format(self):
        agent = self._build_agent()
        prompt = agent.system_prompt
        # BuilderAgent uses Gemma-4-E4B (tool-calling): the embedded-JSON
        # format template is not injected since the model uses native tool_calls.
        assert "You must respond ONLY in valid JSON" not in prompt

    def test_builder_no_compose_override(self):
        """BuilderAgent should no longer override _compose_system_prompt."""
        from gaia.agents.builder.agent import BuilderAgent

        assert "_compose_system_prompt" not in BuilderAgent.__dict__


# ---------------------------------------------------------------------------
# BlenderAgent no longer has duplicate format
# ---------------------------------------------------------------------------


class TestBlenderAgentFormat:
    def test_blender_no_duplicate_format(self):
        pytest.importorskip("gaia_agent_blender")
        from gaia_agent_blender.agent import BlenderAgent

        prompt = BlenderAgent._get_system_prompt(None)
        assert "==== JSON RESPONSE FORMAT ====" not in prompt
        assert "==== CRITICAL RULES ====" in prompt


class TestDockerAgentFormat:
    def test_docker_no_duplicate_format(self):
        pytest.importorskip("gaia_agent_docker")
        with patch("gaia.agents.base.agent.AgentSDK"):
            from gaia_agent_docker.agent import DockerAgent

            agent = DockerAgent(skip_lemonade=True, silent_mode=True)
        prompt = agent._get_system_prompt()
        assert "RESPONSE FORMAT - Use EXACTLY this structure" not in prompt
        assert "CRITICAL RULES" not in prompt
        assert "EXAMPLES" in prompt


class TestJiraAgentFormat:
    def test_jira_no_duplicate_format(self):
        pytest.importorskip("gaia_agent_jira")
        with patch("gaia.agents.base.agent.AgentSDK"):
            from gaia_agent_jira.agent import JiraAgent

            agent = JiraAgent(skip_lemonade=True, silent_mode=True)
        prompt = agent._get_system_prompt()
        assert "RESPONSE FORMAT - Use EXACTLY this structure" not in prompt
        assert "EXAMPLES" in prompt
        assert "JQL RULES" in prompt


class TestSDAgentFormat:
    def test_sd_no_duplicate_format(self):
        pytest.importorskip("gaia_agent_sd")
        from gaia_agent_sd.agent import SDAgent

        prompt = SDAgent._get_system_prompt(None)
        assert "DYNAMIC PARAMETER PLACEHOLDERS" not in prompt
        assert "$PREV.image_path" in prompt
