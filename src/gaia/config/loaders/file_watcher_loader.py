# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
File watcher-based configuration loader with hot-reload support for GAIA.

Provides automatic configuration reloading when files change,
using the Sprint 1 FileWatcher utility.

Example:
    from gaia.config.loaders import FileWatcherLoader

    def on_reload(config):
        print(f"Config reloaded: {config}")

    loader = FileWatcherLoader(
        path="./config/app.json",
        on_reload=on_reload
    )
    loader.start()  # Begin watching for changes
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from gaia.utils.file_watcher import FileWatcher, FileChangeHandler

logger = logging.getLogger(__name__)


class FileWatcherLoader:
    """
    Configuration loader with hot-reload support.

    Monitors configuration files for changes and automatically
    reloads configuration, notifying registered callbacks.

    Features:
        - Automatic reload on file change
        - Multiple file support with merge
        - Debounced reloading
        - Callback notifications
        - Graceful shutdown

    Example:
        >>> def on_reload(config):
        ...     print(f"New config: {config}")
        >>>
        >>> loader = FileWatcherLoader(
        ...     path="./config/app.json",
        ...     on_reload=on_reload,
        ...     debounce_seconds=1.0,
        ... )
        >>> loader.start()
        >>> # Config auto-reloads on file changes...
        >>> loader.stop()
    """

    def __init__(
        self,
        path: Union[str, Path, List[Union[str, Path]]],
        loader_func: Optional[Callable[[str], Dict[str, Any]]] = None,
        on_reload: Optional[Callable[[Dict[str, Any]], None]] = None,
        debounce_seconds: float = 1.0,
        recursive: bool = False,
        auto_load: bool = True,
    ):
        """
        Initialize file watcher loader.

        Args:
            path: Path or list of paths to configuration files
            loader_func: Function to load config from path.
                        If None, uses JSON loader.
            on_reload: Callback invoked when config reloads.
                      Receives new configuration dict.
            debounce_seconds: Minimum seconds between reloads (default: 1.0)
            recursive: Whether to watch directories recursively
            auto_load: Whether to load config on initialization

        Example:
            >>> from gaia.config.loaders import JSONLoader
            >>>
            >>> def loader_func(path):
            ...     return JSONLoader(path).load()
            >>>
            >>> loader = FileWatcherLoader(
            ...     path="./config.json",
            ...     loader_func=loader_func,
            ... )
        """
        # Normalize paths to list
        if isinstance(path, (str, Path)):
            self._paths = [Path(path)]
        else:
            self._paths = [Path(p) for p in path]

        self._loader_func = loader_func or self._default_loader
        self._on_reload = on_reload
        self._debounce_seconds = debounce_seconds
        self._recursive = recursive

        # Current configuration
        self._config: Dict[str, Any] = {}
        self._reload_pending = False
        self._reload_task: Optional[asyncio.Task] = None

        # File watcher
        self._watcher: Optional[FileWatcher] = None
        self._watching = False

        # Last reload times per file (for debouncing)
        self._last_reload: Dict[str, float] = {}

        if auto_load:
            self._load_all()

    def _default_loader(self, path: str) -> Dict[str, Any]:
        """
        Default loader using JSON.

        Args:
            path: Path to configuration file

        Returns:
            Configuration dictionary
        """
        from gaia.config.loaders.json_loader import JSONLoader

        try:
            return JSONLoader(path, required=False).load()
        except Exception as e:
            logger.warning(f"Failed to load config from {path}: {e}")
            return {}

    def _load_all(self) -> Dict[str, Any]:
        """
        Load all configuration files and merge.

        Later files override earlier files.

        Returns:
            Merged configuration dictionary
        """
        config = {}

        for path in self._paths:
            if path.exists():
                try:
                    file_config = self._loader_func(str(path))
                    config = self._deep_merge(config, file_config)
                    logger.debug(f"Loaded config from {path}")
                except Exception as e:
                    logger.error(f"Error loading {path}: {e}")
            else:
                logger.debug(f"Config file not found: {path}")

        self._config = config
        return config

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

    def _on_file_changed(self, path: str) -> None:
        """
        Handle file change event.

        Schedules a debounced reload.

        Args:
            path: Path to changed file
        """
        import time

        current_time = time.time()
        last_time = self._last_reload.get(path, 0)

        # Check debounce
        if current_time - last_time < self._debounce_seconds:
            logger.debug(f"Debouncing reload for {path}")
            self._reload_pending = True
            return

        # Schedule reload
        self._reload_pending = False
        self._schedule_reload(path)

    def _schedule_reload(self, path: str) -> None:
        """
        Schedule configuration reload.

        Args:
            path: Path that triggered reload
        """
        import time

        try:
            loop = asyncio.get_event_loop()

            if self._reload_task and not self._reload_task.done():
                self._reload_task.cancel()

            self._reload_task = loop.create_task(self._reload_async(path))

        except RuntimeError:
            # No event loop running - do synchronous reload
            self._reload_sync(path)

    async def _reload_async(self, path: str) -> None:
        """
        Async reload configuration.

        Args:
            path: Path that triggered reload
        """
        import time

        # Wait debounce period
        await asyncio.sleep(self._debounce_seconds)

        # Reload all configs
        new_config = self._load_all()

        # Update last reload time
        for p in self._paths:
            self._last_reload[str(p)] = time.time()

        # Check if config actually changed
        if new_config != self._config:
            logger.info(f"Configuration reloaded from {path}")
            self._config = new_config

            # Notify callbacks
            if self._on_reload:
                try:
                    if asyncio.iscoroutinefunction(self._on_reload):
                        await self._on_reload(self._config)
                    else:
                        self._on_reload(self._config)
                except Exception as e:
                    logger.error(f"Error in reload callback: {e}")

    def _reload_sync(self, path: str) -> None:
        """
        Synchronous reload configuration.

        Args:
            path: Path that triggered reload
        """
        import time

        new_config = self._load_all()

        # Update last reload time
        for p in self._paths:
            self._last_reload[str(p)] = time.time()

        # Check if config changed
        if new_config != self._config:
            logger.info(f"Configuration reloaded from {path}")
            self._config = new_config

            # Notify callbacks
            if self._on_reload:
                try:
                    self._on_reload(self._config)
                except Exception as e:
                    logger.error(f"Error in reload callback: {e}")

    def start(self) -> "FileWatcherLoader":
        """
        Start watching configuration files.

        Returns:
            Self for method chaining

        Example:
            >>> loader.start()
            >>> # Config now auto-reloads on changes
        """
        if self._watching:
            logger.warning("FileWatcherLoader already started")
            return self

        # Create watcher for each path
        for path in self._paths:
            watch_path = path if path.is_dir() else path.parent

            self._watcher = FileWatcher(
                directory=watch_path,
                on_modified=self._on_file_changed,
                extensions=[".json", ".yaml", ".yml"],
                debounce_seconds=self._debounce_seconds,
                recursive=self._recursive,
            )

            self._watcher.start()
            self._watching = True

        logger.info(f"Started watching {len(self._paths)} config file(s)")
        return self

    def stop(self) -> None:
        """
        Stop watching configuration files.

        Example:
            >>> loader.stop()
        """
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

        self._watching = False

        logger.info("Stopped watching configuration files")

    def reload(self) -> Dict[str, Any]:
        """
        Manually trigger configuration reload.

        Returns:
            New configuration dictionary

        Example:
            >>> config = loader.reload()
        """
        new_config = self._load_all()

        if new_config != self._config:
            self._config = new_config

            if self._on_reload:
                try:
                    self._on_reload(self._config)
                except Exception as e:
                    logger.error(f"Error in reload callback: {e}")

        return self._config

    def get_config(self) -> Dict[str, Any]:
        """
        Get current configuration.

        Returns:
            Current configuration dictionary
        """
        return dict(self._config)

    @property
    def is_watching(self) -> bool:
        """
        Check if watcher is running.

        Returns:
            True if watching for changes
        """
        return self._watching

    @property
    def paths(self) -> List[Path]:
        """
        Get watched file paths.

        Returns:
            List of configuration file paths
        """
        return list(self._paths)

    def add_reload_callback(
        self,
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """
        Add a reload callback.

        Args:
            callback: Function to call on reload

        Example:
            >>> loader.add_reload_callback(lambda c: print(c))
        """
        if self._on_reload is None:
            self._on_reload = callback
        else:
            # Chain callbacks
            original = self._on_reload

            def chained(config):
                original(config)
                callback(config)

            self._on_reload = chained

    def __enter__(self) -> "FileWatcherLoader":
        """Context manager entry - starts watching."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - stops watching."""
        self.stop()

    def __repr__(self) -> str:
        """Return string representation."""
        status = "watching" if self._watching else "stopped"
        return f"FileWatcherLoader(paths={len(self._paths)}, status={status})"


class ConfigHotReload:
    """
    High-level hot reload manager for multiple config files.

    Provides a simplified interface for hot-reload configuration
    management with multiple file support.

    Example:
        >>> manager = ConfigHotReload(
        ...     files=["./config/base.json", "./config/local.json"],
        ...     on_reload=lambda c: print("Updated:", c)
        ... )
        >>> with manager:
        ...     # Config auto-reloads while in context
        ...     run_application()
    """

    def __init__(
        self,
        files: List[str],
        on_reload: Optional[Callable[[Dict[str, Any]], None]] = None,
        debounce_seconds: float = 1.0,
    ):
        """
        Initialize hot reload manager.

        Args:
            files: List of configuration file paths
            on_reload: Callback for reload events
            debounce_seconds: Debounce time between reloads
        """
        self._loader = FileWatcherLoader(
            path=files,
            on_reload=on_reload,
            debounce_seconds=debounce_seconds,
        )

    def start(self) -> None:
        """Start hot reload watching."""
        self._loader.start()

    def stop(self) -> None:
        """Stop hot reload watching."""
        self._loader.stop()

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return self._loader.get_config()

    def reload(self) -> Dict[str, Any]:
        """Manually trigger reload."""
        return self._loader.reload()

    def __enter__(self) -> "ConfigHotReload":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop()
