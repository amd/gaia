# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Cache statistics and metrics tracking for the GAIA caching system.

Provides comprehensive performance tracking including hit/miss rates,
latency measurements, and cache size metrics.

Example:
    from gaia.cache import CacheStats

    stats = CacheStats()
    stats.record_hit()
    stats.record_miss()
    stats.record_get_latency(2.5)  # ms

    print(f"Hit rate: {stats.hit_rate:.1%}")
    print(f"Avg get latency: {stats.avg_get_latency_ms:.2f}ms")

    # Convert to dict for logging/monitoring
    metrics = stats.to_dict()
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CacheStats:
    """
    Cache performance statistics.

    Tracks comprehensive metrics for cache performance monitoring,
    including hit/miss rates, latency percentiles, and size metrics.
    Thread-safe for concurrent access.

    Attributes:
        hits: Number of cache hits (successful retrievals)
        misses: Number of cache misses (key not found)
        memory_size: Current number of entries in memory cache
        disk_size: Current number of entries in disk cache
        evictions: Total entries evicted due to capacity limits
        total_gets: Total get() operations performed
        total_sets: Total set() operations performed
        total_get_latency_ms: Cumulative get() latency in milliseconds
        total_set_latency_ms: Cumulative set() latency in milliseconds

    Example:
        >>> stats = CacheStats()
        >>> stats.record_hit()
        >>> stats.record_hit()
        >>> stats.record_miss()
        >>> print(f"Hit rate: {stats.hit_rate:.1%}")  # 66.7%
        >>> print(f"Total operations: {stats.total_operations}")  # 3
    """

    hits: int = 0
    misses: int = 0
    memory_size: int = 0
    disk_size: int = 0
    evictions: int = 0
    total_gets: int = 0
    total_sets: int = 0
    total_get_latency_ms: float = 0.0
    total_set_latency_ms: float = 0.0

    # Internal lock for thread safety
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def record_hit(self, latency_ms: float = 0.0) -> None:
        """
        Record a cache hit.

        Args:
            latency_ms: Optional latency of the get operation in milliseconds

        Example:
            >>> stats = CacheStats()
            >>> stats.record_hit(latency_ms=1.5)
            >>> print(stats.hits)  # 1
        """
        with self._lock:
            self.hits += 1
            self.total_gets += 1
            self.total_get_latency_ms += latency_ms

    def record_miss(self, latency_ms: float = 0.0) -> None:
        """
        Record a cache miss.

        Args:
            latency_ms: Optional latency of the get operation in milliseconds

        Example:
            >>> stats = CacheStats()
            >>> stats.record_miss(latency_ms=0.5)
            >>> print(stats.misses)  # 1
        """
        with self._lock:
            self.misses += 1
            self.total_gets += 1
            self.total_get_latency_ms += latency_ms

    def record_set(self, latency_ms: float = 0.0) -> None:
        """
        Record a cache set operation.

        Args:
            latency_ms: Optional latency of the set operation in milliseconds

        Example:
            >>> stats = CacheStats()
            >>> stats.record_set(latency_ms=2.0)
            >>> print(stats.total_sets)  # 1
        """
        with self._lock:
            self.total_sets += 1
            self.total_set_latency_ms += latency_ms

    def record_eviction(self, count: int = 1) -> None:
        """
        Record cache eviction(s).

        Args:
            count: Number of entries evicted (default: 1)

        Example:
            >>> stats = CacheStats()
            >>> stats.record_eviction()
            >>> stats.record_eviction(3)  # Multiple evictions
            >>> print(stats.evictions)  # 4
        """
        with self._lock:
            self.evictions += count

    def update_memory_size(self, size: int) -> None:
        """
        Update current memory cache size.

        Args:
            size: Current number of entries in memory cache

        Example:
            >>> stats = CacheStats()
            >>> stats.update_memory_size(100)
            >>> print(stats.memory_size)  # 100
        """
        with self._lock:
            self.memory_size = size

    def update_disk_size(self, size: int) -> None:
        """
        Update current disk cache size.

        Args:
            size: Current number of entries in disk cache

        Example:
            >>> stats = CacheStats()
            >>> stats.update_disk_size(500)
            >>> print(stats.disk_size)  # 500
        """
        with self._lock:
            self.disk_size = size

    @property
    def hit_rate(self) -> float:
        """
        Compute cache hit rate percentage.

        Returns:
            Hit rate as a float between 0.0 and 1.0.
            Returns 0.0 if no operations have been performed.

        Example:
            >>> stats = CacheStats()
            >>> stats.record_hit()
            >>> stats.record_hit()
            >>> stats.record_miss()
            >>> print(f"{stats.hit_rate:.1%}")  # 66.7%
        """
        with self._lock:
            total = self.hits + self.misses
            if total == 0:
                return 0.0
            return self.hits / total

    @property
    def miss_rate(self) -> float:
        """
        Compute cache miss rate percentage.

        Returns:
            Miss rate as a float between 0.0 and 1.0.
            Returns 0.0 if no operations have been performed.

        Example:
            >>> stats = CacheStats()
            >>> stats.record_hit()
            >>> stats.record_miss()
            >>> print(f"{stats.miss_rate:.1%}")  # 50.0%
        """
        with self._lock:
            total = self.hits + self.misses
            if total == 0:
                return 0.0
            return self.misses / total

    @property
    def avg_get_latency_ms(self) -> float:
        """
        Compute average get() operation latency.

        Returns:
            Average latency in milliseconds.
            Returns 0.0 if no get operations have been performed.

        Example:
            >>> stats = CacheStats()
            >>> stats.record_hit(latency_ms=1.0)
            >>> stats.record_hit(latency_ms=3.0)
            >>> print(f"{stats.avg_get_latency_ms:.2f}ms")  # 2.00ms
        """
        with self._lock:
            if self.total_gets == 0:
                return 0.0
            return self.total_get_latency_ms / self.total_gets

    @property
    def avg_set_latency_ms(self) -> float:
        """
        Compute average set() operation latency.

        Returns:
            Average latency in milliseconds.
            Returns 0.0 if no set operations have been performed.

        Example:
            >>> stats = CacheStats()
            >>> stats.record_set(latency_ms=2.0)
            >>> stats.record_set(latency_ms=4.0)
            >>> print(f"{stats.avg_set_latency_ms:.2f}ms")  # 3.00ms
        """
        with self._lock:
            if self.total_sets == 0:
                return 0.0
            return self.total_set_latency_ms / self.total_sets

    @property
    def total_operations(self) -> int:
        """
        Get total number of cache operations.

        Returns:
            Sum of all get and set operations.

        Example:
            >>> stats = CacheStats()
            >>> stats.record_hit()
            >>> stats.record_set()
            >>> print(stats.total_operations)  # 2
        """
        with self._lock:
            return self.total_gets + self.total_sets

    @property
    def cache_size(self) -> int:
        """
        Get total cache size (memory + disk).

        Returns:
            Total number of cached entries.

        Example:
            >>> stats = CacheStats()
            >>> stats.update_memory_size(100)
            >>> stats.update_disk_size(500)
            >>> print(stats.cache_size)  # 600
        """
        with self._lock:
            return self.memory_size + self.disk_size

    def reset(self) -> None:
        """
        Reset all statistics counters.

        Clears all metrics except configuration values.
        Useful for periodic metric collection.

        Example:
            >>> stats = CacheStats()
            >>> stats.record_hit()
            >>> stats.record_miss()
            >>> stats.reset()
            >>> print(stats.hits)  # 0
        """
        with self._lock:
            self.hits = 0
            self.misses = 0
            self.total_gets = 0
            self.total_sets = 0
            self.total_get_latency_ms = 0.0
            self.total_set_latency_ms = 0.0
            self.evictions = 0
            # Note: memory_size and disk_size are not reset

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert statistics to dictionary for logging/monitoring.

        Returns:
            Dictionary containing all statistics including computed metrics.

        Example:
            >>> stats = CacheStats()
            >>> stats.record_hit()
            >>> stats.record_miss()
            >>> metrics = stats.to_dict()
            >>> print(metrics["hit_rate"])  # 0.5
            >>> print(metrics["hits"])  # 1
        """
        with self._lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": self.hit_rate,
                "miss_rate": self.miss_rate,
                "memory_size": self.memory_size,
                "disk_size": self.disk_size,
                "cache_size": self.cache_size,
                "evictions": self.evictions,
                "total_operations": self.total_operations,
                "total_gets": self.total_gets,
                "total_sets": self.total_sets,
                "avg_get_latency_ms": round(self.avg_get_latency_ms, 3),
                "avg_set_latency_ms": round(self.avg_set_latency_ms, 3),
            }

    def __str__(self) -> str:
        """Return human-readable statistics summary."""
        with self._lock:
            return (
                f"CacheStats(hits={self.hits}, misses={self.misses}, "
                f"hit_rate={self.hit_rate:.1%}, memory_size={self.memory_size}, "
                f"disk_size={self.disk_size}, evictions={self.evictions}, "
                f"avg_get_latency={self.avg_get_latency_ms:.2f}ms)"
            )

    def __repr__(self) -> str:
        """Return detailed representation."""
        with self._lock:
            return (
                f"CacheStats(hits={self.hits}, misses={self.misses}, "
                f"hit_rate={self.hit_rate:.4f}, memory_size={self.memory_size}, "
                f"disk_size={self.disk_size}, evictions={self.evictions}, "
                f"total_gets={self.total_gets}, total_sets={self.total_sets})"
            )


class CacheStatsCollector:
    """
    Collector for aggregating statistics from multiple cache instances.

    Useful for monitoring multiple caches or collecting stats over time periods.

    Example:
        >>> collector = CacheStatsCollector()
        >>> collector.add(stats1, "memory_cache")
        >>> collector.add(stats2, "disk_cache")
        >>> aggregated = collector.aggregate()
        >>> print(f"Total hits: {aggregated['total_hits']}")
    """

    def __init__(self):
        """Initialize the collector."""
        self._stats: Dict[str, CacheStats] = {}
        self._lock = threading.RLock()

    def add(self, stats: CacheStats, name: str) -> None:
        """
        Add statistics for tracking.

        Args:
            stats: CacheStats instance to track
            name: Name identifier for these stats

        Example:
            >>> collector = CacheStatsCollector()
            >>> collector.add(stats, "user_cache")
        """
        with self._lock:
            self._stats[name] = stats

    def remove(self, name: str) -> None:
        """
        Remove tracked statistics.

        Args:
            name: Name identifier to remove

        Example:
            >>> collector.remove("old_cache")
        """
        with self._lock:
            self._stats.pop(name, None)

    def aggregate(self) -> Dict[str, Any]:
        """
        Aggregate statistics from all tracked caches.

        Returns:
            Dictionary with aggregated metrics across all caches.

        Example:
            >>> aggregated = collector.aggregate()
            >>> print(f"Total operations: {aggregated['total_operations']}")
        """
        with self._lock:
            if not self._stats:
                return {"cache_count": 0}

            total_hits = sum(s.hits for s in self._stats.values())
            total_misses = sum(s.misses for s in self._stats.values())
            total_gets = sum(s.total_gets for s in self._stats.values())
            total_sets = sum(s.total_sets for s in self._stats.values())
            total_get_latency = sum(s.total_get_latency_ms for s in self._stats.values())
            total_set_latency = sum(s.total_set_latency_ms for s in self._stats.values())
            total_evictions = sum(s.evictions for s in self._stats.values())
            total_memory = sum(s.memory_size for s in self._stats.values())
            total_disk = sum(s.disk_size for s in self._stats.values())

            total_ops = total_hits + total_misses
            hit_rate = total_hits / total_ops if total_ops > 0 else 0.0
            avg_get_latency = total_get_latency / total_gets if total_gets > 0 else 0.0
            avg_set_latency = total_set_latency / total_sets if total_sets > 0 else 0.0

            return {
                "cache_count": len(self._stats),
                "total_hits": total_hits,
                "total_misses": total_misses,
                "hit_rate": round(hit_rate, 4),
                "total_operations": total_gets + total_sets,
                "total_evictions": total_evictions,
                "total_memory_size": total_memory,
                "total_disk_size": total_disk,
                "avg_get_latency_ms": round(avg_get_latency, 3),
                "avg_set_latency_ms": round(avg_set_latency, 3),
            }

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """
        Get individual statistics for each tracked cache.

        Returns:
            Dictionary mapping cache names to their stats dicts.

        Example:
            >>> all_stats = collector.get_all()
            >>> for name, stats in all_stats.items():
            ...     print(f"{name}: {stats['hit_rate']:.1%} hit rate")
        """
        with self._lock:
            return {name: stats.to_dict() for name, stats in self._stats.items()}

    def clear(self) -> None:
        """Clear all tracked statistics."""
        with self._lock:
            self._stats.clear()
