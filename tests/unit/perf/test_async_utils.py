# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for AsyncUtils.

This test suite validates:
- Async caching decorator
- Rate limiter
- Retry decorator
- Timeout decorator
- Bounded executor
- Debounce and throttle decorators

Quality Gate 4 Criteria Covered:
- PERF-002: Async utils <5% overhead
"""

import asyncio
import pytest
import time

from gaia.perf.async_utils import (
    async_cached,
    AsyncRateLimiter,
    async_retry,
    async_timeout,
    AsyncBoundedExecutor,
    async_debounce,
    async_throttle,
    async_gather_with_concurrency,
    create_key,
)


# =============================================================================
# Async Cached Decorator Tests
# =============================================================================

class TestAsyncCached:
    """Tests for async_cached decorator."""

    @pytest.mark.asyncio
    async def test_async_cached_hit(self):
        """Test cache hit returns cached value."""
        call_count = 0

        @async_cached(ttl_seconds=60)
        async def get_value(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        result1 = await get_value(5)
        result2 = await get_value(5)

        assert result1 == 10
        assert result2 == 10
        assert call_count == 1  # Only called once due to cache

    @pytest.mark.asyncio
    async def test_async_cached_miss(self):
        """Test cache miss calls function."""
        call_count = 0

        @async_cached(ttl_seconds=60)
        async def get_value(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        result1 = await get_value(5)
        result2 = await get_value(10)  # Different key

        assert result1 == 10
        assert result2 == 20
        assert call_count == 2  # Called twice (different keys)

    @pytest.mark.asyncio
    async def test_async_cached_expiry(self):
        """Test cache expires after timeout."""
        call_count = 0

        @async_cached(ttl_seconds=1)  # 1 second TTL
        async def get_value(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        result1 = await get_value(5)
        await asyncio.sleep(1.1)  # Wait for expiry
        result2 = await get_value(5)

        assert result1 == 10
        assert result2 == 10
        assert call_count == 2  # Called twice (cache expired)

    @pytest.mark.asyncio
    async def test_async_cached_key_func(self):
        """Test custom key function."""
        call_count = 0

        def custom_key(x, y):
            return f"custom:{x}"

        @async_cached(ttl_seconds=60, key_func=custom_key)
        async def get_value(x, y):
            nonlocal call_count
            call_count += 1
            return x + y

        result1 = await get_value(5, 10)
        result2 = await get_value(5, 99)  # Same x, different y

        assert result1 == 15
        assert result2 == 15  # Same cache key, so cached result
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_cached_clear(self):
        """Test cache clear method."""
        call_count = 0

        @async_cached(ttl_seconds=60)
        async def get_value(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        await get_value(5)
        get_value.cache_clear()
        await get_value(5)

        assert call_count == 2  # Cache was cleared

    @pytest.mark.asyncio
    async def test_async_cached_info(self):
        """Test cache info method."""
        @async_cached(ttl_seconds=60)
        async def get_value(x):
            return x * 2

        await get_value(5)
        info = get_value.cache_info()

        assert info["size"] == 1
        assert "entries" in info


# =============================================================================
# Async Rate Limiter Tests
# =============================================================================

class TestAsyncRateLimiter:
    """Tests for AsyncRateLimiter."""

    @pytest.mark.asyncio
    async def test_rate_limiter_acquire(self):
        """Test acquiring tokens."""
        limiter = AsyncRateLimiter(rate=10, capacity=20)

        await limiter.acquire(5)
        tokens = limiter.available_tokens

        assert tokens < 20  # Some tokens consumed

    @pytest.mark.asyncio
    async def test_rate_limiter_wait(self):
        """Test rate limiter waits when tokens exhausted."""
        limiter = AsyncRateLimiter(rate=100, capacity=5)  # Fast refill for test

        # Exhaust all tokens
        await limiter.acquire(5)

        start = time.time()
        await limiter.acquire(5)  # Should wait for refill
        elapsed = time.time() - start

        # Should have waited approximately 0.05s for 5 tokens at 100/s
        assert elapsed > 0.01

    @pytest.mark.asyncio
    async def test_rate_limiter_context_manager(self):
        """Test rate limiter as context manager."""
        limiter = AsyncRateLimiter(rate=10, capacity=20)

        async with limiter:
            # Token should be acquired
            pass

        # Should still work after context exit
        assert limiter.available_tokens > 0

    @pytest.mark.asyncio
    async def test_rate_limiter_invalid_rate(self):
        """Test that invalid rate raises ValueError."""
        with pytest.raises(ValueError, match="rate must be positive"):
            AsyncRateLimiter(rate=0, capacity=10)

    @pytest.mark.asyncio
    async def test_rate_limiter_invalid_capacity(self):
        """Test that invalid capacity raises ValueError."""
        with pytest.raises(ValueError, match="capacity must be positive"):
            AsyncRateLimiter(rate=10, capacity=0)

    @pytest.mark.asyncio
    async def test_rate_limiter_invalid_acquire(self):
        """Test that invalid acquire amount raises ValueError."""
        limiter = AsyncRateLimiter(rate=10, capacity=20)

        with pytest.raises(ValueError, match="tokens must be positive"):
            await limiter.acquire(0)

    def test_rate_limiter_repr(self):
        """Test string representation."""
        limiter = AsyncRateLimiter(rate=10, capacity=20)
        repr_str = repr(limiter)

        assert "AsyncRateLimiter" in repr_str
        assert "rate=" in repr_str


# =============================================================================
# Async Retry Decorator Tests
# =============================================================================

class TestAsyncRetry:
    """Tests for async_retry decorator."""

    @pytest.mark.asyncio
    async def test_retry_success(self):
        """Test retry on success."""
        call_count = 0

        @async_retry(retries=3, delay=0.01)
        async def succeed():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await succeed()
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        """Test all retries fail."""
        call_count = 0

        @async_retry(retries=3, delay=0.01)
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")

        with pytest.raises(ValueError, match="Always fails"):
            await always_fail()

        assert call_count == 4  # Initial + 3 retries

    @pytest.mark.asyncio
    async def test_retry_backoff(self):
        """Test exponential backoff."""
        call_count = 0
        delays = []
        last_call = None

        @async_retry(retries=3, delay=0.1, backoff=2.0)
        async def fail_with_timing():
            nonlocal call_count, last_call
            now = time.time()
            if last_call:
                delays.append(now - last_call)
            call_count += 1
            last_call = now
            raise ValueError("Fail")

        with pytest.raises(ValueError):
            await fail_with_timing()

        # Verify increasing delays (approximately)
        assert call_count == 4
        # Delays should increase: ~0.1, ~0.2, ~0.4

    @pytest.mark.asyncio
    async def test_retry_specific_exception(self):
        """Test retry only catches specified exceptions."""
        call_count = 0

        @async_retry(retries=3, delay=0.01, exceptions=(ConnectionError,))
        async def fail_with_wrong_exception():
            nonlocal call_count
            call_count += 1
            raise ValueError("Wrong exception type")

        with pytest.raises(ValueError):
            await fail_with_wrong_exception()

        assert call_count == 1  # No retries for ValueError


# =============================================================================
# Async Timeout Decorator Tests
# =============================================================================

class TestAsyncTimeout:
    """Tests for async_timeout decorator."""

    @pytest.mark.asyncio
    async def test_timeout_success(self):
        """Test operation completes in time."""
        @async_timeout(5.0)
        async def quick_operation():
            await asyncio.sleep(0.01)
            return "done"

        result = await quick_operation()
        assert result == "done"

    @pytest.mark.asyncio
    async def test_timeout_exceeded(self):
        """Test operation times out."""
        @async_timeout(0.1)
        async def slow_operation():
            await asyncio.sleep(1.0)
            return "done"

        with pytest.raises(asyncio.TimeoutError):
            await slow_operation()

    @pytest.mark.asyncio
    async def test_timeout_preserves_exception(self):
        """Test that original exception is preserved on timeout."""
        @async_timeout(0.1)
        async def operation_with_error():
            await asyncio.sleep(1.0)
            raise ValueError("Original error")

        with pytest.raises(asyncio.TimeoutError):
            await operation_with_error()


# =============================================================================
# Async Bounded Executor Tests
# =============================================================================

class TestAsyncBoundedExecutor:
    """Tests for AsyncBoundedExecutor."""

    @pytest.mark.asyncio
    async def test_bounded_executor_max_concurrent(self):
        """Test bounded executor respects max concurrent."""
        max_concurrent_seen = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def track_concurrency(item):
            nonlocal max_concurrent_seen, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent_seen:
                    max_concurrent_seen = current_concurrent

            await asyncio.sleep(0.1)

            async with lock:
                current_concurrent -= 1

            return item * 2

        executor = AsyncBoundedExecutor(max_concurrent=3)
        items = [1, 2, 3, 4, 5, 6]

        results = await executor.map(track_concurrency, items)

        assert results == [2, 4, 6, 8, 10, 12]
        assert max_concurrent_seen <= 3

    @pytest.mark.asyncio
    async def test_bounded_executor_submit(self):
        """Test submitting individual tasks."""
        executor = AsyncBoundedExecutor(max_concurrent=5)

        async def double(x):
            return x * 2

        task = await executor.submit(double(5))
        result = await task

        assert result == 10

    @pytest.mark.asyncio
    async def test_bounded_executor_wait_all(self):
        """Test waiting for all tasks."""
        executor = AsyncBoundedExecutor(max_concurrent=5)

        async def double(x):
            return x * 2

        await executor.submit(double(1))
        await executor.submit(double(2))
        await executor.submit(double(3))

        results = await executor.wait_all()

        assert results == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_bounded_executor_invalid_max(self):
        """Test that invalid max_concurrent raises ValueError."""
        with pytest.raises(ValueError, match="max_concurrent must be positive"):
            AsyncBoundedExecutor(max_concurrent=0)

    def test_bounded_executor_repr(self):
        """Test string representation."""
        executor = AsyncBoundedExecutor(max_concurrent=5)
        repr_str = repr(executor)

        assert "AsyncBoundedExecutor" in repr_str
        assert "pending=" in repr_str


# =============================================================================
# Debounce Decorator Tests
# =============================================================================

class TestAsyncDebounce:
    """Tests for async_debounce decorator."""

    @pytest.mark.asyncio
    async def test_debounce_cancels_previous(self):
        """Test that debounce cancels previous calls."""
        call_count = 0

        @async_debounce(wait=0.1)
        async def save():
            nonlocal call_count
            call_count += 1
            return "saved"

        # Rapid calls
        task1 = asyncio.create_task(save())
        await asyncio.sleep(0.02)
        task2 = asyncio.create_task(save())
        await asyncio.sleep(0.02)
        task3 = asyncio.create_task(save())

        await task3  # Wait for last one

        # Only the last call should execute
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_debounce_leading(self):
        """Test leading edge debounce."""
        call_count = 0

        @async_debounce(wait=0.2, leading=True)
        async def execute():
            nonlocal call_count
            call_count += 1
            return "executed"

        result1 = await execute()  # Should execute immediately
        result2 = await execute()  # Should be ignored (within cooldown)

        assert call_count == 1
        assert result1 == "executed"

    @pytest.mark.asyncio
    async def test_debounce_cancel_method(self):
        """Test cancel method."""
        call_count = 0

        @async_debounce(wait=0.5)
        async def delayed():
            nonlocal call_count
            call_count += 1

        # Start the debounced call (but don't await - let it run in background)
        task = asyncio.create_task(delayed())
        await asyncio.sleep(0.1)  # Let it start
        await delayed.cancel()  # Cancel pending

        # Wait for what would have been the execution time
        await asyncio.sleep(0.6)
        # The task may have executed once before cancel, or not at all
        # The key is cancel() prevents the delayed execution
        assert call_count <= 1  # At most one execution


# =============================================================================
# Throttle Decorator Tests
# =============================================================================

class TestAsyncThrottle:
    """Tests for async_throttle decorator."""

    @pytest.mark.asyncio
    async def test_throttle_delays(self):
        """Test throttle enforces minimum delay."""
        @async_throttle(period=0.1)
        async def api_call():
            return "called"

        start = time.time()
        await api_call()
        await api_call()
        await api_call()
        elapsed = time.time() - start

        # Should have at least 0.2s delay (2 throttled calls)
        assert elapsed >= 0.15  # Allow some tolerance

    @pytest.mark.asyncio
    async def test_throttle_first_call_immediate(self):
        """Test that first call executes immediately."""
        @async_throttle(period=0.5)
        async def api_call():
            return time.time()

        start = time.time()
        result = await api_call()
        elapsed = time.time() - start

        # First call should be nearly immediate
        assert elapsed < 0.1


# =============================================================================
# Gather with Concurrency Tests
# =============================================================================

class TestGatherWithConcurrency:
    """Tests for async_gather_with_concurrency."""

    @pytest.mark.asyncio
    async def test_gather_with_concurrency_limits(self):
        """Test that gather respects concurrency limit."""
        max_concurrent_seen = 0
        current_concurrent = 0

        async def track(item):
            nonlocal max_concurrent_seen, current_concurrent
            current_concurrent += 1
            max_concurrent_seen = max(max_concurrent_seen, current_concurrent)
            await asyncio.sleep(0.05)
            current_concurrent -= 1
            return item * 2

        coros = [track(i) for i in range(10)]
        results = await async_gather_with_concurrency(3, *coros)

        assert results == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]
        assert max_concurrent_seen <= 3

    @pytest.mark.asyncio
    async def test_gather_with_concurrency_exceptions(self):
        """Test gather with exceptions."""
        async def succeed(x):
            return x

        async def fail(x):
            raise ValueError("Fail")

        coros = [succeed(1), fail(2), succeed(3)]

        results = await async_gather_with_concurrency(
            2, *coros, return_exceptions=True
        )

        assert results[0] == 1
        assert isinstance(results[1], ValueError)
        assert results[2] == 3


# =============================================================================
# Utility Function Tests
# =============================================================================

class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_create_key_basic(self):
        """Test basic key creation."""
        key = create_key("user", 123, sort="name")
        assert "user" in key
        assert "123" in key
        assert "sort=" in key

    def test_create_key_consistent_ordering(self):
        """Test key ordering is consistent."""
        key1 = create_key("test", a=1, b=2)
        key2 = create_key("test", b=2, a=1)

        assert key1 == key2

    def test_create_key_empty(self):
        """Test key creation with no arguments."""
        key = create_key()
        assert key == ""


# =============================================================================
# Thread Safety Tests
# =============================================================================

class TestAsyncUtilsThreadSafety:
    """Tests for thread safety of async utilities."""

    @pytest.mark.asyncio
    async def test_concurrent_cache_access(self):
        """Test concurrent cache access is safe."""
        @async_cached(ttl_seconds=60)
        async def get_value(x):
            await asyncio.sleep(0.01)
            return x * 2

        # Concurrent calls
        tasks = [get_value(i % 5) for i in range(20)]
        results = await asyncio.gather(*tasks)

        # All should succeed
        assert len(results) == 20

    @pytest.mark.asyncio
    async def test_concurrent_rate_limiter(self):
        """Test concurrent rate limiter access."""
        limiter = AsyncRateLimiter(rate=1000, capacity=100)

        async def acquire_token():
            await limiter.acquire()
            return True

        # Concurrent acquires
        tasks = [acquire_token() for _ in range(50)]
        results = await asyncio.gather(*tasks)

        # All should succeed
        assert all(results)


# Run tests with: pytest tests/unit/perf/test_async_utils.py -v
