# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for LRUCache.

Tests the in-memory LRU cache implementation.
"""

import asyncio
import pytest
import time

from gaia.cache.lru_cache import LRUCache


class TestLRUCacheInit:
    """Test LRUCache initialization."""

    def test_init_default(self):
        """Test default initialization."""
        cache = LRUCache()
        assert cache.max_size == 1000
        assert cache.current_size == 0

    def test_init_custom_size(self):
        """Test initialization with custom size."""
        cache = LRUCache(max_size=50)
        assert cache.max_size == 50

    def test_init_invalid_size(self):
        """Test initialization with invalid size."""
        with pytest.raises(ValueError):
            LRUCache(max_size=0)

        with pytest.raises(ValueError):
            LRUCache(max_size=-100)


class TestLRUCacheBasic:
    """Test basic LRUCache operations."""

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        """Test basic set and get."""
        cache = LRUCache(max_size=10)
        await cache.set("key1", "value1", time.time() + 3600)

        result = await cache.get("key1")
        assert result is not None
        assert result[0] == "value1"

    @pytest.mark.asyncio
    async def test_get_missing(self):
        """Test getting non-existent key."""
        cache = LRUCache()
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_value_only(self):
        """Test get_value_only method."""
        cache = LRUCache()
        await cache.set("key1", "value1", time.time() + 3600)

        value = await cache.get_value_only("key1")
        assert value == "value1"

    @pytest.mark.asyncio
    async def test_delete(self):
        """Test delete operation."""
        cache = LRUCache()
        await cache.set("key1", "value1", time.time() + 3600)

        deleted = await cache.delete("key1")
        assert deleted is True

        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_missing(self):
        """Test deleting non-existent key."""
        cache = LRUCache()
        deleted = await cache.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_clear(self):
        """Test clear operation."""
        cache = LRUCache()
        for i in range(5):
            await cache.set(f"key{i}", f"value{i}", time.time() + 3600)

        await cache.clear()
        assert len(cache) == 0


class TestLRUCacheEviction:
    """Test LRU eviction behavior."""

    @pytest.mark.asyncio
    async def test_lru_eviction(self):
        """Test that LRU entries are evicted when full."""
        cache = LRUCache(max_size=3)

        # Fill cache
        await cache.set("key1", "value1", time.time() + 3600)
        await cache.set("key2", "value2", time.time() + 3600)
        await cache.set("key3", "value3", time.time() + 3600)

        # Access key1 to make it recently used
        await cache.get("key1")

        # Add new entry - should evict key2 (LRU, since key1 was just accessed)
        evicted = await cache.set("key4", "value4", time.time() + 3600)

        assert evicted is not None
        assert evicted[0] == "key2"

    @pytest.mark.asyncio
    async def test_evict_lru_manual(self):
        """Test manual LRU eviction."""
        cache = LRUCache(max_size=3)

        await cache.set("key1", "value1", time.time() + 3600)
        await cache.set("key2", "value2", time.time() + 3600)

        evicted = await cache.evict_lru()
        assert evicted[0] == "key1"
        assert len(cache) == 1

    @pytest.mark.asyncio
    async def test_evict_lru_empty(self):
        """Test manual eviction on empty cache."""
        cache = LRUCache()
        evicted = await cache.evict_lru()
        assert evicted is None

    @pytest.mark.asyncio
    async def test_eviction_count(self):
        """Test eviction count tracking."""
        cache = LRUCache(max_size=2)

        await cache.set("key1", "value1", time.time() + 3600)
        await cache.set("key2", "value2", time.time() + 3600)
        await cache.set("key3", "value3", time.time() + 3600)  # Eviction

        count = await cache.get_eviction_count()
        assert count == 1


class TestLRUCacheExpiry:
    """Test LRUCache TTL/expiration."""

    @pytest.mark.asyncio
    async def test_evict_expired(self):
        """Test eviction of expired entries."""
        cache = LRUCache()

        # Add entry that expires immediately
        await cache.set("key1", "value1", time.time() - 1)
        await cache.set("key2", "value2", time.time() + 3600)

        evicted = await cache.evict_expired()
        assert "key1" in evicted
        assert "key2" not in evicted

    @pytest.mark.asyncio
    async def test_is_expired_check(self):
        """Test expiry check."""
        cache = LRUCache()

        # Add entry with short TTL
        await cache.set("key1", "value1", time.time() + 0.1)
        await asyncio.sleep(0.2)

        result = await cache.get("key1")
        # Entry is still in cache but expired
        assert result is not None
        assert result[1] < time.time()  # Expired


class TestLRUCacheKeys:
    """Test LRUCache key operations."""

    @pytest.mark.asyncio
    async def test_keys(self):
        """Test getting all keys."""
        cache = LRUCache()

        await cache.set("key1", "value1", time.time() + 3600)
        await cache.set("key2", "value2", time.time() + 3600)

        keys = await cache.keys()
        assert "key1" in keys
        assert "key2" in keys

    @pytest.mark.asyncio
    async def test_values(self):
        """Test getting all values."""
        cache = LRUCache()

        await cache.set("key1", "value1", time.time() + 3600)
        await cache.set("key2", "value2", time.time() + 3600)

        values = await cache.values()
        assert "value1" in values
        assert "value2" in values

    @pytest.mark.asyncio
    async def test_items(self):
        """Test getting all items."""
        cache = LRUCache()

        await cache.set("key1", "value1", time.time() + 3600)

        items = await cache.items()
        assert len(items) == 1
        assert items[0][0] == "key1"
        assert items[0][1] == "value1"

    @pytest.mark.asyncio
    async def test_contains(self):
        """Test contains check."""
        cache = LRUCache()

        await cache.set("key1", "value1", time.time() + 3600)

        assert await cache.contains("key1") is True
        assert await cache.contains("key2") is False


class TestLRUCacheResize:
    """Test LRUCache resize operations."""

    @pytest.mark.asyncio
    async def test_resize_larger(self):
        """Test resizing to larger capacity."""
        cache = LRUCache(max_size=2)

        await cache.set("key1", "value1", time.time() + 3600)
        await cache.set("key2", "value2", time.time() + 3600)

        evicted = await cache.resize(10)
        assert len(evicted) == 0
        assert cache.max_size == 10

    @pytest.mark.asyncio
    async def test_resize_smaller(self):
        """Test resizing to smaller capacity."""
        cache = LRUCache(max_size=5)

        for i in range(5):
            await cache.set(f"key{i}", f"value{i}", time.time() + 3600)

        evicted = await cache.resize(2)
        assert len(evicted) == 3
        assert cache.max_size == 2
        assert len(cache) == 2

    @pytest.mark.asyncio
    async def test_resize_invalid(self):
        """Test resizing to invalid capacity."""
        cache = LRUCache()

        with pytest.raises(ValueError):
            await cache.resize(0)

        with pytest.raises(ValueError):
            await cache.resize(-100)


class TestLRUCacheStats:
    """Test LRUCache statistics."""

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """Test getting cache stats."""
        cache = LRUCache(max_size=100)

        await cache.set("key1", "value1", time.time() + 3600)
        await cache.set("key2", "value2", time.time() + 3600)

        stats = await cache.get_stats()
        assert stats["current_size"] == 2
        assert stats["max_size"] == 100
        assert stats["utilization"] == 0.02
        assert stats["eviction_count"] == 0

    def test_len(self):
        """Test __len__ method."""
        cache = LRUCache()

        assert len(cache) == 0

        asyncio.run(cache.set("key1", "value1", time.time() + 3600))
        assert len(cache) == 1


class TestLRUCacheLRUOrder:
    """Test LRU ordering behavior."""

    @pytest.mark.asyncio
    async def test_access_updates_order(self):
        """Test that access updates LRU order."""
        cache = LRUCache(max_size=3)

        await cache.set("key1", "value1", time.time() + 3600)
        await cache.set("key2", "value2", time.time() + 3600)
        await cache.set("key3", "value3", time.time() + 3600)

        # Access key1 to make it most recently used
        await cache.get("key1")

        # Keys should be in LRU order: key2, key3, key1
        keys = await cache.keys()
        assert keys == ["key2", "key3", "key1"]

    @pytest.mark.asyncio
    async def test_update_preserves_order(self):
        """Test that updating a key moves it to MRU."""
        cache = LRUCache(max_size=3)

        await cache.set("key1", "value1", time.time() + 3600)
        await cache.set("key2", "value2", time.time() + 3600)

        # Update key1
        await cache.set("key1", "updated_value1", time.time() + 3600)

        # key1 should now be MRU
        keys = await cache.keys()
        assert keys == ["key2", "key1"]
