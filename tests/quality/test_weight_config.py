"""
Tests for GAIA Quality Weight Configuration System.

Tests cover:
- QualityWeightConfig dataclass
- QualityWeightConfigManager
- Pre-defined profiles
- YAML/JSON loading
- Weight merging and overrides
"""

import os
import pytest
import json
import yaml
from pathlib import Path
from tempfile import NamedTemporaryFile

from gaia.quality.models import QualityWeightConfig
from gaia.quality.weight_config import (
    QualityWeightConfigManager,
    PROFILES,
    get_manager,
    get_profile,
    get_default_profile,
)


class TestQualityWeightConfig:
    """Tests for QualityWeightConfig dataclass."""

    def test_create_basic_config(self):
        """Test creating basic weight config."""
        config = QualityWeightConfig(
            name="test",
            weights={
                "code_quality": 0.25,
                "testing": 0.25,
                "documentation": 0.25,
                "best_practices": 0.25,
            },
        )

        assert config.name == "test"
        assert len(config.weights) == 4
        assert config.description == ""

    def test_validate_weights_sum_to_one(self):
        """Test that weights must sum to 1.0."""
        config = QualityWeightConfig(
            name="valid",
            weights={
                "code_quality": 0.25,
                "testing": 0.25,
                "documentation": 0.25,
                "best_practices": 0.25,
            },
        )

        assert config.validate() is True

    def test_validate_weights_reject_invalid_sum(self):
        """Test that invalid weight sums are rejected."""
        config = QualityWeightConfig(
            name="invalid",
            weights={
                "code_quality": 0.50,
                "testing": 0.50,
                "documentation": 0.50,  # Total = 1.50
            },
        )

        with pytest.raises(ValueError, match="sum to"):
            config.validate()

    def test_validate_with_tolerance(self):
        """Test validation with tolerance."""
        # 0.999 should pass with default 0.01 tolerance
        config = QualityWeightConfig(
            name="near_one",
            weights={
                "code_quality": 0.333,
                "testing": 0.333,
                "documentation": 0.334,
            },
        )

        assert config.validate() is True

    def test_get_weight(self):
        """Test getting weight for dimension."""
        config = QualityWeightConfig(
            name="test",
            weights={
                "code_quality": 0.30,
                "testing": 0.20,
            },
        )

        assert config.get_weight("code_quality") == 0.30
        assert config.get_weight("testing") == 0.20
        assert config.get_weight("nonexistent") == 0.0

    def test_get_category_weight_no_override(self):
        """Test getting category weight without override."""
        config = QualityWeightConfig(
            name="test",
            weights={"code_quality": 0.25},
        )

        result = config.get_category_weight("code_quality", "CQ-01", 0.05)
        assert result == 0.05  # Returns default

    def test_get_category_weight_with_override(self):
        """Test getting category weight with override."""
        config = QualityWeightConfig(
            name="test",
            weights={"code_quality": 0.25},
            category_overrides={
                "code_quality": {
                    "CQ-01": 0.10,
                    "CQ-02": 0.05,
                }
            },
        )

        assert config.get_category_weight("code_quality", "CQ-01", 0.05) == 0.10
        assert config.get_category_weight("code_quality", "CQ-02", 0.05) == 0.05
        # Non-overridden category returns default
        assert config.get_category_weight("code_quality", "CQ-99", 0.05) == 0.05

    def test_to_dict(self):
        """Test conversion to dictionary."""
        config = QualityWeightConfig(
            name="test",
            weights={"code_quality": 0.25},
            category_overrides={"code_quality": {"CQ-01": 0.10}},
            description="Test config",
        )

        result = config.to_dict()

        assert result["name"] == "test"
        assert result["weights"] == {"code_quality": 0.25}
        assert result["category_overrides"]["code_quality"]["CQ-01"] == 0.10
        assert result["description"] == "Test config"
        assert "total_weight" in result

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "name": "imported",
            "weights": {"code_quality": 0.30, "testing": 0.70},
            "category_overrides": {},
            "description": "Imported config",
        }

        config = QualityWeightConfig.from_dict(data)

        assert config.name == "imported"
        assert config.weights["code_quality"] == 0.30
        assert config.description == "Imported config"

    def test_from_dict_minimal(self):
        """Test creation from minimal dictionary."""
        data = {
            "weights": {"code_quality": 0.25, "testing": 0.75},
        }

        config = QualityWeightConfig.from_dict(data)

        assert config.name == "custom"  # Default name
        assert config.description == ""  # Default description


class TestPredefinedProfiles:
    """Tests for pre-defined weight profiles."""

    def test_profiles_exist(self):
        """Test that pre-defined profiles exist."""
        assert "balanced" in PROFILES
        assert "security_heavy" in PROFILES
        assert "speed_heavy" in PROFILES
        assert "documentation_heavy" in PROFILES

    def test_balanced_profile_weights(self):
        """Test balanced profile weights sum to 1.0."""
        weights = PROFILES["balanced"]
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01

    def test_security_heavy_profile(self):
        """Test security_heavy profile emphasizes best_practices."""
        weights = PROFILES["security_heavy"]
        # best_practices should be highest or among highest
        assert weights["best_practices"] == 0.30
        assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)

    def test_speed_heavy_profile(self):
        """Test speed_heavy profile de-emphasizes documentation."""
        weights = PROFILES["speed_heavy"]
        # documentation should be lowest
        assert weights["documentation"] == 0.05
        assert weights["code_quality"] == 0.35  # Highest
        assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)

    def test_documentation_heavy_profile(self):
        """Test documentation_heavy profile emphasizes documentation."""
        weights = PROFILES["documentation_heavy"]
        assert weights["documentation"] == 0.30  # Highest
        assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)


class TestQualityWeightConfigManager:
    """Tests for QualityWeightConfigManager."""

    @pytest.fixture
    def manager(self) -> QualityWeightConfigManager:
        """Create test manager."""
        return QualityWeightConfigManager()

    def test_get_profile_balanced(self, manager: QualityWeightConfigManager):
        """Test getting balanced profile."""
        config = manager.get_profile("balanced")

        assert config.name == "balanced"
        assert "code_quality" in config.weights
        config.validate()  # Should not raise

    def test_get_profile_nonexistent(self, manager: QualityWeightConfigManager):
        """Test getting nonexistent profile raises error."""
        with pytest.raises(KeyError, match="not found"):
            manager.get_profile("nonexistent")

    def test_get_default_profile(self, manager: QualityWeightConfigManager):
        """Test getting default profile."""
        config = manager.get_default_profile()
        assert config.name == "balanced"

    def test_create_custom_config(self, manager: QualityWeightConfigManager):
        """Test creating custom configuration."""
        config = manager.create_custom_config(
            name="custom_test",
            weights={"code_quality": 0.40, "testing": 0.60},
            description="Custom test config",
        )

        assert config.name == "custom_test"
        assert config.weights["code_quality"] == 0.40
        config.validate()

    def test_create_custom_config_invalid(self, manager: QualityWeightConfigManager):
        """Test creating custom config with invalid weights."""
        with pytest.raises(ValueError):
            manager.create_custom_config(
                name="invalid",
                weights={"code_quality": 0.90, "testing": 0.90},  # Sum > 1.0
            )

    def test_merge_weights(self, manager: QualityWeightConfigManager):
        """Test merging weight overrides."""
        base = manager.get_profile("balanced")

        # Increase testing weight
        merged = manager.merge_weights(base, {"testing": 0.30})

        assert merged.weights["testing"] == 0.30
        # Other weights should be scaled proportionally
        merged.validate()  # Should still sum to 1.0

    def test_merge_weights_invalid_overrides(self, manager: QualityWeightConfigManager):
        """Test merging with invalid overrides."""
        base = manager.get_profile("balanced")

        with pytest.raises(ValueError, match="exceeding 1.0"):
            manager.merge_weights(base, {"testing": 0.60, "code_quality": 0.60})

    def test_get_all_profiles(self, manager: QualityWeightConfigManager):
        """Test getting all profile names."""
        profiles = manager.get_all_profiles()

        assert "balanced" in profiles
        assert "security_heavy" in profiles
        assert "speed_heavy" in profiles
        assert "documentation_heavy" in profiles

    def test_validate_weights_standalone(self, manager: QualityWeightConfigManager):
        """Test standalone weight validation."""
        valid_weights = {"a": 0.5, "b": 0.5}
        assert manager.validate_weights(valid_weights) is True

        invalid_weights = {"a": 0.6, "b": 0.6}
        with pytest.raises(ValueError):
            manager.validate_weights(invalid_weights)


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_get_profile_function(self):
        """Test get_profile convenience function."""
        config = get_profile("balanced")
        assert config is not None
        assert "code_quality" in config.weights

    def test_get_default_profile_function(self):
        """Test get_default_profile convenience function."""
        config = get_default_profile()
        assert config.name == "balanced"

    def test_get_manager_singleton(self):
        """Test that get_manager returns same instance."""
        manager1 = get_manager()
        manager2 = get_manager()
        assert manager1 is manager2


class TestQualityWeightConfigIntegration:
    """Integration tests for weight configuration."""

    def test_profile_roundtrip(self):
        """Test saving and loading a profile.

        Uses delete=False and closes the file before writing/reading to
        avoid the Windows NamedTemporaryFile exclusive-lock issue where a
        second open() on the same path fails with PermissionError.
        """
        import os
        manager = QualityWeightConfigManager()
        original = manager.get_profile("balanced")

        with NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            tmp_path = f.name
        # File is now closed; safe to open again on Windows
        try:
            manager.save_to_yaml(original, tmp_path)
            loaded = manager.load_from_yaml(tmp_path)

            assert loaded.name == original.name
            assert loaded.weights == original.weights
        finally:
            os.unlink(tmp_path)

    def test_custom_config_with_overrides(self):
        """Test custom config with category overrides."""
        manager = QualityWeightConfigManager()

        config = manager.create_custom_config(
            name="enterprise",
            weights={
                "code_quality": 0.20,
                "requirements_coverage": 0.20,
                "testing": 0.25,
                "documentation": 0.15,
                "best_practices": 0.20,
            },
            category_overrides={
                "testing": {
                    "TS-01": 0.12,
                    "TS-02": 0.08,
                },
                "best_practices": {
                    "BP-01": 0.10,
                },
            },
        )

        config.validate()

        # Verify overrides are applied
        assert config.get_category_weight("testing", "TS-01", 0.05) == 0.12
        assert config.get_category_weight("testing", "TS-02", 0.05) == 0.08
        assert config.get_category_weight("best_practices", "BP-01", 0.05) == 0.10
