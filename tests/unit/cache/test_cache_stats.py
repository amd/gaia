# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for CacheStats.

Tests the cache statistics tracking functionality.
"""

import pytest

from gaia.cache.stats import CacheStats, CacheStatsCollector


class TestCacheStatsInit:
    """Test CacheStats initialization."""

    def test_init_default(self):
        """Test default initialization."""
        stats = CacheStats()
        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.memory_size == 0
        assert stats.disk_size == 0
        assert stats.evictions == 0


class TestCacheStatsRecord:
    """Test CacheStats recording methods."""

    def test_record_hit(self):
        """Test recording a hit."""
        stats = CacheStats()
        stats.record_hit()

        assert stats.hits == 1
        assert stats.total_gets == 1

    def test_record_hit_with_latency(self):
        """Test recording a hit with latency."""
        stats = CacheStats()
        stats.record_hit(latency_ms=2.5)

        assert stats.hits == 1
        assert stats.total_get_latency_ms == 2.5

    def test_record_miss(self):
        """Test recording a miss."""
        stats = CacheStats()
        stats.record_miss()

        assert stats.misses == 1
        assert stats.total_gets == 1

    def test_record_miss_with_latency(self):
        """Test recording a miss with latency."""
        stats = CacheStats()
        stats.record_miss(latency_ms=1.5)

        assert stats.misses == 1
        assert stats.total_get_latency_ms == 1.5

    def test_record_set(self):
        """Test recording a set operation."""
        stats = CacheStats()
        stats.record_set()

        assert stats.total_sets == 1

    def test_record_set_with_latency(self):
        """Test recording a set with latency."""
        stats = CacheStats()
        stats.record_set(latency_ms=3.0)

        assert stats.total_sets == 1
        assert stats.total_set_latency_ms == 3.0

    def test_record_eviction(self):
        """Test recording eviction."""
        stats = CacheStats()
        stats.record_eviction()

        assert stats.evictions == 1

    def test_record_eviction_multiple(self):
        """Test recording multiple evictions."""
        stats = CacheStats()
        stats.record_eviction(count=5)

        assert stats.evictions == 5

    def test_update_memory_size(self):
        """Test updating memory size."""
        stats = CacheStats()
        stats.update_memory_size(100)

        assert stats.memory_size == 100

    def test_update_disk_size(self):
        """Test updating disk size."""
        stats = CacheStats()
        stats.update_disk_size(500)

        assert stats.disk_size == 500


class TestCacheStatsComputed:
    """Test CacheStats computed properties."""

    def test_hit_rate_zero(self):
        """Test hit rate with zero operations."""
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate_all_hits(self):
        """Test hit rate with all hits."""
        stats = CacheStats()
        stats.record_hit()
        stats.record_hit()
        stats.record_hit()

        assert stats.hit_rate == 1.0

    def test_hit_rate_mixed(self):
        """Test hit rate with mixed hits/misses."""
        stats = CacheStats()
        stats.record_hit()
        stats.record_hit()
        stats.record_miss()

        assert stats.hit_rate == pytest.approx(2/3, rel=0.01)

    def test_miss_rate(self):
        """Test miss rate calculation."""
        stats = CacheStats()
        stats.record_hit()
        stats.record_miss()
        stats.record_miss()

        assert stats.miss_rate == pytest.approx(2/3, rel=0.01)

    def test_avg_get_latency_zero(self):
        """Test avg get latency with no operations."""
        stats = CacheStats()
        assert stats.avg_get_latency_ms == 0.0

    def test_avg_get_latency(self):
        """Test avg get latency calculation."""
        stats = CacheStats()
        stats.record_hit(latency_ms=1.0)
        stats.record_hit(latency_ms=3.0)
        stats.record_miss(latency_ms=2.0)

        assert stats.avg_get_latency_ms == pytest.approx(2.0, rel=0.01)

    def test_avg_set_latency_zero(self):
        """Test avg set latency with no operations."""
        stats = CacheStats()
        assert stats.avg_set_latency_ms == 0.0

    def test_avg_set_latency(self):
        """Test avg set latency calculation."""
        stats = CacheStats()
        stats.record_set(latency_ms=2.0)
        stats.record_set(latency_ms=4.0)

        assert stats.avg_set_latency_ms == pytest.approx(3.0, rel=0.01)

    def test_total_operations(self):
        """Test total operations calculation."""
        stats = CacheStats()
        stats.record_hit()
        stats.record_miss()
        stats.record_set()
        stats.record_set()

        assert stats.total_operations == 4

    def test_cache_size(self):
        """Test cache size calculation."""
        stats = CacheStats()
        stats.update_memory_size(100)
        stats.update_disk_size(500)

        assert stats.cache_size == 600


class TestCacheStatsReset:
    """Test CacheStats reset functionality."""

    def test_reset(self):
        """Test resetting stats."""
        stats = CacheStats()
        stats.record_hit()
        stats.record_miss()
        stats.record_set()
        stats.record_eviction()

        stats.reset()

        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.total_gets == 0
        assert stats.total_sets == 0
        assert stats.total_get_latency_ms == 0.0
        assert stats.total_set_latency_ms == 0.0
        assert stats.evictions == 0
        # Note: memory_size and disk_size are not reset

    def test_reset_preserves_sizes(self):
        """Test that reset preserves sizes."""
        stats = CacheStats()
        stats.update_memory_size(100)
        stats.update_disk_size(500)

        stats.reset()

        assert stats.memory_size == 100
        assert stats.disk_size == 500


class TestCacheStatsToDict:
    """Test CacheStats to_dict method."""

    def test_to_dict(self):
        """Test converting to dictionary."""
        stats = CacheStats()
        stats.record_hit()
        stats.record_miss()
        stats.record_set()
        stats.update_memory_size(50)
        stats.update_disk_size(100)

        result = stats.to_dict()

        assert isinstance(result, dict)
        assert "hits" in result
        assert "misses" in result
        assert "hit_rate" in result
        assert "memory_size" in result
        assert "disk_size" in result
        assert "cache_size" in result
        assert "evictions" in result
        assert "total_operations" in result
        assert "avg_get_latency_ms" in result
        assert "avg_set_latency_ms" in result

    def test_to_dict_values(self):
        """Test dictionary values are correct."""
        stats = CacheStats()
        stats.record_hit()
        stats.record_hit()

        result = stats.to_dict()

        assert result["hits"] == 2
        assert result["misses"] == 0
        assert result["hit_rate"] == 1.0


class TestCacheStatsCollector:
    """Test CacheStatsCollector for aggregating stats."""

    def test_add(self):
        """Test adding stats to collector."""
        collector = CacheStatsCollector()
        stats1 = CacheStats()
        stats1.record_hit()

        collector.add(stats1, "cache1")

        assert "cache1" in collector._stats

    def test_remove(self):
        """Test removing stats from collector."""
        collector = CacheStatsCollector()
        stats1 = CacheStats()

        collector.add(stats1, "cache1")
        collector.remove("cache1")

        assert "cache1" not in collector._stats

    def test_aggregate_empty(self):
        """Test aggregating with no stats."""
        collector = CacheStatsCollector()
        result = collector.aggregate()

        assert result["cache_count"] == 0

    def test_aggregate(self):
        """Test aggregating multiple stats."""
        collector = CacheStatsCollector()

        stats1 = CacheStats()
        stats1.record_hit()
        stats1.record_hit()

        stats2 = CacheStats()
        stats2.record_hit()
        stats2.record_miss()

        collector.add(stats1, "cache1")
        collector.add(stats2, "cache2")

        result = collector.aggregate()

        assert result["cache_count"] == 2
        assert result["total_hits"] == 3
        assert result["total_misses"] == 1
        assert result["hit_rate"] == pytest.approx(0.75, rel=0.01)

    def test_get_all(self):
        """Test getting all stats individually."""
        collector = CacheStatsCollector()

        stats1 = CacheStats()
        stats1.record_hit()

        stats2 = CacheStats()
        stats2.record_miss()

        collector.add(stats1, "cache1")
        collector.add(stats2, "cache2")

        all_stats = collector.get_all()

        assert "cache1" in all_stats
        assert "cache2" in all_stats
        assert all_stats["cache1"]["hits"] == 1
        assert all_stats["cache2"]["misses"] == 1

    def test_clear(self):
        """Test clearing collector."""
        collector = CacheStatsCollector()
        stats1 = CacheStats()

        collector.add(stats1, "cache1")
        collector.clear()

        assert len(collector._stats) == 0
