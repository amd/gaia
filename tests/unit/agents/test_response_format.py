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
    def test_builder_uses_conversational_mode(self):
        with patch("gaia.agents.base.agent.AgentSDK"):
            from gaia.agents.builder.agent import BuilderAgent

            agent = BuilderAgent()
        assert agent.response_mode == "conversational"

    def test_builder_prompt_has_conversational_format(self):
        with patch("gaia.agents.base.agent.AgentSDK"):
            from gaia.agents.builder.agent import BuilderAgent

            agent = BuilderAgent()
        prompt = agent.system_prompt
        assert "Respond in plain text for normal conversation" in prompt
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
        from gaia.agents.blender.agent import BlenderAgent

        prompt = BlenderAgent._get_system_prompt(None)
        assert "==== JSON RESPONSE FORMAT ====" not in prompt
        assert "==== CRITICAL RULES ====" in prompt


class TestDockerAgentFormat:
    def test_docker_no_duplicate_format(self):
        with patch("gaia.agents.base.agent.AgentSDK"):
            from gaia.agents.docker.agent import DockerAgent

            agent = DockerAgent(skip_lemonade=True, silent_mode=True)
        prompt = agent._get_system_prompt()
        assert "RESPONSE FORMAT - Use EXACTLY this structure" not in prompt
        assert "CRITICAL RULES" not in prompt
        assert "EXAMPLES" in prompt


class TestJiraAgentFormat:
    def test_jira_no_duplicate_format(self):
        with patch("gaia.agents.base.agent.AgentSDK"):
            from gaia.agents.jira.agent import JiraAgent

            agent = JiraAgent(skip_lemonade=True, silent_mode=True)
        prompt = agent._get_system_prompt()
        assert "RESPONSE FORMAT - Use EXACTLY this structure" not in prompt
        assert "EXAMPLES" in prompt
        assert "JQL RULES" in prompt


class TestSDAgentFormat:
    def test_sd_no_duplicate_format(self):
        from gaia.agents.sd.agent import SDAgent

        prompt = SDAgent._get_system_prompt(None)
        assert "DYNAMIC PARAMETER PLACEHOLDERS" not in prompt
        assert "$PREV.image_path" in prompt
