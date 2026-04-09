"""
Unit tests for CircuitBreaker implementation.

Tests cover:
- State transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
- Failure threshold detection
- Recovery timeout behavior
- Thread safety with concurrent operations
- Decorator usage
- Async support
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from gaia.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
    CircuitOpenError,
)


class TestCircuitBreakerConfig:
    """Tests for CircuitBreakerConfig validation."""

    def test_default_config(self):
        """Test default configuration values."""
        config = CircuitBreakerConfig()
        assert config.failure_threshold == 5
        assert config.recovery_timeout == 30.0
        assert config.success_threshold == 2
        assert config.expected_exceptions == (Exception,)

    def test_custom_config(self):
        """Test custom configuration values."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=10.0,
            success_threshold=1,
            expected_exceptions=(ValueError,),
        )
        assert config.failure_threshold == 3
        assert config.recovery_timeout == 10.0
        assert config.success_threshold == 1
        assert config.expected_exceptions == (ValueError,)

    def test_invalid_failure_threshold(self):
        """Test validation of failure_threshold."""
        with pytest.raises(ValueError, match="failure_threshold must be >= 1"):
            CircuitBreakerConfig(failure_threshold=0)

    def test_invalid_recovery_timeout(self):
        """Test validation of recovery_timeout."""
        with pytest.raises(ValueError, match="recovery_timeout must be > 0"):
            CircuitBreakerConfig(recovery_timeout=0)

    def test_invalid_success_threshold(self):
        """Test validation of success_threshold."""
        with pytest.raises(ValueError, match="success_threshold must be >= 1"):
            CircuitBreakerConfig(success_threshold=0)


class TestCircuitBreakerInitialState:
    """Tests for initial circuit breaker state."""

    def test_initial_state_closed(self):
        """Test circuit starts in CLOSED state."""
        breaker = CircuitBreaker()
        assert breaker.state == CircuitBreakerState.CLOSED
        assert breaker.is_closed
        assert not breaker.is_open
        assert not breaker.is_half_open

    def test_initial_failure_count_zero(self):
        """Test initial failure count is zero."""
        breaker = CircuitBreaker()
        assert breaker.failure_count == 0

    def test_repr_initial_state(self):
        """Test string representation."""
        breaker = CircuitBreaker()
        repr_str = repr(breaker)
        assert "CLOSED" in repr_str
        assert "failure_count=0" in repr_str


class TestCircuitBreakerClosedState:
    """Tests for circuit breaker in CLOSED state."""

    def test_successful_call_passes_through(self):
        """Test successful calls pass through in CLOSED state."""
        breaker = CircuitBreaker()
        result = breaker.call(lambda: "success")
        assert result == "success"
        assert breaker.is_closed

    def test_successful_call_resets_failure_count(self):
        """Test success resets failure count."""
        breaker = CircuitBreaker()
        # Simulate some failures then success
        breaker._failure_count = 3
        breaker.call(lambda: "success")
        assert breaker.failure_count == 0

    def test_failure_increments_count(self):
        """Test failure increments count."""
        breaker = CircuitBreaker()

        def failing_func():
            raise ValueError("error")

        with pytest.raises(ValueError):
            breaker.call(failing_func)

        assert breaker.failure_count == 1

    def test_failure_below_threshold_keeps_closed(self):
        """Test circuit stays closed when failures below threshold."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5))

        def failing_func():
            raise ValueError("error")

        for _ in range(4):
            with pytest.raises(ValueError):
                breaker.call(failing_func)

        assert breaker.is_closed

    def test_failure_at_threshold_opens_circuit(self):
        """Test circuit opens when failures reach threshold."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))

        def failing_func():
            raise ValueError("error")

        for _ in range(3):
            with pytest.raises(ValueError):
                breaker.call(failing_func)

        assert breaker.is_open

    def test_unexpected_exception_re_raises_immediately(self):
        """Test exceptions not in expected_exceptions re-raise without counting."""
        # By default, all exceptions are expected
        breaker = CircuitBreaker(CircuitBreakerConfig(expected_exceptions=(ValueError,)))

        def runtime_error_func():
            raise RuntimeError("unexpected")

        # Should re-raise but not count toward threshold
        with pytest.raises(RuntimeError):
            breaker.call(runtime_error_func)

        assert breaker.failure_count == 0
        assert breaker.is_closed


class TestCircuitBreakerOpenState:
    """Tests for circuit breaker in OPEN state."""

    def test_open_state_rejects_calls(self):
        """Test OPEN state rejects calls with CircuitOpenError."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))

        def failing_func():
            raise ValueError("error")

        # Trip the circuit
        with pytest.raises(ValueError):
            breaker.call(failing_func)

        assert breaker.is_open

        # Next call should be rejected
        with pytest.raises(CircuitOpenError) as exc_info:
            breaker.call(lambda: "success")

        assert "Circuit breaker is open" in str(exc_info.value)

    def test_open_state_provides_retry_time(self):
        """Test CircuitOpenError includes time until retry."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=30))

        def failing_func():
            raise ValueError("error")

        with pytest.raises(ValueError):
            breaker.call(failing_func)

        with pytest.raises(CircuitOpenError) as exc_info:
            breaker.call(lambda: "success")

        assert exc_info.value.time_until_retry is not None
        assert 0 < exc_info.value.time_until_retry <= 30

    def test_manual_trip(self):
        """Test manual trip to OPEN state."""
        breaker = CircuitBreaker()
        breaker.trip()
        assert breaker.is_open


class TestCircuitBreakerHalfOpenState:
    """Tests for circuit breaker in HALF_OPEN state."""

    def test_recovery_timeout_transitions_to_half_open(self):
        """Test automatic transition to HALF_OPEN after recovery timeout."""
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.1,  # 100ms for fast test
                success_threshold=1,
            )
        )

        def failing_func():
            raise ValueError("error")

        # Trip the circuit
        with pytest.raises(ValueError):
            breaker.call(failing_func)

        assert breaker.is_open

        # Wait for recovery timeout
        time.sleep(0.15)

        # Should transition to HALF_OPEN on next state check
        assert breaker.state == CircuitBreakerState.HALF_OPEN

    def test_half_open_success_closes_circuit(self):
        """Test successful call in HALF_OPEN closes circuit."""
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.05,
                success_threshold=1,
            )
        )

        def failing_func():
            raise ValueError("error")

        # Trip the circuit
        with pytest.raises(ValueError):
            breaker.call(failing_func)

        # Wait for recovery timeout
        time.sleep(0.1)

        # Successful call should close circuit
        result = breaker.call(lambda: "success")
        assert result == "success"
        assert breaker.is_closed
        assert breaker.failure_count == 0

    def test_half_open_failure_reopens_circuit(self):
        """Test failed call in HALF_OPEN reopens circuit."""
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.05,
                success_threshold=2,
            )
        )

        def failing_func():
            raise ValueError("error")

        # Trip the circuit
        with pytest.raises(ValueError):
            breaker.call(failing_func)

        # Wait for recovery timeout
        time.sleep(0.1)

        # Failed call should reopen circuit
        with pytest.raises(ValueError):
            breaker.call(failing_func)

        assert breaker.is_open

    def test_multiple_successes_needed(self):
        """Test multiple successes needed to close based on success_threshold."""
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.05,
                success_threshold=3,
            )
        )

        # Trip the circuit
        with pytest.raises(ValueError):
            breaker.call(lambda: (_ for _ in ()).throw(ValueError("error")))

        # Wait for recovery timeout
        time.sleep(0.1)

        # Two successes should not be enough
        breaker.call(lambda: "success1")
        breaker.call(lambda: "success2")
        assert breaker.is_half_open

        # Third success should close
        breaker.call(lambda: "success3")
        assert breaker.is_closed

    def test_manual_half_open(self):
        """Test manual transition to HALF_OPEN state."""
        breaker = CircuitBreaker()
        breaker.trip()
        assert breaker.is_open

        breaker.half_open()
        assert breaker.is_half_open


class TestCircuitBreakerReset:
    """Tests for circuit breaker reset functionality."""

    def test_reset_closes_circuit(self):
        """Test reset closes the circuit."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))

        def failing_func():
            raise ValueError("error")

        with pytest.raises(ValueError):
            breaker.call(failing_func)

        assert breaker.is_open

        breaker.reset()
        assert breaker.is_closed

    def test_reset_clears_failure_count(self):
        """Test reset clears failure count."""
        breaker = CircuitBreaker()
        breaker._failure_count = 5

        breaker.reset()
        assert breaker.failure_count == 0

    def test_reset_clears_last_failure_time(self):
        """Test reset clears last failure time."""
        breaker = CircuitBreaker()
        breaker._last_failure_time = time.time()

        breaker.reset()
        assert breaker._last_failure_time is None


class TestCircuitBreakerDecorator:
    """Tests for circuit breaker decorator usage."""

    def test_sync_decorator(self):
        """Test decorator with synchronous function."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))

        @breaker
        def my_func(x, y):
            return x + y

        assert my_func(2, 3) == 5

    def test_async_decorator(self):
        """Test decorator with asynchronous function."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))

        @breaker
        async def my_async_func(x, y):
            return x + y

        result = asyncio.run(my_async_func(2, 3))
        assert result == 5

    def test_decorator_trips_on_failures(self):
        """Test decorator trips circuit on failures."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))

        @breaker
        def failing_func():
            raise ValueError("error")

        with pytest.raises(ValueError):
            failing_func()
        with pytest.raises(ValueError):
            failing_func()

        assert breaker.is_open

        with pytest.raises(CircuitOpenError):
            failing_func()


class TestCircuitBreakerAsync:
    """Tests for async circuit breaker operations."""

    @pytest.mark.asyncio
    async def test_async_call_success(self):
        """Test successful async call."""
        breaker = CircuitBreaker()

        async def async_func():
            return "async_result"

        result = await breaker.acall(async_func)
        assert result == "async_result"
        assert breaker.is_closed

    @pytest.mark.asyncio
    async def test_async_call_failure(self):
        """Test failed async call."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))

        async def async_failing_func():
            raise ValueError("async error")

        with pytest.raises(ValueError):
            await breaker.acall(async_failing_func)

        assert breaker.failure_count == 1

    @pytest.mark.asyncio
    async def test_async_call_open_circuit(self):
        """Test async call with open circuit."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))

        async def async_failing_func():
            raise ValueError("async error")

        # Trip the circuit
        with pytest.raises(ValueError):
            await breaker.acall(async_failing_func)

        assert breaker.is_open

        with pytest.raises(CircuitOpenError):
            await breaker.acall(lambda: "should_not_run")


class TestCircuitBreakerThreadSafety:
    """Tests for circuit breaker thread safety."""

    def test_concurrent_successful_calls(self):
        """Test concurrent successful calls don't cause issues."""
        breaker = CircuitBreaker()
        results = []

        def worker():
            result = breaker.call(lambda: threading.current_thread().name)
            results.append(result)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker) for _ in range(100)]
            for future in futures:
                future.result()

        assert len(results) == 100
        assert breaker.is_closed

    def test_concurrent_failure_recording(self):
        """Test concurrent failure recording trips circuit correctly."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=50))

        def failing_func():
            raise ValueError("error")

        def worker():
            try:
                breaker.call(failing_func)
            except (ValueError, CircuitOpenError):
                pass

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker) for _ in range(100)]
            for future in futures:
                future.result()

        # Circuit should be open after 50+ failures
        assert breaker.is_open

    def test_state_transitions_under_concurrency(self):
        """Test state transitions are safe under concurrent access."""
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=5,
                recovery_timeout=0.1,
                success_threshold=2,
            )
        )

        def failing_func():
            raise ValueError("error")

        def success_func():
            return "success"

        def worker(fail: bool):
            try:
                if fail:
                    breaker.call(failing_func)
                else:
                    breaker.call(success_func)
            except (ValueError, CircuitOpenError):
                pass

        # Start with failures to open circuit
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, True) for _ in range(20)]
            for future in futures:
                future.result()

        # Wait for recovery timeout
        time.sleep(0.15)

        # Mix of successes and failures in HALF_OPEN
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i % 3 == 0) for i in range(50)]
            for future in futures:
                future.result()

        # Should not have crashed - circuit should be in some valid state
        assert breaker.state in [
            CircuitBreakerState.CLOSED,
            CircuitBreakerState.OPEN,
            CircuitBreakerState.HALF_OPEN,
        ]

    def test_100_concurrent_operations(self):
        """Test thread safety with 100+ concurrent operations (THREAD-002)."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=100))
        successful_calls = []
        failed_calls = []
        open_errors = []
        lock = threading.Lock()

        def worker(should_fail: bool):
            def operation():
                if should_fail:
                    raise ValueError("error")
                return "ok"

            try:
                result = breaker.call(operation)
                with lock:
                    successful_calls.append(result)
            except ValueError:
                with lock:
                    failed_calls.append(True)
            except CircuitOpenError:
                with lock:
                    open_errors.append(True)

        with ThreadPoolExecutor(max_workers=20) as executor:
            # Submit 100 operations
            futures = []
            for i in range(100):
                futures.append(executor.submit(worker, should_fail=(i < 80)))
            for future in futures:
                future.result()

        # Verify no race condition crashes
        total = len(successful_calls) + len(failed_calls) + len(open_errors)
        assert total == 100


class TestCircuitBreakerEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_single_failure_threshold(self):
        """Test with failure_threshold=1."""
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))

        with pytest.raises(ValueError):
            breaker.call(lambda: (_ for _ in ()).throw(ValueError("error")))

        assert breaker.is_open

    def test_single_success_threshold(self):
        """Test with success_threshold=1."""
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.05,
                success_threshold=1,
            )
        )

        # Trip
        with pytest.raises(ValueError):
            breaker.call(lambda: (_ for _ in ()).throw(ValueError("error")))

        # Wait
        time.sleep(0.1)

        # Single success should close
        breaker.call(lambda: "success")
        assert breaker.is_closed

    def test_very_short_recovery_timeout(self):
        """Test with very short recovery timeout."""
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout=0.01,  # 10ms
            )
        )

        with pytest.raises(ValueError):
            breaker.call(lambda: (_ for _ in ()).throw(ValueError("error")))

        time.sleep(0.05)

        # Should be HALF_OPEN
        assert breaker.state == CircuitBreakerState.HALF_OPEN

    def test_expected_exceptions_filtering(self):
        """Test that only expected exceptions count toward threshold."""
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=2,
                expected_exceptions=(ValueError,),
            )
        )

        # Unexpected exception - shouldn't count
        with pytest.raises(RuntimeError):
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("unexpected")))

        assert breaker.failure_count == 0
        assert breaker.is_closed

        # Expected exception - should count
        with pytest.raises(ValueError):
            breaker.call(lambda: (_ for _ in ()).throw(ValueError("expected")))

        assert breaker.failure_count == 1
        assert breaker.is_closed
