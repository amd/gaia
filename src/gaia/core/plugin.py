# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Plugin Registry for the modular architecture core.

This module provides the PluginRegistry singleton class for registering,
discovering, and managing plugins that extend agent functionality.

Key Components:
    - PluginRegistry: Thread-safe singleton for plugin management
    - PluginMetadata: Dataclass for plugin information
    - Plugin lifecycle management (enable/disable)
    - Lazy plugin loading support

Example Usage:
    ```python
    from gaia.core.plugin import PluginRegistry, PluginMetadata

    # Get singleton instance
    registry = PluginRegistry.get_instance()

    # Define a plugin
    def my_plugin(context):
        return "Plugin executed"

    # Register plugin
    metadata = PluginMetadata(
        name="my-plugin",
        version="1.0.0",
        description="My custom plugin",
        plugin_fn=my_plugin,
    )
    registry.register_plugin("my-plugin", my_plugin, metadata=metadata)

    # Execute plugin
    result = registry.execute_plugin("my-plugin", context={})

    # List plugins
    plugins = registry.list_plugins()
    ```
"""

import importlib
import inspect
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from gaia.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PluginMetadata:
    """
    Metadata for a plugin.

    This dataclass captures information about a plugin including
    its identity, version, author, and capabilities.

    Attributes:
        name: Unique plugin identifier.
        version: Plugin version string (semver format).
        description: Human-readable description.
        author: Plugin author name.
        author_email: Plugin author email.
        homepage: Plugin homepage URL.
        license: License identifier (e.g., "MIT", "Apache-2.0").
        dependencies: List of plugin dependencies.
        tags: List of tags for categorization.
        plugin_fn: The plugin function itself.
        enabled: Whether the plugin is enabled.
        created_at: Unix timestamp when plugin was registered.
        updated_at: Unix timestamp when plugin was last updated.

    Example:
        >>> def my_plugin(ctx):
        ...     return "Hello"
        >>> metadata = PluginMetadata(
        ...     name="my-plugin",
        ...     version="1.0.0",
        ...     description="My custom plugin",
        ...     author="John Doe",
        ...     plugin_fn=my_plugin,
        ... )
        >>> print(metadata.name)
        my-plugin
    """

    # Required fields
    name: str
    version: str = "1.0.0"
    description: str = ""

    # Optional fields
    author: str = ""
    author_email: str = ""
    homepage: str = ""
    license: str = "MIT"
    dependencies: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    # Plugin function
    plugin_fn: Optional[Callable] = None

    # Lifecycle
    enabled: bool = True
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())

    def __post_init__(self):
        """Validate and initialize metadata."""
        self.dependencies = list(self.dependencies) if self.dependencies else []
        self.tags = list(self.tags) if self.tags else []
        self._update_timestamp()

    def _update_timestamp(self):
        """Update the updated_at timestamp."""
        self.updated_at = time.time()

    def validate(self) -> bool:
        """
        Validate the plugin metadata.

        Returns:
            True if metadata is valid.

        Raises:
            ValueError: If metadata is invalid.
        """
        if not self.name or not self.name.strip():
            raise ValueError("Plugin name cannot be empty")
        if not self.version:
            raise ValueError("Plugin version is required")
        # Basic semver validation
        version_base = self.version.split("-")[0]
        parts = version_base.split(".")
        if len(parts) < 1 or len(parts) > 4:
            raise ValueError(f"Invalid version format: {self.version}")
        for part in parts:
            if not part.isdigit() and part != "*":
                raise ValueError(f"Invalid version part: {part}")

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "author_email": self.author_email,
            "homepage": self.homepage,
            "license": self.license,
            "dependencies": list(self.dependencies),
            "tags": list(self.tags),
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginMetadata":
        """Create metadata from dictionary."""
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            author_email=data.get("author_email", ""),
            homepage=data.get("homepage", ""),
            license=data.get("license", "MIT"),
            dependencies=data.get("dependencies", []),
            tags=data.get("tags", []),
            enabled=data.get("enabled", True),
        )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"PluginMetadata(name='{self.name}', version='{self.version}', "
            f"enabled={self.enabled})"
        )


class PluginRegistry:
    """
    Thread-safe singleton registry for plugin management.

    This class provides centralized plugin registration, discovery,
    and lifecycle management with full thread safety.

    Features:
        - Thread-safe singleton with double-checked locking
        - Plugin registration with metadata
        - Plugin lifecycle management (enable/disable)
        - Lazy plugin loading support
        - Plugin execution with context passing
        - Performance metrics (<1ms lookup target)

    Thread Safety:
        All public methods are thread-safe using RLock for reentrant locking.

    Example:
        >>> registry = PluginRegistry.get_instance()
        >>>
        >>> def my_plugin(ctx):
        ...     return f"Hello, {ctx.get('name', 'World')}!"
        >>>
        >>> registry.register_plugin("my-plugin", my_plugin)
        >>> result = registry.execute_plugin("my-plugin", context={"name": "Alice"})
        >>> print(result)
        Hello, Alice!
    """

    _instance: Optional["PluginRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "PluginRegistry":
        """Create singleton instance using double-checked locking."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize registry internals (only once per singleton)."""
        if self._initialized:
            return

        self._plugins: Dict[str, Dict[str, Any]] = {}
        self._metadata: Dict[str, PluginMetadata] = {}
        self._enabled: Dict[str, bool] = {}
        self._registry_lock = threading.RLock()
        self._lazy_load_funcs: Dict[str, Callable] = {}
        self._execution_stats: Dict[str, Dict[str, Any]] = {}
        self._initialized = True

        logger.info("PluginRegistry initialized")

    @classmethod
    def get_instance(cls) -> "PluginRegistry":
        """Get the singleton instance."""
        return cls()

    def register_plugin(
        self,
        name: str,
        plugin_fn: Callable,
        metadata: Optional[PluginMetadata] = None,
        lazy: bool = False,
    ) -> None:
        """
        Register a plugin with the registry.

        Args:
            name: Unique plugin identifier.
            plugin_fn: The plugin function to register.
            metadata: Optional PluginMetadata instance.
            lazy: If True, plugin is registered but not loaded until first use.

        Example:
            >>> def my_plugin(ctx):
            ...     return "Hello"
            >>> registry.register_plugin("my-plugin", my_plugin)
        """
        with self._registry_lock:
            # Create metadata if not provided
            if metadata is None:
                metadata = PluginMetadata(name=name, plugin_fn=plugin_fn)
            else:
                metadata.plugin_fn = plugin_fn

            # Validate metadata
            metadata.validate()

            # Register plugin
            self._plugins[name] = {
                "name": name,
                "function": plugin_fn,
                "metadata": metadata,
                "lazy": lazy,
            }
            self._metadata[name] = metadata
            self._enabled[name] = True
            self._execution_stats[name] = {
                "execution_count": 0,
                "total_time_ms": 0.0,
                "last_execution_at": None,
                "avg_time_ms": 0.0,
            }

            logger.info(f"Plugin registered: {name}")

    def register_lazy(
        self,
        name: str,
        module_path: str,
        function_name: str,
        metadata: Optional[PluginMetadata] = None,
    ) -> None:
        """
        Register a plugin for lazy loading.

        This method registers a plugin that will be loaded from a module
        when first accessed, rather than at registration time.

        Args:
            name: Unique plugin identifier.
            module_path: Python module path (e.g., "my_package.my_module").
            function_name: Name of the function in the module.
            metadata: Optional PluginMetadata instance.

        Example:
            >>> registry.register_lazy(
            ...     "lazy-plugin",
            ...     "my_package.plugins",
            ...     "my_plugin"
            ... )
        """
        with self._registry_lock:
            # Create metadata if not provided
            if metadata is None:
                metadata = PluginMetadata(name=name)

            # Store lazy load function
            def lazy_loader():
                module = importlib.import_module(module_path)
                return getattr(module, function_name)

            self._lazy_load_funcs[name] = lazy_loader
            self._plugins[name] = {
                "name": name,
                "function": None,  # Will be loaded on first access
                "metadata": metadata,
                "lazy": True,
                "module_path": module_path,
                "function_name": function_name,
            }
            self._metadata[name] = metadata
            self._enabled[name] = True
            self._execution_stats[name] = {
                "execution_count": 0,
                "total_time_ms": 0.0,
                "last_execution_at": None,
                "avg_time_ms": 0.0,
            }

            logger.info(f"Plugin registered for lazy loading: {name}")

    def get_plugin(self, name: str) -> Optional[Callable]:
        """
        Get a plugin function by name.

        This method retrieves a plugin function, loading it first if
        it was registered for lazy loading.

        Args:
            name: Plugin name to retrieve.

        Returns:
            Plugin function, or None if not found.

        Example:
            >>> plugin = registry.get_plugin("my-plugin")
            >>> if plugin:
            ...     result = plugin({"key": "value"})
        """
        with self._registry_lock:
            if name not in self._plugins:
                logger.debug(f"Plugin not found: {name}")
                return None

            plugin_info = self._plugins[name]

            # Load lazy plugin if needed
            if plugin_info.get("lazy") and plugin_info["function"] is None:
                lazy_loader = self._lazy_load_funcs.get(name)
                if lazy_loader:
                    try:
                        plugin_fn = lazy_loader()
                        plugin_info["function"] = plugin_fn
                        plugin_info["lazy"] = False
                        if plugin_info["metadata"]:
                            plugin_info["metadata"].plugin_fn = plugin_fn
                        logger.debug(f"Lazy plugin loaded: {name}")
                    except Exception as e:
                        logger.error(f"Failed to load lazy plugin {name}: {e}")
                        return None

            return plugin_info["function"]

    def execute_plugin(
        self,
        name: str,
        context: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        """
        Execute a plugin with the given context.

        This method retrieves and executes a plugin, tracking execution
        time and statistics.

        Args:
            name: Plugin name to execute.
            context: Context dictionary to pass to the plugin.
            **kwargs: Additional keyword arguments.

        Returns:
            Result of plugin execution.

        Raises:
            ValueError: If plugin not found or not enabled.
            Exception: Any exception raised by the plugin.

        Example:
            >>> def my_plugin(ctx):
            ...     return ctx.get("value", 0) * 2
            >>> registry.register_plugin("my-plugin", my_plugin)
            >>> result = registry.execute_plugin("my-plugin", context={"value": 5})
            >>> print(result)
            10
        """
        import time

        start_time = time.perf_counter()

        with self._registry_lock:
            # Check if plugin exists
            if name not in self._plugins:
                raise ValueError(f"Plugin not found: {name}")

            # Check if plugin is enabled
            if not self._enabled.get(name, False):
                raise ValueError(f"Plugin not enabled: {name}")

            # Get plugin function (loads lazy if needed)
            plugin_fn = self.get_plugin(name)
            if plugin_fn is None:
                raise ValueError(f"Failed to load plugin: {name}")

        # Execute plugin (outside lock for concurrency)
        try:
            context = context or {}
            result = plugin_fn(context, **kwargs)

            # Handle async plugins
            if asyncio.iscoroutine(result):
                # For sync execution, run async plugins
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        future = asyncio.run_coroutine_threadsafe(result, loop)
                        result = future.result(timeout=30)
                    else:
                        result = loop.run_until_complete(result)
                except RuntimeError:
                    result = asyncio.run(result)

            # Record execution stats
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            with self._registry_lock:
                stats = self._execution_stats.get(name, {})
                stats["execution_count"] = stats.get("execution_count", 0) + 1
                stats["total_time_ms"] = stats.get("total_time_ms", 0.0) + elapsed_ms
                stats["last_execution_at"] = time.time()
                stats["avg_time_ms"] = stats["total_time_ms"] / stats["execution_count"]

            # Performance check
            if elapsed_ms > 1.0:
                logger.warning(
                    f"Plugin execution took {elapsed_ms:.2f}ms (target: <1ms): {name}"
                )
            else:
                logger.debug(f"Plugin executed in {elapsed_ms:.3f}ms: {name}")

            return result

        except Exception as e:
            logger.exception(f"Plugin execution failed: {name}: {e}")
            raise

    async def execute_plugin_async(
        self,
        name: str,
        context: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        """
        Execute a plugin asynchronously.

        Args:
            name: Plugin name to execute.
            context: Context dictionary to pass to the plugin.
            **kwargs: Additional keyword arguments.

        Returns:
            Result of plugin execution.
        """
        import time

        start_time = time.perf_counter()

        with self._registry_lock:
            if name not in self._plugins:
                raise ValueError(f"Plugin not found: {name}")

            if not self._enabled.get(name, False):
                raise ValueError(f"Plugin not enabled: {name}")

            plugin_fn = self.get_plugin(name)
            if plugin_fn is None:
                raise ValueError(f"Failed to load plugin: {name}")

        try:
            context = context or {}
            result = plugin_fn(context, **kwargs)

            # Handle async plugins
            if asyncio.iscoroutine(result):
                result = await result

            # Record execution stats
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            with self._registry_lock:
                stats = self._execution_stats.get(name, {})
                stats["execution_count"] = stats.get("execution_count", 0) + 1
                stats["total_time_ms"] = stats.get("total_time_ms", 0.0) + elapsed_ms
                stats["last_execution_at"] = time.time()
                stats["avg_time_ms"] = stats["total_time_ms"] / stats["execution_count"]

            return result

        except Exception as e:
            logger.exception(f"Plugin execution failed: {name}: {e}")
            raise

    def unregister_plugin(self, name: str) -> bool:
        """
        Unregister a plugin from the registry.

        Args:
            name: Plugin name to unregister.

        Returns:
            True if plugin was removed, False if not found.
        """
        with self._registry_lock:
            if name not in self._plugins:
                return False

            del self._plugins[name]
            del self._metadata[name]
            del self._enabled[name]
            del self._execution_stats[name]
            self._lazy_load_funcs.pop(name, None)

            logger.info(f"Plugin unregistered: {name}")
            return True

    def enable_plugin(self, name: str) -> bool:
        """
        Enable a plugin for execution.

        Args:
            name: Plugin name to enable.

        Returns:
            True if plugin was enabled, False if not found.
        """
        with self._registry_lock:
            if name not in self._plugins:
                return False

            self._enabled[name] = True
            if self._metadata.get(name):
                self._metadata[name].enabled = True
                self._metadata[name]._update_timestamp()

            logger.info(f"Plugin enabled: {name}")
            return True

    def disable_plugin(self, name: str) -> bool:
        """
        Disable a plugin to prevent execution.

        Args:
            name: Plugin name to disable.

        Returns:
            True if plugin was disabled, False if not found.
        """
        with self._registry_lock:
            if name not in self._plugins:
                return False

            self._enabled[name] = False
            if self._metadata.get(name):
                self._metadata[name].enabled = False
                self._metadata[name]._update_timestamp()

            logger.info(f"Plugin disabled: {name}")
            return True

    def is_enabled(self, name: str) -> bool:
        """
        Check if a plugin is enabled.

        Args:
            name: Plugin name to check.

        Returns:
            True if plugin is enabled, False otherwise.
        """
        with self._registry_lock:
            if name not in self._plugins:
                return False
            return self._enabled.get(name, False)

    def list_plugins(self) -> List[str]:
        """
        Get a list of all registered plugin names.

        Returns:
            List of plugin names.
        """
        with self._registry_lock:
            return list(self._plugins.keys())

    def list_enabled_plugins(self) -> List[str]:
        """
        Get a list of enabled plugin names.

        Returns:
            List of enabled plugin names.
        """
        with self._registry_lock:
            return [name for name, enabled in self._enabled.items() if enabled]

    def list_disabled_plugins(self) -> List[str]:
        """
        Get a list of disabled plugin names.

        Returns:
            List of disabled plugin names.
        """
        with self._registry_lock:
            return [name for name, enabled in self._enabled.items() if not enabled]

    def get_metadata(self, name: str) -> Optional[PluginMetadata]:
        """
        Get metadata for a plugin.

        Args:
            name: Plugin name.

        Returns:
            PluginMetadata instance, or None if not found.
        """
        with self._registry_lock:
            return self._metadata.get(name)

    def get_all_metadata(self) -> Dict[str, PluginMetadata]:
        """
        Get metadata for all plugins.

        Returns:
            Dictionary mapping plugin names to metadata.
        """
        with self._registry_lock:
            return dict(self._metadata)

    def get_stats(self, name: str) -> Dict[str, Any]:
        """
        Get execution statistics for a plugin.

        Args:
            name: Plugin name.

        Returns:
            Dictionary with execution statistics.
        """
        with self._registry_lock:
            stats = self._execution_stats.get(name, {})
            return {
                "execution_count": stats.get("execution_count", 0),
                "total_time_ms": stats.get("total_time_ms", 0.0),
                "avg_time_ms": stats.get("avg_time_ms", 0.0),
                "last_execution_at": stats.get("last_execution_at"),
            }

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Get execution statistics for all plugins.

        Returns:
            Dictionary mapping plugin names to statistics.
        """
        with self._registry_lock:
            return {name: self.get_stats(name) for name in self._plugins}

    def clear_stats(self, name: Optional[str] = None) -> None:
        """
        Clear execution statistics.

        Args:
            name: Plugin name to clear stats for. If None, clears all.
        """
        with self._registry_lock:
            if name:
                if name in self._execution_stats:
                    self._execution_stats[name] = {
                        "execution_count": 0,
                        "total_time_ms": 0.0,
                        "last_execution_at": None,
                        "avg_time_ms": 0.0,
                    }
            else:
                for plugin_name in self._execution_stats:
                    self._execution_stats[plugin_name] = {
                        "execution_count": 0,
                        "total_time_ms": 0.0,
                        "last_execution_at": None,
                        "avg_time_ms": 0.0,
                    }

    def has_plugin(self, name: str) -> bool:
        """
        Check if a plugin is registered.

        Args:
            name: Plugin name to check.

        Returns:
            True if plugin is registered.
        """
        with self._registry_lock:
            return name in self._plugins

    def get_plugin_count(self) -> int:
        """
        Get the total number of registered plugins.

        Returns:
            Number of registered plugins.
        """
        with self._registry_lock:
            return len(self._plugins)

    def get_enabled_count(self) -> int:
        """
        Get the number of enabled plugins.

        Returns:
            Number of enabled plugins.
        """
        with self._registry_lock:
            return sum(1 for enabled in self._enabled.values() if enabled)

    def cleanup(self) -> None:
        """
        Clean up all plugins and reset the registry.

        This method should be called when shutting down to release resources.
        """
        with self._registry_lock:
            self._plugins.clear()
            self._metadata.clear()
            self._enabled.clear()
            self._execution_stats.clear()
            self._lazy_load_funcs.clear()
            logger.info("PluginRegistry cleaned up")

    def __repr__(self) -> str:
        """Return string representation."""
        with self._registry_lock:
            return (
                f"PluginRegistry(plugins={len(self._plugins)}, "
                f"enabled={self.get_enabled_count()})"
        )


# Import asyncio at module level for use in methods
import asyncio
