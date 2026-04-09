# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Caching Module - Enterprise-grade multi-level caching system.

This module provides a comprehensive caching infrastructure with:
- Multi-level caching (in-memory LRU + SQLite disk backing)
- TTL-based expiration with background cleanup
- Cache hit/miss tracking and statistics
- Async-safe operations with proper locking
- @cached decorator for easy function caching

Example:
    from gaia.cache import CacheLayer, cached

    # Direct usage
    cache = CacheLayer(
        memory_max_size=1000,
        disk_path="./gaia_cache.db",
        default_ttl=3600,
    )
    await cache.set("key", "value")
    value = await cache.get("key")
    stats = cache.stats()

    # Decorator usage
    @cached(ttl=600)
    async def get_user_data(user_id: int) -> dict:
        return await db.query("SELECT * FROM users WHERE id = ?", user_id)

    # Context manager
    async with CacheLayer() as cache:
        await cache.set("key", "value")
        value = await cache.get("key")
"""

from gaia.cache.cache_layer import (
    CacheLayer,
    cached,
    get_default_cache,
    cache_get,
    cache_set,
)
from gaia.cache.lru_cache import LRUCache
from gaia.cache.disk_cache import DiskCache
from gaia.cache.ttl_manager import TTLManager, TTLRegistry
from gaia.cache.stats import CacheStats, CacheStatsCollector
from gaia.cache.exceptions import (
    CacheError,
    CacheKeyError,
    CacheSerializationError,
    CacheConnectionError,
    CacheConfigurationError,
    CacheTTLExpiredError,
    CacheEvictionError,
    CacheStatsError,
    CacheDecoratorError,
    CacheMissError,
    CacheTimeoutError,
)

__all__ = [
    # Main cache layer
    "CacheLayer",
    "cached",
    "get_default_cache",
    "cache_get",
    "cache_set",
    # Cache implementations
    "LRUCache",
    "DiskCache",
    # TTL management
    "TTLManager",
    "TTLRegistry",
    # Statistics
    "CacheStats",
    "CacheStatsCollector",
    # Exceptions
    "CacheError",
    "CacheKeyError",
    "CacheSerializationError",
    "CacheConnectionError",
    "CacheConfigurationError",
    "CacheTTLExpiredError",
    "CacheEvictionError",
    "CacheStatsError",
    "CacheDecoratorError",
    "CacheMissError",
    "CacheTimeoutError",
]

__version__ = "1.0.0"


def get_version() -> str:
    """Return module version."""
    return __version__
