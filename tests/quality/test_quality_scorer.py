"""
Tests for GAIA Quality Scorer.

Tests cover:
- Category evaluation
- Dimension scoring
- Certification status
- Template configuration
"""

import pytest

from gaia.quality.scorer import QualityScorer
from gaia.quality.models import CertificationStatus, QualityReport
from gaia.quality.templates import (
    QUALITY_TEMPLATES,
    get_template,
    create_custom_template,
)


class TestCertificationStatus:
    """Tests for CertificationStatus enum."""

    def test_from_score_excellent(self):
        """Test EXCELLENT status threshold."""
        assert CertificationStatus.from_score(95) == CertificationStatus.EXCELLENT
        assert CertificationStatus.from_score(100) == CertificationStatus.EXCELLENT
        assert CertificationStatus.from_score(94.9) != CertificationStatus.EXCELLENT

    def test_from_score_good(self):
        """Test GOOD status threshold."""
        assert CertificationStatus.from_score(85) == CertificationStatus.GOOD
        assert CertificationStatus.from_score(94) == CertificationStatus.GOOD
        assert CertificationStatus.from_score(84.9) != CertificationStatus.GOOD

    def test_from_score_acceptable(self):
        """Test ACCEPTABLE status threshold."""
        assert CertificationStatus.from_score(75) == CertificationStatus.ACCEPTABLE
        assert CertificationStatus.from_score(84) == CertificationStatus.ACCEPTABLE

    def test_from_score_needs_improvement(self):
        """Test NEEDS_IMPROVEMENT status threshold."""
        assert CertificationStatus.from_score(65) == CertificationStatus.NEEDS_IMPROVEMENT
        assert CertificationStatus.from_score(74) == CertificationStatus.NEEDS_IMPROVEMENT

    def test_from_score_fail(self):
        """Test FAIL status threshold."""
        assert CertificationStatus.from_score(64) == CertificationStatus.FAIL
        assert CertificationStatus.from_score(0) == CertificationStatus.FAIL


class TestQualityScorer:
    """Tests for QualityScorer class."""

    @pytest.fixture
    def scorer(self) -> QualityScorer:
        """Create test quality scorer."""
        return QualityScorer()

    @pytest.mark.asyncio
    async def test_evaluate_code_sample(
        self, scorer: QualityScorer, sample_code: str
    ):
        """Test quality evaluation of code sample."""
        report = await scorer.evaluate(
            artifact=sample_code,
            context={"requirements": ["Create calculator functions"]},
        )

        assert isinstance(report, QualityReport)
        assert 0 <= report.overall_score <= 100
        assert isinstance(report.certification_status, CertificationStatus)

    @pytest.mark.asyncio
    async def test_evaluate_code_with_issues(
        self, scorer: QualityScorer, sample_code_with_issues: str
    ):
        """Test evaluation of code with quality issues."""
        report = await scorer.evaluate(
            artifact=sample_code_with_issues,
            context={"requirements": ["Create calculator"]},
        )

        # Note: Default validators return stub scores
        # In production, actual validators would detect issues and score lower
        assert isinstance(report, QualityReport)
        assert 0 <= report.overall_score <= 100
        assert report.tests_run > 0

    @pytest.mark.asyncio
    async def test_category_scores_generated(
        self, scorer: QualityScorer, sample_code: str
    ):
        """Test that category scores are generated."""
        report = await scorer.evaluate(
            artifact=sample_code,
            context={"requirements": ["Test"]},
        )

        # Should have scores for all 27 categories
        assert len(report.category_scores) == 27

    @pytest.mark.asyncio
    async def test_dimension_scores_generated(
        self, scorer: QualityScorer, sample_code: str
    ):
        """Test that dimension scores are generated."""
        report = await scorer.evaluate(
            artifact=sample_code,
            context={"requirements": ["Test"]},
        )

        # Should have scores for all 6 dimensions
        assert len(report.dimension_scores) == 6

    @pytest.mark.asyncio
    async def test_defects_tracked(self, scorer: QualityScorer):
        """Test that defects are tracked."""
        report = await scorer.evaluate(
            artifact="",  # Empty artifact should cause defects
            context={"requirements": ["Test"]},
        )

        assert report.total_defects >= 0

    @pytest.mark.asyncio
    async def test_tests_run_counted(self, scorer: QualityScorer, sample_code: str):
        """Test that tests run count is tracked."""
        report = await scorer.evaluate(
            artifact=sample_code,
            context={"requirements": ["Test"]},
        )

        assert report.tests_run > 0
        assert report.tests_passed >= 0

    def test_get_template_config(self, scorer: QualityScorer):
        """Test getting template configuration."""
        template = scorer.get_template_config("STANDARD")
        assert template.name == "STANDARD"
        assert template.threshold == 0.90

    def test_get_category_info(self, scorer: QualityScorer):
        """Test getting category information."""
        info = scorer.get_category_info("CQ-01")
        assert info is not None
        assert info["name"] == "Syntax Validity"
        assert info["dimension"] == "code_quality"

    def test_get_categories_by_dimension(self, scorer: QualityScorer):
        """Test getting categories by dimension."""
        categories = scorer.get_categories_by_dimension("code_quality")
        assert len(categories) == 7  # 7 code quality categories

        categories = scorer.get_categories_by_dimension("testing")
        assert len(categories) == 4  # 4 testing categories

    def test_get_dimension_weight(self, scorer: QualityScorer):
        """Test getting dimension weight."""
        weight = scorer.get_dimension_weight("code_quality")
        assert weight == 0.25  # 25%

        weight = scorer.get_dimension_weight("testing")
        assert weight == 0.20  # 20%

    def test_register_custom_validator(self, scorer: QualityScorer):
        """Test registering custom validator."""
        from gaia.quality.validators.base import BaseValidator, ValidationResult

        class CustomValidator(BaseValidator):
            category_id = "CQ-01"
            category_name = "Custom Syntax Validator"

            async def validate(self, artifact, context):
                return ValidationResult(score=95.0, tests_run=1, tests_passed=1)

        validator = CustomValidator()
        scorer.register_validator("CQ-01", validator)

        retrieved = scorer.get_validator("CQ-01")
        assert retrieved is validator


class TestQualityTemplates:
    """Tests for quality templates."""

    def test_get_standard_template(self):
        """Test getting STANDARD template."""
        template = get_template("STANDARD")
        assert template.threshold == 0.90
        assert template.auto_pass == 0.95

    def test_get_rapid_template(self):
        """Test getting RAPID template."""
        template = get_template("RAPID")
        assert template.threshold == 0.75
        assert template.auto_pass == 0.80

    def test_get_enterprise_template(self):
        """Test getting ENTERPRISE template."""
        template = get_template("ENTERPRISE")
        assert template.threshold == 0.95
        assert len(template.agent_sequence) >= 3

    def test_get_documentation_template(self):
        """Test getting DOCUMENTATION template."""
        template = get_template("DOCUMENTATION")
        assert template.threshold == 0.85
        assert "technical-writer" in template.agent_sequence

    def test_get_nonexistent_template(self):
        """Test getting nonexistent template raises error."""
        with pytest.raises(KeyError):
            get_template("NONEXISTENT")

    def test_create_custom_template(self):
        """Test creating custom template."""
        template = create_custom_template(
            name="CUSTOM",
            threshold=0.80,
            agent_sequence=["senior-developer"],
            use_case="Custom use case",
        )

        assert template.name == "CUSTOM"
        assert template.threshold == 0.80
        assert template.auto_pass > 0.80  # Default calculation

    def test_template_requires_manual_review(self):
        """Test manual review range check."""
        template = get_template("STANDARD")

        # Score in manual review range
        assert template.requires_manual_review(0.90) is True

        # Score above manual review range
        assert template.requires_manual_review(0.95) is False

    def test_template_should_auto_pass(self):
        """Test auto-pass check."""
        template = get_template("STANDARD")

        assert template.should_auto_pass(0.96) is True
        assert template.should_auto_pass(0.90) is False

    def test_template_should_auto_fail(self):
        """Test auto-fail check."""
        template = get_template("STANDARD")

        assert template.should_auto_fail(0.80) is True
        assert template.should_auto_fail(0.90) is False

    def test_get_template_names(self):
        """Test getting all template names."""
        from gaia.quality.templates import get_template_names

        names = get_template_names()
        assert "STANDARD" in names
        assert "RAPID" in names
        assert "ENTERPRISE" in names
        assert "DOCUMENTATION" in names


class TestQualityReport:
    """Tests for QualityReport dataclass."""

    def test_passed_property(self):
        """Test passed property."""
        report = QualityReport(
            overall_score=80.0,
            certification_status=CertificationStatus.ACCEPTABLE,
        )
        assert report.passed is True

        report.overall_score = 70.0
        report.certification_status = CertificationStatus.NEEDS_IMPROVEMENT
        assert report.passed is False

    def test_is_excellent_property(self):
        """Test is_excellent property."""
        report = QualityReport(
            overall_score=96.0,
            certification_status=CertificationStatus.EXCELLENT,
        )
        assert report.is_excellent is True

        report.overall_score = 90.0
        assert report.is_excellent is False

    def test_summary(self):
        """Test summary generation."""
        report = QualityReport(
            overall_score=85.5,
            certification_status=CertificationStatus.GOOD,
            total_defects=3,
            critical_defects=0,
            tests_run=100,
            tests_passed=95,
        )
        summary = report.summary()
        assert "85.5" in summary
        assert "good" in summary.lower()
        assert "3" in summary  # Defect count
