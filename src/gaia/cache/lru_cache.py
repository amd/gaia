# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
In-memory LRU (Least Recently Used) cache implementation for GAIA.

Provides a thread-safe, async-compatible LRU cache with O(1) get/set operations
using OrderedDict for efficient eviction management.

Example:
    from gaia.cache import LRUCache

    cache = LRUCache(max_size=1000)
    await cache.set("key1", "value1", expires_at=time.time() + 3600)
    value, expiry = await cache.get("key1")
    await cache.delete("key1")
"""

import asyncio
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple


class LRUCache:
    """
    Thread-safe async LRU cache implementation.

    Uses OrderedDict for O(1) get/set operations with automatic
    least-recently-used eviction when max_size is exceeded.
    Supports TTL-based expiration through expires_at timestamps.

    Attributes:
        max_size: Maximum number of entries before eviction
        current_size: Current number of entries in cache

    Example:
        >>> cache = LRUCache(max_size=100)
        >>> await cache.set("user:1", {"name": "Alice"}, time.time() + 3600)
        >>> value, expiry = await cache.get("user:1")
        >>> print(f"Value: {value}, Expires: {expiry}")
        >>> print(f"Cache size: {len(cache)}")
    """

    def __init__(self, max_size: int = 1000):
        """
        Initialize LRU cache.

        Args:
            max_size: Maximum entries before LRU eviction (default: 1000)

        Raises:
            ValueError: If max_size is not positive

        Example:
            >>> cache = LRUCache(max_size=500)
            >>> print(cache.max_size)  # 500
        """
        if max_size <= 0:
            raise ValueError("max_size must be positive")

        self.max_size = max_size
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._sync_lock = threading.RLock()

        # Eviction tracking
        self._eviction_count = 0

    @property
    def current_size(self) -> int:
        """
        Get current number of entries in cache.

        Returns:
            Current cache size (thread-safe)

        Example:
            >>> print(f"Cache has {cache.current_size} entries")
        """
        with self._sync_lock:
            return len(self._cache)

    async def get(self, key: str) -> Optional[Tuple[Any, float]]:
        """
        Get value and expiry timestamp from cache.

        Moves accessed key to end (most recently used) for LRU tracking.

        Args:
            key: Cache key to retrieve

        Returns:
            Tuple of (value, expires_at) if found, None if not found

        Example:
            >>> await cache.set("key", "value", time.time() + 3600)
            >>> result = await cache.get("key")
            >>> if result:
            ...     value, expiry = result
            ...     if time.time() < expiry:
            ...         print(f"Valid: {value}")
        """
        async with self._lock:
            if key not in self._cache:
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return self._cache[key]

    async def get_value_only(self, key: str) -> Optional[Any]:
        """
        Get only the cached value (without expiry).

        Convenience method when expiry time is not needed.

        Args:
            key: Cache key to retrieve

        Returns:
            Cached value if found, None otherwise

        Example:
            >>> value = await cache.get_value_only("key")
            >>> if value is not None:
            ...     print(f"Found: {value}")
        """
        result = await self.get(key)
        return result[0] if result else None

    async def set(
        self,
        key: str,
        value: Any,
        expires_at: float,
    ) -> Optional[Tuple[str, Any]]:
        """
        Set value with expiration timestamp.

        Automatically evicts LRU entry if cache is at capacity.

        Args:
            key: Cache key
            value: Value to cache (any serializable type)
            expires_at: Absolute Unix timestamp when entry expires

        Returns:
            Evicted (key, value) tuple if eviction occurred, None otherwise

        Example:
            >>> import time
            >>> expiry = time.time() + 3600  # 1 hour TTL
            >>> evicted = await cache.set("user:1", data, expiry)
            >>> if evicted:
            ...     print(f"Evicted: {evicted[0]}")
        """
        evicted = None

        async with self._lock:
            # If key exists, update and move to end
            if key in self._cache:
                self._cache[key] = (value, expires_at)
                self._cache.move_to_end(key)
            else:
                # Check if eviction needed
                if len(self._cache) >= self.max_size:
                    # Pop oldest (least recently used)
                    evicted = self._cache.popitem(last=False)
                    self._eviction_count += 1

                # Add new entry
                self._cache[key] = (value, expires_at)

        return evicted

    async def delete(self, key: str) -> bool:
        """
        Delete key from cache if exists.

        Args:
            key: Cache key to delete

        Returns:
            True if key was deleted, False if not found

        Example:
            >>> deleted = await cache.delete("key")
            >>> if deleted:
            ...     print("Key removed")
        """
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def clear(self) -> None:
        """
        Clear all entries from cache.

        Resets eviction counter and removes all cached data.

        Example:
            >>> await cache.clear()
            >>> print(len(cache))  # 0
        """
        async with self._lock:
            self._cache.clear()
            self._eviction_count = 0

    async def keys(self) -> List[str]:
        """
        Get all keys in cache (in LRU order).

        Returns:
            List of keys from least to most recently used

        Example:
            >>> keys = await cache.keys()
            >>> print(f"LRU order: {keys}")
        """
        async with self._lock:
            return list(self._cache.keys())

    async def values(self) -> List[Any]:
        """
        Get all values in cache (in LRU order).

        Returns:
            List of values from least to most recently used

        Example:
            >>> values = await cache.values()
            >>> print(f"Values: {values}")
        """
        async with self._lock:
            return [v for v, _ in self._cache.values()]

    async def items(self) -> List[Tuple[str, Any, float]]:
        """
        Get all items in cache (in LRU order).

        Returns:
            List of (key, value, expires_at) tuples

        Example:
            >>> items = await cache.items()
            >>> for key, value, expiry in items:
            ...     print(f"{key}: {value} (expires {expiry})")
        """
        async with self._lock:
            return [
                (key, value, expires_at)
                for key, (value, expires_at) in self._cache.items()
            ]

    async def contains(self, key: str) -> bool:
        """
        Check if key exists in cache.

        Does NOT update LRU order (read-only check).

        Args:
            key: Cache key to check

        Returns:
            True if key exists, False otherwise

        Example:
            >>> exists = await cache.contains("key")
            >>> if exists:
            ...     print("Key is cached")
        """
        async with self._lock:
            return key in self._cache

    async def evict_lru(self) -> Optional[Tuple[str, Any]]:
        """
        Evict least recently used entry.

        Manual eviction for capacity management.

        Returns:
            Evicted (key, value) tuple if cache was non-empty, None otherwise

        Example:
            >>> evicted = await cache.evict_lru()
            >>> if evicted:
            ...     print(f"Evicted oldest: {evicted[0]}")
        """
        async with self._lock:
            if not self._cache:
                return None

            self._eviction_count += 1
            return self._cache.popitem(last=False)

    async def evict_expired(self, current_time: Optional[float] = None) -> List[str]:
        """
        Evict all expired entries.

        Args:
            current_time: Current timestamp (uses time.time() if None)

        Returns:
            List of evicted keys

        Example:
            >>> evicted = await cache.evict_expired()
            >>> print(f"Removed {len(evicted)} expired entries")
        """
        if current_time is None:
            current_time = time.time()

        evicted_keys = []

        async with self._lock:
            # Collect expired keys first
            expired = [
                key
                for key, (_, expires_at) in self._cache.items()
                if expires_at <= current_time
            ]

            # Remove expired entries
            for key in expired:
                del self._cache[key]
                evicted_keys.append(key)
                self._eviction_count += 1

        return evicted_keys

    def __len__(self) -> int:
        """
        Get current cache size.

        Returns:
            Number of entries in cache

        Example:
            >>> print(f"Cache size: {len(cache)}")
        """
        with self._sync_lock:
            return len(self._cache)

    async def get_eviction_count(self) -> int:
        """
        Get total number of evictions performed.

        Returns:
            Cumulative eviction count

        Example:
            >>> evictions = await cache.get_eviction_count()
            >>> print(f"Total evictions: {evictions}")
        """
        async with self._lock:
            return self._eviction_count

    async def resize(self, new_max_size: int) -> List[Tuple[str, Any]]:
        """
        Resize cache capacity.

        Evicts entries if new size is smaller than current.

        Args:
            new_max_size: New maximum cache size

        Returns:
            List of evicted (key, value) tuples

        Example:
            >>> evicted = await cache.resize(50)
            >>> print(f"Evicted {len(evicted)} entries to resize")
        """
        if new_max_size <= 0:
            raise ValueError("max_size must be positive")

        evicted = []

        async with self._lock:
            self.max_size = new_max_size

            # Evict if over capacity
            while len(self._cache) > new_max_size:
                entry = self._cache.popitem(last=False)
                evicted.append(entry)
                self._eviction_count += 1

        return evicted

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert cache to dictionary representation.

        Note: This is a synchronous snapshot - for async access use items().

        Returns:
            Dictionary of {key: (value, expires_at)}

        Example:
            >>> snapshot = cache.to_dict()
            >>> print(f"Snapshot: {len(snapshot)} entries")
        """
        with self._sync_lock:
            return dict(self._cache)

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with size, capacity, and eviction stats

        Example:
            >>> stats = await cache.get_stats()
            >>> print(f"Utilization: {stats['utilization']:.1%}")
        """
        async with self._lock:
            return {
                "current_size": len(self._cache),
                "max_size": self.max_size,
                "utilization": len(self._cache) / self.max_size,
                "eviction_count": self._eviction_count,
            }

    def __repr__(self) -> str:
        """Return string representation."""
        return f"LRUCache(max_size={self.max_size}, current_size={self.current_size})"
