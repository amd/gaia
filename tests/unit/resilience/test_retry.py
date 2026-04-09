"""
Unit tests for Retry with Exponential Backoff implementation.

Tests cover:
- Retry configuration validation
- Exponential backoff calculation
- Jitter application
- Sync and async retry execution
- Decorator usage
- Callback invocation
- Edge cases
"""

import asyncio
import random
import time
from unittest.mock import MagicMock, call, patch

import pytest

from gaia.resilience.retry import (
    RetryConfig,
    RetryError,
    RetryExecutor,
    retry,
)


class TestRetryConfig:
    """Tests for RetryConfig validation."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.jitter is True
        assert config.jitter_factor == 0.1

    def test_custom_config(self):
        """Test custom configuration values."""
        config = RetryConfig(
            max_retries=5,
            base_delay=0.5,
            max_delay=30.0,
            jitter=False,
            jitter_factor=0.2,
        )
        assert config.max_retries == 5
        assert config.base_delay == 0.5
        assert config.max_delay == 30.0
        assert config.jitter is False
        assert config.jitter_factor == 0.2

    def test_invalid_max_retries(self):
        """Test validation of max_retries."""
        with pytest.raises(ValueError, match="max_retries must be >= 0"):
            RetryConfig(max_retries=-1)

    def test_invalid_base_delay(self):
        """Test validation of base_delay."""
        with pytest.raises(ValueError, match="base_delay must be > 0"):
            RetryConfig(base_delay=0)

    def test_invalid_max_delay(self):
        """Test validation of max_delay."""
        with pytest.raises(ValueError, match="max_delay must be > 0"):
            RetryConfig(max_delay=0)

    def test_base_delay_exceeds_max_delay(self):
        """Test validation that base_delay <= max_delay."""
        with pytest.raises(ValueError, match="base_delay must be <= max_delay"):
            RetryConfig(base_delay=10.0, max_delay=5.0)

    def test_invalid_jitter_factor(self):
        """Test validation of jitter_factor range."""
        with pytest.raises(ValueError, match="jitter_factor must be between 0 and 1"):
            RetryConfig(jitter_factor=1.5)

    def test_jitter_factor_at_boundaries(self):
        """Test jitter_factor at valid boundaries."""
        config_zero = RetryConfig(jitter_factor=0)
        assert config_zero.jitter_factor == 0

        config_one = RetryConfig(jitter_factor=1)
        assert config_one.jitter_factor == 1


class TestRetryConfigCalculateDelay:
    """Tests for delay calculation with exponential backoff."""

    def test_exponential_backoff_without_jitter(self):
        """Test exponential backoff formula: base * 2^(attempt-1)."""
        config = RetryConfig(base_delay=1.0, jitter=False)

        assert config.calculate_delay(1) == 1.0  # 1 * 2^0 = 1
        assert config.calculate_delay(2) == 2.0  # 1 * 2^1 = 2
        assert config.calculate_delay(3) == 4.0  # 1 * 2^2 = 4
        assert config.calculate_delay(4) == 8.0  # 1 * 2^3 = 8

    def test_custom_base_delay(self):
        """Test with custom base delay."""
        config = RetryConfig(base_delay=0.5, jitter=False)

        assert config.calculate_delay(1) == 0.5
        assert config.calculate_delay(2) == 1.0
        assert config.calculate_delay(3) == 2.0

    def test_max_delay_cap(self):
        """Test delay is capped at max_delay."""
        config = RetryConfig(base_delay=1.0, max_delay=5.0, jitter=False)

        # Attempt 4 would be 8.0 without cap
        assert config.calculate_delay(4) == 5.0
        assert config.calculate_delay(10) == 5.0

    def test_jitter_applied(self):
        """Test jitter is applied to delay."""
        config = RetryConfig(base_delay=10.0, jitter=True, jitter_factor=0.1)

        # With 10% jitter on 10s delay, result should be in [9, 11]
        for _ in range(10):
            delay = config.calculate_delay(1)
            assert 9.0 <= delay <= 11.0

    def test_jitter_never_produces_negative_delay(self):
        """Test jitter doesn't produce negative delays."""
        config = RetryConfig(base_delay=0.1, jitter=True, jitter_factor=0.5)

        # Even with max jitter, delay should never be negative
        for _ in range(100):
            delay = config.calculate_delay(1)
            assert delay >= 0

    def test_jitter_factor_zero_means_no_jitter(self):
        """Test jitter_factor=0 means no jitter."""
        config = RetryConfig(base_delay=5.0, jitter=True, jitter_factor=0)

        # Should always return exact delay
        for _ in range(10):
            assert config.calculate_delay(1) == 5.0


class TestRetryDecorator:
    """Tests for retry decorator functionality."""

    def test_successful_function_no_retry(self):
        """Test successful function doesn't retry."""
        call_count = 0

        @retry(max_retries=3)
        def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = success_func()
        assert result == "success"
        assert call_count == 1

    def test_retry_on_failure(self):
        """Test function retries on failure."""
        call_count = 0

        @retry(max_retries=3, base_delay=0.01, jitter=False)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("temporary failure")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert call_count == 3

    def test_exhausts_retries_raises_retry_error(self):
        """Test exhausted retries raises RetryError."""
        call_count = 0

        @retry(max_retries=2, base_delay=0.01, jitter=False)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("always fails")

        with pytest.raises(RetryError) as exc_info:
            always_fails()

        assert call_count == 3  # Initial + 2 retries
        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.last_exception, ValueError)

    def test_retry_error_contains_last_exception(self):
        """Test RetryError contains the last exception."""
        @retry(max_retries=1, base_delay=0.01)
        def fails_with_specific_error():
            raise ValueError("specific message")

        with pytest.raises(RetryError) as exc_info:
            fails_with_specific_error()

        assert exc_info.value.last_exception is not None
        assert str(exc_info.value.last_exception) == "specific message"


class TestRetryWithCustomExceptions:
    """Tests for custom exception handling in retry."""

    def test_only_retryable_exceptions_trigger_retry(self):
        """Test only specified exceptions trigger retry."""
        call_count = 0

        @retry(
            max_retries=3,
            base_delay=0.01,
            retryable_exceptions=(ValueError,),
        )
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("retryable")
            elif call_count == 2:
                raise TypeError("not retryable")
            return "success"

        # Should raise TypeError immediately without retrying
        with pytest.raises(TypeError):
            flaky_func()

        # Should have been called twice (once for ValueError, once for TypeError)
        # Actually, TypeError on second call means first call succeeded in raising ValueError
        # and retrying, then second call raised TypeError which wasn't retried
        assert call_count == 2

    def test_all_exceptions_retryable_by_default(self):
        """Test all exceptions are retryable by default."""
        call_count = 0

        @retry(max_retries=2, base_delay=0.01, jitter=False)
        def fails_with_type_error():
            nonlocal call_count
            call_count += 1
            raise TypeError("error")

        with pytest.raises(RetryError):
            fails_with_type_error()

        assert call_count == 3  # Initial + 2 retries


class TestRetryCallback:
    """Tests for on_retry callback."""

    def test_on_retry_callback_invoked(self):
        """Test on_retry callback is called on each retry."""
        callback_calls = []

        def on_retry(attempt, exception, delay):
            callback_calls.append((attempt, type(exception).__name__, delay))

        @retry(
            max_retries=3,
            base_delay=0.01,
            jitter=False,
            on_retry=on_retry,
        )
        def flaky_func():
            raise ValueError("error")

        with pytest.raises(RetryError):
            flaky_func()

        # Callback should be called 3 times (for retries 1, 2, 3)
        assert len(callback_calls) == 3
        assert callback_calls[0][0] == 1  # First retry attempt
        assert callback_calls[1][0] == 2  # Second retry attempt
        assert callback_calls[2][0] == 3  # Third retry attempt

    def test_on_retry_receives_correct_delay(self):
        """Test on_retry callback receives calculated delay."""
        callback_delays = []

        @retry(
            max_retries=2,
            base_delay=1.0,
            jitter=False,
            on_retry=lambda attempt, exc, delay: callback_delays.append(delay),
        )
        def flaky_func():
            raise ValueError("error")

        with pytest.raises(RetryError):
            flaky_func()

        # Delays should be 1.0, 2.0 (exponential: 1*2^0, 1*2^1)
        assert callback_delays == [1.0, 2.0]


class TestRetryAsync:
    """Tests for async retry functionality."""

    @pytest.mark.asyncio
    async def test_async_success_no_retry(self):
        """Test successful async function doesn't retry."""
        call_count = 0

        @retry(max_retries=3)
        async def success_func():
            nonlocal call_count
            call_count += 1
            return "async_success"

        result = await success_func()
        assert result == "async_success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_retry_on_failure(self):
        """Test async function retries on failure."""
        call_count = 0

        @retry(max_retries=3, base_delay=0.01, jitter=False)
        async def flaky_async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("temporary failure")
            return "async_success"

        result = await flaky_async_func()
        assert result == "async_success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_async_exhausts_retries(self):
        """Test async function exhausts retries and raises RetryError."""
        call_count = 0

        @retry(max_retries=2, base_delay=0.01, jitter=False)
        async def always_fails_async():
            nonlocal call_count
            call_count += 1
            raise ValueError("always fails")

        with pytest.raises(RetryError) as exc_info:
            await always_fails_async()

        assert call_count == 3
        assert exc_info.value.attempts == 3

    @pytest.mark.asyncio
    async def test_async_with_actual_delay(self):
        """Test async retry with actual delays."""
        start_time = time.time()

        @retry(max_retries=2, base_delay=0.1, jitter=False)
        async def slow_failing_func():
            raise ValueError("error")

        with pytest.raises(RetryError):
            await slow_failing_func()

        elapsed = time.time() - start_time
        # Should have delays of 0.1s and 0.2s = 0.3s total
        assert elapsed >= 0.25  # Allow some tolerance


class TestRetryDecoratorSyntax:
    """Tests for different retry decorator syntaxes."""

    def test_decorator_with_config_object(self):
        """Test decorator with RetryConfig object."""
        config = RetryConfig(max_retries=2, base_delay=0.01, jitter=False)

        @retry(config)
        def flaky_func():
            flaky_func.calls += 1
            if flaky_func.calls < 3:
                raise ValueError("error")
            return "success"

        flaky_func.calls = 0

        result = flaky_func()
        assert result == "success"
        assert flaky_func.calls == 3

    def test_decorator_with_keyword_args(self):
        """Test decorator with keyword arguments."""
        @retry(max_retries=2, base_delay=0.01, jitter=False)
        def flaky_func():
            flaky_func.calls += 1
            if flaky_func.calls < 3:
                raise ValueError("error")
            return "success"

        flaky_func.calls = 0

        result = flaky_func()
        assert result == "success"
        assert flaky_func.calls == 3

    def test_decorator_overrides_config(self):
        """Test that keyword args override config values."""
        config = RetryConfig(max_retries=5, base_delay=1.0, jitter=False)

        @retry(config, max_retries=1, base_delay=0.01)
        def flaky_func():
            flaky_func.calls += 1
            raise ValueError("error")

        flaky_func.calls = 0

        with pytest.raises(RetryError) as exc_info:
            flaky_func()

        # Should use max_retries=1 from override, not 5 from config
        assert exc_info.value.attempts == 2  # Initial + 1 retry


class TestRetryExecutor:
    """Tests for RetryExecutor class."""

    def test_executor_initialization(self):
        """Test RetryExecutor initialization."""
        executor = RetryExecutor()
        assert executor.config.max_retries == 3
        assert executor.config.base_delay == 1.0

        custom_config = RetryConfig(max_retries=5)
        executor_custom = RetryExecutor(custom_config)
        assert executor_custom.config.max_retries == 5

    def test_executor_sync_execute(self):
        """Test synchronous execute method."""
        executor = RetryExecutor(RetryConfig(max_retries=2, base_delay=0.01, jitter=False))

        call_count = 0

        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("error")
            return "success"

        result = executor.execute(flaky_func)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_executor_async_execute(self):
        """Test async execute method."""
        executor = RetryExecutor(RetryConfig(max_retries=2, base_delay=0.01, jitter=False))

        call_count = 0

        async def flaky_async_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("error")
            return "success"

        result = await executor.aexecute(flaky_async_func)
        assert result == "success"
        assert call_count == 3

    def test_executor_exhausts_retries(self):
        """Test executor exhausts retries."""
        executor = RetryExecutor(RetryConfig(max_retries=1, base_delay=0.01))

        def always_fails():
            raise ValueError("error")

        with pytest.raises(RetryError) as exc_info:
            executor.execute(always_fails)

        assert exc_info.value.attempts == 2


class TestRetryEdgeCases:
    """Tests for edge cases."""

    def test_zero_retries(self):
        """Test with max_retries=0 (no retries)."""
        call_count = 0

        @retry(max_retries=0, base_delay=0.01)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("error")

        with pytest.raises(RetryError) as exc_info:
            flaky_func()

        assert call_count == 1  # Only initial attempt, no retries
        assert exc_info.value.attempts == 1

    def test_very_short_delay(self):
        """Test with very short base delay."""
        call_count = 0

        @retry(max_retries=3, base_delay=0.001, jitter=False)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise ValueError("error")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert call_count == 4

    def test_max_delay_lower_than_base(self):
        """Test when max_delay equals base_delay (no exponential growth)."""
        config = RetryConfig(base_delay=5.0, max_delay=5.0, jitter=False)

        # All attempts should have same delay
        assert config.calculate_delay(1) == 5.0
        assert config.calculate_delay(5) == 5.0
        assert config.calculate_delay(10) == 5.0

    def test_function_succeeds_on_last_retry(self):
        """Test function that succeeds on the last possible attempt."""
        call_count = 0

        @retry(max_retries=3, base_delay=0.01, jitter=False)
        def barely_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise ValueError("error")
            return "success"

        result = barely_succeeds()
        assert result == "success"
        assert call_count == 4  # Initial + 3 retries

    def test_return_value_preserved(self):
        """Test return value is correctly returned."""
        @retry(max_retries=3)
        def returns_complex_value():
            return {"key": "value", "nested": [1, 2, 3]}

        result = returns_complex_value()
        assert result == {"key": "value", "nested": [1, 2, 3]}


class TestRetryQualityGate:
    """Tests for Quality Gate RESIL-003 verification."""

    def test_resil_003_exponential_backoff_pattern(self):
        """
        RESIL-003: Retry backoff follows exponential pattern.

        Verify that delays follow the formula: base_delay * 2^(attempt-1)
        """
        config = RetryConfig(base_delay=1.0, jitter=False, max_delay=60.0)

        # Verify exponential pattern
        expected_delays = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]

        for attempt, expected in enumerate(expected_delays, start=1):
            actual = config.calculate_delay(attempt)
            assert actual == expected, f"Attempt {attempt}: expected {expected}, got {actual}"

    def test_resil_003_backoff_with_jitter_stays_near_exponential(self):
        """
        RESIL-003: Verify jitter doesn't break exponential pattern significantly.

        With 10% jitter, delays should stay within +/- 10% of expected.
        """
        # Use high max_delay to avoid capping during test
        config = RetryConfig(base_delay=10.0, jitter=True, jitter_factor=0.1, max_delay=100.0)

        # Run multiple times to check jitter range
        # Only test attempts 1-3 to avoid very large delays
        for attempt in [1, 2, 3]:
            expected = 10.0 * (2 ** (attempt - 1))
            tolerance = expected * 0.15  # 15% tolerance for statistical variation

            for _ in range(20):
                actual = config.calculate_delay(attempt)
                assert abs(actual - expected) <= tolerance, \
                    f"Attempt {attempt}: expected {expected} +/- {tolerance}, got {actual}"

    def test_retry_actually_waits_exponential_time(self):
        """Test actual retry timing follows exponential pattern."""
        callback_times = []
        callback_delays = []  # Track the delays passed to callback

        def on_retry(attempt, exc, delay):
            callback_times.append(time.time())
            callback_delays.append(delay)

        @retry(
            max_retries=3,
            base_delay=0.1,
            jitter=False,
            on_retry=on_retry,
        )
        def always_fails():
            raise ValueError("error")

        start_time = time.time()

        with pytest.raises(RetryError):
            always_fails()

        # Verify we have timestamps for 3 retries
        assert len(callback_times) == 3
        assert len(callback_delays) == 3

        # Verify the delays passed to callback follow exponential pattern
        # Delays should be: 0.1, 0.2, 0.4 (base * 2^(attempt-1))
        assert 0.09 <= callback_delays[0] <= 0.11, f"First callback delay {callback_delays[0]} not near 0.1"
        assert 0.18 <= callback_delays[1] <= 0.22, f"Second callback delay {callback_delays[1]} not near 0.2"
        assert 0.36 <= callback_delays[2] <= 0.44, f"Third callback delay {callback_delays[2]} not near 0.4"

        # Verify actual elapsed time includes the delays
        elapsed = time.time() - start_time
        expected_total_delay = sum(callback_delays)  # Should be ~0.7s
        assert elapsed >= expected_total_delay * 0.9, f"Elapsed time {elapsed} less than expected {expected_total_delay}"
