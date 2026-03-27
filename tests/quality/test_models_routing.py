"""Tests for QualityReport.get_defects_by_type() and get_routing_decisions().

Import chain assertion: confirms that DefectType from gaia.pipeline.defect_types
is the real enum and not the fallback defined inside models.py.
"""
import pytest
from gaia.quality.models import QualityReport, CategoryScore, CertificationStatus
from gaia.pipeline.defect_types import DefectType

# ---------------------------------------------------------------------------
# Import chain assertion — fires at module collection time if broken.
# ---------------------------------------------------------------------------
from gaia.pipeline.defect_types import DefectType as RealDefectType
from gaia.quality.models import DefectType as ModelDefectType

assert ModelDefectType is RealDefectType, (
    "models.py is using the fallback DefectType enum. "
    "Ensure gaia.pipeline.defect_types is importable and the try/except "
    "in models.py resolved to the real enum."
)


# ---------------------------------------------------------------------------
# Helper factory functions
# ---------------------------------------------------------------------------


def make_category_score(
    category_id: str = "CQ-01",
    category_name: str = "Syntax Validity",
    defects: list = None,
) -> CategoryScore:
    """Build a CategoryScore with specified defects and plausible defaults."""
    return CategoryScore(
        category_id=category_id,
        category_name=category_name,
        weight=0.05,
        raw_score=85.0,
        weighted_score=4.25,
        validation_details={},
        defects=defects or [],
    )


def make_report(*category_scores: CategoryScore) -> QualityReport:
    """Build a QualityReport with the given CategoryScore instances."""
    return QualityReport(
        overall_score=85.0,
        certification_status=CertificationStatus.GOOD,
        category_scores=list(category_scores),
    )


# ---------------------------------------------------------------------------
# TestGetDefectsByType
# ---------------------------------------------------------------------------


class TestGetDefectsByType:
    """Tests for QualityReport.get_defects_by_type()."""

    def test_string_defect_type_match(self):
        """A single SECURITY defect is returned when queried by uppercase string "SECURITY"."""
        defect = {"defect_type": "SECURITY", "description": "sql_injection", "severity": "high"}
        report = make_report(make_category_score(defects=[defect]))

        result = report.get_defects_by_type("SECURITY")

        assert len(result) == 1
        assert result[0]["description"] == "sql_injection"

    def test_case_insensitive_matching(self):
        """Lowercase "security" and uppercase "SECURITY" must return the same defects."""
        defect = {"defect_type": "SECURITY", "description": "xss", "severity": "high"}
        report = make_report(make_category_score(defects=[defect]))

        result_lower = report.get_defects_by_type("security")
        result_upper = report.get_defects_by_type("SECURITY")

        assert len(result_lower) == 1
        assert len(result_upper) == 1
        assert result_lower == result_upper

    def test_no_match_returns_empty(self):
        """Querying for PERFORMANCE when only SECURITY defects exist returns empty list."""
        defect = {"defect_type": "SECURITY", "description": "auth bypass", "severity": "critical"}
        report = make_report(make_category_score(defects=[defect]))

        result = report.get_defects_by_type("PERFORMANCE")

        assert result == []

    def test_multiple_categories_aggregated(self):
        """Defects of the same type spread across multiple CategoryScores are all returned."""
        defect_a = {"defect_type": "SECURITY", "description": "xss in form A", "severity": "high"}
        defect_b = {"defect_type": "SECURITY", "description": "xss in form B", "severity": "high"}

        cs_a = make_category_score(category_id="BP-01", defects=[defect_a])
        cs_b = make_category_score(category_id="CQ-01", defects=[defect_b])
        report = make_report(cs_a, cs_b)

        result = report.get_defects_by_type("SECURITY")

        assert len(result) == 2
        descriptions = {d["description"] for d in result}
        assert "xss in form A" in descriptions
        assert "xss in form B" in descriptions

    def test_enum_value_with_name_attr(self):
        """
        Passing a DefectType enum instance (not a string) exercises the
        hasattr(defect_type_value, 'name') branch in get_defects_by_type().
        """
        # The defect dict stores the enum instance as defect_type value
        defect = {"defect_type": DefectType.SECURITY, "description": "enum-stored defect"}
        report = make_report(make_category_score(defects=[defect]))

        # Query with the enum
        result = report.get_defects_by_type(DefectType.SECURITY)

        assert len(result) == 1
        assert result[0]["description"] == "enum-stored defect"

    def test_empty_report_returns_empty(self):
        """A QualityReport with no category_scores returns an empty list."""
        report = make_report()  # no category scores

        result = report.get_defects_by_type("SECURITY")

        assert result == []

    def test_mixed_type_defects_filtered(self):
        """Only the defects matching the queried type are returned; others are excluded."""
        sec_defect = {"defect_type": "SECURITY", "description": "injection risk"}
        perf_defect = {"defect_type": "PERFORMANCE", "description": "slow query"}
        report = make_report(make_category_score(defects=[sec_defect, perf_defect]))

        result = report.get_defects_by_type("SECURITY")

        assert len(result) == 1
        assert result[0]["description"] == "injection risk"


# ---------------------------------------------------------------------------
# TestGetRoutingDecisions
# ---------------------------------------------------------------------------


class TestGetRoutingDecisions:
    """Tests for QualityReport.get_routing_decisions()."""

    def test_defect_with_routing_key_returned(self):
        """A defect dict that contains a "routing" key must be included in results."""
        routed_defect = {
            "description": "reroute to security team",
            "routing": "security-auditor",
            "severity": "high",
        }
        report = make_report(make_category_score(defects=[routed_defect]))

        result = report.get_routing_decisions()

        assert len(result) == 1
        assert result[0]["routing"] == "security-auditor"

    def test_defect_with_target_phase_key_returned(self):
        """A defect dict that contains "target_phase" must be included in results."""
        defect = {"description": "needs security review", "target_phase": "SECURITY_REVIEW"}
        report = make_report(make_category_score(defects=[defect]))

        result = report.get_routing_decisions()

        assert len(result) == 1
        assert result[0]["target_phase"] == "SECURITY_REVIEW"

    def test_defect_without_routing_excluded(self):
        """Plain defects with neither "routing" nor "target_phase" are excluded."""
        plain_defect = {"description": "minor issue", "severity": "low"}
        report = make_report(make_category_score(defects=[plain_defect]))

        result = report.get_routing_decisions()

        assert result == []

    def test_multiple_routed_defects(self):
        """Multiple defects with routing information are all returned."""
        defect_a = {"description": "sec issue", "routing": "security-auditor"}
        defect_b = {"description": "perf issue", "target_phase": "PERFORMANCE_REVIEW"}
        report = make_report(make_category_score(defects=[defect_a, defect_b]))

        result = report.get_routing_decisions()

        assert len(result) == 2

    def test_empty_report_returns_empty(self):
        """QualityReport with no category_scores returns an empty list."""
        report = make_report()

        result = report.get_routing_decisions()

        assert result == []

    def test_mixed_defects_only_routed_returned(self):
        """When the report mixes routed and plain defects, only routed ones are returned."""
        routed = {"description": "routed defect", "routing": "senior-developer"}
        plain = {"description": "plain defect", "severity": "low"}
        report = make_report(make_category_score(defects=[routed, plain]))

        result = report.get_routing_decisions()

        assert len(result) == 1
        assert result[0]["description"] == "routed defect"
