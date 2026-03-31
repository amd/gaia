"""
Integration tests for GAIA template weight configuration.

Tests cover:
- Template weight integration with RecursivePipelineTemplate
- Weight configuration in template loader
- End-to-end weight application in scorer
"""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
import yaml

from gaia.pipeline.recursive_template import (
    RecursivePipelineTemplate,
    get_recursive_template,
)
from gaia.pipeline.template_loader import TemplateLoader
from gaia.quality.models import QualityWeightConfig
from gaia.quality.scorer import QualityScorer
from gaia.quality.weight_config import (
    QualityWeightConfigManager,
    get_profile,
)


class TestRecursivePipelineTemplateWeights:
    """Tests for weight configuration in RecursivePipelineTemplate."""

    def test_template_default_weights(self):
        """Test template has default weights."""
        template = get_recursive_template("generic")

        assert "code_quality" in template.quality_weights
        assert "testing" in template.quality_weights
        # Default weights should sum to ~1.0
        total = sum(template.quality_weights.values())
        assert abs(total - 1.0) < 0.01

    def test_template_with_weight_config(self):
        """Test template creation with QualityWeightConfig."""
        weight_config = QualityWeightConfig(
            name="custom",
            weights={
                "code_quality": 0.30,
                "testing": 0.30,
                "documentation": 0.20,
                "best_practices": 0.20,
            },
        )

        template = RecursivePipelineTemplate(
            name="test_template",
            description="Test with custom weights",
            weight_config=weight_config,
        )

        assert template.weight_config is weight_config
        assert template.quality_weights["code_quality"] == 0.30
        assert template.quality_weights["testing"] == 0.30

    def test_template_set_weight_profile(self):
        """Test setting weight profile on template."""
        template = get_recursive_template("generic")

        # Start with default (balanced)
        original_weights = template.quality_weights.copy()

        # Change to security_heavy profile
        template.set_weight_profile("security_heavy")

        assert template.quality_weights["best_practices"] == 0.30
        assert template.quality_weights != original_weights

    def test_template_set_weight_profile_nonexistent(self):
        """Test setting nonexistent profile raises error."""
        template = get_recursive_template("generic")

        with pytest.raises(KeyError):
            template.set_weight_profile("nonexistent_profile")

    def test_template_get_weight_config(self):
        """Test getting weight config from template."""
        # Create a fresh template to avoid contamination from other tests
        template = RecursivePipelineTemplate(
            name="test_fresh",
            description="Fresh test template",
            quality_weights={
                "code_quality": 0.25,
                "requirements_coverage": 0.25,
                "testing": 0.20,
                "documentation": 0.15,
                "best_practices": 0.15,
            },
        )

        config = template.get_weight_config()

        assert isinstance(config, QualityWeightConfig)
        assert config.name == "test_fresh_weights"

    def test_template_apply_weight_overrides(self):
        """Test applying weight overrides to template."""
        template = get_recursive_template("generic")

        original_testing = template.quality_weights.get("testing", 0.20)

        # Override testing weight
        template.apply_weight_overrides({"testing": 0.30})

        assert template.quality_weights["testing"] == 0.30
        # Other weights should be scaled
        template.validate_weights()

    def test_template_validate_weights(self):
        """Test weight validation on template."""
        template = RecursivePipelineTemplate(
            name="test",
            quality_weights={
                "code_quality": 0.25,
                "testing": 0.25,
                "documentation": 0.25,
                "best_practices": 0.25,
            },
        )

        assert template.validate_weights() is True

    def test_template_validate_weights_invalid(self):
        """Test validation rejects invalid weights."""
        template = RecursivePipelineTemplate(
            name="test",
            quality_weights={
                "code_quality": 0.50,
                "testing": 0.50,
                "documentation": 0.50,  # Total > 1.0
            },
        )

        with pytest.raises(ValueError):
            template.validate_weights()

    def test_template_weight_profiles_affect_scoring(self):
        """Test that different profiles affect scoring emphasis."""
        # Create templates with different profiles
        security_template = RecursivePipelineTemplate(
            name="security",
            quality_weights=get_profile("security_heavy").weights.copy(),
        )

        speed_template = RecursivePipelineTemplate(
            name="speed",
            quality_weights=get_profile("speed_heavy").weights.copy(),
        )

        # Security should weight best_practices higher
        assert (
            security_template.quality_weights["best_practices"]
            > speed_template.quality_weights["best_practices"]
        )

        # Speed should weight code_quality higher
        assert (
            speed_template.quality_weights["code_quality"]
            > security_template.quality_weights["code_quality"]
        )


class TestTemplateLoaderWeightIntegration:
    """Tests for weight configuration loading in TemplateLoader."""

    @pytest.fixture
    def loader(self) -> TemplateLoader:
        """Create template loader."""
        return TemplateLoader()

    def test_load_template_with_simple_weights(self, loader: TemplateLoader):
        """Test loading template with simple weight dict."""
        yaml_content = """
templates:
  test_weights:
    description: Test with simple weights
    configuration:
      quality_threshold: 0.85
    quality_weights:
      code_quality: 0.30
      testing: 0.30
      documentation: 0.20
      best_practices: 0.20
"""
        # Close before reading to avoid Windows exclusive-lock on NamedTemporaryFile
        with NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            tmp_path = f.name
        try:
            templates = loader.load_from_file(tmp_path)
            template = templates["test_weights"]

            assert template.quality_weights["code_quality"] == 0.30
            assert template.weight_config is not None
            template.weight_config.validate()
        finally:
            os.unlink(tmp_path)

    def test_load_template_with_full_weight_config(self, loader: TemplateLoader):
        """Test loading template with full QualityWeightConfig format."""
        yaml_content = """
templates:
  test_full_weights:
    description: Test with full weight config
    configuration:
      quality_threshold: 0.90
    quality_weights:
      name: enterprise_weights
      description: Enterprise weight configuration
      weights:
        code_quality: 0.20
        requirements_coverage: 0.20
        testing: 0.25
        documentation: 0.15
        best_practices: 0.20
      category_overrides:
        testing:
          TS-01: 0.12
"""
        with NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            tmp_path = f.name
        try:
            templates = loader.load_from_file(tmp_path)
            template = templates["test_full_weights"]

            assert template.weight_config is not None
            assert template.weight_config.name == "enterprise_weights"
            assert template.weight_config.category_overrides["testing"]["TS-01"] == 0.12
        finally:
            os.unlink(tmp_path)

    def test_load_template_without_weights(self, loader: TemplateLoader):
        """Test loading template without explicit weights uses defaults."""
        yaml_content = """
templates:
  test_no_weights:
    description: Test without weights
    configuration:
      quality_threshold: 0.80
"""
        with NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            tmp_path = f.name
        try:
            templates = loader.load_from_file(tmp_path)
            template = templates["test_no_weights"]

            # Should have default weights
            assert "code_quality" in template.quality_weights
            assert abs(sum(template.quality_weights.values()) - 1.0) < 0.01
        finally:
            os.unlink(tmp_path)


class TestScorerWeightIntegration:
    """Tests for weight configuration integration with QualityScorer."""

    @pytest.fixture
    def scorer(self) -> QualityScorer:
        """Create quality scorer."""
        return QualityScorer()

    @pytest.mark.asyncio
    async def test_evaluate_with_weight_config(self, scorer: QualityScorer):
        """Test evaluation with custom weight config."""
        weight_config = QualityWeightConfig(
            name="test_heavy",
            weights={
                "code_quality": 0.40,
                "requirements_coverage": 0.30,
                "testing": 0.15,
                "documentation": 0.10,
                "best_practices": 0.05,
            },
        )

        report = await scorer.evaluate(
            artifact="def add(a, b): return a + b",
            context={"requirements": ["Add numbers"]},
            weight_config=weight_config,
        )

        # Report should be generated successfully
        assert report.overall_score >= 0
        # Dimension weights should reflect custom config

    @pytest.mark.asyncio
    async def test_evaluate_with_weight_profile_in_context(self, scorer: QualityScorer):
        """Test evaluation with weight profile specified in context."""
        report = await scorer.evaluate(
            artifact="def add(a, b): return a + b",
            context={
                "requirements": ["Add numbers"],
                "weight_profile": "security_heavy",
            },
        )

        assert report.overall_score >= 0

    @pytest.mark.asyncio
    async def test_evaluate_with_invalid_weight_profile(self, scorer: QualityScorer):
        """Test evaluation gracefully handles invalid weight profile."""
        report = await scorer.evaluate(
            artifact="def add(a, b): return a + b",
            context={
                "requirements": ["Add numbers"],
                "weight_profile": "nonexistent_profile",
            },
        )

        # Should fall back to defaults and still work
        assert report.overall_score >= 0

    @pytest.mark.asyncio
    async def test_weight_config_affects_dimension_scoring(self, scorer: QualityScorer):
        """Test that weight config affects dimension contribution."""
        # Test with documentation-heavy weights
        doc_heavy = get_profile("documentation_heavy")

        report = await scorer.evaluate(
            artifact="def add(a, b): return a + b",
            context={"requirements": ["Add numbers"]},
            weight_config=doc_heavy,
        )

        # Documentation dimension should exist in the report
        # Dimension names use display names like "Documentation"
        doc_dimension = report.get_dimension_score("Documentation")
        if doc_dimension is None:
            # Try alternative name
            doc_dimension = report.get_dimension_score("documentation")
        assert (
            doc_dimension is not None
        ), "Documentation dimension should exist in report"

    @pytest.mark.asyncio
    async def test_evaluate_without_weight_config_uses_defaults(
        self, scorer: QualityScorer
    ):
        """Test that evaluation without weight_config uses defaults."""
        report = await scorer.evaluate(
            artifact="def add(a, b): return a + b",
            context={"requirements": ["Add numbers"]},
        )

        assert report.overall_score >= 0
        # Should use default CATEGORIES weights


class TestEndToEndWeightConfiguration:
    """End-to-end tests for complete weight configuration workflow."""

    def test_full_workflow_create_and_apply(self):
        """Test complete workflow: create config, apply to template, use in scorer."""
        # 1. Create custom weight config
        manager = QualityWeightConfigManager()
        custom_config = manager.create_custom_config(
            name="mobile_app",
            weights={
                "code_quality": 0.25,
                "requirements_coverage": 0.20,
                "testing": 0.30,  # Higher testing for mobile
                "documentation": 0.10,  # Less documentation
                "best_practices": 0.15,
            },
            category_overrides={
                "testing": {
                    "TS-04": 0.10,  # Mock/stub appropriateness important for mobile
                }
            },
        )

        # 2. Apply to template
        template = RecursivePipelineTemplate(
            name="mobile_pipeline",
            description="Mobile app development pipeline",
            weight_config=custom_config,
        )

        # 3. Verify template has correct weights
        assert template.quality_weights["testing"] == 0.30
        assert template.weight_config.category_overrides["testing"]["TS-04"] == 0.10

        # 4. Save config for reuse (close before re-opening to avoid Windows lock)
        with NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            tmp_path = f.name
        try:
            manager.save_to_yaml(custom_config, tmp_path)

            # 5. Load config back
            loaded_config = manager.load_from_yaml(tmp_path)
            assert loaded_config.name == "mobile_app"
            assert loaded_config.weights["testing"] == 0.30
        finally:
            os.unlink(tmp_path)

    def test_profile_comparison_workflow(self):
        """Test comparing different profiles for decision making."""
        manager = QualityWeightConfigManager()

        profiles_to_compare = ["balanced", "security_heavy", "speed_heavy"]

        comparison = {}
        for profile_name in profiles_to_compare:
            config = manager.get_profile(profile_name)
            comparison[profile_name] = {
                "code_quality": config.get_weight("code_quality"),
                "testing": config.get_weight("testing"),
                "documentation": config.get_weight("documentation"),
                "best_practices": config.get_weight("best_practices"),
            }

        # Verify security_heavy has highest best_practices weight
        assert (
            comparison["security_heavy"]["best_practices"]
            > comparison["balanced"]["best_practices"]
        )
        assert (
            comparison["security_heavy"]["best_practices"]
            > comparison["speed_heavy"]["best_practices"]
        )

        # Verify speed_heavy has highest code_quality weight
        assert (
            comparison["speed_heavy"]["code_quality"]
            > comparison["balanced"]["code_quality"]
        )

    def test_template_merge_and_override_workflow(self):
        """Test workflow of merging and overriding weights."""
        template = get_recursive_template("generic")

        # Start with balanced
        original = template.quality_weights.copy()

        # Merge with testing emphasis
        template.apply_weight_overrides({"testing": 0.30})
        assert template.quality_weights["testing"] == 0.30

        # Override again with documentation emphasis
        template.apply_weight_overrides({"documentation": 0.25})
        assert template.quality_weights["documentation"] == 0.25

        # Weights should still sum to 1.0
        template.validate_weights()
