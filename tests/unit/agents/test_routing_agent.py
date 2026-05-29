# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for RoutingAgent — routing logic, disambiguation, and agent creation."""

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_llm_client():
    """Return a mocked LLM client that responds with valid routing JSON."""
    client = MagicMock()
    client.generate.return_value = json.dumps(
        {
            "agent": "code",
            "parameters": {"language": "typescript", "project_type": "fullstack"},
            "confidence": 0.95,
            "reasoning": "Next.js detected",
        }
    )
    return client


@pytest.fixture()
def _patch_create_client(mock_llm_client):
    """Patch create_client so RoutingAgent.__init__ uses the mock LLM."""
    with patch("gaia.agents.routing.agent.create_client", return_value=mock_llm_client):
        yield


@pytest.fixture()
def _patch_code_agent():
    """Patch CodeAgent so _create_agent never touches the real agent stack."""
    with patch("gaia.agents.code.agent.CodeAgent") as cls:
        cls.return_value = MagicMock()
        yield cls


@pytest.fixture()
def router(_patch_create_client):
    """Return a RoutingAgent wired to the mock LLM."""
    from gaia.agents.routing.agent import RoutingAgent

    return RoutingAgent(api_mode=True)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestRoutingAgentInit:
    """Constructor and configuration."""

    def test_import_and_exposes_process_query(self):
        from gaia.agents.routing.agent import RoutingAgent

        assert hasattr(RoutingAgent, "process_query")

    def test_default_routing_model(self, router):
        assert router.routing_model == "Qwen3.5-35B-A3B-GGUF"

    def test_custom_routing_model_via_env(self, _patch_create_client, monkeypatch):
        monkeypatch.setenv("AGENT_ROUTING_MODEL", "custom-model")
        from gaia.agents.routing.agent import RoutingAgent

        r = RoutingAgent(api_mode=True)
        assert r.routing_model == "custom-model"

    def test_api_mode_stored(self, _patch_create_client):
        from gaia.agents.routing.agent import RoutingAgent

        r = RoutingAgent(api_mode=True)
        assert r.api_mode is True

    def test_cli_mode_default(self, _patch_create_client):
        from gaia.agents.routing.agent import RoutingAgent

        r = RoutingAgent()
        assert r.api_mode is False

    def test_agent_kwargs_stored(self, _patch_create_client):
        from gaia.agents.routing.agent import RoutingAgent

        r = RoutingAgent(api_mode=True, foo="bar")
        assert r.agent_kwargs["foo"] == "bar"


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------


class TestAnalyzeWithLLM:
    """_analyze_with_llm parses LLM JSON correctly."""

    def test_parses_clean_json(self, router, mock_llm_client):
        result = router._analyze_with_llm(
            [{"role": "user", "content": "Create a Next.js app"}]
        )
        assert result["agent"] == "code"
        assert result["parameters"]["language"] == "typescript"
        assert result["confidence"] == 0.95

    def test_parses_json_in_markdown_code_block(self, router, mock_llm_client):
        mock_llm_client.generate.return_value = (
            '```json\n{"agent":"code","parameters":{"language":"python",'
            '"project_type":"api"},"confidence":0.9,"reasoning":"Flask"}\n```'
        )
        result = router._analyze_with_llm(
            [{"role": "user", "content": "Build a Flask API"}]
        )
        assert result["parameters"]["language"] == "python"

    def test_parses_json_in_generic_code_block(self, router, mock_llm_client):
        mock_llm_client.generate.return_value = (
            '```\n{"agent":"code","parameters":{"language":"python",'
            '"project_type":"script"},"confidence":0.8,"reasoning":"generic"}\n```'
        )
        result = router._analyze_with_llm(
            [{"role": "user", "content": "Write a script"}]
        )
        assert result["parameters"]["project_type"] == "script"

    def test_json_parse_failure_returns_fallback(self, router, mock_llm_client):
        mock_llm_client.generate.return_value = "NOT VALID JSON AT ALL"
        result = router._analyze_with_llm([{"role": "user", "content": "whatever"}])
        assert result["confidence"] == 0.0
        assert result["parameters"]["language"] == "unknown"

    def test_llm_exception_propagates(self, router, mock_llm_client):
        mock_llm_client.generate.side_effect = ConnectionError("offline")
        with pytest.raises(RuntimeError, match="Failed to analyze query"):
            router._analyze_with_llm([{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# has_unknowns
# ---------------------------------------------------------------------------


class TestHasUnknowns:
    """_has_unknowns detects missing or low-confidence parameters."""

    def test_no_unknowns_high_confidence(self, router):
        analysis = {
            "parameters": {"language": "typescript", "project_type": "fullstack"},
            "confidence": 0.95,
        }
        assert router._has_unknowns(analysis) is False

    def test_unknown_language(self, router):
        analysis = {
            "parameters": {"language": "unknown", "project_type": "fullstack"},
            "confidence": 0.95,
        }
        assert router._has_unknowns(analysis) is True

    def test_unknown_project_type(self, router):
        analysis = {
            "parameters": {"language": "python", "project_type": "unknown"},
            "confidence": 0.95,
        }
        assert router._has_unknowns(analysis) is True

    def test_low_confidence_triggers_unknowns(self, router):
        analysis = {
            "parameters": {"language": "python", "project_type": "api"},
            "confidence": 0.5,
        }
        assert router._has_unknowns(analysis) is True

    def test_boundary_confidence_0_9(self, router):
        analysis = {
            "parameters": {"language": "python", "project_type": "api"},
            "confidence": 0.9,
        }
        assert router._has_unknowns(analysis) is False


# ---------------------------------------------------------------------------
# Clarification questions
# ---------------------------------------------------------------------------


class TestClarificationQuestions:
    """_generate_clarification_question returns context-appropriate prompts."""

    def test_both_unknown(self, router):
        analysis = {
            "parameters": {"language": "unknown", "project_type": "unknown"},
        }
        q = router._generate_clarification_question(analysis)
        assert "What kind of application" in q

    def test_language_unknown_fullstack(self, router):
        analysis = {
            "parameters": {"language": "unknown", "project_type": "fullstack"},
        }
        q = router._generate_clarification_question(analysis)
        assert "language" in q.lower() or "framework" in q.lower()

    def test_language_unknown_script(self, router):
        analysis = {
            "parameters": {"language": "unknown", "project_type": "script"},
        }
        q = router._generate_clarification_question(analysis)
        assert "language" in q.lower()

    def test_project_type_unknown_typescript(self, router):
        analysis = {
            "parameters": {"language": "typescript", "project_type": "unknown"},
        }
        q = router._generate_clarification_question(analysis)
        assert "TypeScript" in q

    def test_project_type_unknown_python(self, router):
        analysis = {
            "parameters": {"language": "python", "project_type": "unknown"},
        }
        q = router._generate_clarification_question(analysis)
        assert "Python" in q


# ---------------------------------------------------------------------------
# Keyword fallback detection
# ---------------------------------------------------------------------------


class TestFallbackKeywordDetection:
    """_fallback_keyword_detection finds language from framework keywords."""

    @pytest.mark.parametrize(
        "query, expected_lang",
        [
            ("Build a Next.js blog", "typescript"),
            ("Create a React dashboard", "typescript"),
            ("Express REST API", "typescript"),
            ("Angular admin panel", "typescript"),
            ("Svelte app", "typescript"),
        ],
    )
    def test_typescript_keywords(self, router, query, expected_lang):
        result = router._fallback_keyword_detection(query)
        assert result["parameters"]["language"] == expected_lang

    @pytest.mark.parametrize(
        "query, expected_lang",
        [
            ("Django REST API", "python"),
            ("Flask microservice", "python"),
            ("FastAPI server", "python"),
            ("Pandas data analysis", "python"),
        ],
    )
    def test_python_keywords(self, router, query, expected_lang):
        result = router._fallback_keyword_detection(query)
        assert result["parameters"]["language"] == expected_lang

    def test_no_keywords_returns_unknown(self, router):
        result = router._fallback_keyword_detection("Build something cool")
        assert result["parameters"]["language"] == "unknown"

    def test_typescript_cli_is_script(self, router):
        result = router._fallback_keyword_detection("Node.js CLI tool")
        assert result["parameters"]["project_type"] == "script"

    def test_python_api_project_type(self, router):
        result = router._fallback_keyword_detection("FastAPI REST backend")
        assert result["parameters"]["project_type"] == "api"


# ---------------------------------------------------------------------------
# Default-to-TypeScript logic
# ---------------------------------------------------------------------------


class TestDefaultUnknownLanguage:
    """_default_unknown_language_to_typescript fills unknowns."""

    def test_unknown_language_becomes_typescript(self, router):
        analysis = {
            "parameters": {"language": "unknown", "project_type": "unknown"},
            "confidence": 0.5,
            "reasoning": "ambiguous",
        }
        result = router._default_unknown_language_to_typescript(analysis)
        assert result["parameters"]["language"] == "typescript"
        assert result["parameters"]["project_type"] == "fullstack"
        assert result["confidence"] == 1.0

    def test_known_language_unchanged(self, router):
        analysis = {
            "parameters": {"language": "python", "project_type": "api"},
            "confidence": 0.9,
            "reasoning": "clear",
        }
        result = router._default_unknown_language_to_typescript(analysis)
        assert result["parameters"]["language"] == "python"
        assert result["confidence"] == 0.9


# ---------------------------------------------------------------------------
# enforce_typescript_only
# ---------------------------------------------------------------------------


class TestEnforceTypescriptOnly:
    """_enforce_typescript_only rejects non-TS languages."""

    def test_typescript_fullstack_passes(self, router):
        console = MagicMock()
        lang, pt = router._enforce_typescript_only("typescript", "fullstack", console)
        assert lang == "typescript"
        assert pt == "fullstack"

    def test_python_raises_system_exit(self, router):
        console = MagicMock()
        with pytest.raises(SystemExit):
            router._enforce_typescript_only("python", "script", console)
        console.print_error.assert_called_once()


# ---------------------------------------------------------------------------
# Agent creation
# ---------------------------------------------------------------------------


class TestCreateAgent:
    """_create_agent produces a CodeAgent with correct params."""

    def test_creates_code_agent(self, router, _patch_code_agent):
        analysis = {
            "agent": "code",
            "parameters": {"language": "typescript", "project_type": "fullstack"},
        }
        agent = router._create_agent(analysis)
        assert agent is not None
        _patch_code_agent.assert_called_once()
        call_kwargs = _patch_code_agent.call_args
        assert (
            call_kwargs.kwargs.get("language") == "typescript"
            or call_kwargs[1].get("language") == "typescript"
        )

    def test_unknown_agent_type_raises(self, router):
        analysis = {
            "agent": "unknown_agent",
            "parameters": {},
        }
        with pytest.raises(ValueError, match="Unknown agent type"):
            router._create_agent(analysis)


class TestCreateAgentWithDefaults:
    """_create_agent_with_defaults fills unknowns before creating."""

    def test_unknown_typescript_defaults_to_fullstack(self, router, _patch_code_agent):
        analysis = {
            "parameters": {"language": "typescript", "project_type": "unknown"},
        }
        router._create_agent_with_defaults(analysis)
        call_kwargs = _patch_code_agent.call_args
        assert (
            call_kwargs.kwargs.get("project_type") == "fullstack"
            or call_kwargs[1].get("project_type") == "fullstack"
        )


# ---------------------------------------------------------------------------
# process_query end-to-end (API mode)
# ---------------------------------------------------------------------------


class TestProcessQueryAPIMode:
    """process_query in API mode auto-executes the agent."""

    def test_resolved_query_executes(self, router, mock_llm_client, _patch_code_agent):
        mock_agent = _patch_code_agent.return_value
        mock_agent.process_query.return_value = "done"

        result = router.process_query("Create a Next.js blog")
        assert result == "done"
        mock_agent.process_query.assert_called_once()

    def test_execute_false_returns_agent(
        self, router, mock_llm_client, _patch_code_agent
    ):
        agent = router.process_query("Create a Next.js blog", execute=False)
        assert agent is _patch_code_agent.return_value

    def test_low_confidence_api_mode_uses_defaults(
        self, router, mock_llm_client, _patch_code_agent
    ):
        mock_llm_client.generate.return_value = json.dumps(
            {
                "agent": "code",
                "parameters": {"language": "typescript", "project_type": "unknown"},
                "confidence": 0.4,
                "reasoning": "unclear",
            }
        )
        mock_agent = _patch_code_agent.return_value
        mock_agent.process_query.return_value = "ok"
        result = router.process_query("Build something")
        assert result == "ok"
