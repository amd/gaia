# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Environment variable configuration loader for GAIA.

Provides environment variable loading with prefix support,
type coercion, and hierarchical configuration.

Example:
    from gaia.config.loaders import EnvLoader

    loader = EnvLoader(prefix="GAIA_")
    config = loader.load()
    # Loads all GAIA_* environment variables
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class EnvLoader:
    """
    Environment variable configuration loader.

    Loads configuration from environment variables with support for:
    - Prefix filtering (e.g., GAIA_*)
    - Nested key support via underscores
    - Type coercion
    - Default values

    Example:
        >>> loader = EnvLoader(prefix="GAIA_")
        >>> config = loader.load()
        >>> # GAIA_DEBUG=true -> {"debug": True}
        >>> # GAIA_DB_HOST=localhost -> {"db": {"host": "localhost"}}

        # With type coercion
        >>> loader = EnvLoader(
        ...     prefix="GAIA_",
        ...     coerce_types=True
        ... )
        >>> config = loader.load()
    """

    # Default type coercion mappings
    TRUE_VALUES = {"true", "1", "yes", "on", "enabled"}
    FALSE_VALUES = {"false", "0", "no", "off", "disabled"}

    def __init__(
        self,
        prefix: Optional[str] = None,
        separator: str = "_",
        coerce_types: bool = True,
        case_sensitive: bool = False,
        nested_separator: str = "_",
    ):
        """
        Initialize environment variable loader.

        Args:
            prefix: Environment variable prefix (e.g., "GAIA_")
                   If None, loads all environment variables
            separator: Separator between prefix and key (default: "_")
            coerce_types: Whether to coerce string values to types
                         (default: True)
            case_sensitive: Whether keys are case-sensitive (default: False)
            nested_separator: Separator for nested keys
                             (default: "_", so DB_HOST -> {"db": {"host"}})

        Example:
            >>> loader = EnvLoader(prefix="APP_")
            >>> os.environ["APP_DEBUG"] = "true"
            >>> config = loader.load()
            >>> print(config)
            {'debug': True}
        """
        self.prefix = prefix or ""
        self.separator = separator
        self.coerce_types = coerce_types
        self.case_sensitive = case_sensitive
        self.nested_separator = nested_separator

    def load(
        self,
        keys: Optional[List[str]] = None,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Load configuration from environment variables.

        Args:
            keys: Specific keys to load (without prefix).
                 If None, loads all matching environment variables.
            defaults: Default values to use if env vars not set

        Returns:
            Configuration dictionary

        Example:
            >>> loader = EnvLoader(prefix="GAIA_")
            >>> os.environ["GAIA_DEBUG"] = "true"
            >>> os.environ["GAIA_LOG_LEVEL"] = "DEBUG"
            >>> config = loader.load()
            >>> print(config)
            {'debug': True, 'log_level': 'DEBUG'}

            # Load specific keys only
            >>> config = loader.load(keys=["debug"])
            >>> print(config)
            {'debug': True}
        """
        config = {}

        if keys is not None:
            # Load specific keys
            for key in keys:
                value = self._get_env_value(key, defaults)
                if value is not None:
                    self._set_nested_value(config, key, value)
        else:
            # Load all matching environment variables
            for env_key, env_value in os.environ.items():
                if self._matches_prefix(env_key):
                    key = self._strip_prefix(env_key)
                    value = self._coerce_value(env_value, key)
                    self._set_nested_value(config, key, value)

        # Merge with defaults
        if defaults:
            config = self._deep_merge(defaults, config)

        logger.debug(
            f"Loaded {len(config)} values from environment "
            f"(prefix={self.prefix or 'none'})"
        )

        return config

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a single environment variable value.

        Args:
            key: Configuration key (without prefix)
            default: Default value if not found

        Returns:
            Coerced value or default

        Example:
            >>> loader = EnvLoader(prefix="GAIA_")
            >>> os.environ["GAIA_DEBUG"] = "true"
            >>> debug = loader.get("debug")
            >>> print(debug)
            True
        """
        env_key = self._make_env_key(key)
        env_value = os.environ.get(env_key)

        if env_value is None:
            return default

        return self._coerce_value(env_value, key)

    def _matches_prefix(self, env_key: str) -> bool:
        """
        Check if environment variable matches prefix.

        Args:
            env_key: Environment variable name

        Returns:
            True if matches prefix
        """
        if not self.prefix:
            return True

        if self.case_sensitive:
            return env_key.startswith(self.prefix)

        return env_key.upper().startswith(self.prefix.upper())

    def _strip_prefix(self, env_key: str) -> str:
        """
        Strip prefix from environment variable name.

        Args:
            env_key: Environment variable name

        Returns:
            Key without prefix
        """
        if not self.prefix:
            return env_key

        if self.case_sensitive:
            key = env_key[len(self.prefix):]
        else:
            key = env_key[len(self.prefix):]

        # Convert to lowercase for consistency
        return key.lower()

    def _make_env_key(self, key: str) -> str:
        """
        Create environment variable name from key.

        Args:
            key: Configuration key

        Returns:
            Environment variable name
        """
        env_key = key.upper().replace(self.nested_separator, self.separator)

        if self.prefix:
            prefix = self.prefix.upper()
            if not prefix.endswith(self.separator):
                prefix += self.separator
            env_key = prefix + env_key

        return env_key

    def _coerce_value(self, value: str, key: str) -> Any:
        """
        Coerce string value to appropriate type.

        Args:
            value: String value from environment
            key: Configuration key (for context)

        Returns:
            Coerced value (bool, int, float, or str)
        """
        if not self.coerce_types:
            return value

        # Boolean coercion
        lower_value = value.lower().strip()
        if lower_value in self.TRUE_VALUES:
            return True
        if lower_value in self.FALSE_VALUES:
            return False

        # Integer coercion
        try:
            return int(value)
        except ValueError:
            pass

        # Float coercion
        try:
            return float(value)
        except ValueError:
            pass

        # Return as string
        return value

    def _get_env_value(
        self,
        key: str,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """
        Get environment variable value with default fallback.

        Args:
            key: Configuration key
            defaults: Default values dictionary

        Returns:
            Value or None
        """
        env_key = self._make_env_key(key)
        env_value = os.environ.get(env_key)

        if env_value is not None:
            return self._coerce_value(env_value, key)

        # Check defaults
        if defaults:
            return self._get_nested_value(defaults, key)

        return None

    def _get_nested_value(
        self,
        config: Dict[str, Any],
        key: str,
    ) -> Optional[Any]:
        """
        Get value from nested dictionary using dot notation.

        Args:
            config: Configuration dictionary
            key: Dot-separated key path

        Returns:
            Value or None
        """
        parts = key.split(self.nested_separator)
        value = config

        for part in parts:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
            if value is None:
                return None

        return value

    def _set_nested_value(
        self,
        config: Dict[str, Any],
        key: str,
        value: Any,
    ) -> None:
        """
        Set value in nested dictionary using dot notation.

        Args:
            config: Configuration dictionary
            key: Dot-separated key path
            value: Value to set

        Example:
            >>> config = {}
            >>> loader._set_nested_value(config, "db.host", "localhost")
            >>> print(config)
            {'db': {'host': 'localhost'}}
        """
        parts = key.split(self.nested_separator)

        # Navigate/create nested structure
        current = config
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            elif not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

        # Set final value
        current[parts[-1]] = value

    def _deep_merge(
        self,
        base: Dict[str, Any],
        override: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Deep merge two dictionaries.

        Override values take precedence.

        Args:
            base: Base dictionary
            override: Override dictionary

        Returns:
            Merged dictionary
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

    def load_typed(
        self,
        schema: Dict[str, type],
    ) -> Dict[str, Any]:
        """
        Load environment variables based on type schema.

        Args:
            schema: Dictionary mapping keys to expected types

        Returns:
            Configuration dictionary with typed values

        Example:
            >>> loader = EnvLoader(prefix="GAIA_")
            >>> config = loader.load_typed({
            ...     "debug": bool,
            ...     "port": int,
            ...     "timeout": float,
            ...     "host": str
            ... })
        """
        config = {}

        for key, expected_type in schema.items():
            value = self.get(key)

            if value is None:
                continue

            # Attempt type conversion
            try:
                if expected_type is bool:
                    if isinstance(value, bool):
                        config[key] = value
                    else:
                        config[key] = str(value).lower() in self.TRUE_VALUES
                elif expected_type in (int, float, str):
                    config[key] = expected_type(value)
                else:
                    config[key] = value
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Failed to convert {key}={value} to {expected_type}: {e}"
                )
                config[key] = value

        return config

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"EnvLoader(prefix={self.prefix!r}, "
            f"coerce_types={self.coerce_types})"
        )


# Convenience functions

def load_env(
    prefix: Optional[str] = None,
    coerce_types: bool = True,
) -> Dict[str, Any]:
    """
    Convenience function to load environment variables.

    Args:
        prefix: Environment variable prefix
        coerce_types: Whether to coerce types

    Returns:
        Configuration dictionary

    Example:
        >>> config = load_env(prefix="GAIA_")
    """
    loader = EnvLoader(prefix=prefix, coerce_types=coerce_types)
    return loader.load()


def get_env(
    key: str,
    prefix: Optional[str] = None,
    default: Any = None,
    coerce_types: bool = True,
) -> Any:
    """
    Convenience function to get single environment variable.

    Args:
        key: Configuration key
        prefix: Environment variable prefix
        default: Default value
        coerce_types: Whether to coerce types

    Returns:
        Configuration value

    Example:
        >>> debug = get_env("debug", prefix="GAIA_", default=False)
    """
    loader = EnvLoader(prefix=prefix, coerce_types=coerce_types)
    return loader.get(key, default=default)
