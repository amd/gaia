# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for TTLManager.

Tests the TTL expiration management and background cleanup.
"""

import asyncio
import pytest
import time

from gaia.cache.ttl_manager import TTLManager, TTLRegistry


class TestTTLManagerInit:
    """Test TTLManager initialization."""

    def test_init_default(self):
        """Test default initialization."""
        ttl_mgr = TTLManager()
        assert ttl_mgr.default_ttl == 3600

    def test_init_custom_ttl(self):
        """Test initialization with custom TTL."""
        ttl_mgr = TTLManager(default_ttl=1800)
        assert ttl_mgr.default_ttl == 1800

    def test_init_invalid_ttl(self):
        """Test initialization with invalid TTL."""
        with pytest.raises(ValueError):
            TTLManager(default_ttl=0)

        with pytest.raises(ValueError):
            TTLManager(default_ttl=-100)


class TestTTLManagerComputeExpiry:
    """Test TTLManager expiry computation."""

    def test_compute_expiry_default(self):
        """Test expiry computation with default TTL."""
        ttl_mgr = TTLManager(default_ttl=3600)
        before = time.time()
        expiry = ttl_mgr.compute_expiry()
        after = time.time()

        assert before + 3600 <= expiry <= after + 3600

    def test_compute_expiry_custom(self):
        """Test expiry computation with custom TTL."""
        ttl_mgr = TTLManager(default_ttl=3600)
        expiry = ttl_mgr.compute_expiry(ttl=600)

        assert time.time() + 599 <= expiry <= time.time() + 601

    def test_compute_expiry_invalid(self):
        """Test expiry computation with invalid TTL."""
        ttl_mgr = TTLManager()
        with pytest.raises(ValueError):
            ttl_mgr.compute_expiry(ttl=0)

        with pytest.raises(ValueError):
            ttl_mgr.compute_expiry(ttl=-100)


class TestTTLManagerIsExpired:
    """Test TTLManager expiry checking."""

    def test_is_expired_false(self):
        """Test is_expired for non-expired timestamp."""
        ttl_mgr = TTLManager()
        future_expiry = time.time() + 3600
        assert ttl_mgr.is_expired(future_expiry) is False

    def test_is_expired_true(self):
        """Test is_expired for expired timestamp."""
        ttl_mgr = TTLManager()
        past_expiry = time.time() - 1
        assert ttl_mgr.is_expired(past_expiry) is True

    def test_time_to_expire(self):
        """Test time_to_expire calculation."""
        ttl_mgr = TTLManager()
        future_expiry = time.time() + 60

        remaining = ttl_mgr.time_to_expire(future_expiry)
        assert 59 <= remaining <= 60

    def test_time_to_expire_past(self):
        """Test time_to_expire for expired timestamp."""
        ttl_mgr = TTLManager()
        past_expiry = time.time() - 10

        remaining = ttl_mgr.time_to_expire(past_expiry)
        assert remaining < 0


class TestTTLManagerCallbacks:
    """Test TTLManager callback registration."""

    def test_on_expired(self):
        """Test registering expired callback."""
        ttl_mgr = TTLManager()
        called = []

        def callback(key: str):
            called.append(key)

        ttl_mgr.on_expired(callback)
        assert len(ttl_mgr._expired_callbacks) == 1

    def test_on_cleanup(self):
        """Test registering cleanup callback."""
        ttl_mgr = TTLManager()
        called = []

        def callback(keys: list):
            called.append(keys)

        ttl_mgr.on_cleanup(callback)
        assert len(ttl_mgr._cleanup_callbacks) == 1

    def test_remove_callback(self):
        """Test removing callback."""
        ttl_mgr = TTLManager()

        def callback(key: str):
            pass

        ttl_mgr.on_expired(callback)
        ttl_mgr.remove_callback(callback)
        assert len(ttl_mgr._expired_callbacks) == 0

    @pytest.mark.asyncio
    async def test_callback_invocation(self):
        """Test that callbacks are invoked on cleanup."""
        ttl_mgr = TTLManager()
        expired_keys = []

        def on_expired(key: str):
            expired_keys.append(key)

        ttl_mgr.on_expired(on_expired)

        # Manually trigger notification
        await ttl_mgr._notify_expired("test_key")

        assert "test_key" in expired_keys


class TestTTLManagerStartStop:
    """Test TTLManager start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start(self):
        """Test starting TTL manager."""
        ttl_mgr = TTLManager()
        await ttl_mgr.start(cleanup_interval=1)

        assert ttl_mgr.is_running is True

        await ttl_mgr.stop()

    @pytest.mark.asyncio
    async def test_stop(self):
        """Test stopping TTL manager."""
        ttl_mgr = TTLManager()
        await ttl_mgr.start(cleanup_interval=1)
        await ttl_mgr.stop()

        assert ttl_mgr.is_running is False

    @pytest.mark.asyncio
    async def test_start_already_running(self):
        """Test starting when already running."""
        ttl_mgr = TTLManager()
        await ttl_mgr.start(cleanup_interval=1)

        with pytest.raises(RuntimeError):
            await ttl_mgr.start(cleanup_interval=1)

        await ttl_mgr.stop()

    @pytest.mark.asyncio
    async def test_start_invalid_interval(self):
        """Test starting with invalid interval."""
        ttl_mgr = TTLManager()
        with pytest.raises(ValueError):
            await ttl_mgr.start(cleanup_interval=0)

        with pytest.raises(ValueError):
            await ttl_mgr.start(cleanup_interval=-1)

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test using TTLManager as context manager."""
        async with TTLManager() as ttl_mgr:
            assert ttl_mgr.is_running is True

        assert ttl_mgr.is_running is False


class TestTTLManagerStats:
    """Test TTLManager statistics."""

    def test_get_stats(self):
        """Test getting stats."""
        ttl_mgr = TTLManager(default_ttl=1800)

        stats = ttl_mgr.get_stats()

        assert stats["default_ttl"] == 1800
        assert stats["is_running"] is False
        assert stats["cleanup_count"] == 0

    def test_reset_stats(self):
        """Test resetting stats."""
        ttl_mgr = TTLManager()
        ttl_mgr._cleanup_count = 10
        ttl_mgr._total_expired = 50

        ttl_mgr.reset_stats()

        assert ttl_mgr._cleanup_count == 0
        assert ttl_mgr._total_expired == 0


class TestTTLRegistry:
    """Test TTLRegistry for pattern-based TTL."""

    def test_init_default(self):
        """Test default initialization."""
        registry = TTLRegistry()
        assert registry._default_ttl == 600

    def test_init_custom_default(self):
        """Test initialization with custom default."""
        registry = TTLRegistry(default_ttl=1200)
        assert registry._default_ttl == 1200

    def test_register_pattern(self):
        """Test registering pattern."""
        registry = TTLRegistry()
        registry.register_pattern("user:*", 3600)

        patterns = registry.get_patterns()
        assert "user:*" in patterns
        assert patterns["user:*"] == 3600

    def test_register_default(self):
        """Test setting default TTL."""
        registry = TTLRegistry()
        registry.register_default(1800)

        assert registry._default_ttl == 1800

    def test_get_ttl_match(self):
        """Test getting TTL for matching pattern."""
        registry = TTLRegistry(default_ttl=600)
        registry.register_pattern("user:*", 3600)
        registry.register_pattern("session:*", 300)

        assert registry.get_ttl("user:123") == 3600
        assert registry.get_ttl("session:abc") == 300

    def test_get_ttl_no_match(self):
        """Test getting TTL for non-matching key."""
        registry = TTLRegistry(default_ttl=600)
        registry.register_pattern("user:*", 3600)

        assert registry.get_ttl("other:key") == 600

    def test_remove_pattern(self):
        """Test removing pattern."""
        registry = TTLRegistry()
        registry.register_pattern("user:*", 3600)

        removed = registry.remove_pattern("user:*")
        assert removed is True

        patterns = registry.get_patterns()
        assert "user:*" not in patterns

    def test_remove_nonexistent_pattern(self):
        """Test removing non-existent pattern."""
        registry = TTLRegistry()
        removed = registry.remove_pattern("nonexistent:*")
        assert removed is False


class TestTTLRegistryPatternMatching:
    """Test TTLRegistry pattern matching."""

    def test_prefix_pattern(self):
        """Test prefix pattern matching."""
        registry = TTLRegistry()
        registry.register_pattern("api:*", 1800)

        assert registry.get_ttl("api:users") == 1800
        assert registry.get_ttl("api:posts:123") == 1800
        assert registry.get_ttl("other:key") != 1800

    def test_exact_pattern(self):
        """Test exact pattern matching."""
        registry = TTLRegistry()
        registry.register_pattern("config", 7200)

        assert registry.get_ttl("config") == 7200
        assert registry.get_ttl("config:other") != 7200

    def test_multiple_patterns(self):
        """Test multiple patterns with different priorities."""
        registry = TTLRegistry()
        registry.register_pattern("cache:*", 300)
        registry.register_pattern("cache:user:*", 600)
        registry.register_pattern("cache:user:session:*", 60)

        # First matching pattern wins
        assert registry.get_ttl("cache:data") == 300
        assert registry.get_ttl("cache:user:profile") == 300
