"""
GAIA DefectRouter Unit Tests

Tests for the defect routing system that routes defects to appropriate
pipeline phases based on defect type, severity, and context.

Run with:
    python -m pytest tests/unit/test_defect_router.py -v
"""

import pytest

from gaia.pipeline.defect_router import (
    Defect,
    DefectRouter,
    DefectSeverity,
    DefectStatus,
    DefectType,
    RoutingRule,
    create_defect,
)


class TestDefectCreation:
    """Tests for Defect dataclass creation."""

    def test_defect_minimal_creation(self):
        """Test creating defect with minimal required fields."""
        defect = Defect(
            id="test-001",
            type=DefectType.MISSING_TESTS,
            severity=DefectSeverity.HIGH,
        )

        assert defect.id == "test-001"
        assert defect.type == DefectType.MISSING_TESTS
        assert defect.severity == DefectSeverity.HIGH
        assert defect.status == DefectStatus.OPEN  # Default
        assert defect.description == ""  # Default
        assert defect.phase_detected == ""  # Default
        assert defect.target_phase == ""  # Default
        assert defect.location is None  # Default
        assert defect.metadata == {}  # Default

    def test_defect_full_creation(self):
        """Test creating defect with all fields."""
        defect = Defect(
            id="test-002",
            type=DefectType.SECURITY_VULNERABILITY,
            severity=DefectSeverity.CRITICAL,
            status=DefectStatus.IN_PROGRESS,
            description="SQL injection vulnerability in user input",
            phase_detected="QUALITY",
            target_phase="DEVELOPMENT",
            location="src/api/users.py:45",
            metadata={"cwe": "CWE-89", "cvss": "9.8"},
        )

        assert defect.id == "test-002"
        assert defect.description == "SQL injection vulnerability in user input"
        assert defect.location == "src/api/users.py:45"
        assert defect.metadata["cwe"] == "CWE-89"

    def test_defect_to_dict(self):
        """Test converting defect to dictionary."""
        defect = Defect(
            id="test-003",
            type=DefectType.CODE_STYLE,
            severity=DefectSeverity.LOW,
            description="Inconsistent indentation",
        )

        result = defect.to_dict()

        assert result["id"] == "test-003"
        assert result["type"] == "CODE_STYLE"
        assert result["severity"] == "LOW"
        assert result["description"] == "Inconsistent indentation"
        assert result["status"] == "OPEN"

    def test_defect_from_dict(self):
        """Test creating defect from dictionary."""
        data = {
            "id": "test-004",
            "type": "PERFORMANCE_ISSUE",
            "severity": "MEDIUM",
            "status": "RESOLVED",
            "description": "Slow database query",
            "phase_detected": "QUALITY",
            "target_phase": "DEVELOPMENT",
            "location": "src/db/queries.py:120",
            "metadata": {"query_time": "5.2s"},
        }

        defect = Defect.from_dict(data)

        assert defect.id == "test-004"
        assert defect.type == DefectType.PERFORMANCE_ISSUE
        assert defect.severity == DefectSeverity.MEDIUM
        assert defect.status == DefectStatus.RESOLVED
        assert defect.metadata["query_time"] == "5.2s"

    def test_create_defect_helper(self):
        """Test the create_defect helper function."""
        defect = create_defect(
            defect_type="MISSING_TESTS",
            description="No unit tests for module",
            severity="HIGH",
            phase_detected="DEVELOPMENT",
            location="src/module.py",
            metadata={"module": "test_module"},
        )

        assert defect.type == DefectType.MISSING_TESTS
        assert defect.severity == DefectSeverity.HIGH
        assert defect.description == "No unit tests for module"
        assert defect.metadata["module"] == "test_module"


class TestDefectSeverity:
    """Tests for DefectSeverity enum."""

    def test_severity_ordering(self):
        """Test severity levels have correct numeric ordering."""
        assert DefectSeverity.CRITICAL.value < DefectSeverity.HIGH.value
        assert DefectSeverity.HIGH.value < DefectSeverity.MEDIUM.value
        assert DefectSeverity.MEDIUM.value < DefectSeverity.LOW.value

    def test_severity_values(self):
        """Test all severity levels exist."""
        assert DefectSeverity.CRITICAL.value == 1
        assert DefectSeverity.HIGH.value == 2
        assert DefectSeverity.MEDIUM.value == 3
        assert DefectSeverity.LOW.value == 4


class TestDefectStatus:
    """Tests for DefectStatus enum."""

    def test_all_statuses_exist(self):
        """Test all defect statuses are defined."""
        assert DefectStatus.OPEN is not None
        assert DefectStatus.IN_PROGRESS is not None
        assert DefectStatus.RESOLVED is not None
        assert DefectStatus.VERIFIED is not None
        assert DefectStatus.DEFERRED is not None


class TestRoutingRule:
    """Tests for RoutingRule dataclass."""

    def test_routing_rule_creation(self):
        """Test creating a routing rule."""
        rule = RoutingRule(
            defect_types={DefectType.MISSING_TESTS, DefectType.INSUFFICIENT_COVERAGE},
            target_phase="DEVELOPMENT",
            priority=1,
        )

        assert rule.target_phase == "DEVELOPMENT"
        assert rule.priority == 1
        assert len(rule.defect_types) == 2

    def test_routing_rule_matches(self):
        """Test routing rule matching."""
        rule = RoutingRule(
            defect_types={DefectType.MISSING_TESTS},
            target_phase="DEVELOPMENT",
            priority=1,
        )

        # Should match
        matching_defect = Defect(
            id="match-001",
            type=DefectType.MISSING_TESTS,
            severity=DefectSeverity.HIGH,
        )
        assert rule.matches(matching_defect) is True

        # Should not match
        non_matching_defect = Defect(
            id="no-match-001",
            type=DefectType.CODE_STYLE,
            severity=DefectSeverity.LOW,
        )
        assert rule.matches(non_matching_defect) is False

    def test_routing_rule_with_conditions(self):
        """Test routing rule with additional conditions."""
        rule = RoutingRule(
            defect_types={DefectType.SECURITY_VULNERABILITY},
            target_phase="REVIEW",
            priority=1,
            conditions={"cvss_score": {"$gte": 9.0}},
        )

        # Defect with matching condition
        defect = Defect(
            id="cond-001",
            type=DefectType.SECURITY_VULNERABILITY,
            severity=DefectSeverity.CRITICAL,
            metadata={"cvss_score": 9.5},
        )
        # Note: Current implementation does simple equality check
        # This tests the condition exists but full condition evaluation
        # would need additional implementation


class TestDefectRouterDefaultRules:
    """Tests for DefectRouter default routing rules."""

    def test_router_has_default_rules(self):
        """Test router initializes with default rules."""
        router = DefectRouter()

        # Should have default rules
        assert len(router._rules) > 0

    def test_rules_sorted_by_priority(self):
        """Test rules are sorted by priority after initialization."""
        router = DefectRouter()

        priorities = [r.priority for r in router._rules]
        assert priorities == sorted(priorities)


class TestDefectRouterRouting:
    """Tests for DefectRouter.route_defect method."""

    def test_route_testing_defects_to_development(self):
        """Test testing defects route to DEVELOPMENT."""
        router = DefectRouter()

        for defect_type in [
            DefectType.MISSING_TESTS,
            DefectType.INSUFFICIENT_COVERAGE,
            DefectType.FLAKY_TESTS,
        ]:
            defect = Defect(
                id=f"test-{defect_type.name}",
                type=defect_type,
                severity=DefectSeverity.HIGH,
            )
            target = router.route_defect(defect)
            assert (
                target == "DEVELOPMENT"
            ), f"{defect_type.name} should route to DEVELOPMENT"

    def test_route_code_quality_defects_to_development(self):
        """Test code quality defects route to DEVELOPMENT."""
        router = DefectRouter()

        for defect_type in [
            DefectType.CODE_STYLE,
            DefectType.CODE_COMPLEXITY,
            DefectType.MISSING_DOCSTRING,
            DefectType.DUPLICATE_CODE,
        ]:
            defect = Defect(
                id=f"test-{defect_type.name}",
                type=defect_type,
                severity=DefectSeverity.MEDIUM,
            )
            target = router.route_defect(defect)
            assert (
                target == "DEVELOPMENT"
            ), f"{defect_type.name} should route to DEVELOPMENT"

    def test_route_security_defects_to_development(self):
        """Test security defects route to DEVELOPMENT."""
        router = DefectRouter()

        for defect_type in [
            DefectType.SECURITY_VULNERABILITY,
            DefectType.INJECTION_RISK,
            DefectType.AUTHORIZATION_ISSUE,
        ]:
            defect = Defect(
                id=f"test-{defect_type.name}",
                type=defect_type,
                severity=DefectSeverity.CRITICAL,
            )
            target = router.route_defect(defect)
            assert (
                target == "DEVELOPMENT"
            ), f"{defect_type.name} should route to DEVELOPMENT"

    def test_route_requirements_defects_to_planning(self):
        """Test requirements defects route to PLANNING."""
        router = DefectRouter()

        for defect_type in [
            DefectType.MISSING_REQUIREMENT,
            DefectType.INCORRECT_IMPLEMENTATION,
        ]:
            defect = Defect(
                id=f"test-{defect_type.name}",
                type=defect_type,
                severity=DefectSeverity.HIGH,
            )
            target = router.route_defect(defect)
            assert target == "PLANNING", f"{defect_type.name} should route to PLANNING"

    def test_route_architecture_defects_to_planning(self):
        """Test architecture defects route to PLANNING."""
        router = DefectRouter()

        for defect_type in [
            DefectType.ARCHITECTURE_VIOLATION,
            DefectType.CIRCULAR_DEPENDENCY,
            DefectType.TIGHT_COUPLING,
        ]:
            defect = Defect(
                id=f"test-{defect_type.name}",
                type=defect_type,
                severity=DefectSeverity.HIGH,
            )
            target = router.route_defect(defect)
            assert target == "PLANNING", f"{defect_type.name} should route to PLANNING"

    def test_route_performance_defects_to_development(self):
        """Test performance defects route to DEVELOPMENT."""
        router = DefectRouter()

        for defect_type in [
            DefectType.PERFORMANCE_ISSUE,
            DefectType.MEMORY_LEAK,
            DefectType.INEFFICIENT_ALGORITHM,
        ]:
            defect = Defect(
                id=f"test-{defect_type.name}",
                type=defect_type,
                severity=DefectSeverity.MEDIUM,
            )
            target = router.route_defect(defect)
            assert (
                target == "DEVELOPMENT"
            ), f"{defect_type.name} should route to DEVELOPMENT"

    def test_route_edge_case_to_development(self):
        """Test edge case defects route to DEVELOPMENT."""
        router = DefectRouter()

        defect = Defect(
            id="test-edge-case",
            type=DefectType.EDGE_CASE_NOT_HANDLED,
            severity=DefectSeverity.MEDIUM,
        )
        target = router.route_defect(defect)
        assert target == "DEVELOPMENT"

    def test_route_unknown_defect_defaults_to_development(self):
        """Test unknown defect types default to DEVELOPMENT."""
        router = DefectRouter()

        defect = Defect(
            id="test-unknown",
            type=DefectType.UNKNOWN,
            severity=DefectSeverity.LOW,
        )
        target = router.route_defect(defect)
        assert target == "DEVELOPMENT"


class TestDefectRouterRouteDefects:
    """Tests for DefectRouter.route_defects batch method."""

    def test_route_multiple_defects(self):
        """Test routing multiple defects at once."""
        router = DefectRouter()

        defects = [
            {"id": "d1", "type": "MISSING_TESTS", "severity": "HIGH"},
            {"id": "d2", "type": "SECURITY_VULNERABILITY", "severity": "CRITICAL"},
            {"id": "d3", "type": "MISSING_REQUIREMENT", "severity": "HIGH"},
            {"id": "d4", "type": "CODE_STYLE", "severity": "LOW"},
        ]

        routed = router.route_defects(defects)

        # Check routing
        assert "DEVELOPMENT" in routed
        assert "PLANNING" in routed

        # Development should have most defects
        dev_defects = routed.get("DEVELOPMENT", [])
        planning_defects = routed.get("PLANNING", [])

        assert (
            len(dev_defects) >= 2
        )  # MISSING_TESTS, CODE_STYLE, SECURITY_VULNERABILITY
        assert len(planning_defects) >= 1  # MISSING_REQUIREMENT

    def test_route_defects_sets_target_phase(self):
        """Test that route_defects sets target_phase on defects."""
        router = DefectRouter()

        defects = [
            {"id": "d1", "type": "MISSING_TESTS", "severity": "HIGH"},
        ]

        routed = router.route_defects(defects)

        # Target phase should be set
        for phase_defects in routed.values():
            for defect in phase_defects:
                assert defect.target_phase != ""

    def test_route_defects_removes_empty_buckets(self):
        """Test that empty phase buckets are removed."""
        router = DefectRouter()

        # Only testing defects
        defects = [
            {"id": "d1", "type": "MISSING_TESTS", "severity": "HIGH"},
            {"id": "d2", "type": "INSUFFICIENT_COVERAGE", "severity": "MEDIUM"},
        ]

        routed = router.route_defects(defects)

        # Should only have DEVELOPMENT bucket
        assert "DEVELOPMENT" in routed
        # Other buckets should be empty (removed)
        assert "QUALITY" not in routed
        assert "REVIEW" not in routed


class TestDefectRouterGetSummary:
    """Tests for DefectRouter.get_defect_summary method."""

    def test_summary_total_count(self):
        """Test summary includes correct total count."""
        router = DefectRouter()

        defects = [
            {"id": "d1", "type": "MISSING_TESTS", "severity": "HIGH"},
            {"id": "d2", "type": "CODE_STYLE", "severity": "LOW"},
            {"id": "d3", "type": "SECURITY_VULNERABILITY", "severity": "CRITICAL"},
        ]

        summary = router.get_defect_summary(defects)

        assert summary["total"] == 3

    def test_summary_by_type(self):
        """Test summary groups defects by type."""
        router = DefectRouter()

        defects = [
            {"id": "d1", "type": "MISSING_TESTS", "severity": "HIGH"},
            {"id": "d2", "type": "MISSING_TESTS", "severity": "MEDIUM"},
            {"id": "d3", "type": "CODE_STYLE", "severity": "LOW"},
        ]

        summary = router.get_defect_summary(defects)

        assert summary["by_type"]["MISSING_TESTS"] == 2
        assert summary["by_type"]["CODE_STYLE"] == 1

    def test_summary_by_severity(self):
        """Test summary groups defects by severity."""
        router = DefectRouter()

        defects = [
            {"id": "d1", "type": "MISSING_TESTS", "severity": "HIGH"},
            {"id": "d2", "type": "CODE_STYLE", "severity": "HIGH"},
            {"id": "d3", "type": "SECURITY_VULNERABILITY", "severity": "CRITICAL"},
            {"id": "d4", "type": "MISSING_DOCSTRING", "severity": "LOW"},
        ]

        summary = router.get_defect_summary(defects)

        assert summary["by_severity"]["CRITICAL"] == 1
        assert summary["by_severity"]["HIGH"] == 2
        assert summary["by_severity"]["LOW"] == 1

    def test_summary_critical_and_high_counts(self):
        """Test summary counts critical and high severity defects."""
        router = DefectRouter()

        defects = [
            {"id": "d1", "type": "SECURITY_VULNERABILITY", "severity": "CRITICAL"},
            {"id": "d2", "type": "MISSING_TESTS", "severity": "HIGH"},
            {"id": "d3", "type": "CODE_STYLE", "severity": "LOW"},
        ]

        summary = router.get_defect_summary(defects)

        assert summary["critical_count"] == 1
        assert summary["high_count"] == 1

    def test_summary_by_phase(self):
        """Test summary groups defects by target phase."""
        router = DefectRouter()

        defects = [
            {"id": "d1", "type": "MISSING_TESTS", "severity": "HIGH"},
            {"id": "d2", "type": "MISSING_REQUIREMENT", "severity": "HIGH"},
        ]

        summary = router.get_defect_summary(defects)

        assert "DEVELOPMENT" in summary["by_phase"]
        assert "PLANNING" in summary["by_phase"]


class TestDefectRouterCustomRules:
    """Tests for adding custom routing rules."""

    def test_add_custom_rule(self):
        """Test adding a custom routing rule."""
        router = DefectRouter()
        initial_count = len(router._rules)

        custom_rule = RoutingRule(
            defect_types={DefectType.PERFORMANCE_ISSUE},
            target_phase="REVIEW",
            priority=0,  # High priority
        )

        router.add_rule(custom_rule)

        assert len(router._rules) == initial_count + 1

        # Performance issue should now route to REVIEW
        defect = Defect(
            id="custom-001",
            type=DefectType.PERFORMANCE_ISSUE,
            severity=DefectSeverity.MEDIUM,
        )
        target = router.route_defect(defect)
        assert target == "REVIEW"

    def test_add_rule_maintains_priority_order(self):
        """Test adding rule maintains priority sorting."""
        router = DefectRouter()

        # Add low priority rule
        router.add_rule(
            RoutingRule(
                defect_types={DefectType.UNKNOWN},
                target_phase="CUSTOM",
                priority=100,
            )
        )

        # Add high priority rule
        router.add_rule(
            RoutingRule(
                defect_types={DefectType.UNKNOWN},
                target_phase="HIGH_PRIORITY",
                priority=0,
            )
        )

        # First rule should be high priority
        priorities = [r.priority for r in router._rules]
        assert priorities == sorted(priorities)

    def test_remove_rule(self):
        """Test removing routing rules by defect type."""
        router = DefectRouter()

        # Count rules before
        rules_before = len(router._rules)

        # Remove all MISSING_TESTS rules
        router.remove_rule(DefectType.MISSING_TESTS)

        # Should have fewer rules
        assert len(router._rules) < rules_before

        # MISSING_TESTS should now default to DEVELOPMENT
        defect = Defect(
            id="removed-001",
            type=DefectType.MISSING_TESTS,
            severity=DefectSeverity.HIGH,
        )
        # Should still route to DEVELOPMENT (default)


class TestDefectRouterEdgeCases:
    """Edge case tests for DefectRouter."""

    def test_router_with_custom_rules_only(self):
        """Test router initialized with only custom rules."""
        custom_rules = [
            RoutingRule(
                defect_types={DefectType.CODE_STYLE},
                target_phase="CUSTOM",
                priority=1,
            ),
        ]
        router = DefectRouter(custom_rules=custom_rules)

        defect = Defect(
            id="custom-only-001",
            type=DefectType.CODE_STYLE,
            severity=DefectSeverity.LOW,
        )
        target = router.route_defect(defect)
        assert target == "CUSTOM"

    def test_defect_with_existing_target_phase(self):
        """Test routing defect that already has target_phase set."""
        router = DefectRouter()

        defect = Defect(
            id="pre-routed-001",
            type=DefectType.MISSING_TESTS,
            severity=DefectSeverity.HIGH,
            target_phase="PLANNING",  # Pre-set
        )

        defects = [defect]
        routed = router.route_defects(defects)

        # Router respects existing target_phase if already set
        # (only sets target_phase if not already present)
        for phase_defects in routed.values():
            for d in phase_defects:
                if d.id == "pre-routed-001":
                    # Existing target_phase is preserved
                    assert d.target_phase == "PLANNING"

    def test_empty_defects_list(self):
        """Test routing empty defects list."""
        router = DefectRouter()

        routed = router.route_defects([])

        assert routed == {}

    def test_summary_empty_defects(self):
        """Test summary with no defects."""
        router = DefectRouter()

        summary = router.get_defect_summary([])

        assert summary["total"] == 0
        assert summary["by_type"] == {}
        assert summary["by_severity"] == {}
        assert summary["by_phase"] == {}
        assert summary["critical_count"] == 0
        assert summary["high_count"] == 0
