"""
Unit tests for Bulkhead implementation.

Tests cover:
- Concurrency limiting
- Permit acquisition and release
- Timeout behavior
- Thread safety with concurrent operations
- Decorator usage
- Async support
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from gaia.resilience.bulkhead import (
    Bulkhead,
    BulkheadConfig,
    BulkheadFullError,
)


class TestBulkheadConfig:
    """Tests for BulkheadConfig validation."""

    def test_default_config(self):
        """Test default configuration values."""
        config = BulkheadConfig()
        assert config.max_concurrency == 10
        assert config.acquire_timeout == 30.0

    def test_custom_config(self):
        """Test custom configuration values."""
        config = BulkheadConfig(max_concurrency=5, acquire_timeout=10.0)
        assert config.max_concurrency == 5
        assert config.acquire_timeout == 10.0

    def test_no_timeout_config(self):
        """Test configuration with no timeout (wait indefinitely)."""
        config = BulkheadConfig(acquire_timeout=None)
        assert config.acquire_timeout is None

    def test_invalid_max_concurrency(self):
        """Test validation of max_concurrency."""
        with pytest.raises(ValueError, match="max_concurrency must be >= 1"):
            BulkheadConfig(max_concurrency=0)

    def test_invalid_acquire_timeout(self):
        """Test validation of acquire_timeout."""
        with pytest.raises(ValueError, match="acquire_timeout must be > 0 or None"):
            BulkheadConfig(acquire_timeout=0)


class TestBulkheadInitialState:
    """Tests for initial bulkhead state."""

    def test_initial_available_permits(self):
        """Test initial available permits equals max_concurrency."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=5))
        assert bulkhead.available_permits == 5

    def test_initial_active_count_zero(self):
        """Test initial active count is zero."""
        bulkhead = Bulkhead()
        assert bulkhead.active_count == 0

    def test_initial_utilization_zero(self):
        """Test initial utilization is zero."""
        bulkhead = Bulkhead()
        assert bulkhead.utilization == 0.0

    def test_max_concurrency_property(self):
        """Test max_concurrency property."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=7))
        assert bulkhead.max_concurrency == 7

    def test_repr(self):
        """Test string representation."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=5))
        repr_str = repr(bulkhead)
        assert "max=5" in repr_str
        assert "active=0" in repr_str


class TestBulkheadExecution:
    """Tests for bulkhead execution."""

    def test_execute_passes_through(self):
        """Test successful execution passes through."""
        bulkhead = Bulkhead()
        result = bulkhead.execute(lambda x: x * 2, 5)
        assert result == 10

    def test_execute_with_args_and_kwargs(self):
        """Test execution with positional and keyword arguments."""
        bulkhead = Bulkhead()

        def my_func(a, b, c=10):
            return a + b + c

        result = bulkhead.execute(my_func, 1, 2, c=5)
        assert result == 8

    def test_execute_increments_active_count(self):
        """Test active count increments during execution."""
        bulkhead = Bulkhead()
        active_during = []

        def track_active():
            active_during.append(bulkhead.active_count)
            return "done"

        bulkhead.execute(track_active)
        assert active_during[0] == 1
        assert bulkhead.active_count == 0  # Back to 0 after completion

    def test_execute_decrements_on_completion(self):
        """Test active count decrements after execution completes."""
        bulkhead = Bulkhead()
        bulkhead.execute(lambda: "result")
        assert bulkhead.active_count == 0
        assert bulkhead.available_permits == bulkhead.max_concurrency

    def test_execute_tracks_total_acquired(self):
        """Test total_acquired counter increments."""
        bulkhead = Bulkhead()
        for _ in range(5):
            bulkhead.execute(lambda: "ok")
        assert bulkhead.total_acquired == 5


class TestBulkheadConcurrencyLimiting:
    """Tests for bulkhead concurrency limiting."""

    def test_limits_concurrent_operations(self):
        """Test bulkhead limits concurrent operations."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=3, acquire_timeout=5.0))
        max_concurrent_seen = []
        lock = threading.Lock()

        def track_concurrency():
            with lock:
                current = bulkhead.active_count
                if not max_concurrent_seen or current > max_concurrent_seen[0]:
                    max_concurrent_seen.append(current)
            time.sleep(0.05)
            return "done"

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(bulkhead.execute, track_concurrency) for _ in range(10)]
            results = [f.result() for f in futures]

        assert len(results) == 10
        # Due to timing, we might not see exactly 3, but shouldn't exceed
        if max_concurrent_seen:
            assert max_concurrent_seen[-1] <= 3

    def test_rejects_when_at_capacity_with_timeout(self):
        """Test bulkhead rejects when at capacity and timeout expires."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=1, acquire_timeout=0.1))

        # Hold the permit
        def hold_permit():
            time.sleep(0.5)
            return "held"

        # Try to acquire while permit is held
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Start holding permit
            hold_future = executor.submit(bulkhead.execute, hold_permit)
            time.sleep(0.05)  # Let first acquire

            # Try to acquire second - should timeout
            with pytest.raises(BulkheadFullError) as exc_info:
                bulkhead.execute(lambda: "should_timeout")

            assert "timeout" in str(exc_info.value).lower()

        hold_future.result()  # Clean up

    def test_rejected_increments_total_rejected(self):
        """Test total_rejected counter increments on rejection."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=1, acquire_timeout=0.1))

        def hold():
            time.sleep(0.3)
            return "held"

        with ThreadPoolExecutor(max_workers=2) as executor:
            executor.submit(bulkhead.execute, hold)
            time.sleep(0.05)

            # Try to acquire - should timeout and be rejected
            for _ in range(3):
                try:
                    bulkhead.execute(lambda: "timeout")
                except BulkheadFullError:
                    pass

        assert bulkhead.total_rejected >= 1

    def test_bulkhead_full_error_includes_max_concurrency(self):
        """Test BulkheadFullError includes max_concurrency."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=5, acquire_timeout=0.1))

        with pytest.raises(BulkheadFullError) as exc_info:
            # Force rejection by holding all permits
            permits = []
            for _ in range(5):
                acquired = bulkhead._semaphore.acquire(blocking=False)
                if acquired:
                    permits.append(True)

            # Manually call execute to trigger rejection
            try:
                bulkhead.execute(lambda: "reject")
            finally:
                for _ in permits:
                    bulkhead._semaphore.release()

        assert exc_info.value.max_concurrency == 5


class TestBulkheadDecorator:
    """Tests for bulkhead decorator usage."""

    def test_sync_decorator(self):
        """Test decorator with synchronous function."""
        bulkhead = Bulkhead()

        @bulkhead
        def my_func(x, y):
            return x + y

        assert my_func(2, 3) == 5

    def test_async_decorator(self):
        """Test decorator with asynchronous function."""
        bulkhead = Bulkhead()

        @bulkhead
        async def my_async_func(x, y):
            return x + y

        result = asyncio.run(my_async_func(2, 3))
        assert result == 5

    def test_decorator_limits_concurrency(self):
        """Test decorator enforces concurrency limits."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=2, acquire_timeout=0.1))

        @bulkhead
        def slow_func():
            time.sleep(0.2)
            return "done"

        # Try to run many at once
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(slow_func) for _ in range(5)]
            results = []
            for f in futures:
                try:
                    results.append(f.result(timeout=1.0))
                except BulkheadFullError:
                    results.append("rejected")

        # Some should succeed, some should be rejected
        assert "done" in results
        assert "rejected" in results


class TestBulkheadAsync:
    """Tests for async bulkhead operations."""

    @pytest.mark.asyncio
    async def test_async_execute_success(self):
        """Test successful async execute."""
        bulkhead = Bulkhead()

        async def async_func():
            return "async_result"

        result = await bulkhead.aexecute(async_func)
        assert result == "async_result"
        assert bulkhead.active_count == 0

    @pytest.mark.asyncio
    async def test_async_execute_with_args(self):
        """Test async execute with arguments."""
        bulkhead = Bulkhead()

        async def async_func(x, y, z=10):
            return x + y + z

        result = await bulkhead.aexecute(async_func, 1, 2, z=5)
        assert result == 8

    @pytest.mark.asyncio
    async def test_async_execute_concurrency_limit(self):
        """Test async concurrency limiting."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=2, acquire_timeout=0.1))

        async def slow_task():
            await asyncio.sleep(0.2)
            return "done"

        # Run multiple tasks
        tasks = [bulkhead.aexecute(slow_task) for _ in range(5)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Some should succeed, some should timeout
        successes = [r for r in results if r == "done"]
        rejections = [r for r in results if isinstance(r, BulkheadFullError)]

        assert len(successes) >= 1
        assert len(rejections) >= 1


class TestBulkheadThreadSafety:
    """Tests for bulkhead thread safety."""

    def test_concurrent_executions(self):
        """Test concurrent executions don't cause issues."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=5))
        results = []
        lock = threading.Lock()

        def worker():
            result = bulkhead.execute(lambda: threading.current_thread().name)
            with lock:
                results.append(result)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker) for _ in range(50)]
            for future in futures:
                future.result()

        assert len(results) == 50
        assert bulkhead.active_count == 0

    def test_try_acquire_thread_safe(self):
        """Test try_acquire is thread safe."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=10))
        acquired = []
        lock = threading.Lock()

        def worker():
            result = bulkhead.try_acquire()
            with lock:
                acquired.append(result)
            if result:
                bulkhead.release()

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker) for _ in range(50)]
            for future in futures:
                future.result()

        # Exactly 10 should have acquired at any time, but all 50 should complete
        assert len(acquired) == 50
        assert sum(acquired) == 50  # All should acquire since we release

    def test_100_concurrent_operations(self):
        """Test thread safety with 100+ concurrent operations (THREAD-003)."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=10))
        successful_calls = []
        rejected_calls = []
        lock = threading.Lock()

        def worker():
            try:
                result = bulkhead.execute(lambda: "ok")
                with lock:
                    successful_calls.append(result)
            except BulkheadFullError:
                with lock:
                    rejected_calls.append(True)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker) for _ in range(100)]
            for future in futures:
                future.result()

        # Verify no race condition crashes
        total = len(successful_calls) + len(rejected_calls)
        assert total == 100

    def test_utilization_calculation_thread_safe(self):
        """Test utilization calculation is thread safe."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=10))
        utilization_readings = []
        lock = threading.Lock()
        stop_flag = threading.Event()

        def reader():
            while not stop_flag.is_set():
                util = bulkhead.utilization
                with lock:
                    utilization_readings.append(util)
                time.sleep(0.01)

        def worker():
            bulkhead.execute(lambda: time.sleep(0.05))

        # Start reader thread
        reader_thread = threading.Thread(target=reader)
        reader_thread.start()

        # Run workers
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker) for _ in range(20)]
            for future in futures:
                future.result()

        stop_flag.set()
        reader_thread.join()

        # All readings should be valid (0.0 to 1.0)
        for util in utilization_readings:
            assert 0.0 <= util <= 1.0


class TestBulkheadEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_single_concurrency(self):
        """Test with max_concurrency=1."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=1, acquire_timeout=0.1))

        result = bulkhead.execute(lambda: "single")
        assert result == "single"

        # Try to acquire while holding
        def hold():
            time.sleep(0.3)
            return "held"

        with ThreadPoolExecutor(max_workers=2) as executor:
            hold_future = executor.submit(bulkhead.execute, hold)
            time.sleep(0.05)

            with pytest.raises(BulkheadFullError):
                bulkhead.execute(lambda: "reject")

        hold_future.result()

    def test_no_timeout_waits_indefinitely(self):
        """Test acquire_timeout=None waits indefinitely."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=1, acquire_timeout=None))

        def hold():
            time.sleep(0.2)
            return "held"

        def acquire_after_delay():
            time.sleep(0.1)
            return bulkhead.execute(lambda: "acquired")

        with ThreadPoolExecutor(max_workers=2) as executor:
            hold_future = executor.submit(bulkhead.execute, hold)
            acquire_future = executor.submit(acquire_after_delay)

            result = acquire_future.result(timeout=1.0)
            assert result == "acquired"

        hold_future.result()

    def test_manual_acquire_release(self):
        """Test manual try_acquire and release."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=2))

        # Manual acquire
        assert bulkhead.try_acquire() is True
        assert bulkhead.active_count == 1

        assert bulkhead.try_acquire() is True
        assert bulkhead.active_count == 2

        # Third should fail
        assert bulkhead.try_acquire() is False

        # Release one
        bulkhead.release()
        assert bulkhead.active_count == 1

        # Now should succeed
        assert bulkhead.try_acquire() is True

        # Clean up
        bulkhead.release()
        bulkhead.release()

    def test_exception_during_execution_releases_permit(self):
        """Test permit is released even if function raises exception."""
        bulkhead = Bulkhead()

        def failing_func():
            raise ValueError("error")

        with pytest.raises(ValueError):
            bulkhead.execute(failing_func)

        assert bulkhead.active_count == 0
        assert bulkhead.available_permits == bulkhead.max_concurrency

    def test_repr_with_activity(self):
        """Test repr during active operations."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=5))
        bulkhead._active_count = 3

        repr_str = repr(bulkhead)
        assert "active=3" in repr_str
        assert "available=2" in repr_str


class TestBulkheadQualityGate:
    """Tests for Quality Gate RESIL-002 verification."""

    def test_resil_002_bulkhead_limits_concurrency_correctly(self):
        """
        RESIL-002: Bulkhead limits concurrency correctly.

        Verify that with max_concurrency=N, no more than N operations
        run concurrently.
        """
        max_concurrency = 5
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=max_concurrency, acquire_timeout=2.0))
        observed_concurrent = []
        lock = threading.Lock()

        def track_concurrency():
            with lock:
                current = bulkhead.active_count
                observed_concurrent.append(current)
            # Ensure we capture the active count
            time.sleep(0.1)
            return "done"

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(bulkhead.execute, track_concurrency) for _ in range(20)]
            for future in futures:
                future.result()

        # Verify no more than max_concurrency were ever active
        assert max(observed_concurrent) <= max_concurrency
