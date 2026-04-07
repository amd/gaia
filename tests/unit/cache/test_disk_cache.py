# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for DiskCache.

Tests the SQLite-based disk cache implementation.
"""

import asyncio
import pickle
import pytest
import tempfile
import time
from pathlib import Path

from gaia.cache.disk_cache import DiskCache
from gaia.cache.exceptions import CacheConnectionError


@pytest.fixture
def temp_db_path():
    """Provide temporary database path for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield str(Path(tmpdir) / "test_cache.db")


class TestDiskCacheInit:
    """Test DiskCache initialization."""

    def test_init(self, temp_db_path):
        """Test basic initialization."""
        cache = DiskCache(temp_db_path)
        assert cache.db_path == str(Path(temp_db_path).resolve())
        assert cache.max_entries == 10000

    def test_init_custom_max_entries(self, temp_db_path):
        """Test initialization with custom max_entries."""
        cache = DiskCache(temp_db_path, max_entries=500)
        assert cache.max_entries == 500

    def test_init_creates_directory(self, temp_db_path):
        """Test that parent directory is created."""
        nested_path = str(Path(temp_db_path) / "subdir" / "cache.db")
        cache = DiskCache(nested_path)
        assert Path(nested_path).exists()

    def test_init_invalid_max_entries(self, temp_db_path):
        """Test initialization with invalid max_entries."""
        with pytest.raises(ValueError):
            DiskCache(temp_db_path, max_entries=0)


class TestDiskCacheBasic:
    """Test basic DiskCache operations."""

    @pytest.mark.asyncio
    async def test_set_and_get(self, temp_db_path):
        """Test basic set and get."""
        cache = DiskCache(temp_db_path)
        try:
            data = pickle.dumps("test_value")
            await cache.set("key1", data, time.time() + 3600)

            result = await cache.get("key1")
            assert result is not None
            assert pickle.loads(result) == "test_value"
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_get_missing(self, temp_db_path):
        """Test getting non-existent key."""
        cache = DiskCache(temp_db_path)
        try:
            result = await cache.get("nonexistent")
            assert result is None
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_get_expired(self, temp_db_path):
        """Test getting expired entry."""
        cache = DiskCache(temp_db_path)
        try:
            data = pickle.dumps("test_value")
            await cache.set("key1", data, time.time() - 1)  # Already expired

            result = await cache.get("key1")
            assert result is None
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_delete(self, temp_db_path):
        """Test delete operation."""
        cache = DiskCache(temp_db_path)
        try:
            data = pickle.dumps("test_value")
            await cache.set("key1", data, time.time() + 3600)

            deleted = await cache.delete("key1")
            assert deleted is True

            result = await cache.get("key1")
            assert result is None
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_delete_missing(self, temp_db_path):
        """Test deleting non-existent key."""
        cache = DiskCache(temp_db_path)
        try:
            deleted = await cache.delete("nonexistent")
            assert deleted is False
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_clear(self, temp_db_path):
        """Test clear operation."""
        cache = DiskCache(temp_db_path)
        try:
            for i in range(5):
                data = pickle.dumps(f"value{i}")
                await cache.set(f"key{i}", data, time.time() + 3600)

            count = await cache.clear()
            assert count == 5

            result = await cache.get("key0")
            assert result is None
        finally:
            await cache.close()


class TestDiskCacheCount:
    """Test DiskCache count operations."""

    @pytest.mark.asyncio
    async def test_count(self, temp_db_path):
        """Test count operation."""
        cache = DiskCache(temp_db_path)
        try:
            assert await cache.count() == 0

            for i in range(5):
                data = pickle.dumps(f"value{i}")
                await cache.set(f"key{i}", data, time.time() + 3600)

            assert await cache.count() == 5
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_contains(self, temp_db_path):
        """Test contains operation."""
        cache = DiskCache(temp_db_path)
        try:
            data = pickle.dumps("value")
            await cache.set("key1", data, time.time() + 3600)

            assert await cache.contains("key1") is True
            assert await cache.contains("key2") is False
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_contains_expired(self, temp_db_path):
        """Test contains with expired entry."""
        cache = DiskCache(temp_db_path)
        try:
            data = pickle.dumps("value")
            await cache.set("key1", data, time.time() - 1)

            assert await cache.contains("key1") is False
        finally:
            await cache.close()


class TestDiskCacheCleanup:
    """Test DiskCache cleanup operations."""

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, temp_db_path):
        """Test cleanup of expired entries."""
        cache = DiskCache(temp_db_path)
        try:
            # Add expired entries
            for i in range(3):
                data = pickle.dumps(f"value{i}")
                await cache.set(f"expired{i}", data, time.time() - 1)

            # Add valid entries
            for i in range(2):
                data = pickle.dumps(f"value{i}")
                await cache.set(f"valid{i}", data, time.time() + 3600)

            removed = await cache.cleanup_expired()
            assert removed == 3

            assert await cache.count() == 2
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_cleanup_lru(self, temp_db_path):
        """Test LRU cleanup."""
        cache = DiskCache(temp_db_path, max_entries=10)
        try:
            for i in range(10):
                data = pickle.dumps(f"value{i}")
                await cache.set(f"key{i}", data, time.time() + 3600)

            # Cleanup to keep only 5
            removed = await cache.cleanup_lru(keep_count=5)
            assert removed == 5
            assert await cache.count() == 5
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_cleanup_lru_no_action(self, temp_db_path):
        """Test LRU cleanup when under limit."""
        cache = DiskCache(temp_db_path, max_entries=10)
        try:
            for i in range(3):
                data = pickle.dumps(f"value{i}")
                await cache.set(f"key{i}", data, time.time() + 3600)

            removed = await cache.cleanup_lru(keep_count=10)
            assert removed == 0
        finally:
            await cache.close()


class TestDiskCacheStats:
    """Test DiskCache statistics."""

    @pytest.mark.asyncio
    async def test_get_stats(self, temp_db_path):
        """Test getting cache stats."""
        cache = DiskCache(temp_db_path, max_entries=100)
        try:
            for i in range(5):
                data = pickle.dumps(f"value{i}")
                await cache.set(f"key{i}", data, time.time() + 3600)

            stats = await cache.get_stats()

            assert stats["total_count"] == 5
            assert stats["valid_count"] == 5
            assert stats["expired_count"] == 0
            assert stats["max_entries"] == 100
            assert "db_path" in stats
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_size_bytes(self, temp_db_path):
        """Test size_bytes calculation."""
        cache = DiskCache(temp_db_path)
        try:
            size_before = await cache.size_bytes()
            assert size_before == 0

            data = pickle.dumps("test_value")
            await cache.set("key1", data, time.time() + 3600)

            size_after = await cache.size_bytes()
            assert size_after > size_before
        finally:
            await cache.close()

    @pytest.mark.asyncio
    async def test_keys(self, temp_db_path):
        """Test getting all keys."""
        cache = DiskCache(temp_db_path)
        try:
            for i in range(5):
                data = pickle.dumps(f"value{i}")
                await cache.set(f"key{i}", data, time.time() + 3600)

            keys = await cache.keys()
            assert len(keys) == 5
            assert "key0" in keys
        finally:
            await cache.close()


class TestDiskCacheContextManager:
    """Test DiskCache as context manager."""

    @pytest.mark.asyncio
    async def test_async_context_manager(self, temp_db_path):
        """Test using DiskCache as async context manager."""
        async with DiskCache(temp_db_path) as cache:
            data = pickle.dumps("test_value")
            await cache.set("key1", data, time.time() + 3600)

            result = await cache.get("key1")
            assert result is not None

        # After context exit, cache should be closed


class TestDiskCacheVacuum:
    """Test DiskCache vacuum operation."""

    @pytest.mark.asyncio
    async def test_vacuum(self, temp_db_path):
        """Test vacuum operation."""
        cache = DiskCache(temp_db_path)
        try:
            for i in range(10):
                data = pickle.dumps(f"value{i}")
                await cache.set(f"key{i}", data, time.time() + 3600)

            await cache.delete("key0")
            await cache.delete("key1")

            # Vacuum to reclaim space
            await cache.vacuum()

            # Should still work after vacuum
            result = await cache.get("key2")
            assert result is not None
        finally:
            await cache.close()


class TestDiskCacheConcurrency:
    """Test DiskCache concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_access(self, temp_db_path):
        """Test concurrent read/write operations."""
        cache = DiskCache(temp_db_path)
        errors = []

        async def writer(worker_id: int):
            try:
                for i in range(10):
                    data = pickle.dumps(f"value:{worker_id}:{i}")
                    await cache.set(f"key:{worker_id}:{i}", data, time.time() + 3600)
            except Exception as e:
                errors.append(("write", worker_id, str(e)))

        async def reader(worker_id: int):
            try:
                for i in range(10):
                    await cache.get(f"key:{worker_id}:{i}")
            except Exception as e:
                errors.append(("read", worker_id, str(e)))

        # Run concurrent workers
        tasks = []
        for i in range(5):
            tasks.append(writer(i))
            tasks.append(reader(i))

        await asyncio.gather(*tasks)

        await cache.close()

        assert len(errors) == 0, f"Concurrency errors: {errors}"
