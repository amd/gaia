# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Multi-level caching system for GAIA with in-memory + disk backing.

Provides a unified facade for two-tier caching with TTL-based expiration,
hit/miss tracking, and async-safe operations.

Example:
    from gaia.cache import CacheLayer

    cache = CacheLayer(
        memory_max_size=1000,
        disk_path="./gaia_cache.db",
        default_ttl=3600,
    )
    await cache.set("key", {"data": "value"})
    value = await cache.get("key")
    stats = cache.stats()
    await cache.stop()
"""

import asyncio
import functools
import logging
import pickle
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

from gaia.cache.lru_cache import LRUCache
from gaia.cache.disk_cache import DiskCache
from gaia.cache.ttl_manager import TTLManager
from gaia.cache.stats import CacheStats
from gaia.cache.exceptions import (
    CacheError,
    CacheKeyError,
    CacheSerializationError,
    CacheConfigurationError,
    CacheDecoratorError,
)

logger = logging.getLogger(__name__)

T = TypeVar('T')
F = TypeVar('F', bound=Callable)


class CacheLayer:
    """
    Multi-level caching system with in-memory + disk backing.

    Provides a unified interface for two-tier caching:
    - Tier 1: LRU in-memory cache for fast access
    - Tier 2: SQLite disk cache for overflow and persistence

    Features:
        - Two-tier caching with automatic spill to disk
        - TTL-based expiration with background cleanup
        - Cache hit/miss tracking and statistics
        - Async-safe with proper locking
        - Integration with Sprint 2 async utilities

    Attributes:
        memory_max_size: Maximum entries in memory cache
        default_ttl: Default TTL for cached entries

    Example:
        >>> cache = CacheLayer(
        ...     memory_max_size=1000,
        ...     disk_path="./gaia_cache.db",
        ...     default_ttl=3600,
        ... )
        >>> await cache.set("key", {"data": "value"})
        >>> value = await cache.get("key")
        >>> stats = cache.stats()
        >>> print(f"Hit rate: {stats.hit_rate:.1%}")
        >>> await cache.stop()
    """

    def __init__(
        self,
        memory_max_size: int = 1000,
        disk_path: Optional[str] = None,
        default_ttl: int = 3600,
        enable_stats: bool = True,
        enable_disk_spill: bool = True,
    ):
        """
        Initialize CacheLayer.

        Args:
            memory_max_size: Maximum entries in memory cache (default: 1000)
            disk_path: Path to SQLite database for disk cache.
                      If None, disk cache is disabled.
            default_ttl: Default TTL in seconds (default: 3600)
            enable_stats: Whether to track cache statistics (default: True)
            enable_disk_spill: Whether to spill to disk when memory is full
                              (default: True)

        Raises:
            CacheConfigurationError: If configuration is invalid

        Example:
            >>> cache = CacheLayer(
            ...     memory_max_size=500,
            ...     disk_path="./cache.db",
            ...     default_ttl=1800,
            ... )
        """
        # Validate configuration
        if memory_max_size <= 0:
            raise CacheConfigurationError(
                "memory_max_size must be positive",
                original_error=ValueError("memory_max_size must be positive"),
            )

        if default_ttl <= 0:
            raise CacheConfigurationError(
                "default_ttl must be positive",
                original_error=ValueError("default_ttl must be positive"),
            )

        self.memory_max_size = memory_max_size
        self.default_ttl = default_ttl
        self.enable_stats = enable_stats
        self.enable_disk_spill = enable_disk_spill and disk_path is not None

        # Initialize cache tiers
        self._memory_cache = LRUCache(max_size=memory_max_size)
        self._disk_cache: Optional[DiskCache] = None

        if self.enable_disk_spill:
            self._disk_cache = DiskCache(disk_path)

        # Initialize TTL manager
        self._ttl_manager = TTLManager(default_ttl=default_ttl)

        # Initialize statistics
        self._stats = CacheStats() if enable_stats else None

        # Lock for atomic operations
        self._lock = asyncio.Lock()

        # Track running state
        self._running = False

        logger.info(
            f"CacheLayer initialized (memory={memory_max_size}, "
            f"disk={self.enable_disk_spill}, ttl={default_ttl}s)"
        )

    async def get(self, key: str, default: Any = None) -> Any:
        """
        Retrieve value from cache.

        Checks memory cache first, then disk cache on miss.
        Updates LRU order and TTL on access.

        Args:
            key: Cache key to retrieve
            default: Default value if not found (default: None)

        Returns:
            Cached value or default if not found/expired

        Example:
            >>> await cache.set("user:1", {"name": "Alice"})
            >>> user = await cache.get("user:1")
            >>> missing = await cache.get("nonexistent", default={})
        """
        start_time = time.perf_counter()

        async with self._lock:
            # Try memory cache first
            memory_result = await self._memory_cache.get(key)

            if memory_result is not None:
                value, expires_at = memory_result

                # Check expiration
                if self._ttl_manager.is_expired(expires_at):
                    # Remove expired entry
                    await self._memory_cache.delete(key)
                    if self._stats:
                        self._stats.record_miss()
                    logger.debug(f"Cache miss (expired): {key}")
                    return default

                # Cache hit
                if self._stats:
                    latency_ms = (time.perf_counter() - start_time) * 1000
                    self._stats.record_hit(latency_ms)
                logger.debug(f"Cache hit (memory): {key}")
                return value

            # Memory miss - try disk cache
            if self._disk_cache:
                disk_data = await self._disk_cache.get(key)

                if disk_data is not None:
                    try:
                        # Disk stores (value, expires_at) tuple
                        disk_value, disk_expires_at = pickle.loads(disk_data)

                        # Check if expired
                        if self._ttl_manager.is_expired(disk_expires_at):
                            await self._disk_cache.delete(key)
                            if self._stats:
                                self._stats.record_miss()
                            logger.debug(f"Cache miss (expired on disk): {key}")
                            return default

                        # Promote to memory cache
                        await self._memory_cache.set(key, disk_value, disk_expires_at)

                        if self._stats:
                            latency_ms = (time.perf_counter() - start_time) * 1000
                            self._stats.record_hit(latency_ms)
                        logger.debug(f"Cache hit (disk): {key}")
                        return disk_value

                    except (pickle.PickleError, TypeError, ValueError) as e:
                        logger.warning(f"Failed to deserialize disk cache value: {e}")
                        await self._disk_cache.delete(key)

            # Complete miss
            if self._stats:
                latency_ms = (time.perf_counter() - start_time) * 1000
                self._stats.record_miss(latency_ms)
            logger.debug(f"Cache miss: {key}")
            return default

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        serialize: bool = True,
    ) -> None:
        """
        Store value in cache.

        Stores in memory cache; spills to disk if memory full.
        Sets TTL based on provided value or default.

        Args:
            key: Cache key
            value: Value to cache (auto-serialized if needed)
            ttl: Time-to-live in seconds (uses default if None)
            serialize: Whether to serialize non-primitive values

        Raises:
            CacheSerializationError: If serialization fails

        Example:
            >>> await cache.set("user:1", {"name": "Alice"}, ttl=3600)
            >>> await cache.set("counter", 42)  # Uses default TTL
        """
        start_time = time.perf_counter()

        # Compute expiry
        expires_at = self._ttl_manager.compute_expiry(ttl)

        async with self._lock:
            # Try to set in memory cache first
            evicted = await self._memory_cache.set(key, value, expires_at)

            # Handle eviction - spill to disk
            if evicted and self._disk_cache and self.enable_disk_spill:
                evicted_key, evicted_data = evicted
                # evicted_data is (value, expires_at) tuple from LRUCache
                evicted_value, evicted_expires_at = evicted_data
                try:
                    # Store (value, expires_at) tuple for disk retrieval
                    serialized = pickle.dumps((evicted_value, evicted_expires_at)) if serialize else evicted_value
                    await self._disk_cache.set(evicted_key, serialized, evicted_expires_at)
                    logger.debug(f"Spilled to disk: {evicted_key}")

                    if self._stats:
                        self._stats.record_eviction()

                except (pickle.PickleError, TypeError) as e:
                    logger.warning(f"Failed to serialize evicted value: {e}")

            # Update stats
            if self._stats:
                latency_ms = (time.perf_counter() - start_time) * 1000
                self._stats.record_set(latency_ms)
                self._stats.update_memory_size(self._memory_cache.current_size)

                if self._disk_cache:
                    disk_count = await self._disk_cache.count()
                    self._stats.update_disk_size(disk_count)

    async def delete(self, key: str) -> bool:
        """
        Delete key from both memory and disk cache.

        Args:
            key: Cache key to delete

        Returns:
            True if key was deleted from either cache, False otherwise

        Example:
            >>> deleted = await cache.delete("user:1")
            >>> if deleted:
            ...     print("Key removed from cache")
        """
        async with self._lock:
            memory_deleted = await self._memory_cache.delete(key)
            disk_deleted = False

            if self._disk_cache:
                disk_deleted = await self._disk_cache.delete(key)

            return memory_deleted or disk_deleted

    async def clear(self) -> None:
        """
        Clear all cache entries (memory + disk).

        Example:
            >>> await cache.clear()
            >>> print("Cache cleared")
        """
        async with self._lock:
            await self._memory_cache.clear()

            if self._disk_cache:
                await self._disk_cache.clear()

            if self._stats:
                self._stats.update_memory_size(0)
                self._stats.update_disk_size(0)

            logger.info("Cache cleared")

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Any],
        ttl: Optional[int] = None,
    ) -> Any:
        """
        Get value or compute and cache using factory.

        Atomic operation - prevents thundering herd problem
        by locking during factory execution.

        Args:
            key: Cache key
            factory: Async function to compute value on miss
            ttl: TTL for cached result (uses default if None)

        Returns:
            Cached or computed value

        Raises:
            CacheError: If factory execution fails

        Example:
            >>> async def fetch_user(user_id: int) -> dict:
            ...     return await db.query("SELECT * FROM users WHERE id = ?", user_id)
            >>>
            >>> user = await cache.get_or_set(
            ...     f"user:{user_id}",
            ...     lambda: fetch_user(user_id),
            ...     ttl=3600,
            ... )
        """
        async with self._lock:
            # Check if already cached
            value = await self.get(key)

            if value is not None:
                return value

            # Compute value
            try:
                if asyncio.iscoroutinefunction(factory):
                    value = await factory()
                else:
                    value = factory()

            except Exception as e:
                raise CacheError(
                    f"Factory execution failed for key {key}: {e}",
                    key=key,
                    original_error=e,
                )

            # Cache the result
            await self.set(key, value, ttl=ttl)
            return value

    def stats(self) -> CacheStats:
        """
        Return current cache statistics.

        Returns:
            CacheStats object with hit/miss rates, sizes, etc.

        Raises:
            CacheError: If statistics are disabled

        Example:
            >>> stats = cache.stats()
            >>> print(f"Hit rate: {stats.hit_rate:.1%}")
            >>> print(f"Memory size: {stats.memory_size}")
            >>> print(f"Disk size: {stats.disk_size}")
        """
        if not self._stats:
            raise CacheError("Statistics are disabled")

        # Update sizes
        self._stats.update_memory_size(self._memory_cache.current_size)

        return self._stats

    async def start_ttl_cleanup(self, interval: int = 60) -> None:
        """
        Start background TTL cleanup task.

        Args:
            interval: Seconds between cleanup runs (default: 60)

        Raises:
            RuntimeError: If already running

        Example:
            >>> await cache.start_ttl_cleanup(interval=30)
            >>> # Background cleanup now running
        """
        if self._running:
            raise RuntimeError("CacheLayer is already running")

        self._running = True

        # Start TTL manager
        await self._ttl_manager.start(cleanup_interval=interval)

        logger.info(f"CacheLayer TTL cleanup started (interval={interval}s)")

    async def stop(self) -> None:
        """
        Graceful shutdown - flush disk, stop tasks.

        Example:
            >>> await cache.stop()
            >>> print("CacheLayer stopped")
        """
        if not self._running:
            return

        self._running = False

        # Stop TTL manager
        await self._ttl_manager.stop()

        # Close disk cache
        if self._disk_cache:
            await self._disk_cache.close()

        logger.info("CacheLayer stopped")

    async def keys(self) -> List[str]:
        """
        Get all keys in memory cache.

        Returns:
            List of cache keys

        Example:
            >>> keys = await cache.keys()
            >>> print(f"Keys: {keys}")
        """
        return await self._memory_cache.keys()

    async def contains(self, key: str) -> bool:
        """
        Check if key exists in cache (memory or disk).

        Args:
            key: Cache key to check

        Returns:
            True if key exists and is not expired

        Example:
            >>> exists = await cache.contains("user:1")
            >>> if exists:
            ...     print("Key is cached")
        """
        async with self._lock:
            # Check memory
            result = await self._memory_cache.get(key)
            if result is not None:
                _, expires_at = result
                return not self._ttl_manager.is_expired(expires_at)

            # Check disk
            if self._disk_cache:
                return await self._disk_cache.contains(key)

            return False

    async def get_expiry(self, key: str) -> Optional[float]:
        """
        Get expiration timestamp for a key.

        Args:
            key: Cache key

        Returns:
            Expiration timestamp or None if not found

        Example:
            >>> expiry = await cache.get_expiry("user:1")
            >>> if expiry:
            ...     remaining = expiry - time.time()
            ...     print(f"{remaining:.0f}s remaining")
        """
        result = await self._memory_cache.get(key)
        if result:
            return result[1]

        return None

    async def refresh(self, key: str, ttl: Optional[int] = None) -> bool:
        """
        Refresh TTL for an existing key.

        Args:
            key: Cache key to refresh
            ttl: New TTL (uses default if None)

        Returns:
            True if key was refreshed, False if not found

        Example:
            >>> refreshed = await cache.refresh("user:1", ttl=7200)
            >>> if refreshed:
            ...     print("TTL extended")
        """
        async with self._lock:
            result = await self._memory_cache.get(key)

            if result is None:
                return False

            value, _ = result
            expires_at = self._ttl_manager.compute_expiry(ttl)
            await self._memory_cache.set(key, value, expires_at)
            return True

    async def get_stats_dict(self) -> Dict[str, Any]:
        """
        Get cache statistics as dictionary.

        Returns:
            Dictionary with all cache metrics

        Example:
            >>> metrics = await cache.get_stats_dict()
            >>> print(f"Hit rate: {metrics['hit_rate']:.1%}")
        """
        stats = self.stats()
        stats_dict = stats.to_dict()

        # Add TTL manager stats
        stats_dict["ttl_stats"] = self._ttl_manager.get_stats()

        if self._disk_cache:
            stats_dict["disk_stats"] = await self._disk_cache.get_stats()

        return stats_dict

    async def __aenter__(self) -> "CacheLayer":
        """Async context manager entry."""
        await self.start_ttl_cleanup()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.stop()

    def __repr__(self) -> str:
        """Return string representation."""
        status = "running" if self._running else "stopped"
        return (
            f"CacheLayer(memory={self.memory_max_size}, "
            f"disk={self.enable_disk_spill}, ttl={self.default_ttl}s, "
            f"status={status})"
        )


# ==================== @cached Decorator ====================

def cached(
    cache: Optional[CacheLayer] = None,
    ttl: int = 3600,
    key_func: Optional[Callable] = None,
    skip_cache_on: Optional[Callable[[Any], bool]] = None,
    prefix: str = "",
) -> Callable[[F], F]:
    """
    Decorator for caching async function results.

    Caches the result of async function calls with configurable TTL
    and custom key generation.

    Args:
        cache: CacheLayer instance (uses default if None)
        ttl: Time-to-live in seconds (default: 3600)
        key_func: Function to generate cache key from args/kwargs.
                 If None, uses default key generation.
        skip_cache_on: Predicate to skip caching on certain results.
                      Receives result, returns True to skip caching.
        prefix: Optional prefix for cache keys

    Returns:
        Decorated async function with caching

    Example:
        >>> @cached(ttl=600)
        ... async def get_user_data(user_id: int) -> dict:
        ...     return await db.query("SELECT * FROM users WHERE id = ?", user_id)

        >>> @cached(key_func=lambda uid, refresh: f"user:{uid}")
        ... async def get_user(uid: int, refresh: bool = False) -> dict:
        ...     ...

        >>> @cached(skip_cache_on=lambda r: r is None)
        ... async def get_optional_data(key: str) -> Optional[dict]:
        ...     ...
    """
    # Default cache instance
    _default_cache: Optional[CacheLayer] = cache

    def make_key(func: Callable, args: tuple, kwargs: dict) -> str:
        """Generate cache key from function and arguments."""
        if key_func:
            return f"{prefix}{key_func(*args, **kwargs)}"

        # Default key generation
        key_parts = [prefix, func.__module__, func.__qualname__]
        key_parts.extend(str(a) for a in args)
        key_parts.extend(f"{k}={v!r}" for k, v in sorted(kwargs.items()))
        return ":".join(filter(None, key_parts))

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # Get cache instance
            nonlocal _default_cache
            if _default_cache is None:
                # Try to get from function context or create default
                _default_cache = CacheLayer()

            cache_key = make_key(func, args, kwargs)

            # Try cache first
            try:
                cached_value = await _default_cache.get(cache_key)
                if cached_value is not None:
                    logger.debug(f"Cache hit: {cache_key}")
                    return cached_value
            except Exception as e:
                logger.warning(f"Cache get failed: {e}")

            # Cache miss - compute value
            try:
                result = await func(*args, **kwargs)
            except Exception as e:
                raise CacheDecoratorError(
                    f"Function {func.__name__} failed: {e}",
                    key=cache_key,
                    original_error=e,
                )

            # Check if we should skip caching
            if skip_cache_on and skip_cache_on(result):
                logger.debug(f"Skip caching: {cache_key}")
                return result

            # Cache the result
            try:
                await _default_cache.set(cache_key, result, ttl=ttl)
                logger.debug(f"Cached: {cache_key}")
            except CacheSerializationError as e:
                logger.warning(f"Cache set failed (serialization): {e}")
            except Exception as e:
                logger.warning(f"Cache set failed: {e}")

            return result

        def cache_clear() -> None:
            """Clear cached entries for this function."""
            # Note: This is a no-op for the shared cache
            logger.debug("cache_clear called (shared cache)")

        def cache_info() -> Dict[str, Any]:
            """Get cache statistics."""
            if _default_cache:
                return _default_cache.stats().to_dict()
            return {"error": "No cache available"}

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        wrapper.cache_info = cache_info  # type: ignore[attr-defined]

        return wrapper  # type: ignore[return-value]

    return decorator


# Module convenience functions

def get_default_cache() -> CacheLayer:
    """
    Get or create default cache instance.

    Returns:
        Default CacheLayer instance

    Example:
        >>> cache = get_default_cache()
        >>> await cache.set("key", "value")
    """
    return CacheLayer()


async def cache_get(key: str, default: Any = None) -> Any:
    """
    Convenience function for cache get using default cache.

    Args:
        key: Cache key
        default: Default value if not found

    Returns:
        Cached value or default

    Example:
        >>> value = await cache_get("user:1")
    """
    cache = get_default_cache()
    return await cache.get(key, default)


async def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    """
    Convenience function for cache set using default cache.

    Args:
        key: Cache key
        value: Value to cache
        ttl: Optional TTL in seconds

    Example:
        >>> await cache_set("user:1", {"name": "Alice"}, ttl=3600)
    """
    cache = get_default_cache()
    await cache.set(key, value, ttl=ttl)
