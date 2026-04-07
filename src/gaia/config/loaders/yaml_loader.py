# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
YAML configuration file loader for GAIA.

Provides YAML file loading with schema validation support
and environment variable interpolation.

Example:
    from gaia.config.loaders import YAMLLoader

    loader = YAMLLoader("./config/app.yaml")
    config = loader.load()
"""

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None

logger = logging.getLogger(__name__)


class YAMLLoader:
    """
    YAML configuration file loader.

    Loads configuration from YAML files with support for:
    - Environment variable interpolation
    - Nested configuration
    - Schema validation integration
    - Comments and multi-line strings

    Example:
        >>> loader = YAMLLoader("./config/base.yaml")
        >>> config = loader.load()
        >>> print(config)
        {'debug': False, 'log_level': 'INFO'}

        # With environment variable interpolation
        >>> loader = YAMLLoader("./config/secrets.yaml")
        >>> config = loader.load(interpolate_env=True)
        >>> # ${DB_HOST} in YAML becomes os.environ['DB_HOST']
    """

    # Environment variable pattern: ${VAR_NAME} or $VAR_NAME
    ENV_VAR_PATTERN = re.compile(r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)')

    def __init__(
        self,
        path: str,
        required: bool = True,
        encoding: str = "utf-8",
        safe_load: bool = True,
    ):
        """
        Initialize YAML loader.

        Args:
            path: Path to YAML configuration file
            required: Whether file must exist (default: True)
            encoding: File encoding (default: utf-8)
            safe_load: Use safe loader (default: True, recommended)

        Raises:
            ImportError: If PyYAML is not installed
            FileNotFoundError: If required file doesn't exist

        Example:
            >>> loader = YAMLLoader("./config.yaml", required=False)
        """
        if not YAML_AVAILABLE:
            raise ImportError(
                "PyYAML is required for YAMLLoader. "
                "Install with: pip install pyyaml"
            )

        self.path = Path(path)
        self.required = required
        self.encoding = encoding
        self.safe_load = safe_load

        if required and not self.path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

    def load(self, interpolate_env: bool = False) -> Dict[str, Any]:
        """
        Load configuration from YAML file.

        Args:
            interpolate_env: Whether to replace ${VAR} patterns with
                           environment variable values (default: False)

        Returns:
            Configuration dictionary

        Raises:
            FileNotFoundError: If file doesn't exist and is required
            yaml.YAMLError: If YAML is invalid
            ValueError: If environment variable interpolation fails

        Example:
            >>> loader = YAMLLoader("./config.yaml")
            >>> config = loader.load()

            # With environment variable interpolation
            >>> config = loader.load(interpolate_env=True)
        """
        if not self.path.exists():
            if self.required:
                raise FileNotFoundError(f"Configuration file not found: {self.path}")
            logger.debug(f"Optional config file not found: {self.path}")
            return {}

        try:
            with open(self.path, "r", encoding=self.encoding) as f:
                content = f.read()

            # Interpolate environment variables if requested
            if interpolate_env:
                content = self._interpolate_env_vars(content)

            # Load YAML
            if self.safe_load:
                config = yaml.safe_load(content)
            else:
                config = yaml.load(content, Loader=yaml.FullLoader)

            # Handle empty file
            if config is None:
                return {}

            if not isinstance(config, dict):
                raise ValueError(
                    f"YAML configuration must be an object, got {type(config).__name__}"
                )

            logger.debug(f"Loaded YAML config: {self.path}")
            return config

        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Invalid YAML in {self.path}: {e}")

    def load_with_defaults(
        self,
        defaults: Dict[str, Any],
        interpolate_env: bool = False,
    ) -> Dict[str, Any]:
        """
        Load configuration and merge with defaults.

        File values override defaults.

        Args:
            defaults: Default configuration values
            interpolate_env: Whether to interpolate environment variables

        Returns:
            Merged configuration dictionary

        Example:
            >>> loader = YAMLLoader("./config.yaml")
            >>> config = loader.load_with_defaults({
            ...     "debug": False,
            ...     "log_level": "INFO"
            ... })
        """
        config = self.load(interpolate_env)

        # Deep merge with defaults
        return self._deep_merge(defaults, config)

    def _interpolate_env_vars(self, content: str) -> str:
        """
        Replace environment variable patterns with values.

        Supports ${VAR_NAME} and $VAR_NAME patterns.

        Args:
            content: YAML content string

        Returns:
            Content with environment variables replaced

        Raises:
            ValueError: If required environment variable is not set

        Example:
            >>> content = 'host: ${DB_HOST}'
            >>> os.environ['DB_HOST'] = 'localhost'
            >>> result = loader._interpolate_env_vars(content)
            >>> print(result)
            'host: localhost'
        """

        def replace_match(match: re.Match) -> str:
            # Match groups: ${VAR} is group 1, $VAR is group 2
            var_name = match.group(1) or match.group(2)

            if var_name not in os.environ:
                raise ValueError(
                    f"Environment variable '{var_name}' not found "
                    f"in configuration file {self.path}"
                )

            return os.environ[var_name]

        return self.ENV_VAR_PATTERN.sub(replace_match, content)

    def _deep_merge(
        self,
        base: Dict[str, Any],
        override: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Deep merge two dictionaries.

        Override values take precedence. Nested dicts are merged recursively.

        Args:
            base: Base dictionary
            override: Override dictionary

        Returns:
            Merged dictionary

        Example:
            >>> base = {"db": {"host": "localhost", "port": 5432}}
            >>> override = {"db": {"host": "prod.example.com"}}
            >>> merged = loader._deep_merge(base, override)
            >>> print(merged)
            {'db': {'host': 'prod.example.com', 'port': 5432}}
        """
        result = dict(base)

        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result

    def save(
        self,
        config: Dict[str, Any],
        default_flow_style: bool = False,
        sort_keys: bool = False,
    ) -> None:
        """
        Save configuration to YAML file.

        Args:
            config: Configuration dictionary to save
            default_flow_style: Use flow style (inline) if True (default: False)
            sort_keys: Sort dictionary keys if True (default: False)

        Example:
            >>> loader = YAMLLoader("./config.yaml", required=False)
            >>> loader.save({
            ...     "debug": True,
            ...     "log_level": "DEBUG"
            ... })
        """
        # Create parent directory if needed
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.path, "w", encoding=self.encoding) as f:
            yaml.dump(
                config,
                f,
                default_flow_style=default_flow_style,
                sort_keys=sort_keys,
                allow_unicode=True,
            )

        logger.debug(f"Saved YAML config: {self.path}")

    def exists(self) -> bool:
        """
        Check if configuration file exists.

        Returns:
            True if file exists
        """
        return self.path.exists()

    def get_path(self) -> Path:
        """
        Get the configuration file path.

        Returns:
            Path object for the configuration file
        """
        return self.path

    @staticmethod
    def is_yaml_available() -> bool:
        """
        Check if PyYAML is available.

        Returns:
            True if PyYAML is installed
        """
        return YAML_AVAILABLE

    def __repr__(self) -> str:
        """Return string representation."""
        status = "exists" if self.exists() else "not found"
        return f"YAMLLoader(path={self.path!r}, required={self.required}, status={status})"


# Convenience functions

def load_yaml(
    path: str,
    required: bool = True,
    interpolate_env: bool = False,
) -> Dict[str, Any]:
    """
    Convenience function to load YAML configuration.

    Args:
        path: Path to YAML file
        required: Whether file must exist
        interpolate_env: Whether to interpolate environment variables

    Returns:
        Configuration dictionary

    Example:
        >>> config = load_yaml("./config.yaml")
    """
    loader = YAMLLoader(path, required=required)
    return loader.load(interpolate_env=interpolate_env)


def save_yaml(
    path: str,
    config: Dict[str, Any],
) -> None:
    """
    Convenience function to save YAML configuration.

    Args:
        path: Path to YAML file
        config: Configuration dictionary

    Example:
        >>> save_yaml("./config.yaml", {"debug": True})
    """
    loader = YAMLLoader(path, required=False)
    loader.save(config)
