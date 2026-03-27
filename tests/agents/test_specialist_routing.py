"""Tests for specialist agent routing via get_specialist_agent and get_specialist_agents."""
import pytest
from gaia.agents.registry import AgentRegistry
from gaia.agents.base import AgentDefinition, AgentTriggers, AgentCapabilities, AgentConstraints
from gaia.pipeline.defect_types import DefectType, DEFECT_SPECIALISTS


def _make_agent(agent_id: str, enabled: bool = True, capabilities: list = None) -> AgentDefinition:
    """Create a minimal AgentDefinition for testing."""
    return AgentDefinition(
        id=agent_id,
        name=agent_id.replace("-", " ").title(),
        version="1.0.0",
        category="review",
        description=f"Test agent {agent_id}",
        triggers=AgentTriggers(),
        capabilities=AgentCapabilities(capabilities=capabilities or []),
        enabled=enabled,
    )


@pytest.fixture
def populated_registry() -> AgentRegistry:
    """
    Create a registry populated with known agents without file system access.

    Agents inserted:
    - "security-auditor": enabled, primary for SECURITY defect type
    - "performance-analyst": enabled, primary for PERFORMANCE defect type
    - "senior-developer": enabled, serves as universal fallback
    - "quality-reviewer": enabled, secondary for CODE_QUALITY
    - "disabled-specialist": disabled, verifies skip logic
    - "test-coverage-analyzer": enabled, primary for TESTING defect type
    """
    registry = AgentRegistry(agents_dir=None, auto_reload=False)

    # Insert agents directly, bypassing async register_agent path
    registry._agents["security-auditor"] = _make_agent(
        "security-auditor", enabled=True, capabilities=["security"]
    )
    registry._agents["performance-analyst"] = _make_agent(
        "performance-analyst", enabled=True, capabilities=["performance"]
    )
    registry._agents["senior-developer"] = _make_agent(
        "senior-developer", enabled=True, capabilities=["development"]
    )
    registry._agents["quality-reviewer"] = _make_agent(
        "quality-reviewer", enabled=True, capabilities=["quality"]
    )
    registry._agents["test-coverage-analyzer"] = _make_agent(
        "test-coverage-analyzer", enabled=True, capabilities=["testing"]
    )
    registry._agents["disabled-specialist"] = _make_agent(
        "disabled-specialist", enabled=False, capabilities=["security"]
    )

    # Rebuild indexes after direct mutation
    registry._build_indexes()
    return registry


class TestGetSpecialistAgent:
    """Tests for AgentRegistry.get_specialist_agent()."""

    def test_security_defect_routes_to_security_auditor(self, populated_registry: AgentRegistry):
        """SECURITY defect type should return the first enabled candidate from DEFECT_SPECIALISTS."""
        result = populated_registry.get_specialist_agent("SECURITY")
        # The result must be one of the DEFECT_SPECIALISTS candidates for SECURITY
        candidates = DEFECT_SPECIALISTS.get(DefectType.SECURITY, [])
        assert result in candidates, (
            f"Expected result '{result}' to be in DEFECT_SPECIALISTS[SECURITY]={candidates}"
        )
        # The returned agent must be enabled
        agent = populated_registry.get_agent(result)
        assert agent is not None
        assert agent.enabled is True

    def test_performance_defect_routes_to_performance_analyst(self, populated_registry: AgentRegistry):
        """PERFORMANCE defect type should return the first enabled candidate."""
        result = populated_registry.get_specialist_agent("PERFORMANCE")
        candidates = DEFECT_SPECIALISTS.get(DefectType.PERFORMANCE, [])
        assert result in candidates, (
            f"Expected result '{result}' to be in DEFECT_SPECIALISTS[PERFORMANCE]={candidates}"
        )
        agent = populated_registry.get_agent(result)
        assert agent is not None
        assert agent.enabled is True

    def test_unknown_defect_type_falls_back_to_senior_developer(self, populated_registry: AgentRegistry):
        """An unrecognised defect type key should fall back to the specified fallback agent."""
        result = populated_registry.get_specialist_agent(
            "NONEXISTENT_XYZ", fallback="senior-developer"
        )
        assert result == "senior-developer", (
            f"Expected fallback 'senior-developer', got '{result}'"
        )

    def test_custom_fallback_agent(self, populated_registry: AgentRegistry):
        """
        Verify fallback path: when all DEFECT_SPECIALISTS candidates for a type
        are absent from the registry, the caller-supplied fallback is returned.

        For "NONEXISTENT_XYZ", get_specialist_agent maps to DefectType.UNKNOWN whose
        DEFECT_SPECIALISTS list is ["senior-developer"].  Because "senior-developer" IS
        registered and enabled, that candidate is returned before the fallback arg is
        consulted.  To exercise the fallback arg we must pass a defect type whose
        DEFECT_SPECIALISTS candidates are entirely absent from the registry.

        DEFECT_SPECIALISTS[DefectType.DOCUMENTATION] = ["technical-writer", "senior-developer"].
        "technical-writer" is NOT in the populated_registry; "senior-developer" IS, so it
        is returned first.  To reach the fallback we need a type whose candidates are all
        absent. DEFECT_SPECIALISTS[DefectType.REQUIREMENTS] = ["software-program-manager",
        "planning-analysis-strategist"] — neither is in the populated_registry.
        """
        # REQUIREMENTS candidates ("software-program-manager", "planning-analysis-strategist")
        # are not in the populated registry, so the fallback agent is consulted.
        result = populated_registry.get_specialist_agent(
            "REQUIREMENTS", fallback="quality-reviewer"
        )
        # "quality-reviewer" is registered and enabled; it is not in the REQUIREMENTS
        # candidates list, so the fallback branch is reached and it is returned.
        assert result == "quality-reviewer"

    def test_disabled_specialist_skipped_to_fallback(self, populated_registry: AgentRegistry):
        """
        When the primary SECURITY specialist is disabled the method must skip it.

        The registry has 'disabled-specialist' (enabled=False). However, the real
        DEFECT_SPECIALISTS mapping for SECURITY starts with 'security-auditor' which
        IS enabled, so we test a scenario where we temporarily disable all registered
        SECURITY candidates and verify the fallback path.
        """
        # Temporarily disable all SECURITY primary candidates that are registered
        security_candidates = DEFECT_SPECIALISTS.get(DefectType.SECURITY, [])
        originally_enabled = {}
        for cid in security_candidates:
            agent = populated_registry.get_agent(cid)
            if agent is not None:
                originally_enabled[cid] = agent.enabled
                agent.enabled = False

        try:
            result = populated_registry.get_specialist_agent(
                "SECURITY", fallback="quality-reviewer"
            )
            # With all SECURITY candidates disabled, we expect the fallback or any enabled agent
            # quality-reviewer is registered and enabled
            enabled_agents = populated_registry.get_enabled_agents()
            assert result in enabled_agents or result == "quality-reviewer", (
                f"Expected an enabled agent, got '{result}'"
            )
        finally:
            # Restore
            for cid, was_enabled in originally_enabled.items():
                agent = populated_registry.get_agent(cid)
                if agent is not None:
                    agent.enabled = was_enabled

    def test_unknown_defect_key_string_handled(self, populated_registry: AgentRegistry):
        """Passing an arbitrary string that is not a DefectType member must not raise."""
        result = populated_registry.get_specialist_agent("TOTALLY_UNKNOWN_KEY_99999")
        # Should not raise; result is None or an enabled agent (last-resort path)
        if result is not None:
            agent = populated_registry.get_agent(result)
            # result may be a last-resort enabled agent
            enabled = populated_registry.get_enabled_agents()
            assert result in enabled

    def test_no_agents_available_returns_none(self):
        """An empty registry must return None, not raise."""
        empty_registry = AgentRegistry(agents_dir=None, auto_reload=False)
        result = empty_registry.get_specialist_agent("SECURITY")
        assert result is None

    def test_defect_type_case_insensitive(self, populated_registry: AgentRegistry):
        """Lowercase 'security' must produce the same result as uppercase 'SECURITY'."""
        result_lower = populated_registry.get_specialist_agent("security")
        result_upper = populated_registry.get_specialist_agent("SECURITY")
        assert result_lower == result_upper


class TestGetSpecialistAgents:
    """Tests for AgentRegistry.get_specialist_agents() batch routing."""

    def test_multiple_defect_types_all_resolved(self, populated_registry: AgentRegistry):
        """Passing multiple known defect types returns a dict with one entry per type."""
        result = populated_registry.get_specialist_agents(["SECURITY", "PERFORMANCE"])
        assert isinstance(result, dict)
        assert len(result) == 2
        assert "SECURITY" in result
        assert "PERFORMANCE" in result

    def test_empty_list_returns_empty_dict(self, populated_registry: AgentRegistry):
        """An empty input list must yield an empty dict."""
        result = populated_registry.get_specialist_agents([])
        assert result == {}

    def test_duplicate_types_deduplicated_in_result(self, populated_registry: AgentRegistry):
        """
        get_specialist_agents iterates the list as given; if the caller passes
        duplicates the dict will naturally collapse them to one key.
        """
        result = populated_registry.get_specialist_agents(["SECURITY", "SECURITY"])
        # dict comprehension in get_specialist_agents: {dt: ... for dt in defect_types}
        # duplicates overwrite, result has exactly 1 key
        assert len(result) == 1
        assert "SECURITY" in result

    def test_returns_dict_keyed_by_input_strings(self, populated_registry: AgentRegistry):
        """The returned dict keys must be the exact strings passed in the input list."""
        input_types = ["SECURITY", "PERFORMANCE"]
        result = populated_registry.get_specialist_agents(input_types)
        assert set(result.keys()) == set(input_types)

    def test_unknown_type_in_list_handled(self, populated_registry: AgentRegistry):
        """An unknown type in the list must not raise; its value is None or a fallback."""
        result = populated_registry.get_specialist_agents(["NONEXISTENT_XYZ"])
        assert "NONEXISTENT_XYZ" in result
        # Value is None or a string (last-resort enabled agent)
        assert result["NONEXISTENT_XYZ"] is None or isinstance(result["NONEXISTENT_XYZ"], str)
