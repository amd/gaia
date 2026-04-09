# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for CacheLayer.

Tests the main cache facade including get/set operations,
TTL management, and statistics tracking.
"""

import asyncio
import pickle
import pytest
import time
from typing import Any, Dict

from gaia.cache.cache_layer import CacheLayer, cached
from gaia.cache.stats import CacheStats
from gaia.cache.exceptions import (
    CacheConfigurationError,
    CacheError,
)


class TestCacheLayerInit:
    """Test CacheLayer initialization."""

    def test_init_default(self):
        """Test default initialization."""
        cache = CacheLayer()
        assert cache.memory_max_size == 1000
        assert cache.default_ttl == 3600
        assert cache.enable_stats is True

    def test_init_custom_params(self):
        """Test initialization with custom parameters."""
        cache = CacheLayer(
            memory_max_size=500,
            disk_path=None,
            default_ttl=1800,
            enable_stats=False,
        )
        assert cache.memory_max_size == 500
        assert cache.default_ttl == 1800
        assert cache.enable_stats is False

    def test_init_invalid_max_size(self):
        """Test initialization with invalid max_size."""
        with pytest.raises(CacheConfigurationError):
            CacheLayer(memory_max_size=0)

        with pytest.raises(CacheConfigurationError):
            CacheLayer(memory_max_size=-100)

    def test_init_invalid_ttl(self):
        """Test initialization with invalid TTL."""
        with pytest.raises(CacheConfigurationError):
            CacheLayer(default_ttl=0)

        with pytest.raises(CacheConfigurationError):
            CacheLayer(default_ttl=-100)


class TestCacheLayerGetSet:
    """Test CacheLayer get/set operations."""

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        """Test basic set and get."""
        cache = CacheLayer()
        await cache.set("key1", "value1")
        result = await cache.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_get_missing_key(self):
        """Test getting a key that doesn't exist."""
        cache = CacheLayer()
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_with_default(self):
        """Test getting a key with default value."""
        cache = CacheLayer()
        result = await cache.get("nonexistent", default="default_value")
        assert result == "default_value"

    @pytest.mark.asyncio
    async def test_set_overwrite(self):
        """Test overwriting an existing key."""
        cache = CacheLayer()
        await cache.set("key1", "value1")
        await cache.set("key1", "value2")
        result = await cache.get("key1")
        assert result == "value2"

    @pytest.mark.asyncio
    async def test_set_complex_value(self):
        """Test setting complex values."""
        cache = CacheLayer()
        data = {"name": "Alice", "age": 30, "tags": ["a", "b"]}
        await cache.set("user:1", data)
        result = await cache.get("user:1")
        assert result == data

    @pytest.mark.asyncio
    async def test_set_with_custom_ttl(self):
        """Test setting with custom TTL."""
        cache = CacheLayer()
        await cache.set("key1", "value1", ttl=1)
        result = await cache.get("key1")
        assert result == "value1"

        # Wait for expiration
        await asyncio.sleep(1.5)
        result = await cache.get("key1")
        assert result is None


class TestCacheLayerDelete:
    """Test CacheLayer delete operations."""

    @pytest.mark.asyncio
    async def test_delete_existing(self):
        """Test deleting an existing key."""
        cache = CacheLayer()
        await cache.set("key1", "value1")
        deleted = await cache.delete("key1")
        assert deleted is True

        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_missing(self):
        """Test deleting a non-existent key."""
        cache = CacheLayer()
        deleted = await cache.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_clear(self):
        """Test clearing all cache entries."""
        cache = CacheLayer()
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")

        await cache.clear()

        assert await cache.get("key1") is None
        assert await cache.get("key2") is None
        assert await cache.get("key3") is None


class TestCacheLayerGetOrSet:
    """Test CacheLayer get_or_set operations."""

    @pytest.mark.asyncio
    async def test_get_or_set_cache_miss(self):
        """Test get_or_set on cache miss."""
        cache = CacheLayer()

        async def factory():
            return "computed_value"

        result = await cache.get_or_set("key1", factory)
        assert result == "computed_value"

        # Verify it's cached
        cached_result = await cache.get("key1")
        assert cached_result == "computed_value"

    @pytest.mark.asyncio
    async def test_get_or_set_cache_hit(self):
        """Test get_or_set on cache hit."""
        cache = CacheLayer()
        await cache.set("key1", "cached_value")

        call_count = [0]

        async def factory():
            call_count[0] += 1
            return "computed_value"

        result = await cache.get_or_set("key1", factory)
        assert result == "cached_value"
        assert call_count[0] == 0  # Factory not called

    @pytest.mark.asyncio
    async def test_get_or_set_with_ttl(self):
        """Test get_or_set with custom TTL."""
        cache = CacheLayer()

        async def factory():
            return "value"

        await cache.get_or_set("key1", factory, ttl=1)

        # Wait for expiration
        await asyncio.sleep(1.5)
        result = await cache.get("key1")
        assert result is None


class TestCacheLayerStats:
    """Test CacheLayer statistics."""

    @pytest.mark.asyncio
    async def test_stats_tracking(self):
        """Test that stats are tracked."""
        cache = CacheLayer()

        await cache.set("key1", "value1")
        await cache.get("key1")  # Hit
        await cache.get("key2")  # Miss

        stats = cache.stats()
        assert stats.hits == 1
        assert stats.misses == 1
        assert stats.total_sets == 1

    def test_stats_disabled(self):
        """Test behavior when stats are disabled."""
        cache = CacheLayer(enable_stats=False)

        with pytest.raises(CacheError):
            cache.stats()

    @pytest.mark.asyncio
    async def test_hit_rate_calculation(self):
        """Test hit rate calculation."""
        cache = CacheLayer()

        # 2 hits, 1 miss = 66.7% hit rate
        await cache.set("key1", "value1")
        await cache.get("key1")  # Hit
        await cache.get("key2")  # Miss
        await cache.get("key1")  # Hit

        stats = cache.stats()
        assert stats.hit_rate == pytest.approx(2/3, rel=0.01)

    @pytest.mark.asyncio
    async def test_stats_reset_on_clear(self):
        """Test that clear doesn't reset stats."""
        cache = CacheLayer()

        await cache.set("key1", "value1")
        await cache.get("key1")

        await cache.clear()

        stats = cache.stats()
        assert stats.hits == 1  # Stats preserved


class TestCacheLayerTTL:
    """Test CacheLayer TTL management."""

    @pytest.mark.asyncio
    async def test_ttl_expiration(self):
        """Test that entries expire after TTL."""
        cache = CacheLayer(default_ttl=1)

        await cache.set("key1", "value1")
        result = await cache.get("key1")
        assert result == "value1"

        await asyncio.sleep(1.5)

        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_ttl_cleanup(self):
        """Test background TTL cleanup."""
        cache = CacheLayer(default_ttl=1)
        await cache.start_ttl_cleanup(interval=1)

        try:
            await cache.set("key1", "value1")
            await asyncio.sleep(2)

            result = await cache.get("key1")
            assert result is None
        finally:
            await cache.stop()


class TestCacheLayerConcurrency:
    """Test CacheLayer concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_get_set(self):
        """Test concurrent get/set operations."""
        cache = CacheLayer()
        errors = []

        async def worker(worker_id: int):
            try:
                for i in range(10):
                    key = f"worker:{worker_id}:key:{i}"
                    await cache.set(key, f"value:{i}")
                    value = await cache.get(key)
                    assert value == f"value:{i}"
            except Exception as e:
                errors.append((worker_id, str(e)))

        # Run 10 concurrent workers
        tasks = [worker(i) for i in range(10)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Concurrency errors: {errors}"


class TestCacheLayerContextManager:
    """Test CacheLayer as context manager."""

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """Test using CacheLayer as async context manager."""
        async with CacheLayer() as cache:
            await cache.set("key1", "value1")
            result = await cache.get("key1")
            assert result == "value1"

        # After context exit, cache should be stopped


class TestCacheLayerKeys:
    """Test CacheLayer key operations."""

    @pytest.mark.asyncio
    async def test_keys(self):
        """Test getting all keys."""
        cache = CacheLayer()
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")

        keys = await cache.keys()
        assert "key1" in keys
        assert "key2" in keys
        assert "key3" in keys

    @pytest.mark.asyncio
    async def test_contains(self):
        """Test checking if key exists."""
        cache = CacheLayer()
        await cache.set("key1", "value1")

        assert await cache.contains("key1") is True
        assert await cache.contains("key2") is False

    @pytest.mark.asyncio
    async def test_refresh(self):
        """Test refreshing TTL."""
        cache = CacheLayer(default_ttl=1)
        await cache.set("key1", "value1")

        # Refresh with new TTL
        refreshed = await cache.refresh("key1", ttl=5)
        assert refreshed is True

        await asyncio.sleep(1.5)

        # Should still exist due to refresh
        result = await cache.get("key1")
        assert result == "value1"


class TestCacheLayerHitRate:
    """Verify cache achieves target hit rates."""

    @pytest.mark.asyncio
    async def test_hit_rate_exceeds_80_percent(self):
        """Cache hit rate should exceed 80% for repeated accesses."""
        cache = CacheLayer(memory_max_size=100)
        await cache.start_ttl_cleanup()

        try:
            # Set 10 values
            for i in range(10):
                await cache.set(f"key:{i}", f"value:{i}")

            # Access 100 times (10 unique keys * 10 accesses)
            for _ in range(10):
                for i in range(10):
                    result = await cache.get(f"key:{i}")
                    assert result == f"value:{i}"

            stats = cache.stats()
            assert stats.hit_rate > 0.80, f"Hit rate {stats.hit_rate} below 80% target"
        finally:
            await cache.stop()


class TestCachedDecorator:
    """Test @cached decorator."""

    @pytest.mark.asyncio
    async def test_cached_basic(self):
        """Test basic cached decorator usage."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60)
        async def get_value(x: int) -> int:
            call_count[0] += 1
            return x * 2

        # First call - cache miss
        result1 = await get_value(5)
        assert result1 == 10
        assert call_count[0] == 1

        # Second call - cache hit
        result2 = await get_value(5)
        assert result2 == 10
        assert call_count[0] == 1  # Not called again

    @pytest.mark.asyncio
    async def test_cached_different_args(self):
        """Test cached with different arguments."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60)
        async def get_value(x: int) -> int:
            call_count[0] += 1
            return x * 2

        await get_value(5)
        await get_value(10)

        assert call_count[0] == 2  # Called for each unique arg

    @pytest.mark.asyncio
    async def test_cached_custom_key(self):
        """Test cached with custom key function."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60, key_func=lambda x, y: f"sum:{x}:{y}")
        async def compute(x: int, y: int) -> int:
            call_count[0] += 1
            return x + y

        await compute(1, 2)
        await compute(1, 2)  # Cache hit

        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_cached_skip_on_condition(self):
        """Test cached with skip condition."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60, skip_cache_on=lambda r: r is None)
        async def get_optional(x: int) -> int:
            call_count[0] += 1
            return x if x > 0 else None

        await get_optional(-1)  # Result is None, not cached
        await get_optional(-1)  # Called again

        assert call_count[0] == 2

        await get_optional(5)  # Result cached
        await get_optional(5)  # Cache hit

        assert call_count[0] == 3
