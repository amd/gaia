# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
TTL (Time-To-Live) expiration management for GAIA caching.

Provides background cleanup of expired cache entries with
observer pattern for expiration notifications.

Example:
    from gaia.cache import TTLManager

    ttl_mgr = TTLManager(default_ttl=3600)
    ttl_mgr.on_expired(lambda key: print(f"Expired: {key}"))

    expiry = ttl_mgr.compute_expiry(ttl=600)  # 10 minutes
    is_expired = ttl_mgr.is_expired(expiry)

    await ttl_mgr.start(cleanup_interval=60)
    # ... background cleanup running ...
    await ttl_mgr.stop()
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class TTLManager:
    """
    Manages TTL expiration with background cleanup.

    Spawns async task for periodic expired entry removal.
    Notifies observers on expiration events using the observer pattern.

    Attributes:
        default_ttl: Default TTL in seconds for entries without explicit TTL

    Example:
        >>> ttl_mgr = TTLManager(default_ttl=3600)
        >>> ttl_mgr.on_expired(lambda key: log.debug(f"Expired: {key}"))
        >>> await ttl_mgr.start(cleanup_interval=60)
        >>> # Background cleanup now running
        >>> expiry = ttl_mgr.compute_expiry(ttl=300)  # 5 min TTL
        >>> await ttl_mgr.stop()
    """

    def __init__(self, default_ttl: int = 3600):
        """
        Initialize TTL manager.

        Args:
            default_ttl: Default TTL in seconds (default: 3600 = 1 hour)

        Raises:
            ValueError: If default_ttl is not positive

        Example:
            >>> ttl_mgr = TTLManager(default_ttl=1800)  # 30 min default
        """
        if default_ttl <= 0:
            raise ValueError("default_ttl must be positive")

        self.default_ttl = default_ttl

        # Expiration callbacks
        self._expired_callbacks: List[Callable[[str], None]] = []
        self._cleanup_callbacks: List[Callable[[List[str]], None]] = []

        # Background task management
        self._cleanup_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._running = False

        # Tracking
        self._cleanup_count = 0
        self._total_expired = 0
        self._last_cleanup: Optional[float] = None

        # Keys pending cleanup (for observer pattern)
        self._pending_keys: Set[str] = set()
        self._lock = asyncio.Lock()

    def compute_expiry(self, ttl: Optional[int] = None) -> float:
        """
        Compute absolute expiry timestamp from TTL.

        Args:
            ttl: Time-to-live in seconds. If None, uses default_ttl.

        Returns:
            Absolute Unix timestamp when entry expires

        Raises:
            ValueError: If ttl is provided but not positive

        Example:
            >>> ttl_mgr = TTLManager(default_ttl=3600)
            >>> expiry = ttl_mgr.compute_expiry()  # Uses default
            >>> expiry_custom = ttl_mgr.compute_expiry(ttl=300)  # 5 min
            >>> print(f"Expires at: {expiry}")
        """
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")

        effective_ttl = ttl if ttl is not None else self.default_ttl
        return time.time() + effective_ttl

    def is_expired(self, expires_at: float) -> bool:
        """
        Check if timestamp is expired.

        Args:
            expires_at: Absolute Unix timestamp to check

        Returns:
            True if current time is past expires_at

        Example:
            >>> ttl_mgr = TTLManager()
            >>> expiry = ttl_mgr.compute_expiry(ttl=60)
            >>> if ttl_mgr.is_expired(expiry):
            ...     print("Entry has expired")
        """
        return time.time() >= expires_at

    def time_to_expire(self, expires_at: float) -> float:
        """
        Get seconds until expiration.

        Args:
            expires_at: Absolute Unix timestamp

        Returns:
            Seconds until expiration (negative if already expired)

        Example:
            >>> ttl_mgr = TTLManager()
            >>> expiry = ttl_mgr.compute_expiry(ttl=60)
            >>> remaining = ttl_mgr.time_to_expire(expiry)
            >>> print(f"{remaining:.0f}s until expiry")
        """
        return expires_at - time.time()

    def on_expired(self, callback: Callable[[str], None]) -> "TTLManager":
        """
        Register callback for individual key expiration events.

        Called when a specific key is detected as expired.

        Args:
            callback: Function receiving expired key as argument

        Returns:
            Self for method chaining

        Example:
            >>> def on_key_expired(key: str):
            ...     print(f"Key {key} expired")
            >>> ttl_mgr.on_expired(on_key_expired)
        """
        self._expired_callbacks.append(callback)
        return self

    def on_cleanup(self, callback: Callable[[List[str]], None]) -> "TTLManager":
        """
        Register callback for cleanup batch events.

        Called after each cleanup run with list of removed keys.

        Args:
            callback: Function receiving list of expired keys

        Returns:
            Self for method chaining

        Example:
            >>> def on_cleanup(keys: List[str]):
            ...     print(f"Cleaned up {len(keys)} keys")
            >>> ttl_mgr.on_cleanup(on_cleanup)
        """
        self._cleanup_callbacks.append(callback)
        return self

    def remove_callback(self, callback: Callable) -> "TTLManager":
        """
        Remove a registered callback.

        Args:
            callback: Callback to remove

        Returns:
            Self for method chaining

        Example:
            >>> ttl_mgr.remove_callback(my_callback)
        """
        if callback in self._expired_callbacks:
            self._expired_callbacks.remove(callback)
        if callback in self._cleanup_callbacks:
            self._cleanup_callbacks.remove(callback)
        return self

    async def _notify_expired(self, key: str) -> None:
        """
        Notify all callbacks of key expiration.

        Args:
            key: Expired cache key
        """
        for callback in self._expired_callbacks:
            try:
                # Support both sync and async callbacks
                if asyncio.iscoroutinefunction(callback):
                    await callback(key)
                else:
                    callback(key)
            except Exception as e:
                logger.error(f"Error in expired callback for {key}: {e}")

    async def _notify_cleanup(self, keys: List[str]) -> None:
        """
        Notify all callbacks of cleanup completion.

        Args:
            keys: List of expired keys that were cleaned up
        """
        for callback in self._cleanup_callbacks:
            try:
                # Support both sync and async callbacks
                if asyncio.iscoroutinefunction(callback):
                    await callback(keys)
                else:
                    callback(keys)
            except Exception as e:
                logger.error(f"Error in cleanup callback: {e}")

    async def start(self, cleanup_interval: int = 60) -> None:
        """
        Start background cleanup loop.

        Spawns async task that periodically checks for expired entries.

        Args:
            cleanup_interval: Seconds between cleanup runs (default: 60)

        Raises:
            RuntimeError: If already running

        Example:
            >>> ttl_mgr = TTLManager()
            >>> await ttl_mgr.start(cleanup_interval=30)
            >>> # Background cleanup now running every 30 seconds
        """
        if self._running:
            raise RuntimeError("TTLManager is already running")

        if cleanup_interval <= 0:
            raise ValueError("cleanup_interval must be positive")

        self._running = True
        self._stop_event.clear()

        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(cleanup_interval)
        )

        logger.info(f"TTLManager started (interval={cleanup_interval}s)")

    async def _cleanup_loop(self, interval: int) -> None:
        """
        Background cleanup loop.

        Continuously waits and performs cleanup until stopped.

        Args:
            interval: Seconds between cleanup runs
        """
        while not self._stop_event.is_set():
            try:
                # Wait for interval or stop signal
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=interval,
                )
                # Stop event was set, exit loop
                break

            except asyncio.TimeoutError:
                # Timeout means interval elapsed, perform cleanup
                pass

            try:
                await self._run_cleanup()

            except asyncio.CancelledError:
                # Task cancelled, exit gracefully
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

        self._running = False
        logger.info("TTLManager cleanup loop stopped")

    async def _run_cleanup(self) -> int:
        """
        Run a single cleanup cycle.

        Returns:
            Number of entries cleaned up

        Note:
            Subclasses should override this to integrate with
            their specific cache implementation.
        """
        # Default implementation just tracks the cleanup
        self._cleanup_count += 1
        self._last_cleanup = time.time()
        return 0

    async def cleanup_keys(
        self,
        get_keys_func: Callable[[], List[str]],
        is_valid_func: Callable[[str], bool],
    ) -> List[str]:
        """
        Clean up expired keys using provided validation.

        Generic cleanup method that works with any cache implementation.

        Args:
            get_keys_func: Function returning all cache keys
            is_valid_func: Function checking if key is still valid

        Returns:
            List of expired keys that were cleaned up

        Example:
            >>> expired = await ttl_mgr.cleanup_keys(
            ...     get_keys_func=cache.keys,
            ...     is_valid_func=lambda k: not ttl_mgr.is_expired(cache.get_expiry(k))
            ... )
        """
        async with self._lock:
            try:
                # Get all keys
                keys = get_keys_func()

                # Find expired keys
                expired_keys = []
                for key in keys:
                    if not is_valid_func(key):
                        expired_keys.append(key)

                if expired_keys:
                    self._total_expired += len(expired_keys)

                    # Notify callbacks
                    for key in expired_keys:
                        await self._notify_expired(key)

                    await self._notify_cleanup(expired_keys)

                    self._cleanup_count += 1
                    self._last_cleanup = time.time()

                    logger.debug(f"TTL cleanup: removed {len(expired_keys)} expired keys")

                return expired_keys

            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                return []

    async def stop(self, timeout: float = 5.0) -> None:
        """
        Stop background cleanup loop gracefully.

        Args:
            timeout: Maximum seconds to wait for task completion

        Example:
            >>> await ttl_mgr.stop()
            >>> print("TTLManager stopped")
        """
        if not self._running:
            return

        # Signal stop
        self._stop_event.set()

        if self._cleanup_task:
            try:
                # Wait for task to complete
                await asyncio.wait_for(self._cleanup_task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("TTLManager cleanup task didn't stop gracefully, cancelling")
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass

        self._cleanup_task = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """
        Check if cleanup loop is running.

        Returns:
            True if background task is active

        Example:
            >>> if ttl_mgr.is_running:
            ...     print("Cleanup is active")
        """
        return self._running

    def get_stats(self) -> Dict[str, Any]:
        """
        Get TTL manager statistics.

        Returns:
            Dictionary with cleanup count, total expired, etc.

        Example:
            >>> stats = ttl_mgr.get_stats()
            >>> print(f"Cleanups: {stats['cleanup_count']}")
        """
        return {
            "default_ttl": self.default_ttl,
            "is_running": self._running,
            "cleanup_count": self._cleanup_count,
            "total_expired": self._total_expired,
            "last_cleanup": self._last_cleanup,
            "callback_count": len(self._expired_callbacks) + len(self._cleanup_callbacks),
        }

    def reset_stats(self) -> None:
        """
        Reset statistics counters.

        Example:
            >>> ttl_mgr.reset_stats()
            >>> stats = ttl_mgr.get_stats()
            >>> print(f"Cleanups after reset: {stats['cleanup_count']}")
        """
        self._cleanup_count = 0
        self._total_expired = 0
        self._last_cleanup = None

    async def __aenter__(self) -> "TTLManager":
        """Async context manager entry - starts cleanup."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - stops cleanup."""
        await self.stop()

    def __repr__(self) -> str:
        """Return string representation."""
        status = "running" if self._running else "stopped"
        return f"TTLManager(default_ttl={self.default_ttl}s, status={status})"


class TTLRegistry:
    """
    Registry for tracking TTL values by key pattern.

    Useful for applying different TTLs to different types of cached data.

    Example:
        >>> registry = TTLRegistry()
        >>> registry.register_pattern("user:*", ttl=3600)  # Users: 1 hour
        >>> registry.register_pattern("session:*", ttl=300)  # Sessions: 5 min
        >>> registry.register_default(ttl=600)  # Default: 10 min
        >>> ttl = registry.get_ttl("user:123")  # Returns 3600
    """

    def __init__(self, default_ttl: int = 600):
        """
        Initialize TTL registry.

        Args:
            default_ttl: Default TTL for unmatched keys (default: 600)
        """
        self._default_ttl = default_ttl
        self._patterns: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    def register_pattern(self, pattern: str, ttl: int) -> "TTLRegistry":
        """
        Register TTL for key pattern.

        Patterns use simple prefix matching with '*' wildcard.

        Args:
            pattern: Key pattern (e.g., "user:*", "session:*")
            ttl: TTL in seconds for matching keys

        Returns:
            Self for method chaining

        Example:
            >>> registry.register_pattern("cache:*", 1800)
        """
        self._patterns[pattern] = ttl
        return self

    def register_default(self, ttl: int) -> "TTLRegistry":
        """
        Set default TTL for unmatched keys.

        Args:
            ttl: Default TTL in seconds

        Returns:
            Self for method chaining
        """
        self._default_ttl = ttl
        return self

    def get_ttl(self, key: str) -> int:
        """
        Get TTL for a specific key.

        Matches key against registered patterns.
        Returns default if no pattern matches.

        Args:
            key: Cache key to match

        Returns:
            TTL in seconds for this key

        Example:
            >>> ttl = registry.get_ttl("user:123")
            >>> print(f"TTL: {ttl}s")
        """
        # Check patterns in registration order
        for pattern, ttl in self._patterns.items():
            if self._matches_pattern(key, pattern):
                return ttl

        return self._default_ttl

    def _matches_pattern(self, key: str, pattern: str) -> bool:
        """
        Check if key matches pattern.

        Supports '*' wildcard at end of pattern.

        Args:
            key: Cache key
            pattern: Pattern to match

        Returns:
            True if key matches pattern
        """
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return key.startswith(prefix)
        return key == pattern

    def remove_pattern(self, pattern: str) -> bool:
        """
        Remove registered pattern.

        Args:
            pattern: Pattern to remove

        Returns:
            True if pattern was found and removed

        Example:
            >>> registry.remove_pattern("old:*")
        """
        if pattern in self._patterns:
            del self._patterns[pattern]
            return True
        return False

    def get_patterns(self) -> Dict[str, int]:
        """
        Get all registered patterns.

        Returns:
            Copy of pattern-to-TTL mapping

        Example:
            >>> patterns = registry.get_patterns()
            >>> for pattern, ttl in patterns.items():
            ...     print(f"{pattern}: {ttl}s")
        """
        return dict(self._patterns)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"TTLRegistry(default={self._default_ttl}s, patterns={len(self._patterns)})"
