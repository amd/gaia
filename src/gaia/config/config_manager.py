# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Hierarchical configuration management for GAIA.

Provides configuration management with:
- Priority stacking (env > programmatic > files > defaults)
- Environment variable overrides
- Hot-reload via file system watching
- Schema validation on load
- Nested configuration support

Example:
    from gaia.config import ConfigManager, ConfigSchema

    schema = ConfigSchema("app_config")
    schema.add_field("debug", bool, default=False)
    schema.add_field("log_level", str, default="INFO")
    schema.add_field("database_url", str, required=True, secret=True)

    manager = ConfigManager(schema=schema)
    manager.add_json_file("./config/base.json")
    manager.add_json_file("./config/local.json")  # Overrides base
    manager.load()

    debug = manager.get("debug")
    db_url = manager.get("database_url")
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar, Union

from gaia.config.config_schema import ConfigSchema, ValidationResult
from gaia.config.loaders import JSONLoader, YAMLLoader, EnvLoader, FileWatcherLoader
from gaia.cache.cache_layer import CacheLayer

logger = logging.getLogger(__name__)

T = TypeVar('T')


class ConfigManager:
    """
    Hierarchical configuration management with hot-reload support.

    Configuration Priority (highest to lowest):
    1. Environment variables (always takes precedence)
    2. Programmatically set values
    3. Configuration files (JSON/YAML)
    4. Schema defaults

    Features:
        - Hierarchical configuration with priority stacking
        - Environment variable overrides
        - Hot-reload via file system watching
        - Schema validation on load
        - Nested configuration support
        - Integration with CacheLayer for config caching

    Example:
        >>> from gaia.config import ConfigManager, ConfigSchema
        >>>
        >>> # Define schema
        >>> schema = ConfigSchema("app_config")
        >>> schema.add_field("debug", bool, default=False)
        >>> schema.add_field("log_level", str, default="INFO")
        >>> schema.add_field("database_url", str, required=True, secret=True)
        >>>
        >>> # Create manager
        >>> manager = ConfigManager(schema=schema)
        >>> manager.add_json_file("./config/base.json")
        >>> manager.add_json_file("./config/local.json")  # Overrides base
        >>> manager.load()
        >>>
        >>> # Access values
        >>> debug = manager.get("debug")
        >>> db_url = manager.get("database_url")
        >>>
        >>> # Nested access
        >>> manager.add_json_file("./config/app.json")
        >>> model = manager.get("llm.model_id")  # Dot notation
    """

    def __init__(
        self,
        schema: Optional[ConfigSchema] = None,
        cache: Optional[CacheLayer] = None,
        enable_env_overrides: bool = True,
        env_prefix: Optional[str] = None,
    ):
        """
        Initialize ConfigManager.

        Args:
            schema: Configuration schema for validation
            cache: Optional cache for config values
            enable_env_overrides: Whether to check environment variables
            env_prefix: Prefix for environment variables (e.g., "GAIA_")

        Example:
            >>> manager = ConfigManager(
            ...     schema=my_schema,
            ...     enable_env_overrides=True,
            ...     env_prefix="APP_"
            ... )
        """
        self.schema = schema
        self.cache = cache
        self.enable_env_overrides = enable_env_overrides
        self.env_prefix = env_prefix

        # Configuration storage
        self._config: Dict[str, Any] = {}
        self._programmatic: Dict[str, Any] = {}  # Highest priority
        self._file_config: Dict[str, Any] = {}   # From files
        self._defaults: Dict[str, Any] = {}      # From schema

        # Loaders
        self._loaders: List[Callable[[], Dict[str, Any]]] = []
        self._file_paths: List[str] = []

        # Hot reload
        self._watcher: Optional[FileWatcherLoader] = None
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []

        # State
        self._loaded = False
        self._lock = asyncio.Lock()

        # Initialize defaults from schema
        if schema:
            self._defaults = schema.get_defaults()

    def add_json_file(
        self,
        path: str,
        required: bool = True,
        interpolate_env: bool = False,
    ) -> "ConfigManager":
        """
        Add JSON configuration file to load stack.

        Files loaded later take precedence over earlier files.

        Args:
            path: Path to JSON file
            required: Whether file must exist (default: True)
            interpolate_env: Whether to interpolate ${VAR} patterns

        Returns:
            Self for method chaining

        Example:
            >>> manager.add_json_file("./config/base.json")
            >>> manager.add_json_file("./config/local.json")  # Overrides base
        """
        self._file_paths.append(path)

        def loader() -> Dict[str, Any]:
            return JSONLoader(path, required=required).load(
                interpolate_env=interpolate_env
            )

        self._loaders.append(loader)
        return self

    def add_yaml_file(
        self,
        path: str,
        required: bool = True,
        interpolate_env: bool = False,
    ) -> "ConfigManager":
        """
        Add YAML configuration file to load stack.

        Args:
            path: Path to YAML file
            required: Whether file must exist (default: True)
            interpolate_env: Whether to interpolate ${VAR} patterns

        Returns:
            Self for method chaining
        """
        self._file_paths.append(path)

        def loader() -> Dict[str, Any]:
            return YAMLLoader(path, required=required).load(
                interpolate_env=interpolate_env
            )

        self._loaders.append(loader)
        return self

    def load(self, validate: bool = True) -> ValidationResult:
        """
        Load all configuration sources.

        Applies configuration in priority order:
        1. Schema defaults
        2. JSON/YAML files (in order added)
        3. Environment variables

        Args:
            validate: Whether to validate against schema

        Returns:
            ValidationResult (empty if validate=False or no schema)

        Raises:
            ValueError: If validation fails in strict mode

        Example:
            >>> result = manager.load()
            >>> if not result.valid:
            ...     for error in result.errors:
            ...         print(error)
        """
        # Reset file config
        self._file_config = {}

        # Load from files (later files override earlier)
        for loader in self._loaders:
            try:
                file_config = loader()
                self._file_config = self._deep_merge(self._file_config, file_config)
            except FileNotFoundError as e:
                logger.warning(f"Config file not found: {e}")
            except Exception as e:
                logger.error(f"Error loading config file: {e}")

        # Build final config with priority
        self._config = self._defaults.copy()
        self._config = self._deep_merge(self._config, self._file_config)
        self._config = self._deep_merge(self._config, self._programmatic)

        # Apply environment overrides
        if self.enable_env_overrides:
            env_config = self._load_env_overrides()
            self._config = self._deep_merge(self._config, env_config)

        # Validate
        result = ValidationResult()
        if validate and self.schema:
            result = self.schema.validate(self._config)

            if not result.valid:
                logger.error(
                    f"Configuration validation failed: {len(result.errors)} errors"
                )

        # Cache the config
        if self.cache:
            asyncio.create_task(self._cache_config())

        self._loaded = True
        logger.info(f"Configuration loaded ({len(self._config)} keys)")

        return result

    def _load_env_overrides(self) -> Dict[str, Any]:
        """
        Load environment variable overrides.

        Returns:
            Dictionary of environment variable values
        """
        if not self.env_prefix:
            # Try to infer prefix from schema name or use empty
            prefix = None
        else:
            prefix = self.env_prefix

        loader = EnvLoader(
            prefix=prefix,
            coerce_types=True,
            nested_separator="_",
        )

        # If we have a schema, load only known fields
        if self.schema:
            keys = self.schema.get_field_names()
            return loader.load(keys=keys)

        return loader.load()

    def _deep_merge(
        self,
        base: Dict[str, Any],
        override: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Deep merge two dictionaries.

        Override values take precedence. Nested dicts merged recursively.

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

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by key.

        Supports dot notation for nested access.
        Checks environment variable override if enabled.

        Args:
            key: Configuration key (e.g., "llm.model_id")
            default: Default value if not found

        Returns:
            Configuration value or default

        Example:
            >>> manager.get("debug")
            False
            >>> manager.get("llm.model_id")  # Dot notation
            'Qwen3.5-35B'

        Note:
            In async contexts, use get_async() for cache support.
            This sync version skips cache and reads from config only.
        """
        # Check cache first (only if not in running event loop)
        cached = None
        if self.cache:
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_running():
                    # Safe to use run_until_complete
                    cached = loop.run_until_complete(self.cache.get(f"config:{key}"))
                # If loop is running, skip cache (handled by fallback)
            except RuntimeError:
                # No event loop - skip cache
                pass

        if cached is not None:
            return cached

        # Navigate nested key
        value = self._get_nested(self._config, key)

        if value is None:
            value = default

        # Check environment override
        if self.enable_env_overrides:
            env_key = self._make_env_key(key)
            env_value = os.environ.get(env_key)
            if env_value is not None:
                # Coerce type
                value = self._coerce_env_value(env_value, value)

        # Cache the value (only if loop available and not running)
        if self.cache and value is not None:
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_running():
                    loop.run_until_complete(self.cache.set(f"config:{key}", value))
            except RuntimeError:
                pass  # No event loop - skip caching

        return value

    def _get_nested(self, config: Dict[str, Any], key: str) -> Any:
        """
        Get nested value using dot notation.

        Args:
            config: Configuration dictionary
            key: Dot-separated key path

        Returns:
            Value or None if not found
        """
        parts = key.split(".")
        value = config

        for part in parts:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
            if value is None:
                return None

        return value

    def _make_env_key(self, key: str) -> str:
        """
        Create environment variable name from key.

        Args:
            key: Configuration key

        Returns:
            Environment variable name
        """
        env_key = key.upper().replace(".", "_")

        if self.env_prefix:
            prefix = self.env_prefix.upper()
            if not prefix.endswith("_"):
                prefix += "_"
            env_key = prefix + env_key

        return env_key

    def _coerce_env_value(self, env_value: str, current_value: Any) -> Any:
        """
        Coerce environment string to match current value type.

        Args:
            env_value: String from environment
            current_value: Current typed value

        Returns:
            Coerced value
        """
        if current_value is None:
            # Try to infer type
            if env_value.lower() in ("true", "1", "yes"):
                return True
            if env_value.lower() in ("false", "0", "no"):
                return False
            try:
                return int(env_value)
            except ValueError:
                try:
                    return float(env_value)
                except ValueError:
                    return env_value

        if isinstance(current_value, bool):
            return env_value.lower() in ("true", "1", "yes", "on")
        if isinstance(current_value, int):
            try:
                return int(env_value)
            except ValueError:
                return current_value
        if isinstance(current_value, float):
            try:
                return float(env_value)
            except ValueError:
                return current_value

        return env_value

    def get_typed(self, key: str, type_: Type[T], default: Optional[T] = None) -> T:
        """
        Get typed configuration value.

        Attempts to cast value to specified type.
        Raises TypeError if cast fails.

        Args:
            key: Configuration key
            type_: Target type
            default: Default if key not found

        Returns:
            Typed configuration value

        Raises:
            TypeError: If value cannot be cast to type

        Example:
            >>> port = manager.get_typed("server.port", int, default=8080)
        """
        value = self.get(key, default)

        if value is None:
            return default  # type: ignore

        try:
            if type_ == bool and isinstance(value, str):
                return bool(value.lower() in ("true", "1", "yes"))  # type: ignore
            return type_(value)  # type: ignore
        except (ValueError, TypeError) as e:
            raise TypeError(
                f"Cannot convert '{key}' value '{value}' to {type_.__name__}: {e}"
            )

    def get_all(self) -> Dict[str, Any]:
        """
        Get complete configuration dictionary.

        Returns:
            Full configuration dict

        Example:
            >>> config = manager.get_all()
            >>> print(config)
        """
        return dict(self._config)

    def set(self, key: str, value: Any) -> None:
        """
        Set configuration value programmatically.

        Highest priority (overrides files and env vars).

        Args:
            key: Configuration key
            value: Value to set

        Example:
            >>> manager.set("debug", True)
        """
        async def set_value():
            async with self._lock:
                self._set_nested(self._programmatic, key, value)
                self._config = self._deep_merge(self._config, self._programmatic)

                # Update cache
                if self.cache:
                    await self.cache.set(f"config:{key}", value)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Running inside async context - need to handle differently
                # For now, do synchronous update
                self._set_nested(self._programmatic, key, value)
                self._config = self._deep_merge(self._config, self._programmatic)
            else:
                loop.run_until_complete(set_value())
        except RuntimeError:
            # No event loop - do synchronous
            self._set_nested(self._programmatic, key, value)
            self._config = self._deep_merge(self._config, self._programmatic)

    def _set_nested(
        self,
        config: Dict[str, Any],
        key: str,
        value: Any,
    ) -> None:
        """
        Set nested value using dot notation.

        Args:
            config: Configuration dictionary
            key: Dot-separated key path
            value: Value to set
        """
        parts = key.split(".")
        current = config

        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            elif not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]

        current[parts[-1]] = value

    async def _cache_config(self) -> None:
        """Cache the full configuration."""
        if self.cache:
            await self.cache.set("config:_full", self._config)

    def enable_hot_reload(
        self,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        debounce_seconds: float = 1.0,
    ) -> "ConfigManager":
        """
        Enable hot-reload for configuration files.

        Uses FileWatcherLoader to monitor file changes.
        Automatically reloads and notifies callbacks.

        Args:
            callback: Optional callback invoked on reload
            debounce_seconds: Debounce time between reloads

        Returns:
            Self for method chaining

        Example:
            >>> def on_reload(config):
            ...     print(f"Config reloaded: {config.keys()}")
            >>> manager.enable_hot_reload(callback=on_reload)
        """
        if not self._file_paths:
            logger.warning("No config files to watch")
            return self

        if callback:
            self._callbacks.append(callback)

        self._watcher = FileWatcherLoader(
            path=self._file_paths,
            on_reload=self._on_hot_reload,
            debounce_seconds=debounce_seconds,
        )

        self._watcher.start()
        logger.info(f"Hot-reload enabled for {len(self._file_paths)} file(s)")

        return self

    def _on_hot_reload(self, new_config: Dict[str, Any]) -> None:
        """
        Handle hot-reload event.

        Args:
            new_config: New configuration dictionary
        """
        # Update file config
        self._file_config = new_config

        # Rebuild full config
        self._config = self._defaults.copy()
        self._config = self._deep_merge(self._config, self._file_config)
        self._config = self._deep_merge(self._config, self._programmatic)

        # Re-validate if schema exists
        if self.schema:
            result = self.schema.validate(self._config)
            if not result.valid:
                logger.warning(
                    f"Hot-reloaded config validation warnings: {result.warnings}"
                )

        # Clear cache
        if self.cache:
            asyncio.create_task(self.cache.clear())

        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(self._config)
            except Exception as e:
                logger.error(f"Error in hot-reload callback: {e}")

        logger.info("Configuration hot-reloaded")

    def on_reload(self, callback: Callable[[Dict[str, Any]], None]) -> "ConfigManager":
        """
        Register callback for configuration reload events.

        Args:
            callback: Function receiving new config dict

        Returns:
            Self for method chaining
        """
        self._callbacks.append(callback)
        return self

    async def reload(self) -> ValidationResult:
        """
        Manually trigger configuration reload.

        Returns:
            ValidationResult from schema validation
        """
        return self.load(validate=True)

    def validate(self) -> ValidationResult:
        """
        Validate current configuration against schema.

        Returns:
            ValidationResult with errors/warnings
        """
        if not self.schema:
            return ValidationResult()

        return self.schema.validate(self._config)

    def get_file_paths(self) -> List[str]:
        """
        Get list of configuration file paths.

        Returns:
            List of file paths
        """
        return list(self._file_paths)

    def is_loaded(self) -> bool:
        """
        Check if configuration has been loaded.

        Returns:
            True if loaded
        """
        return self._loaded

    def clear(self) -> None:
        """
        Clear all configuration and reset state.
        """
        self._config = {}
        self._programmatic = {}
        self._file_config = {}
        self._loaded = False

        if self._watcher:
            self._watcher.stop()
            self._watcher = None

        logger.info("Configuration cleared")

    def __repr__(self) -> str:
        """Return string representation."""
        status = "loaded" if self._loaded else "not loaded"
        return f"ConfigManager(schema={self.schema}, status={status}, keys={len(self._config)})"
