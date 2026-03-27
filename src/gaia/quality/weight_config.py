"""
GAIA Quality Weight Configuration System

Provides configuration management for quality dimension weights.
Supports profiles, YAML/JSON loading, and runtime overrides.
"""

import json
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

from gaia.quality.models import QualityWeightConfig
from gaia.utils.logging import get_logger


logger = get_logger(__name__)


# Pre-defined weight profiles
PROFILES: Dict[str, Dict[str, float]] = {
    # Balanced weights - default for most use cases
    "balanced": {
        "code_quality": 0.25,
        "requirements_coverage": 0.25,
        "testing": 0.20,
        "documentation": 0.15,
        "best_practices": 0.15,
    },
    # Security-heavy - prioritize security and best practices
    "security_heavy": {
        "code_quality": 0.20,
        "requirements_coverage": 0.15,
        "testing": 0.25,
        "documentation": 0.10,
        "best_practices": 0.30,  # Security practices weighted higher
    },
    # Speed-heavy - prioritize code quality and testing over documentation
    "speed_heavy": {
        "code_quality": 0.35,
        "requirements_coverage": 0.20,
        "testing": 0.30,
        "documentation": 0.05,  # Minimal documentation weight
        "best_practices": 0.10,
    },
    # Documentation-heavy - prioritize documentation and best practices
    "documentation_heavy": {
        "code_quality": 0.20,
        "requirements_coverage": 0.20,
        "testing": 0.15,
        "documentation": 0.30,  # Heavy documentation focus
        "best_practices": 0.15,
    },
}


class QualityWeightConfigManager:
    """
    Manager for quality weight configurations.

    The QualityWeightConfigManager provides:
    - Access to pre-defined weight profiles (balanced, security_heavy, etc.)
    - Load configurations from YAML/JSON files
    - Merge weight configurations
    - Validate weight sums
    - Runtime weight override capability

    Example:
        >>> manager = QualityWeightConfigManager()
        >>> config = manager.get_profile("balanced")
        >>> print(config.weights)

        >>> # Load from YAML
        >>> config = manager.load_from_yaml("weights.yml")

        >>> # Merge configs
        >>> merged = manager.merge_weights(config, {"testing": 0.30})
    """

    def __init__(self):
        """Initialize the weight config manager."""
        self._custom_configs: Dict[str, QualityWeightConfig] = {}
        logger.info("QualityWeightConfigManager initialized")

    def get_profile(self, name: str) -> QualityWeightConfig:
        """
        Get a pre-defined weight profile.

        Args:
            name: Profile name (balanced, security_heavy, speed_heavy, documentation_heavy)

        Returns:
            QualityWeightConfig for the profile

        Raises:
            KeyError: If profile not found
        """
        if name not in PROFILES:
            raise KeyError(
                f"Profile '{name}' not found. "
                f"Available profiles: {list(PROFILES.keys())}"
            )

        weights = PROFILES[name]
        config = QualityWeightConfig(
            name=name,
            weights=weights.copy(),
            description=f"Pre-defined {name} weight profile",
        )
        config.validate()
        return config

    def get_default_profile(self) -> QualityWeightConfig:
        """
        Get the default (balanced) profile.

        Returns:
            QualityWeightConfig for balanced profile
        """
        return self.get_profile("balanced")

    def load_from_yaml(self, file_path: Union[str, Path]) -> QualityWeightConfig:
        """
        Load weight configuration from YAML file.

        Args:
            file_path: Path to YAML configuration file

        Returns:
            QualityWeightConfig instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If weights don't sum to 1.0
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Config file not found: {file_path}")

        logger.info(f"Loading weight config from {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return self._load_from_dict(data, source=str(file_path))

    def load_from_json(self, file_path: Union[str, Path]) -> QualityWeightConfig:
        """
        Load weight configuration from JSON file.

        Args:
            file_path: Path to JSON configuration file

        Returns:
            QualityWeightConfig instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If weights don't sum to 1.0
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Config file not found: {file_path}")

        logger.info(f"Loading weight config from {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return self._load_from_dict(data, source=str(file_path))

    def _load_from_dict(
        self,
        data: Dict[str, Any],
        source: str = "unknown"
    ) -> QualityWeightConfig:
        """
        Load configuration from dictionary.

        Args:
            data: Configuration dictionary
            source: Source identifier for logging

        Returns:
            QualityWeightConfig instance

        Raises:
            ValueError: If configuration is invalid
        """
        if not isinstance(data, dict):
            raise ValueError(f"Invalid config format from {source}: expected dict")

        # Extract weights - handle both flat and nested formats
        if "weights" in data:
            weights = data["weights"]
        else:
            # Assume flat format with weight values directly
            weights = {
                k: v for k, v in data.items()
                if isinstance(v, (int, float)) and k != "category_overrides"
            }

        config = QualityWeightConfig(
            name=data.get("name", "custom"),
            weights=weights,
            category_overrides=data.get("category_overrides", {}),
            description=data.get("description", f"Loaded from {source}"),
        )

        # Validate weights sum to 1.0
        config.validate()

        logger.info(
            f"Loaded weight config '{config.name}' from {source}",
            extra={"total_weight": sum(weights.values())},
        )

        return config

    def create_custom_config(
        self,
        name: str,
        weights: Dict[str, float],
        category_overrides: Optional[Dict[str, Dict[str, float]]] = None,
        description: str = "",
        validate: bool = True,
    ) -> QualityWeightConfig:
        """
        Create a custom weight configuration.

        Args:
            name: Configuration name
            weights: Dictionary mapping dimensions to weights
            category_overrides: Optional per-category overrides
            description: Configuration description
            validate: Whether to validate weights (default: True)

        Returns:
            QualityWeightConfig instance

        Raises:
            ValueError: If validate=True and weights don't sum to 1.0
        """
        config = QualityWeightConfig(
            name=name,
            weights=weights.copy(),
            category_overrides=category_overrides or {},
            description=description,
        )

        if validate:
            config.validate()

        # Cache custom config
        self._custom_configs[name] = config

        logger.info(f"Created custom weight config: {name}")
        return config

    def merge_weights(
        self,
        base_config: QualityWeightConfig,
        overrides: Dict[str, float],
    ) -> QualityWeightConfig:
        """
        Merge weight overrides into a base configuration.

        This method allows runtime adjustment of weights while maintaining
        the constraint that weights sum to 1.0. Non-overridden weights
        are scaled proportionally.

        Args:
            base_config: Base configuration to modify
            overrides: Dictionary of dimension -> new weight

        Returns:
            New QualityWeightConfig with merged weights

        Example:
            >>> base = manager.get_profile("balanced")
            >>> merged = manager.merge_weights(base, {"testing": 0.30})
            >>> # testing is now 0.30, others scaled proportionally
        """
        # Start with base weights
        new_weights = base_config.weights.copy()

        # Apply overrides
        overridden_dims = set(overrides.keys())
        remaining_dims = set(base_config.weights.keys()) - overridden_dims

        # Calculate remaining weight to distribute
        override_total = sum(overrides.values())
        if override_total > 1.0:
            raise ValueError(
                f"Override weights sum to {override_total}, exceeding 1.0"
            )

        remaining_weight = 1.0 - override_total

        # Scale remaining weights proportionally
        original_remaining = sum(
            base_config.weights[d] for d in remaining_dims
        )

        if original_remaining > 0:
            scale_factor = remaining_weight / original_remaining
            for dim in remaining_dims:
                new_weights[dim] = base_config.weights[dim] * scale_factor

        # Add overrides
        new_weights.update(overrides)

        # Create new config
        config = QualityWeightConfig(
            name=f"{base_config.name}_merged",
            weights=new_weights,
            category_overrides=base_config.category_overrides.copy(),
            description=f"Merged from {base_config.name} with overrides",
        )

        config.validate()
        return config

    def validate_weights(self, weights: Dict[str, float], tolerance: float = 0.01) -> bool:
        """
        Validate that weights sum to 1.0 within tolerance.

        Args:
            weights: Dictionary of dimension weights
            tolerance: Acceptable deviation from 1.0

        Returns:
            True if valid

        Raises:
            ValueError: If weights don't sum to 1.0 within tolerance
        """
        total = sum(weights.values())
        if abs(total - 1.0) > tolerance:
            raise ValueError(
                f"Weights sum to {total}, not 1.0 (tolerance: {tolerance})"
            )
        return True

    def get_all_profiles(self) -> List[str]:
        """
        Get list of all available profile names.

        Returns:
            List of profile names including custom configs
        """
        return list(PROFILES.keys()) + list(self._custom_configs.keys())

    def save_to_yaml(
        self,
        config: QualityWeightConfig,
        file_path: Union[str, Path],
    ) -> None:
        """
        Save weight configuration to YAML file.

        Args:
            config: Configuration to save
            file_path: Output file path
        """
        file_path = Path(file_path)

        data = {
            "name": config.name,
            "description": config.description,
            "weights": config.weights,
        }

        if config.category_overrides:
            data["category_overrides"] = config.category_overrides

        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Saved weight config to {file_path}")

    def save_to_json(
        self,
        config: QualityWeightConfig,
        file_path: Union[str, Path],
    ) -> None:
        """
        Save weight configuration to JSON file.

        Args:
            config: Configuration to save
            file_path: Output file path
        """
        file_path = Path(file_path)

        data = {
            "name": config.name,
            "description": config.description,
            "weights": config.weights,
        }

        if config.category_overrides:
            data["category_overrides"] = config.category_overrides

        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved weight config to {file_path}")


# Global manager instance for convenience
_default_manager: Optional[QualityWeightConfigManager] = None


def get_manager() -> QualityWeightConfigManager:
    """Get the default weight config manager."""
    global _default_manager
    if _default_manager is None:
        _default_manager = QualityWeightConfigManager()
    return _default_manager


def get_profile(name: str) -> QualityWeightConfig:
    """
    Get a weight profile by name.

    Convenience function using the default manager.

    Args:
        name: Profile name

    Returns:
        QualityWeightConfig instance
    """
    return get_manager().get_profile(name)


def get_default_profile() -> QualityWeightConfig:
    """Get the default (balanced) profile."""
    return get_manager().get_default_profile()
