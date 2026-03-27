"""Tests for PipelineEngine phase helper methods.

Covers:
  _get_phase_config(phase_name) -> Optional[Any]
  _get_agents_for_phase(phase_name) -> List[str]
  _get_output_artifact_name(phase_name) -> str

NOTE: PipelineEngine.__init__() does set self._current_template = None.
The test fixture bypasses __init__ entirely via __new__ + manual attribute
initialisation to avoid asyncio.Semaphore creation (which requires a running
loop on some Python versions), filesystem access for agents_dir, and logging
setup.  The fixture re-sets engine._current_template = None explicitly for
isolation, so the "if not self._current_template: return None" guard evaluates
correctly.

All tests exercise the None-template fallback path (Gap 4 is deferred to P6).
"""
import pytest
from unittest.mock import patch
from gaia.pipeline.engine import PipelineEngine


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> PipelineEngine:
    """
    Create a PipelineEngine with all constructor side-effects bypassed.

    Uses __new__ + manual attribute initialisation to avoid:
    - asyncio.Semaphore creation (which requires a running loop on some Python versions)
    - filesystem access for agents_dir
    - logging setup

    Sets _current_template = None so the guard in _get_phase_config() works.
    """
    with patch.object(PipelineEngine, "__init__", lambda self, *a, **kw: None):
        e = PipelineEngine.__new__(PipelineEngine)
        # Attributes accessed by the three helper methods under test
        e._current_template = None
        # Attributes accessed by other methods (not under test but referenced
        # during import-time attribute access guards)
        e._loop_manager = None
        e._agent_registry = None
        e._initialized = False
        e._running = False
        return e


# ---------------------------------------------------------------------------
# TestGetPhaseConfig
# ---------------------------------------------------------------------------


class TestGetPhaseConfig:
    """Tests for PipelineEngine._get_phase_config()."""

    def test_no_template_returns_none(self, engine: PipelineEngine):
        """With _current_template=None the method must return None without raising."""
        result = engine._get_phase_config("PLANNING")
        assert result is None

    def test_template_with_phase_returns_config(self, engine: PipelineEngine):
        """
        With a mock template that has a matching phase, _get_phase_config must
        delegate to _current_template.get_phase() and return its result.
        """
        mock_phase_config = object()  # sentinel

        class MockTemplate:
            def get_phase(self, phase_name):
                if phase_name == "PLANNING":
                    return mock_phase_config
                return None

        engine._current_template = MockTemplate()
        result = engine._get_phase_config("PLANNING")
        assert result is mock_phase_config

        # Restore for fixture isolation
        engine._current_template = None


# ---------------------------------------------------------------------------
# TestGetAgentsForPhase
# ---------------------------------------------------------------------------


class TestGetAgentsForPhase:
    """Tests for PipelineEngine._get_agents_for_phase()."""

    def test_no_template_returns_empty_list(self, engine: PipelineEngine):
        """With _current_template=None the method must return [] without raising."""
        result = engine._get_agents_for_phase("DEVELOPMENT")
        assert result == []

    def test_phase_config_agents_returned(self, engine: PipelineEngine):
        """
        When the template returns a phase_config with agents, those agents are returned.

        This verifies the delegation contract: if phase_config is truthy and
        has a non-empty .agents attribute, _get_agents_for_phase returns them.
        """
        expected_agents = ["senior-developer", "quality-reviewer"]

        class MockPhaseConfig:
            agents = expected_agents

        class MockTemplate:
            agent_categories = {}

            def get_phase(self, phase_name):
                if phase_name == "DEVELOPMENT":
                    return MockPhaseConfig()
                return None

        engine._current_template = MockTemplate()
        try:
            result = engine._get_agents_for_phase("DEVELOPMENT")
            assert result == expected_agents
        finally:
            engine._current_template = None


# ---------------------------------------------------------------------------
# TestGetOutputArtifactName
# ---------------------------------------------------------------------------


class TestGetOutputArtifactName:
    """Tests for PipelineEngine._get_output_artifact_name()."""

    def test_planning_phase_default_artifact(self, engine: PipelineEngine):
        """Phase 'planning' maps to 'technical_plan'."""
        result = engine._get_output_artifact_name("planning")
        assert result == "technical_plan"

    def test_development_phase_default_artifact(self, engine: PipelineEngine):
        """Phase 'development' maps to 'implementation'."""
        result = engine._get_output_artifact_name("development")
        assert result == "implementation"

    def test_quality_phase_default_artifact(self, engine: PipelineEngine):
        """Phase 'quality' maps to 'quality_report'."""
        result = engine._get_output_artifact_name("quality")
        assert result == "quality_report"

    def test_unknown_phase_generic_output_name(self, engine: PipelineEngine):
        """An unknown phase name returns '{phase_lower}_output' as the generic fallback."""
        result = engine._get_output_artifact_name("custom_phase")
        assert result == "custom_phase_output"

    def test_template_artifact_overrides_default(self, engine: PipelineEngine):
        """
        When the template provides an artifact name via exit_criteria,
        it must override the default_artifacts mapping.
        """
        class MockPhaseConfig:
            exit_criteria = {"artifact": "custom_plan_v2"}

        class MockTemplate:
            def get_phase(self, phase_name):
                return MockPhaseConfig()

        engine._current_template = MockTemplate()
        try:
            result = engine._get_output_artifact_name("planning")
            assert result == "custom_plan_v2"
        finally:
            engine._current_template = None

    def test_case_insensitive_phase_name(self, engine: PipelineEngine):
        """Uppercase 'PLANNING' must map to 'technical_plan' via .lower() normalisation."""
        result = engine._get_output_artifact_name("PLANNING")
        assert result == "technical_plan"
