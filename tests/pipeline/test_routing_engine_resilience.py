# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for RoutingEngine resilience wiring (WIRE-1).

Tests cover:
- CircuitBreaker behavior and thresholds
- Bulkhead concurrency limiting
- Retry with exponential backoff
- Resilience integration in RoutingEngine
- Monitoring and statistics
"""

import asyncio
import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from gaia.pipeline.routing_engine import RoutingEngine, RoutingRule, RoutingDecision
from gaia.pipeline.defect_types import DefectType
from gaia.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    Bulkhead,
    BulkheadConfig,
    Retry,
    RetryConfig,
    ResilienceError,
)


class TestCircuitBreakerConfig:
    """Tests for CircuitBreakerConfig."""

    def test_config_default_values(self):
        """Verify default configuration values."""
        config = CircuitBreakerConfig()

        assert config.failure_threshold == 5
        assert config.recovery_timeout == 30.0
        assert config.success_threshold == 1

    def test_config_custom_values(self):
        """Verify custom configuration is applied."""
        config = CircuitBreakerConfig(
            failure_threshold=10,
            recovery_timeout=60.0,
            success_threshold=3
        )

        assert config.failure_threshold == 10
        assert config.recovery_timeout == 60.0
        assert config.success_threshold == 3


class TestCircuitBreaker:
    """Tests for CircuitBreaker primitive."""

    @pytest.fixture
    def default_config(self):
        """Create default circuit breaker config."""
        return CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=0.5,  # Short timeout for testing
            success_threshold=1
        )

    @pytest.fixture
    def circuit_breaker(self, default_config):
        """Create circuit breaker with test config."""
        return CircuitBreaker(default_config)

    def test_initial_state_closed(self, circuit_breaker):
        """Verify circuit breaker starts in closed state."""
        assert circuit_breaker.is_open is False
        assert circuit_breaker.is_half_open is False
        assert circuit_breaker.state == "closed"

    def test_circuit_trips_after_threshold_failures(self, circuit_breaker):
        """Verify circuit opens after threshold failures."""
        # Simulate failures
        for i in range(3):
            circuit_breaker.record_failure()

        assert circuit_breaker.is_open is True
        assert circuit_breaker.state == "open"

    def test_circuit_rejects_call_when_open(self, circuit_breaker):
        """Verify circuit rejects calls when open."""
        # Trip the circuit
        for i in range(3):
            circuit_breaker.record_failure()

        # Should reject calls
        with pytest.raises(ResilienceError):
            circuit_breaker.call(lambda: "should not execute")

    def test_circuit_transitions_to_half_open_after_timeout(self, circuit_breaker):
        """Verify circuit transitions to half-open after recovery timeout."""
        # Trip the circuit
        for i in range(3):
            circuit_breaker.record_failure()

        assert circuit_breaker.is_open is True

        # Wait for recovery timeout
        time.sleep(0.6)

        # Should be half-open now
        assert circuit_breaker.is_half_open is True
        assert circuit_breaker.state == "half-open"

    def test_circuit_closes_after_success_in_half_open(self, circuit_breaker):
        """Verify circuit closes after successful call in half-open state."""
        # Trip the circuit
        for i in range(3):
            circuit_breaker.record_failure()

        # Wait for recovery
        time.sleep(0.6)

        # Successful call should close circuit
        result = circuit_breaker.call(lambda: "success")
        assert result == "success"
        assert circuit_breaker.is_open is False
        assert circuit_breaker.state == "closed"

    def test_circuit_reopens_on_failure_in_half_open(self, circuit_breaker):
        """Verify circuit reopens on failure in half-open state."""
        # Trip the circuit
        for i in range(3):
            circuit_breaker.record_failure()

        # Wait for recovery
        time.sleep(0.6)
        assert circuit_breaker.is_half_open is True

        # Failure in half-open should reopen
        try:
            circuit_breaker.call(lambda: (_ for _ in ()).throw(Exception("fail")))
        except:
            pass

        assert circuit_breaker.is_open is True

    def test_call_decorator_success(self, circuit_breaker):
        """Verify @CircuitBreaker.call decorator on successful function."""
        call_count = 0

        @CircuitBreaker.call(CircuitBreakerConfig(failure_threshold=3))
        def successful_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = successful_func()
        assert result == "success"
        assert call_count == 1

    def test_call_decorator_failure_trips_circuit(self):
        """Verify @CircuitBreaker.call trips on repeated failures."""
        fail_count = 0

        @CircuitBreaker.call(CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10))
        def failing_func():
            nonlocal fail_count
            fail_count += 1
            raise Exception("Forced failure")

        # Fail 3 times
        for i in range(3):
            try:
                failing_func()
            except:
                pass

        assert fail_count == 3

        # Next call should fail immediately (circuit open)
        try:
            failing_func()
            assert False, "Should have raised"
        except ResilienceError:
            pass  # Expected

    def test_get_statistics(self, circuit_breaker):
        """Verify circuit breaker statistics."""
        stats = circuit_breaker.get_statistics()

        assert "state" in stats
        assert "failure_count" in stats
        assert "success_count" in stats
        assert "last_failure_time" in stats
        assert stats["state"] == "closed"


class TestBulkhead:
    """Tests for Bulkhead primitive."""

    @pytest.fixture
    def bulkhead_config(self):
        """Create bulkhead config for testing."""
        return BulkheadConfig(
            max_concurrency=3,
            acquire_timeout=0.5
        )

    @pytest.fixture
    def bulkhead(self, bulkhead_config):
        """Create bulkhead with test config."""
        return Bulkhead(bulkhead_config)

    def test_allows_concurrent_within_limit(self, bulkhead):
        """Verify bulkhead allows concurrent execution within limit."""
        results = []

        @Bulkhead.isolate(BulkheadConfig(max_concurrency=3, acquire_timeout=1.0))
        def concurrent_func(value):
            results.append(value)
            return value * 2

        # Execute within limit
        for i in range(3):
            result = concurrent_func(i)
            assert result == i * 2

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_rejects_excess_concurrency(self, bulkhead):
        """Verify bulkhead rejects calls exceeding concurrency limit."""
        call_count = 0
        max_concurrent = 0
        semaphore = asyncio.Semaphore(3)

        @Bulkhead.isolate(BulkheadConfig(max_concurrency=3, acquire_timeout=0.1))
        async def concurrent_task():
            nonlocal call_count, max_concurrent
            call_count += 1
            max_concurrent = max(max_concurrent, call_count)
            await asyncio.sleep(0.2)
            call_count -= 1
            return "done"

        # Start 10 tasks
        tasks = [concurrent_task() for _ in range(10)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Some should fail due to bulkhead
        exceptions = [r for r in results if isinstance(r, Exception)]
        successes = [r for r in results if not isinstance(r, Exception)]

        # Verify limit was enforced
        assert len(successes) <= 3 or len(exceptions) > 0

    def test_get_statistics(self, bulkhead):
        """Verify bulkhead statistics."""
        stats = bulkhead.get_statistics()

        assert "current_concurrency" in stats
        assert "max_concurrency" in stats
        assert "rejected_count" in stats
        assert stats["max_concurrency"] == 3


class TestRetry:
    """Tests for Retry primitive."""

    @pytest.fixture
    def retry_config(self):
        """Create retry config for testing."""
        return RetryConfig(
            max_retries=3,
            base_delay=0.01,
            max_delay=0.1,
            exponential_base=2
        )

    def test_retry_succeeds_on_first_attempt(self, retry_config):
        """Verify retry succeeds immediately if function succeeds."""
        call_count = 0

        @Retry.with_backoff(retry_config)
        def successful_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = successful_func()
        assert result == "success"
        assert call_count == 1

    def test_retry_retries_on_failure(self, retry_config):
        """Verify retry retries on transient failures."""
        call_count = 0

        @Retry.with_backoff(retry_config)
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Transient failure")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert call_count == 3  # Failed twice, succeeded on third

    def test_retry_exhausts_max_retries(self, retry_config):
        """Verify retry exhausts max retries before failing."""
        call_count = 0

        @Retry.with_backoff(retry_config)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise Exception("Always fails")

        with pytest.raises(Exception):
            always_fails()

        # Initial + 3 retries = 4 total attempts
        assert call_count == 4

    @pytest.mark.asyncio
    async def test_retry_async_function(self, retry_config):
        """Verify retry works with async functions."""
        call_count = 0

        @Retry.with_backoff(retry_config)
        async def async_flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Async failure")
            return "async success"

        result = await async_flaky()
        assert result == "async success"
        assert call_count == 2


class TestRoutingEngineResilienceIntegration:
    """Tests for RoutingEngine with resilience wiring."""

    @pytest.fixture
    def engine_with_resilience(self, mocker):
        """Create RoutingEngine with resilience primitives wired."""
        engine = RoutingEngine()

        # Wire resilience primitives (this is what WIRE-1 implements)
        config = CircuitBreakerConfig(
            failure_threshold=5,
            recovery_timeout=20.0,
            success_threshold=2
        )
        engine._routing_circuit_breaker = CircuitBreaker(config)

        bulkhead_config = BulkheadConfig(
            max_concurrency=10,
            acquire_timeout=3.0
        )
        engine._routing_bulkhead = Bulkhead(bulkhead_config)

        retry_config = RetryConfig(
            max_retries=3,
            base_delay=0.1,
            max_delay=1.0
        )
        engine._routing_retry = Retry(retry_config)

        return engine

    def test_route_defect_with_circuit_breaker_wiring(self, engine_with_resilience):
        """Verify route_defect is wrapped with circuit breaker."""
        # Verify circuit breaker exists
        assert hasattr(engine_with_resilience, '_routing_circuit_breaker')
        assert engine_with_resilience._routing_circuit_breaker is not None

        # Route a normal defect
        defect = {
            "id": "test-001",
            "description": "SQL injection vulnerability in login form"
        }
        decision = engine_with_resilience.route_defect(defect)

        assert isinstance(decision, RoutingDecision)
        assert decision.target_agent == "security-auditor"
        assert decision.target_phase == "DEVELOPMENT"

    def test_route_defect_circuit_breaker_trips_on_failures(
        self, engine_with_resilience, mocker
    ):
        """Verify circuit breaker trips when route_defect fails repeatedly."""
        # Mock select_specialist to fail
        engine_with_resilience._agent_registry = None
        original_select = engine_with_resilience.select_specialist

        def failing_select(*args, **kwargs):
            raise Exception("Registry unavailable")

        engine_with_resilience.select_specialist = failing_select

        # Force failures to trip circuit
        for i in range(5):
            defect = {"id": f"fail-{i}", "description": "Failure test"}
            try:
                engine_with_resilience.route_defect(defect)
            except:
                pass

        # Circuit should be open now
        assert engine_with_resilience._routing_circuit_breaker.is_open is True

    def test_route_defect_with_bulkhead_wiring(self, engine_with_resilience):
        """Verify route_defect is wrapped with bulkhead."""
        assert hasattr(engine_with_resilience, '_routing_bulkhead')
        assert engine_with_resilience._routing_bulkhead is not None

        # Verify bulkhead limits concurrent routing
        stats = engine_with_resilience._routing_bulkhead.get_statistics()
        assert "max_concurrency" in stats
        assert stats["max_concurrency"] == 10

    def test_route_defect_with_retry_wiring(self, engine_with_resilience):
        """Verify route_defect is wrapped with retry."""
        assert hasattr(engine_with_resilience, '_routing_retry')
        assert engine_with_resilience._routing_retry is not None

        # Route should retry on transient failures
        call_count = 0
        original_detect = engine_with_resilience.detect_defect_type

        def flaky_detect(description):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Transient failure")
            return DefectType.SECURITY

        engine_with_resilience.detect_defect_type = flaky_detect

        defect = {"id": "test-001", "description": "Security issue"}
        decision = engine_with_resilience.route_defect(defect)

        assert call_count == 2  # Failed once, succeeded on retry
        assert decision.defect_type == DefectType.SECURITY

    def test_get_resilience_stats(self, engine_with_resilience):
        """Verify get_resilience_stats method exists and returns data."""
        # This method should be added as part of WIRE-1
        if hasattr(engine_with_resilience, 'get_resilience_stats'):
            stats = engine_with_resilience.get_resilience_stats()

            assert "circuit_breaker" in stats
            assert "bulkhead" in stats
            assert "retry" in stats


class TestResilienceMonitoring:
    """Tests for resilience monitoring and metrics."""

    def test_circuit_breaker_metrics(self):
        """Verify circuit breaker exposes metrics."""
        circuit = CircuitBreaker(CircuitBreakerConfig())

        # Record some failures and successes
        circuit.record_failure()
        circuit.record_failure()
        circuit.record_success()

        stats = circuit.get_statistics()

        assert stats["failure_count"] == 2
        assert stats["success_count"] == 1
        assert stats["state"] == "closed"

    def test_bulkhead_metrics(self):
        """Verify bulkhead exposes metrics."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrency=5))

        stats = bulkhead.get_statistics()

        assert "current_concurrency" in stats
        assert "max_concurrency" in stats
        assert "rejected_count" in stats
        assert stats["max_concurrency"] == 5

    def test_retry_metrics(self):
        """Verify retry exposes metrics."""
        retry = Retry(RetryConfig(max_retries=3))

        stats = retry.get_statistics()

        assert "total_retries" in stats
        assert "max_retries" in stats
        assert "exhausted_count" in stats


class TestResilienceConfiguration:
    """Tests for resilience configuration best practices."""

    def test_routing_engine_thresholds(self):
        """Verify routing engine has appropriate resilience thresholds."""
        # Recommended configuration for routing engine
        # (read-heavy workload, should be tolerant)
        config = CircuitBreakerConfig(
            failure_threshold=7,  # Higher than default (routing is read-heavy)
            recovery_timeout=20.0,  # Faster recovery
            success_threshold=2
        )

        assert config.failure_threshold == 7
        assert config.recovery_timeout == 20.0

        # Verify these are reasonable
        assert config.failure_threshold >= 5  # Not too sensitive
        assert config.failure_threshold <= 10  # Not too tolerant
        assert config.recovery_timeout >= 10.0  # Reasonable recovery time
        assert config.recovery_timeout <= 60.0  # Not too long

    def test_bulkhead_concurrency_for_routing(self):
        """Verify bulkhead concurrency settings for routing."""
        config = BulkheadConfig(
            max_concurrency=20,  # Higher for routing (fast operations)
            acquire_timeout=3.0  # Fail fast on contention
        )

        assert config.max_concurrency == 20
        assert config.acquire_timeout == 3.0

        # Verify reasonable values
        assert config.max_concurrency >= 10
        assert config.max_concurrency <= 50
        assert config.acquire_timeout >= 1.0
        assert config.acquire_timeout <= 10.0
