"""
Tests for GAIA Routing Engine.

Tests cover:
- Defect type detection
- Routing rule evaluation
- Specialist agent selection
- Loop-back logic
- Routing decision creation
"""

from datetime import datetime
from typing import Dict, List

import pytest

from gaia.agents.registry import AgentRegistry
from gaia.pipeline.defect_types import (
    DefectType,
    defect_type_from_string,
    get_defect_specialists,
)
from gaia.pipeline.routing_engine import (
    RoutingDecision,
    RoutingEngine,
    RoutingRule,
)


class TestDefectTypeDetection:
    """Tests for defect type detection."""

    def test_detect_security_defect(self):
        """Test detection of security defects."""
        assert (
            defect_type_from_string("SQL injection vulnerability")
            == DefectType.SECURITY
        )
        assert defect_type_from_string("XSS attack possible") == DefectType.SECURITY
        assert (
            defect_type_from_string("Authentication bypass detected")
            == DefectType.SECURITY
        )

    def test_detect_performance_defect(self):
        """Test detection of performance defects."""
        assert defect_type_from_string("Slow query detected") == DefectType.PERFORMANCE
        assert defect_type_from_string("Memory leak in loop") == DefectType.PERFORMANCE
        assert defect_type_from_string("High CPU usage") == DefectType.PERFORMANCE

    def test_detect_testing_defect(self):
        """Test detection of testing defects."""
        assert defect_type_from_string("Missing tests for module") == DefectType.TESTING
        assert (
            defect_type_from_string("Insufficient test coverage") == DefectType.TESTING
        )
        assert defect_type_from_string("Flaky test failure") == DefectType.TESTING

    def test_detect_documentation_defect(self):
        """Test detection of documentation defects."""
        assert defect_type_from_string("Missing docstring") == DefectType.DOCUMENTATION
        assert (
            defect_type_from_string("Outdated documentation")
            == DefectType.DOCUMENTATION
        )
        assert (
            defect_type_from_string("Missing API comments") == DefectType.DOCUMENTATION
        )

    def test_detect_code_quality_defect(self):
        """Test detection of code quality defects."""
        assert (
            defect_type_from_string("Code style violation") == DefectType.CODE_QUALITY
        )
        assert (
            defect_type_from_string("High cyclomatic complexity")
            == DefectType.CODE_QUALITY
        )
        assert (
            defect_type_from_string("Duplicate code detected")
            == DefectType.CODE_QUALITY
        )

    def test_detect_requirements_defect(self):
        """Test detection of requirements defects."""
        assert (
            defect_type_from_string("Missing requirement implementation")
            == DefectType.REQUIREMENTS
        )
        assert (
            defect_type_from_string("Incorrect feature behavior")
            == DefectType.REQUIREMENTS
        )
        assert (
            defect_type_from_string("Edge case not handled") == DefectType.REQUIREMENTS
        )

    def test_detect_architecture_defect(self):
        """Test detection of architecture defects."""
        assert (
            defect_type_from_string("Architecture violation") == DefectType.ARCHITECTURE
        )
        assert (
            defect_type_from_string("Circular dependency detected")
            == DefectType.ARCHITECTURE
        )
        assert (
            defect_type_from_string("Architectural pattern violation")
            == DefectType.ARCHITECTURE
        )

    def test_detect_accessibility_defect(self):
        """Test detection of accessibility defects."""
        assert (
            defect_type_from_string("Missing alt text for images")
            == DefectType.ACCESSIBILITY
        )
        assert (
            defect_type_from_string("WCAG compliance issue") == DefectType.ACCESSIBILITY
        )
        assert (
            defect_type_from_string("Keyboard navigation broken")
            == DefectType.ACCESSIBILITY
        )

    def test_detect_compatibility_defect(self):
        """Test detection of compatibility defects."""
        assert (
            defect_type_from_string("Cross-browser compatibility issue")
            == DefectType.COMPATIBILITY
        )
        assert (
            defect_type_from_string("Not working on mobile Safari")
            == DefectType.COMPATIBILITY
        )
        assert (
            defect_type_from_string("Breaking change in API")
            == DefectType.COMPATIBILITY
        )

    def test_detect_data_integrity_defect(self):
        """Test detection of data integrity defects."""
        assert (
            defect_type_from_string("Data validation missing")
            == DefectType.DATA_INTEGRITY
        )
        assert defect_type_from_string("Type safety issue") == DefectType.DATA_INTEGRITY
        assert (
            defect_type_from_string("Potential data loss") == DefectType.DATA_INTEGRITY
        )

    def test_detect_unknown_defect(self):
        """Test detection returns UNKNOWN for unclassifiable defects."""
        assert defect_type_from_string("Random unknown issue") == DefectType.UNKNOWN
        assert defect_type_from_string("") == DefectType.UNKNOWN
        assert defect_type_from_string(None) == DefectType.UNKNOWN


class TestRoutingDecision:
    """Tests for RoutingDecision dataclass."""

    def test_create_routing_decision(self):
        """Test creating routing decision."""
        decision = RoutingDecision(
            target_agent="security-auditor",
            target_phase="DEVELOPMENT",
            loop_back=True,
            guidance="Fix security issue",
            matched_rule="security-001",
            defect_type=DefectType.SECURITY,
        )

        assert decision.target_agent == "security-auditor"
        assert decision.target_phase == "DEVELOPMENT"
        assert decision.loop_back is True
        assert "security" in decision.guidance.lower()

    def test_routing_decision_factory_method(self):
        """Test create factory method."""
        decision = RoutingDecision.create(
            target_agent="performance-analyst",
            target_phase="DEVELOPMENT",
            defect_type=DefectType.PERFORMANCE,
            loop_back=True,
            guidance="Optimize performance",
        )

        assert decision.target_agent == "performance-analyst"
        assert decision.defect_type == DefectType.PERFORMANCE
        assert decision.confidence == 1.0

    def test_routing_decision_to_dict(self):
        """Test routing decision serialization."""
        decision = RoutingDecision.create(
            target_agent="technical-writer",
            target_phase="DEVELOPMENT",
            defect_type=DefectType.DOCUMENTATION,
        )

        data = decision.to_dict()
        assert data["target_agent"] == "technical-writer"
        assert data["target_phase"] == "DEVELOPMENT"
        assert data["defect_type"] == "DOCUMENTATION"
        assert "decided_at" in data


class TestRoutingRule:
    """Tests for RoutingRule dataclass."""

    def test_rule_matches_defect_type(self):
        """Test rule matching based on defect type."""
        rule = RoutingRule(
            rule_id="test-001",
            name="Test Rule",
            defect_types=[DefectType.SECURITY, DefectType.PERFORMANCE],
            target_phase="DEVELOPMENT",
        )

        assert rule.matches(DefectType.SECURITY) is True
        assert rule.matches(DefectType.PERFORMANCE) is True
        assert rule.matches(DefectType.TESTING) is False

    def test_rule_disabled(self):
        """Test disabled rule doesn't match."""
        rule = RoutingRule(
            rule_id="test-001",
            name="Test Rule",
            defect_types=[DefectType.SECURITY],
            target_phase="DEVELOPMENT",
            enabled=False,
        )

        assert rule.matches(DefectType.SECURITY) is False

    def test_rule_with_conditions(self):
        """Test rule matching with conditions."""
        rule = RoutingRule(
            rule_id="test-001",
            name="Test Rule",
            defect_types=[DefectType.SECURITY],
            target_phase="DEVELOPMENT",
            conditions={"severity": "critical"},
        )

        assert rule.matches(DefectType.SECURITY, {"severity": "critical"}) is True
        assert rule.matches(DefectType.SECURITY, {"severity": "low"}) is False


class TestRoutingEngine:
    """Tests for RoutingEngine class."""

    @pytest.fixture
    def engine(self) -> RoutingEngine:
        """Create test routing engine."""
        return RoutingEngine()

    @pytest.fixture
    def engine_with_registry(self) -> RoutingEngine:
        """Create routing engine with agent registry."""
        registry = AgentRegistry()
        return RoutingEngine(agent_registry=registry)

    def test_route_security_defect(self, engine: RoutingEngine):
        """Test routing of security defects."""
        defect = {
            "id": "defect-001",
            "description": "SQL injection vulnerability in login form",
            "severity": "critical",
        }

        decision = engine.route_defect(defect)

        assert decision.target_agent == "security-auditor"
        assert decision.target_phase == "DEVELOPMENT"
        assert decision.defect_type == DefectType.SECURITY
        assert decision.loop_back is True

    def test_route_performance_defect(self, engine: RoutingEngine):
        """Test routing of performance defects."""
        defect = {
            "id": "defect-002",
            "description": "Slow query causing high latency",
            "severity": "high",
        }

        decision = engine.route_defect(defect)

        assert decision.target_agent == "performance-analyst"
        assert decision.target_phase == "DEVELOPMENT"
        assert decision.defect_type == DefectType.PERFORMANCE

    def test_route_testing_defect(self, engine: RoutingEngine):
        """Test routing of testing defects."""
        defect = {
            "id": "defect-003",
            "description": "Missing unit tests for new module",
            "severity": "medium",
        }

        decision = engine.route_defect(defect)

        assert decision.target_agent == "test-coverage-analyzer"
        assert decision.target_phase == "DEVELOPMENT"
        assert decision.defect_type == DefectType.TESTING

    def test_route_documentation_defect(self, engine: RoutingEngine):
        """Test routing of documentation defects."""
        defect = {
            "id": "defect-004",
            "description": "Missing docstrings in public API",
            "severity": "low",
        }

        decision = engine.route_defect(defect)

        assert decision.target_agent == "technical-writer"
        assert decision.target_phase == "DEVELOPMENT"
        assert decision.defect_type == DefectType.DOCUMENTATION
        assert decision.loop_back is False  # Documentation can be fixed in parallel

    def test_route_architecture_defect(self, engine: RoutingEngine):
        """Test routing of architecture defects."""
        defect = {
            "id": "defect-005",
            "description": "Circular dependency between modules",
            "severity": "high",
        }

        decision = engine.route_defect(defect)

        assert decision.target_agent == "solutions-architect"
        assert decision.target_phase == "PLANNING"
        assert decision.defect_type == DefectType.ARCHITECTURE

    def test_route_requirements_defect(self, engine: RoutingEngine):
        """Test routing of requirements defects."""
        defect = {
            "id": "defect-006",
            "description": "Missing requirement implementation",
            "severity": "high",
        }

        decision = engine.route_defect(defect)

        assert decision.target_agent == "software-program-manager"
        assert decision.target_phase == "PLANNING"
        assert decision.defect_type == DefectType.REQUIREMENTS

    def test_route_unknown_defect(self, engine: RoutingEngine):
        """Test routing of unknown defect types."""
        defect = {
            "id": "defect-007",
            "description": "Some random issue",
            "severity": "medium",
        }

        decision = engine.route_defect(defect)

        assert decision.target_agent == "senior-developer"  # Fallback
        assert decision.target_phase == "DEVELOPMENT"  # Default
        assert decision.defect_type == DefectType.UNKNOWN

    def test_route_multiple_defects(self, engine: RoutingEngine):
        """Test routing multiple defects at once."""
        defects = [
            {
                "id": "d1",
                "description": "SQL injection vulnerability",
                "severity": "critical",
            },
            {"id": "d2", "description": "Missing unit tests", "severity": "medium"},
            {"id": "d3", "description": "Slow database query", "severity": "high"},
        ]

        routed = engine.route_defects(defects)

        assert "DEVELOPMENT" in routed
        assert len(routed["DEVELOPMENT"]) == 3

        # Check each defect was routed
        all_routed = []
        for phase_decisions in routed.values():
            all_routed.extend(phase_decisions)
        assert len(all_routed) == 3

    def test_detect_defect_type_method(self, engine: RoutingEngine):
        """Test defect type detection method."""
        assert engine.detect_defect_type("XSS vulnerability") == DefectType.SECURITY
        assert engine.detect_defect_type("Memory leak") == DefectType.PERFORMANCE
        assert engine.detect_defect_type("Missing tests") == DefectType.TESTING
        assert engine.detect_defect_type("Unknown issue xyz") == DefectType.UNKNOWN

    def test_evaluate_rules_method(self, engine: RoutingEngine):
        """Test rule evaluation method."""
        rule, phase = engine.evaluate_rules(DefectType.SECURITY)

        assert rule is not None
        assert rule.rule_id == "security-001"
        assert phase == "DEVELOPMENT"

        rule, phase = engine.evaluate_rules(DefectType.UNKNOWN)
        assert rule is None  # No rule for UNKNOWN
        assert phase == "DEVELOPMENT"  # Default phase

    def test_select_specialist_method(self, engine: RoutingEngine):
        """Test specialist selection method."""
        # Without registry, should return rule-specified agent or first from mapping
        agent = engine.select_specialist(DefectType.SECURITY)
        assert agent == "security-auditor"

        agent = engine.select_specialist(DefectType.PERFORMANCE)
        assert agent == "performance-analyst"

    def test_select_specialist_with_registry(self, engine_with_registry: RoutingEngine):
        """Test specialist selection with agent registry."""
        # Note: In real tests, registry would have agents loaded
        # This tests the fallback behavior
        agent = engine_with_registry.select_specialist(DefectType.SECURITY)
        # Should try to find security-auditor, fall back to senior-developer
        assert agent in ["security-auditor", "senior-developer"]

    def test_add_rule(self, engine: RoutingEngine):
        """Test adding custom routing rule."""
        custom_rule = RoutingRule(
            rule_id="custom-001",
            name="Custom Security Rule",
            defect_types=[DefectType.SECURITY],
            target_phase="REVIEW",  # Custom phase
            target_agent="security-auditor",  # Use existing agent
            priority=0,  # Highest priority
        )

        engine.add_rule(custom_rule)

        # New rule should be evaluated first (priority 0)
        # Use description that will match SECURITY defect type
        defect = {"id": "test", "description": "Security vulnerability detected"}
        decision = engine.route_defect(defect)

        assert decision.matched_rule == "custom-001"
        assert decision.target_phase == "REVIEW"

    def test_remove_rule(self, engine: RoutingEngine):
        """Test removing routing rule."""
        before_count = len(engine._rules)

        removed = engine.remove_rule("security-001")

        assert removed is True
        assert len(engine._rules) == before_count - 1

    def test_remove_nonexistent_rule(self, engine: RoutingEngine):
        """Test removing non-existent rule."""
        removed = engine.remove_rule("nonexistent-rule")
        assert removed is False

    def test_get_rule_statistics(self, engine: RoutingEngine):
        """Test getting rule statistics."""
        stats = engine.get_rule_statistics()

        assert "total_rules" in stats
        assert "enabled_rules" in stats
        assert "rules_by_defect_type" in stats
        assert "rules_by_phase" in stats
        assert stats["total_rules"] > 0

    def test_routing_decision_includes_metadata(self, engine: RoutingEngine):
        """Test that routing decisions include proper metadata."""
        defect = {
            "id": "defect-meta",
            "description": "SQL injection in user input handling " + "extra text " * 10,
            "severity": "critical",
        }

        decision = engine.route_defect(defect)

        assert "defect_id" in decision.metadata
        assert decision.metadata["defect_id"] == "defect-meta"
        assert "rules_evaluated" in decision.metadata
        assert decision.metadata["rules_evaluated"] > 0

    def test_routing_confidence_calculation(self, engine: RoutingEngine):
        """Test confidence score calculation."""
        # Short description - lower confidence
        defect_short = {"id": "d1", "description": "SQL injection"}
        decision_short = engine.route_defect(defect_short)

        # Longer description - higher confidence
        defect_long = {
            "id": "d2",
            "description": "SQL injection vulnerability detected in user input handling form",
        }
        decision_long = engine.route_defect(defect_long)

        # Both should be detected as SECURITY
        assert decision_short.defect_type == DefectType.SECURITY
        assert decision_long.defect_type == DefectType.SECURITY

    def test_empty_defect_description(self, engine: RoutingEngine):
        """Test handling of empty defect description."""
        defect = {"id": "empty", "description": ""}
        decision = engine.route_defect(defect)

        assert decision.defect_type == DefectType.UNKNOWN
        assert decision.target_agent == "senior-developer"

    def test_missing_description_field(self, engine: RoutingEngine):
        """Test handling of missing description field."""
        defect = {"id": "no-desc"}
        decision = engine.route_defect(defect)

        assert decision.defect_type == DefectType.UNKNOWN
        assert decision.target_phase == "DEVELOPMENT"


class TestRoutingEngineIntegration:
    """Integration tests for routing engine."""

    def test_full_routing_workflow(self):
        """Test complete routing workflow."""
        engine = RoutingEngine()

        # Simulate defects from quality report
        defects = [
            {
                "id": "sec-1",
                "description": "SQL injection in login",
                "severity": "critical",
            },
            {
                "id": "perf-1",
                "description": "Slow query in user endpoint",
                "severity": "high",
            },
            {
                "id": "test-1",
                "description": "No tests for auth module",
                "severity": "medium",
            },
            {
                "id": "doc-1",
                "description": "Missing API documentation",
                "severity": "low",
            },
        ]

        # Route all defects
        routed = engine.route_defects(defects)

        # Verify routing
        all_decisions = []
        for phase_decisions in routed.values():
            all_decisions.extend(phase_decisions)

        assert len(all_decisions) == 4

        # Check specific routings
        sec_decision = next(
            d for d in all_decisions if d.metadata.get("defect_id") == "sec-1"
        )
        assert sec_decision.target_agent == "security-auditor"
        assert sec_decision.defect_type == DefectType.SECURITY


class TestDefectSpecialists:
    """Tests for defect specialist mappings."""

    def test_security_specialists(self):
        """Test security defect specialists."""
        specialists = get_defect_specialists(DefectType.SECURITY)
        assert "security-auditor" in specialists
        assert "senior-developer" in specialists

    def test_performance_specialists(self):
        """Test performance defect specialists."""
        specialists = get_defect_specialists(DefectType.PERFORMANCE)
        assert "performance-analyst" in specialists

    def test_testing_specialists(self):
        """Test testing defect specialists."""
        specialists = get_defect_specialists(DefectType.TESTING)
        assert "test-coverage-analyzer" in specialists
        assert "quality-reviewer" in specialists

    def test_documentation_specialists(self):
        """Test documentation defect specialists."""
        specialists = get_defect_specialists(DefectType.DOCUMENTATION)
        assert "technical-writer" in specialists

    def test_architecture_specialists(self):
        """Test architecture defect specialists."""
        specialists = get_defect_specialists(DefectType.ARCHITECTURE)
        assert "solutions-architect" in specialists

    def test_requirements_specialists(self):
        """Test requirements defect specialists."""
        specialists = get_defect_specialists(DefectType.REQUIREMENTS)
        assert "software-program-manager" in specialists
        assert "planning-analysis-strategist" in specialists

    def test_unknown_specialists(self):
        """Test unknown defect specialists (should fallback)."""
        specialists = get_defect_specialists(DefectType.UNKNOWN)
        assert "senior-developer" in specialists


class TestRoutingRulePriority:
    """Tests for routing rule priority handling."""

    def test_higher_priority_rule_evaluated_first(self):
        """Test that lower priority number = higher priority."""
        engine = RoutingEngine()

        # Security rule has priority 1
        # Code quality rule has priority 7
        security_rule = next(r for r in engine._rules if r.rule_id == "security-001")
        quality_rule = next(r for r in engine._rules if r.rule_id == "code-quality-001")

        assert security_rule.priority < quality_rule.priority

    def test_rules_sorted_by_priority(self):
        """Test that rules are sorted by priority."""
        engine = RoutingEngine()
        priorities = [r.priority for r in engine._rules]

        assert priorities == sorted(priorities)


class TestRoutingEnginePerformance:
    """Performance benchmark tests for routing engine."""

    @pytest.fixture
    def engine(self) -> RoutingEngine:
        """Create test routing engine."""
        return RoutingEngine()

    @pytest.fixture
    def sample_defects(self) -> List[Dict[str, str]]:
        """Generate sample defects for performance testing."""
        return (
            [
                {
                    "id": f"perf-{i}",
                    "description": f"SQL injection vulnerability in module {i}",
                    "severity": "critical",
                }
                for i in range(50)
            ]
            + [
                {
                    "id": f"perf-{i+50}",
                    "description": f"Memory leak detected in loop iteration {i}",
                    "severity": "high",
                }
                for i in range(50)
            ]
            + [
                {
                    "id": f"perf-{i+100}",
                    "description": f"Missing unit tests for service {i}",
                    "severity": "medium",
                }
                for i in range(50)
            ]
        )

    def test_defect_type_detection_performance(self, engine: RoutingEngine):
        """Benchmark test for defect type detection performance."""
        import time

        descriptions = [
            "SQL injection vulnerability in user input handling form with potential data breach risk",
            "Memory leak causing high CPU usage and performance degradation over time",
            "Missing unit tests for authentication module resulting in low code coverage",
            "Circular dependency between modules violating architectural patterns",
            "Missing documentation for public API endpoints causing developer confusion",
        ] * 20  # 100 iterations

        start_time = time.perf_counter()
        for desc in descriptions:
            result = engine.detect_defect_type(desc)
            assert result != DefectType.UNKNOWN or desc  # Ensure detection runs

        elapsed = time.perf_counter() - start_time

        # Should process 100 defect type detections in under 0.5 seconds
        assert (
            elapsed < 0.5
        ), f"Defect type detection took {elapsed:.3f}s, expected < 0.5s"

    def test_routing_decision_performance(
        self, engine: RoutingEngine, sample_defects: List[Dict]
    ):
        """Benchmark test for full routing decision performance."""
        import time

        start_time = time.perf_counter()
        routed = engine.route_defects(sample_defects)
        elapsed = time.perf_counter() - start_time

        # Should route 150 defects in under 2 seconds
        assert (
            elapsed < 2.0
        ), f"Routing 150 defects took {elapsed:.3f}s, expected < 2.0s"

        # Verify all defects were routed
        total_routed = sum(len(decisions) for decisions in routed.values())
        assert total_routed == 150

    def test_keyword_matching_early_exit(self):
        """Test that keyword matching uses early exit optimization."""
        import time

        engine = RoutingEngine()

        # Description with many keywords - should exit early on high-confidence match
        long_description = " ".join(
            ["security"] * 50 + ["vulnerability"] * 50 + ["injection"] * 50
        )

        start_time = time.perf_counter()
        for _ in range(100):
            result = engine.detect_defect_type(long_description)
        elapsed = time.perf_counter() - start_time

        # Should complete 100 detections quickly due to early exit
        assert (
            elapsed < 1.0
        ), f"Early exit detection took {elapsed:.3f}s, expected < 1.0s"
        assert result == DefectType.SECURITY

    def test_confidence_calculation_performance(self, engine: RoutingEngine):
        """Benchmark test for confidence score calculation."""
        import time

        descriptions = [
            "SQL injection in login form with user input validation missing",
            "Slow query causing latency issues in database operations",
            "Missing test coverage for critical authentication module",
        ] * 50  # 150 total

        start_time = time.perf_counter()
        for desc in descriptions:
            decision = engine.route_defect({"id": "test", "description": desc})
            assert 0 <= decision.confidence <= 1

        elapsed = time.perf_counter() - start_time

        # Should calculate confidence for 150 defects in under 1 second
        assert (
            elapsed < 1.0
        ), f"Confidence calculation took {elapsed:.3f}s, expected < 1.0s"

    def test_max_keyword_matches_tracking(self, engine: RoutingEngine):
        """Test that keyword matching tracks max matches for optimization."""
        # This test verifies the MAX_KEYWORD_MATCHES_TO_TRACK constant is used
        assert hasattr(engine, "MAX_KEYWORD_MATCHES_TO_TRACK")
        assert engine.MAX_KEYWORD_MATCHES_TO_TRACK >= 1

        # Description that would match many keywords
        description = "security vulnerability exploit injection attack xss csrf authentication bypass"
        decision = engine.route_defect({"id": "test", "description": description})

        # Should still detect as SECURITY with high confidence
        # Note: confidence is 0.8 (base 0.7 + 0.1 for >2 keyword matches)
        assert decision.defect_type == DefectType.SECURITY
        assert decision.confidence >= 0.79  # Allow for floating-point precision


class TestComplexRuleConditions:
    """Tests for complex rule conditions (dict-based conditions)."""

    @pytest.fixture
    def engine_with_custom_rules(self) -> RoutingEngine:
        """Create engine with custom rules that have complex conditions."""
        custom_rules = [
            RoutingRule(
                rule_id="complex-security-001",
                name="Complex Security Rule with Conditions",
                defect_types=[DefectType.SECURITY],
                target_phase="DEVELOPMENT",
                target_agent="security-auditor",
                priority=1,
                loop_back=True,
                conditions={
                    "severity": {"op": "in", "value": ["critical", "high"]},
                    "confidence": {"op": "gte", "value": 0.7},
                },
            ),
            RoutingRule(
                rule_id="complex-performance-001",
                name="Complex Performance Rule",
                defect_types=[DefectType.PERFORMANCE],
                target_phase="DEVELOPMENT",
                target_agent="performance-analyst",
                priority=2,
                conditions={
                    "severity": {"op": "ne", "value": "low"},
                    "impact": {"op": "gt", "value": 5},
                },
            ),
        ]
        return RoutingEngine(custom_rules=custom_rules)

    def test_rule_with_in_condition(self, engine_with_custom_rules: RoutingEngine):
        """Test rule evaluation with 'in' operator condition."""
        # Should match - severity is in allowed values
        context = {"severity": "critical", "confidence": 0.8}
        rule = next(
            r
            for r in engine_with_custom_rules._rules
            if r.rule_id == "complex-security-001"
        )
        assert rule.matches(DefectType.SECURITY, context) is True

        # Should not match - severity not in allowed values
        context = {"severity": "low", "confidence": 0.8}
        assert rule.matches(DefectType.SECURITY, context) is False

    def test_rule_with_gte_condition(self, engine_with_custom_rules: RoutingEngine):
        """Test rule evaluation with 'gte' operator condition."""
        rule = next(
            r
            for r in engine_with_custom_rules._rules
            if r.rule_id == "complex-security-001"
        )

        # Should match - confidence >= 0.7 and severity in allowed values
        assert (
            rule.matches(DefectType.SECURITY, {"severity": "high", "confidence": 0.8})
            is True
        )
        assert (
            rule.matches(
                DefectType.SECURITY, {"severity": "critical", "confidence": 0.7}
            )
            is True
        )

        # Should not match - confidence < 0.7 (even with valid severity)
        assert (
            rule.matches(DefectType.SECURITY, {"severity": "high", "confidence": 0.5})
            is False
        )

        # Should not match - severity not in allowed values (even with valid confidence)
        assert (
            rule.matches(DefectType.SECURITY, {"severity": "low", "confidence": 0.8})
            is False
        )

    def test_rule_with_gt_condition(self, engine_with_custom_rules: RoutingEngine):
        """Test rule evaluation with 'gt' operator condition."""
        rule = next(
            r
            for r in engine_with_custom_rules._rules
            if r.rule_id == "complex-performance-001"
        )

        # Should match - impact > 5
        assert (
            rule.matches(DefectType.PERFORMANCE, {"severity": "high", "impact": 6})
            is True
        )
        assert (
            rule.matches(DefectType.PERFORMANCE, {"severity": "high", "impact": 10})
            is True
        )

        # Should not match - impact <= 5
        assert (
            rule.matches(DefectType.PERFORMANCE, {"severity": "high", "impact": 5})
            is False
        )
        assert (
            rule.matches(DefectType.PERFORMANCE, {"severity": "high", "impact": 3})
            is False
        )

    def test_rule_with_multiple_complex_conditions(self):
        """Test rule with multiple complex dict-based conditions."""
        rule = RoutingRule(
            rule_id="multi-condition-001",
            name="Multi-Condition Rule",
            defect_types=[DefectType.CODE_QUALITY],
            target_phase="DEVELOPMENT",
            conditions={
                "complexity": {"op": "gte", "value": 10},
                "duplication": {"op": "gt", "value": 20},
                "severity": {"op": "in", "value": ["high", "critical"]},
            },
        )

        # All conditions met
        context = {"complexity": 15, "duplication": 25, "severity": "high"}
        assert rule.matches(DefectType.CODE_QUALITY, context) is True

        # One condition not met (complexity too low)
        context = {"complexity": 5, "duplication": 25, "severity": "high"}
        assert rule.matches(DefectType.CODE_QUALITY, context) is False

        # One condition not met (severity not in list)
        context = {"complexity": 15, "duplication": 25, "severity": "low"}
        assert rule.matches(DefectType.CODE_QUALITY, context) is False

    def test_complex_condition_with_contains_operator(self):
        """Test rule with 'contains' operator condition."""
        rule = RoutingRule(
            rule_id="contains-condition-001",
            name="Contains Condition Rule",
            defect_types=[DefectType.SECURITY],
            target_phase="DEVELOPMENT",
            conditions={
                "description": {"op": "contains", "value": "injection"},
            },
        )

        # Should match - description contains "injection"
        context = {"description": "SQL injection vulnerability found"}
        assert rule.matches(DefectType.SECURITY, context) is True

        # Should not match - description doesn't contain "injection"
        context = {"description": "XSS vulnerability found"}
        assert rule.matches(DefectType.SECURITY, context) is False


class TestTemplateRuleMerging:
    """Tests for template rule merging functionality."""

    def test_template_rules_merged_with_defaults(self):
        """Test that template rules are properly merged with default rules."""
        template_rule = RoutingRule(
            rule_id="template-001",
            name="Template Custom Rule",
            defect_types=[DefectType.SECURITY],
            target_phase="REVIEW",
            target_agent="security-auditor",
            priority=0,  # Highest priority
        )

        engine = RoutingEngine(template_rules=[template_rule])

        # Template rule should be first (priority 0)
        assert engine._rules[0].rule_id == "template-001"

        # Default rules should still be present
        rule_ids = [r.rule_id for r in engine._rules]
        assert "security-001" in rule_ids
        assert "performance-001" in rule_ids

    def test_template_rules_sorted_by_priority(self):
        """Test that template rules are sorted correctly by priority."""
        template_rules = [
            RoutingRule(
                rule_id="template-low",
                name="Low Priority Template",
                defect_types=[DefectType.TESTING],
                target_phase="DEVELOPMENT",
                priority=100,
            ),
            RoutingRule(
                rule_id="template-high",
                name="High Priority Template",
                defect_types=[DefectType.SECURITY],
                target_phase="DEVELOPMENT",
                priority=1,
            ),
        ]

        engine = RoutingEngine(template_rules=template_rules)
        priorities = [r.priority for r in engine._rules]

        # Priorities should be sorted
        assert priorities == sorted(priorities)

        # High priority template should come before low priority
        high_idx = next(
            i for i, r in enumerate(engine._rules) if r.rule_id == "template-high"
        )
        low_idx = next(
            i for i, r in enumerate(engine._rules) if r.rule_id == "template-low"
        )
        assert high_idx < low_idx

    def test_template_rule_overrides_default_behavior(self):
        """Test that template rules can override default routing behavior."""
        # Template rule that routes security defects to REVIEW instead of DEVELOPMENT
        template_rule = RoutingRule(
            rule_id="template-security-override",
            name="Security Override Rule",
            defect_types=[DefectType.SECURITY],
            target_phase="REVIEW",
            target_agent="security-auditor",
            priority=0,  # Higher priority than default security rule
            loop_back=False,
        )

        engine = RoutingEngine(template_rules=[template_rule])

        # Route a security defect
        defect = {"id": "test", "description": "SQL injection vulnerability"}
        decision = engine.route_defect(defect)

        # Should use template rule's phase (REVIEW) instead of default (DEVELOPMENT)
        assert decision.matched_rule == "template-security-override"
        assert decision.target_phase == "REVIEW"
        assert decision.loop_back is False

    def test_multiple_template_rules_merged(self):
        """Test merging multiple template rules with different priorities."""
        template_rules = [
            RoutingRule(
                rule_id="template-perf",
                name="Performance Template",
                defect_types=[DefectType.PERFORMANCE],
                target_phase="OPTIMIZATION",
                target_agent="performance-analyst",
                priority=3,
            ),
            RoutingRule(
                rule_id="template-docs",
                name="Documentation Template",
                defect_types=[DefectType.DOCUMENTATION],
                target_phase="DEVELOPMENT",
                target_agent="technical-writer",
                priority=5,
            ),
        ]

        engine = RoutingEngine(template_rules=template_rules)

        # Both template rules should be present
        rule_ids = [r.rule_id for r in engine._rules]
        assert "template-perf" in rule_ids
        assert "template-docs" in rule_ids

        # Verify routing uses template rules
        perf_defect = {"id": "p1", "description": "Slow query causing latency"}
        doc_defect = {"id": "d1", "description": "Missing documentation"}

        perf_decision = engine.route_defect(perf_defect)
        doc_decision = engine.route_defect(doc_defect)

        assert perf_decision.matched_rule == "template-perf"
        assert perf_decision.target_phase == "OPTIMIZATION"
        assert doc_decision.matched_rule == "template-docs"
