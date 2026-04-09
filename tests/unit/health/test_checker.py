"""
Unit tests for HealthChecker.

Covers:
- HealthChecker initialization
- Probe registration
- Custom check registration
- Liveness, readiness, startup checks
- Aggregated health status
- Thread safety
- Async operations
"""

import asyncio
import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from gaia.health.checker import (
    HealthChecker,
    RegisteredCheck,
    get_health_checker,
    reset_health_checker,
)
from gaia.health.models import HealthCheckResult, HealthStatus, AggregatedHealthStatus
from gaia.health.probes import MemoryProbe, DiskProbe, BaseProbe, ProbeConfig


class TestHealthCheckerInit:
    """Test HealthChecker initialization."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        checker = HealthChecker()

        assert checker.service_name == "gaia"
        assert checker.registered_checks == []
        assert checker.is_startup_complete is False
        assert checker.is_ready is False

    def test_init_custom_service_name(self):
        """Should initialize with custom service name."""
        checker = HealthChecker(service_name="test-service")

        assert checker.service_name == "test-service"

    def test_init_with_metrics_collector(self):
        """Should initialize with metrics collector."""
        mock_collector = type('MockCollector', (), {
            'gauge': lambda self, name: type('MockGauge', (), {'set': lambda self, v: None})(),
            'histogram': lambda self, name: type('MockHistogram', (), {'observe': lambda self, v: None})(),
            'counter': lambda self, name, **kw: type('MockCounter', (), {'inc': lambda self, **kw: None})(),
        })()

        checker = HealthChecker(
            service_name="test-service",
            metrics_collector=mock_collector,
        )

        assert checker._metrics_collector is mock_collector


class TestProbeRegistration:
    """Test probe registration."""

    def test_register_probe(self):
        """Should register probe successfully."""
        checker = HealthChecker()
        probe = MemoryProbe()

        checker.register_probe(probe)

        assert "memory_probe" in checker.registered_checks
        assert probe.name in checker._probes

    def test_register_probe_with_tags(self):
        """Should register probe with tags."""
        checker = HealthChecker()
        probe = DiskProbe()

        checker.register_probe(probe, tags=["system", "resources"])

        check = checker._checks.get(probe.name)
        assert check is not None
        assert "system" in check.tags
        assert "resources" in check.tags

    def test_register_probe_with_timeout(self):
        """Should register probe with custom timeout."""
        checker = HealthChecker()
        probe = MemoryProbe()

        checker.register_probe(probe, timeout_seconds=10.0)

        check = checker._checks.get("memory_probe")
        assert check.timeout_seconds == 10.0

    def test_register_multiple_probes(self):
        """Should register multiple probes."""
        checker = HealthChecker()

        checker.register_probe(MemoryProbe())
        checker.register_probe(DiskProbe())

        assert len(checker.registered_checks) == 2
        assert "memory_probe" in checker.registered_checks
        # DiskProbe name includes the path, so check if any disk probe exists
        assert any("disk_probe" in name for name in checker.registered_checks)


class TestCustomCheckRegistration:
    """Test custom check registration."""

    def test_register_check_sync(self):
        """Should register sync check."""
        checker = HealthChecker()

        def my_check() -> HealthCheckResult:
            return HealthCheckResult.healthy("my_check", "OK")

        checker.register_check("my_check", my_check)

        assert "my_check" in checker.registered_checks
        assert checker._checks["my_check"].is_async is False

    def test_register_check_async(self):
        """Should register async check."""
        checker = HealthChecker()

        async def my_check() -> HealthCheckResult:
            return HealthCheckResult.healthy("my_check", "OK")

        checker.register_check("my_check", my_check, is_async=True)

        assert "my_check" in checker.registered_checks
        assert checker._checks["my_check"].is_async is True

    def test_register_check_with_tags(self):
        """Should register check with tags."""
        checker = HealthChecker()

        def my_check() -> HealthCheckResult:
            return HealthCheckResult.healthy("my_check", "OK")

        checker.register_check("my_check", my_check, tags=["custom", "important"])

        check = checker._checks["my_check"]
        assert "custom" in check.tags
        assert "important" in check.tags

    def test_register_check_with_timeout(self):
        """Should register check with custom timeout."""
        checker = HealthChecker()

        def my_check() -> HealthCheckResult:
            return HealthCheckResult.healthy("my_check", "OK")

        checker.register_check("my_check", my_check, timeout_seconds=15.0)

        assert checker._checks["my_check"].timeout_seconds == 15.0


class TestStartupChecks:
    """Test startup check functionality."""

    def test_register_startup_check(self):
        """Should register startup check."""
        checker = HealthChecker()

        def init_check() -> HealthCheckResult:
            return HealthCheckResult.healthy("init", "Initialized")

        checker.register_startup_check("database_init", init_check)

        assert "database_init" in checker._startup_checks
        assert "database_init" in checker.registered_checks

    def test_register_readiness_check(self):
        """Should register readiness check."""
        checker = HealthChecker()

        def readiness_check() -> HealthCheckResult:
            return HealthCheckResult.healthy("ready", "Ready")

        checker.register_readiness_check("load_check", readiness_check)

        assert "load_check" in checker._readiness_checks
        assert "load_check" in checker.registered_checks

    def test_mark_startup_complete(self):
        """Should mark startup as complete."""
        checker = HealthChecker()

        assert checker.is_startup_complete is False

        checker.mark_startup_complete()

        assert checker.is_startup_complete is True


@pytest.mark.asyncio
class TestLivenessChecks:
    """Test liveness check functionality."""

    async def test_check_liveness_no_checks(self):
        """Should handle liveness check with no registered checks."""
        checker = HealthChecker()

        status = await checker.check_liveness()

        assert isinstance(status, AggregatedHealthStatus)
        assert status.total_checks == 0

    async def test_check_liveness_with_probes(self):
        """Should perform liveness check with probes."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        status = await checker.check_liveness()

        assert isinstance(status, AggregatedHealthStatus)
        assert status.total_checks >= 1

    async def test_check_liveness_records_metric(self):
        """Should record metric for liveness check."""
        mock_collector = type('MockCollector', (), {
            'gauge': lambda self, name: type('MockGauge', (), {'set': lambda self, v: None})(),
            'histogram': lambda self, name: type('MockHistogram', (), {'observe': lambda self, v: None})(),
            'counter': lambda self, name, **kw: type('MockCounter', (), {'inc': lambda self, **kw: None})(),
        })()

        checker = HealthChecker(
            service_name="test",
            metrics_collector=mock_collector,
        )
        checker.register_probe(MemoryProbe())

        # Should not raise
        status = await checker.check_liveness()
        assert status is not None


@pytest.mark.asyncio
class TestReadinessChecks:
    """Test readiness check functionality."""

    async def test_check_readiness_no_checks(self):
        """Should handle readiness check with no registered checks."""
        checker = HealthChecker()

        status = await checker.check_readiness()

        assert isinstance(status, AggregatedHealthStatus)

    async def test_check_readiness_updates_state(self):
        """Should update ready state based on check."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        await checker.check_readiness()

        # Ready state should be updated
        assert isinstance(checker.is_ready, bool)


@pytest.mark.asyncio
class TestStartupCheckExecution:
    """Test startup check execution."""

    async def test_check_startup(self):
        """Should perform startup check."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        status = await checker.check_startup()

        assert isinstance(status, AggregatedHealthStatus)
        assert status.total_checks >= 1


@pytest.mark.asyncio
class TestComponentCheck:
    """Test individual component check."""

    async def test_check_component_exists(self):
        """Should check existing component."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        result = await checker.check_component("memory_probe")

        assert isinstance(result, HealthCheckResult)
        assert result.check_name == "memory_probe"

    async def test_check_component_not_found(self):
        """Should raise for unknown component."""
        checker = HealthChecker()

        with pytest.raises(ValueError, match="Unknown component"):
            await checker.check_component("nonexistent")

    async def test_check_component_disabled(self):
        """Should return UNKNOWN for disabled check."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())
        checker.set_check_enabled("memory_probe", False)

        result = await checker.check_component("memory_probe")

        assert result.status == HealthStatus.UNKNOWN


@pytest.mark.asyncio
class TestAggregatedHealth:
    """Test aggregated health functionality."""

    async def test_get_aggregated_health(self):
        """Should get aggregated health status."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        status = await checker.get_aggregated_health()

        assert isinstance(status, AggregatedHealthStatus)
        assert status.total_checks >= 1

    async def test_get_aggregated_health_empty(self):
        """Should handle aggregated health with no checks."""
        checker = HealthChecker()

        status = await checker.get_aggregated_health()

        assert isinstance(status, AggregatedHealthStatus)
        assert status.total_checks == 0


class TestHealthStatusRetrieval:
    """Test health status retrieval."""

    def test_get_health_status(self):
        """Should get last known status."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        # Initially None
        status = checker.get_health_status("memory_probe")
        assert status is None

    def test_get_all_health_statuses(self):
        """Should get all statuses."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())
        checker.register_probe(DiskProbe())

        statuses = checker.get_all_health_statuses()

        assert isinstance(statuses, dict)


class TestCheckHistory:
    """Test check history functionality."""

    def test_get_check_history_empty(self):
        """Should return empty history initially."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        history = checker.get_check_history("memory_probe")

        assert history == []

    @pytest.mark.asyncio
    async def test_get_check_history_after_check(self):
        """Should return history after check."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        await checker.check_component("memory_probe")

        history = checker.get_check_history("memory_probe")

        assert len(history) >= 1
        assert isinstance(history[0], HealthCheckResult)

    def test_get_check_history_with_limit(self):
        """Should respect limit parameter."""
        checker = HealthChecker()

        # Manually add history
        for i in range(20):
            checker._history["test"] = checker._history.get("test", [])
            checker._history["test"].append(
                HealthCheckResult("test", HealthStatus.HEALTHY, f"Check {i}")
            )

        history = checker.get_check_history("test", limit=5)

        assert len(history) == 5


class TestCheckEnableDisable:
    """Test check enable/disable functionality."""

    def test_set_check_enabled(self):
        """Should enable/disable check."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        checker.set_check_enabled("memory_probe", False)
        assert checker._checks["memory_probe"].enabled is False

        checker.set_check_enabled("memory_probe", True)
        assert checker._checks["memory_probe"].enabled is True

    def test_set_check_enabled_unknown(self):
        """Should raise for unknown check."""
        checker = HealthChecker()

        with pytest.raises(ValueError, match="Unknown check"):
            checker.set_check_enabled("nonexistent", False)


class TestHealthCheckerThreadSafety:
    """Test thread safety of HealthChecker."""

    def test_concurrent_probe_registration(self):
        """Should handle concurrent probe registration safely."""
        checker = HealthChecker()
        errors = []

        def register_probe(probe_id: int):
            try:
                probe = MemoryProbe()
                probe._config.name = f"memory_probe_{probe_id}"
                checker.register_probe(probe)
            except Exception as e:
                errors.append((probe_id, str(e)))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(register_probe, i) for i in range(10)]
            for f in futures:
                f.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"

    def test_concurrent_check_execution_sync(self):
        """Should handle concurrent sync checks safely."""
        checker = HealthChecker()

        def my_check() -> HealthCheckResult:
            time.sleep(0.01)
            return HealthCheckResult.healthy("concurrent", "OK")

        checker.register_check("concurrent", my_check)

        errors = []
        results = []
        lock = threading.Lock()

        def run_check():
            try:
                async def runner():
                    result = await checker.check_component("concurrent")
                    with lock:
                        results.append(result)
                asyncio.run(runner())
            except Exception as e:
                with lock:
                    errors.append(str(e))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(run_check) for _ in range(10)]
            for f in futures:
                f.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 10

    @pytest.mark.asyncio
    async def test_concurrent_100_checks(self):
        """Should handle 100+ concurrent checks safely."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        errors = []
        results = []
        lock = asyncio.Lock()

        async def worker(worker_id: int):
            try:
                result = await checker.check_component("memory_probe")
                async with lock:
                    results.append(result)
            except Exception as e:
                async with lock:
                    errors.append((worker_id, str(e)))

        tasks = [asyncio.create_task(worker(i)) for i in range(100)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Concurrency errors: {errors}"
        assert len(results) == 100


class TestGlobalHealthChecker:
    """Test global health checker functions."""

    def teardown_method(self):
        """Reset global checker after each test."""
        reset_health_checker()

    def test_get_health_checker_singleton(self):
        """Should return singleton instance."""
        checker1 = get_health_checker("test")
        checker2 = get_health_checker("test")

        assert checker1 is checker2

    def test_get_health_checker_different_name(self):
        """Should reuse existing instance regardless of name."""
        checker1 = get_health_checker("test1")
        checker2 = get_health_checker("test2")

        # Returns same instance (first created)
        assert checker1 is checker2

    def test_reset_health_checker(self):
        """Should reset global instance."""
        checker1 = get_health_checker("test")
        reset_health_checker()
        checker2 = get_health_checker("test")

        assert checker1 is not checker2


@pytest.mark.asyncio
class TestShutdown:
    """Test shutdown functionality."""

    async def test_shutdown(self):
        """Should shutdown gracefully."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        # Should not raise
        await checker.shutdown()

        # Checks should be cleared
        assert len(checker._checks) == 0
        assert len(checker._probes) == 0

    async def test_shutdown_final_health_check(self):
        """Should perform final health check on shutdown."""
        checker = HealthChecker()
        checker.register_probe(MemoryProbe())

        # Should not raise
        await checker.shutdown()


class TestRegisteredCheck:
    """Test RegisteredCheck dataclass."""

    def test_registered_check_init(self):
        """Should initialize RegisteredCheck correctly."""
        check = RegisteredCheck(
            name="test_check",
            is_probe=False,
            is_async=False,
            timeout_seconds=5.0,
            enabled=True,
            tags=["test"],
        )

        assert check.name == "test_check"
        assert check.is_probe is False
        assert check.is_async is False
        assert check.timeout_seconds == 5.0
        assert check.enabled is True
        assert check.tags == ["test"]
        assert check.last_result is None
        assert check.last_check_time is None


@pytest.mark.asyncio
class TestCheckTimeout:
    """Test check timeout handling."""

    async def test_check_timeout(self):
        """Should handle check timeout."""
        checker = HealthChecker()

        def slow_check() -> HealthCheckResult:
            time.sleep(10)  # Very slow
            return HealthCheckResult.healthy("slow", "OK")

        checker.register_check("slow", slow_check, timeout_seconds=0.1)

        result = await checker.check_component("slow")

        assert result.status == HealthStatus.UNHEALTHY
        assert "timed out" in result.message.lower()

    async def test_async_check_timeout(self):
        """Should handle async check timeout."""
        checker = HealthChecker()

        async def slow_async_check() -> HealthCheckResult:
            await asyncio.sleep(10)
            return HealthCheckResult.healthy("slow", "OK")

        checker.register_check("slow_async", slow_async_check, is_async=True, timeout_seconds=0.1)

        result = await checker.check_component("slow_async")

        assert result.status == HealthStatus.UNHEALTHY
        assert "timed out" in result.message.lower()


@pytest.mark.asyncio
class TestCheckExceptionHandling:
    """Test exception handling in checks."""

    async def test_sync_check_exception(self):
        """Should handle sync check exception."""
        checker = HealthChecker()

        def failing_check() -> HealthCheckResult:
            raise ValueError("Test error")

        checker.register_check("failing", failing_check)

        result = await checker.check_component("failing")

        assert result.status == HealthStatus.UNHEALTHY
        assert "Test error" in result.message

    async def test_async_check_exception(self):
        """Should handle async check exception."""
        checker = HealthChecker()

        async def failing_async_check() -> HealthCheckResult:
            raise ValueError("Async test error")

        checker.register_check("failing_async", failing_async_check, is_async=True)

        result = await checker.check_component("failing_async")

        assert result.status == HealthStatus.UNHEALTHY
        assert "Async test error" in result.message
