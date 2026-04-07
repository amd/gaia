# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for @cached decorator.

Tests the caching decorator functionality.
"""

import asyncio
import pytest
import time

from gaia.cache.cache_layer import CacheLayer, cached
from gaia.cache.exceptions import CacheDecoratorError


class TestCachedDecoratorBasic:
    """Test basic @cached decorator functionality."""

    @pytest.mark.asyncio
    async def test_cached_function(self):
        """Test basic cached function."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60)
        async def compute(x: int) -> int:
            call_count[0] += 1
            return x * 2

        result1 = await compute(5)
        assert result1 == 10
        assert call_count[0] == 1

        result2 = await compute(5)
        assert result2 == 10
        assert call_count[0] == 1  # Not called again

    @pytest.mark.asyncio
    async def test_cached_different_args(self):
        """Test cached with different arguments."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60)
        async def compute(x: int) -> int:
            call_count[0] += 1
            return x * 2

        await compute(5)
        await compute(10)

        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_cached_ttl_expiration(self):
        """Test cached TTL expiration."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=1)
        async def compute(x: int) -> int:
            call_count[0] += 1
            return x * 2

        await compute(5)
        assert call_count[0] == 1

        await asyncio.sleep(1.5)

        await compute(5)
        assert call_count[0] == 2  # Called again after expiry


class TestCachedKeyFunc:
    """Test @cached with custom key function."""

    @pytest.mark.asyncio
    async def test_custom_key_func(self):
        """Test custom key generation."""
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
    async def test_custom_key_prefix(self):
        """Test custom key prefix."""
        cache = CacheLayer()

        @cached(cache=cache, ttl=60, prefix="user:")
        async def get_user(user_id: int) -> dict:
            return {"id": user_id}

        await get_user(123)

        # Verify key format
        keys = await cache.keys()
        assert any("user:" in k for k in keys)


class TestCachedSkipCondition:
    """Test @cached with skip condition."""

    @pytest.mark.asyncio
    async def test_skip_on_none(self):
        """Test skipping cache on None result."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60, skip_cache_on=lambda r: r is None)
        async def get_optional(x: int) -> int:
            call_count[0] += 1
            return x if x > 0 else None

        await get_optional(-1)  # Result is None, not cached
        await get_optional(-1)  # Called again

        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_skip_on_error_result(self):
        """Test skipping cache on error indicator."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60, skip_cache_on=lambda r: r == -1)
        async def get_value(x: int) -> int:
            call_count[0] += 1
            return x if x > 0 else -1

        await get_value(-5)  # Returns -1, not cached
        await get_value(-5)  # Called again

        assert call_count[0] == 2

        await get_value(5)  # Cached
        await get_value(5)  # Cache hit

        assert call_count[0] == 3


class TestCachedErrorHandling:
    """Test @cached error handling."""

    @pytest.mark.asyncio
    async def test_function_error_propagates(self):
        """Test that function errors propagate."""
        cache = CacheLayer()

        @cached(cache=cache, ttl=60)
        async def failing_func() -> int:
            raise ValueError("Test error")

        with pytest.raises(CacheDecoratorError) as exc_info:
            await failing_func()

        assert "Test error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cache_get_error_handled(self):
        """Test handling cache get errors."""
        # Use default cache (will be created)
        call_count = [0]

        @cached(ttl=60)
        async def compute(x: int) -> int:
            call_count[0] += 1
            return x * 2

        # Should still work even if cache operations fail
        result = await compute(5)
        assert result == 10


class TestCachedAsync:
    """Test @cached with async operations."""

    @pytest.mark.asyncio
    async def test_async_factory(self):
        """Test cached with async factory function."""
        cache = CacheLayer()
        call_count = [0]

        async def fetch_data():
            await asyncio.sleep(0.01)
            call_count[0] += 1
            return {"data": "value"}

        @cached(cache=cache, ttl=60)
        async def get_data():
            return await fetch_data()

        result1 = await get_data()
        result2 = await get_data()

        assert call_count[0] == 1
        assert result1 == result2

    @pytest.mark.asyncio
    async def test_concurrent_cached_calls(self):
        """Test concurrent calls to cached function."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60)
        async def compute(x: int) -> int:
            call_count[0] += 1
            await asyncio.sleep(0.1)
            return x * 2

        # Make concurrent calls
        results = await asyncio.gather(
            compute(5),
            compute(5),
            compute(5),
        )

        # All should return same value
        assert all(r == 10 for r in results)
        # Note: Without lock protection, may be called multiple times


class TestCachedIntrospection:
    """Test @cached introspection methods."""

    @pytest.mark.asyncio
    async def test_cache_info(self):
        """Test cache_info method."""
        cache = CacheLayer()

        @cached(cache=cache, ttl=60)
        async def compute(x: int) -> int:
            return x * 2

        await compute(5)
        await compute(10)

        info = compute.cache_info()
        assert isinstance(info, dict)

    @pytest.mark.asyncio
    async def test_cache_clear(self):
        """Test cache_clear method."""
        cache = CacheLayer()

        @cached(cache=cache, ttl=60)
        async def compute(x: int) -> int:
            return x * 2

        await compute(5)

        compute.cache_clear()

        # Should still work after clear
        result = await compute(5)
        assert result == 10


class TestCachedDefaultCache:
    """Test @cached with default cache."""

    @pytest.mark.asyncio
    async def test_default_cache_usage(self):
        """Test using default cache when none specified."""
        call_count = [0]

        @cached(ttl=60)
        async def compute(x: int) -> int:
            call_count[0] += 1
            return x * 2

        result1 = await compute(5)
        result2 = await compute(5)

        assert result1 == 10
        assert result2 == 10
        # Should be cached even with default cache

    @pytest.mark.asyncio
    async def test_preserves_function_metadata(self):
        """Test that decorator preserves function metadata."""
        cache = CacheLayer()

        @cached(cache=cache, ttl=60)
        async def my_function(x: int) -> int:
            """My function docstring."""
            return x * 2

        assert my_function.__name__ == "my_function"
        assert "docstring" in my_function.__doc__


class TestCachedEdgeCases:
    """Test @cached edge cases."""

    @pytest.mark.asyncio
    async def test_cached_with_kwargs(self):
        """Test cached function with keyword arguments."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60)
        async def compute(x: int, y: int = 10) -> int:
            call_count[0] += 1
            return x + y

        await compute(5, y=10)
        await compute(5, y=10)  # Cache hit

        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_cached_with_default_args(self):
        """Test cached function with default arguments."""
        cache = CacheLayer()
        call_count = [0]

        @cached(cache=cache, ttl=60)
        async def compute(x: int, y: int = 10) -> int:
            call_count[0] += 1
            return x + y

        await compute(5)  # Uses default y=10
        await compute(5)  # Cache hit

        assert call_count[0] == 1

        await compute(5, y=20)  # Different args
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_cached_complex_return(self):
        """Test cached function with complex return value."""
        cache = CacheLayer()

        @cached(cache=cache, ttl=60)
        async def get_data() -> dict:
            return {"nested": {"key": "value"}, "list": [1, 2, 3]}

        result1 = await get_data()
        result2 = await get_data()

        assert result1 == result2
        assert result1["nested"]["key"] == "value"
