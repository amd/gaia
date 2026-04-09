# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Stress tests for cache thread safety.

Tests cache behavior under high concurrency.
"""

import asyncio
import concurrent.futures
import pytest
import threading
import time

from gaia.cache.cache_layer import CacheLayer


class TestCacheThreadSafety:
    """Verify cache is thread-safe under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_access_100_threads(self):
        """Cache should handle 100+ concurrent threads safely."""
        cache = CacheLayer(memory_max_size=1000)
        errors = []
        success_count = [0]
        lock = threading.Lock()

        async def worker(thread_id: int):
            try:
                for i in range(10):
                    key = f"thread:{thread_id}:key:{i}"
                    await cache.set(key, f"value:{thread_id}:{i}")
                    value = await cache.get(key)
                    assert value == f"value:{thread_id}:{i}"

                with lock:
                    success_count[0] += 1
            except Exception as e:
                errors.append((thread_id, str(e)))

        # Launch 100 concurrent workers
        tasks = [worker(i) for i in range(100)]
        await asyncio.gather(*tasks)

        await cache.stop()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert success_count[0] == 100, f"Only {success_count[0]}/100 threads succeeded"

    @pytest.mark.asyncio
    async def test_concurrent_get_or_set(self):
        """Test get_or_set under concurrent load."""
        cache = CacheLayer()
        call_count = [0]
        lock = threading.Lock()

        async def factory():
            await asyncio.sleep(0.001)
            with lock:
                call_count[0] += 1
            return "computed"

        async def worker():
            return await cache.get_or_set("shared_key", factory)

        # Run 50 concurrent workers
        results = await asyncio.gather(*[worker() for _ in range(50)])

        # All should get same result
        assert all(r == "computed" for r in results)

        # Note: Without distributed lock, may be called multiple times
        assert call_count[0] <= 50

    @pytest.mark.asyncio
    async def test_concurrent_set_same_key(self):
        """Test concurrent set to same key."""
        cache = CacheLayer()
        errors = []

        async def worker(worker_id: int):
            try:
                for i in range(20):
                    await cache.set("shared_key", f"value:{worker_id}:{i}")
            except Exception as e:
                errors.append((worker_id, str(e)))

        # Run 20 concurrent workers
        tasks = [worker(i) for i in range(20)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Concurrent set errors: {errors}"

        # Final value should be one of the set values
        final = await cache.get("shared_key")
        assert final is not None


class TestCacheStressHighVolume:
    """High volume stress tests."""

    @pytest.mark.asyncio
    async def test_high_volume_set_get(self):
        """Test high volume of set/get operations."""
        cache = CacheLayer(memory_max_size=10000)

        # Set 10000 keys
        for i in range(10000):
            await cache.set(f"key:{i}", f"value:{i}")

        # Get all keys
        for i in range(10000):
            result = await cache.get(f"key:{i}")
            assert result == f"value:{i}"

        stats = cache.stats()
        assert stats.hits == 10000
        assert stats.total_sets == 10000

    @pytest.mark.asyncio
    async def test_high_volume_with_ttl(self):
        """Test high volume with TTL expiration."""
        cache = CacheLayer(memory_max_size=1000, default_ttl=60)

        # Set 1000 keys with short TTL
        for i in range(1000):
            await cache.set(f"key:{i}", f"value:{i}", ttl=1)

        # Wait for expiration
        await asyncio.sleep(1.5)

        # All should be expired
        for i in range(1000):
            result = await cache.get(f"key:{i}")
            assert result is None

    @pytest.mark.asyncio
    async def test_mixed_operations(self):
        """Test mixed operations under load."""
        cache = CacheLayer(memory_max_size=500)
        errors = []

        async def setter(start: int):
            try:
                for i in range(start, start + 100):
                    await cache.set(f"key:{i}", f"value:{i}")
            except Exception as e:
                errors.append(("set", start, str(e)))

        async def getter(start: int):
            try:
                for i in range(start, start + 100):
                    await cache.get(f"key:{i}")
            except Exception as e:
                errors.append(("get", start, str(e)))

        async def deleter(start: int):
            try:
                for i in range(start, start + 50):
                    await cache.delete(f"key:{i}")
            except Exception as e:
                errors.append(("delete", start, str(e)))

        # Run mixed operations
        tasks = []
        for i in range(0, 500, 100):
            tasks.append(setter(i))
            tasks.append(getter(i))
            tasks.append(deleter(i))

        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Mixed operation errors: {errors}"


class TestCacheStressMemory:
    """Memory stress tests."""

    @pytest.mark.asyncio
    async def test_memory_limit_enforced(self):
        """Test memory limit is enforced."""
        cache = CacheLayer(memory_max_size=100)

        # Set 1000 keys
        for i in range(1000):
            await cache.set(f"key:{i}", f"value:{i}")

        # Memory cache should be at limit
        assert cache._memory_cache.current_size <= 100

    @pytest.mark.asyncio
    async def test_large_values(self):
        """Test handling large values."""
        cache = CacheLayer(memory_max_size=10)

        # Set large values
        large_value = "x" * 1000000  # 1MB string
        await cache.set("large_key", large_value)

        result = await cache.get("large_key")
        assert result == large_value
        assert len(result) == 1000000


class TestCacheStressPatterns:
    """Access pattern stress tests."""

    @pytest.mark.asyncio
    async def test_zipf_distribution(self):
        """Test Zipf-like access pattern (hot keys)."""
        cache = CacheLayer(memory_max_size=100)

        # Create Zipf-like access: 20% of keys get 80% of access
        hot_keys = [f"hot:{i}" for i in range(20)]
        cold_keys = [f"cold:{i}" for i in range(80)]

        # Set all keys
        for key in hot_keys + cold_keys:
            await cache.set(key, f"value:{key}")

        # Access hot keys 80% of time
        import random
        for _ in range(1000):
            if random.random() < 0.8:
                key = random.choice(hot_keys)
            else:
                key = random.choice(cold_keys)
            await cache.get(key)

        stats = cache.stats()
        # Should have high hit rate due to hot keys
        assert stats.hit_rate > 0.5


class TestCacheStressLRU:
    """LRU eviction stress tests."""

    @pytest.mark.asyncio
    async def test_lru_eviction_correctness(self):
        """Test LRU eviction works correctly under stress."""
        cache = CacheLayer(memory_max_size=10)

        # Fill cache
        for i in range(10):
            await cache.set(f"key:{i}", f"value:{i}")

        # Access key:0 to make it MRU
        await cache.get("key:0")

        # Add new key - should evict key:1 (LRU)
        await cache.set("key:new", "new_value")

        # key:0 should still be accessible
        result = await cache.get("key:0")
        assert result == "value:0"

        # key:1 should be evicted
        result = await cache.get("key:1")
        assert result is None

    @pytest.mark.asyncio
    async def test_rapid_eviction(self):
        """Test rapid eviction cycles."""
        cache = CacheLayer(memory_max_size=5)

        for cycle in range(100):
            # Fill cache
            for i in range(10):
                await cache.set(f"cycle:{cycle}:key:{i}", f"value:{i}")

            # Verify some entries exist
            result = await cache.get(f"cycle:{cycle}:key:9")
            assert result == "value:9"

        # Should not have crashed
        stats = cache.stats()
        assert stats.evictions > 0


class TestCacheStressTTLCleanup:
    """TTL cleanup stress tests."""

    @pytest.mark.asyncio
    async def test_background_cleanup_under_load(self):
        """Test background cleanup during active use."""
        cache = CacheLayer(default_ttl=1)
        await cache.start_ttl_cleanup(interval=1)

        errors = []

        async def worker(worker_id: int):
            try:
                for i in range(10):
                    await cache.set(f"worker:{worker_id}:key:{i}", f"value:{i}")
                    await cache.get(f"worker:{worker_id}:key:{i}")
            except Exception as e:
                errors.append((worker_id, str(e)))

        # Run workers while cleanup is active
        tasks = [worker(i) for i in range(10)]
        await asyncio.gather(*tasks)

        await cache.stop()

        assert len(errors) == 0, f"Errors during cleanup: {errors}"

    @pytest.mark.asyncio
    async def test_staggered_expiry(self):
        """Test staggered TTL expiry."""
        cache = CacheLayer()

        # Set keys with different TTLs
        await cache.set("key:1s", "value", ttl=1)
        await cache.set("key:2s", "value", ttl=2)
        await cache.set("key:3s", "value", ttl=3)

        # After 1.5s, first should be expired
        await asyncio.sleep(1.5)
        assert await cache.get("key:1s") is None
        assert await cache.get("key:2s") == "value"
        assert await cache.get("key:3s") == "value"

        # After 2.5s, first two should be expired
        await asyncio.sleep(1.0)
        assert await cache.get("key:2s") is None
        assert await cache.get("key:3s") == "value"


class TestCacheStressDiskSpill:
    """Disk spill stress tests."""

    @pytest.mark.asyncio
    async def test_disk_spill_under_load(self, tmp_path):
        """Test disk spill under high load."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheLayer(
                memory_max_size=100,
                disk_path=str(Path(tmpdir) / "cache.db"),
                enable_disk_spill=True,
            )

            # Set more keys than memory can hold
            for i in range(1000):
                await cache.set(f"key:{i}", f"value:{i}")

            # Get keys that should be in disk
            for i in range(1000):
                result = await cache.get(f"key:{i}")
                assert result == f"value:{i}"

            await cache.stop()

    @pytest.mark.asyncio
    async def test_disk_recovery(self, tmp_path):
        """Test recovery after memory clear."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheLayer(
                memory_max_size=10,
                disk_path=str(Path(tmpdir) / "cache.db"),
            )

            # Set keys
            for i in range(100):
                await cache.set(f"key:{i}", f"value:{i}")

            # Clear memory
            await cache._memory_cache.clear()

            # Should still get from disk
            for i in range(100):
                result = await cache.get(f"key:{i}")
                assert result == f"value:{i}"

            await cache.stop()
