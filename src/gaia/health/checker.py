"""
Health Checker for GAIA.

This module provides the HealthChecker class for managing and executing
health checks across all GAIA components. It supports liveness, readiness,
and startup probes, as well as custom health checks.

Features:
    - Liveness probes: Is the component running?
    - Readiness probes: Is the component ready to serve?
    - Startup probes: Is the component still starting up?
    - Custom health checks: User-defined health checks
    - Aggregated health status with degradation detection
    - Thread-safe operations for concurrent environments
    - Integration with MetricsCollector for health metrics

Example:
    >>> from gaia.health.checker import HealthChecker
    >>> from gaia.health.probes import MemoryProbe, DiskProbe
    >>>
    >>> checker = HealthChecker(service_name="gaia-api")
    >>> checker.register_probe(MemoryProbe())
    >>> checker.register_probe(DiskProbe())
    >>>
    >>> # Check liveness
    >>> liveness = await checker.check_liveness()
    >>> print(f"Service alive: {liveness.is_healthy}")
    >>>
    >>> # Get aggregated health
    >>> health = await checker.get_aggregated_health()
    >>> print(health.summary())
"""

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from gaia.health.models import (
    AggregatedHealthStatus,
    HealthCheckResult,
    HealthStatus,
)
from gaia.health.probes import BaseProbe, ProbeConfig, ProbeResult
from gaia.utils.logging import get_logger

logger = get_logger(__name__)

# Type alias for custom health check functions
HealthCheckFn = Union[
    Callable[[], HealthCheckResult],
    Callable[[], Awaitable[HealthCheckResult]],
]


@dataclass
class RegisteredCheck:
    """
    Container for a registered health check.

    Attributes:
        name: Unique identifier for the check
        check_fn: The health check function
        is_probe: Whether this is a probe-based check
        probe: Optional probe instance if is_probe is True
        is_async: Whether the check function is async
        timeout_seconds: Timeout for check execution
        enabled: Whether the check is enabled
        tags: Tags for categorizing checks
        last_result: Last check result
        last_check_time: When the check was last executed
    """

    name: str
    check_fn: Optional[HealthCheckFn] = None
    is_probe: bool = False
    probe: Optional[BaseProbe] = None
    is_async: bool = False
    timeout_seconds: float = 5.0
    enabled: bool = True
    tags: List[str] = field(default_factory=list)
    last_result: Optional[HealthCheckResult] = None
    last_check_time: Optional[datetime] = None


class HealthChecker:
    """
    Centralized health monitoring for GAIA components.

    HealthChecker provides comprehensive health monitoring including:
    - Liveness probes: Is the component running?
    - Readiness probes: Is the component ready to serve traffic?
    - Startup probes: Is the component still initializing?
    - Custom health checks: User-defined checks for specific components

    Thread Safety:
        All public methods are thread-safe and can be called concurrently.
        The checker uses RLock for synchronization.

    Integration:
        - Integrates with MetricsCollector for health metrics
        - Supports Prometheus-style health endpoints
        - Provides aggregated health status with degradation detection

    Example:
        >>> checker = HealthChecker(service_name="gaia-api")
        >>>
        >>> # Register built-in probes
        >>> checker.register_probe(MemoryProbe())
        >>> checker.register_probe(DiskProbe())
        >>>
        >>> # Register custom check
        >>> def my_check() -> HealthCheckResult:
        ...     # Custom logic here
        ...     return HealthCheckResult.healthy("my_check", "OK")
        >>> checker.register_check("custom", my_check)
        >>>
        >>> # Perform health checks
        >>> liveness = await checker.check_liveness()
        >>> readiness = await checker.check_readiness()
        >>> aggregated = await checker.get_aggregated_health()
    """

    def __init__(
        self,
        service_name: str = "gaia",
        metrics_collector: Optional[Any] = None,
    ):
        """
        Initialize HealthChecker.

        Args:
            service_name: Name of the service for identification
            metrics_collector: Optional MetricsCollector for health metrics

        Example:
            >>> checker = HealthChecker(service_name="gaia-api")
        """
        self._service_name = service_name
        self._metrics_collector = metrics_collector

        # Thread-safe storage
        self._lock = threading.RLock()

        # Registered checks
        self._checks: Dict[str, RegisteredCheck] = {}

        # Probe instances
        self._probes: Dict[str, BaseProbe] = {}

        # Startup state
        self._startup_complete = False
        self._startup_checks: List[str] = []

        # Readiness state
        self._ready = False
        self._readiness_checks: List[str] = []

        # Health check history
        self._history: Dict[str, List[HealthCheckResult]] = {}
        self._max_history_size = 100

        logger.info(
            f"HealthChecker initialized for service: {service_name}",
            extra={"service_name": service_name},
        )

    @property
    def service_name(self) -> str:
        """Get service name."""
        return self._service_name

    @property
    def registered_checks(self) -> List[str]:
        """Get list of registered check names."""
        with self._lock:
            return list(self._checks.keys())

    @property
    def is_startup_complete(self) -> bool:
        """Check if startup is complete."""
        return self._startup_complete

    @property
    def is_ready(self) -> bool:
        """Check if service is ready."""
        return self._ready

    def register_probe(
        self,
        probe: BaseProbe,
        tags: Optional[List[str]] = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        """
        Register a probe for health checking.

        Args:
            probe: Probe instance to register
            tags: Optional tags for categorization
            timeout_seconds: Timeout for probe execution

        Example:
            >>> checker.register_probe(MemoryProbe())
            >>> checker.register_probe(DiskProbe(), tags=["system", "resources"])
        """
        with self._lock:
            self._probes[probe.name] = probe
            self._checks[probe.name] = RegisteredCheck(
                name=probe.name,
                is_probe=True,
                probe=probe,
                timeout_seconds=timeout_seconds,
                tags=tags or [],
                enabled=probe.is_enabled,
            )
            logger.debug(f"Registered probe: {probe.name}")

    def register_check(
        self,
        name: str,
        check_fn: HealthCheckFn,
        tags: Optional[List[str]] = None,
        timeout_seconds: float = 5.0,
        is_async: bool = False,
    ) -> None:
        """
        Register a custom health check.

        Args:
            name: Unique identifier for the check
            check_fn: Health check function (sync or async)
            tags: Optional tags for categorization
            timeout_seconds: Timeout for check execution
            is_async: Whether the check function is async

        Example:
            >>> def db_check() -> HealthCheckResult:
            ...     try:
            ...         conn.execute("SELECT 1")
            ...         return HealthCheckResult.healthy("db", "OK")
            ...     except Exception as e:
            ...         return HealthCheckResult.unhealthy("db", str(e))
            >>> checker.register_check("database", db_check)
        """
        with self._lock:
            self._checks[name] = RegisteredCheck(
                name=name,
                check_fn=check_fn,
                is_async=is_async,
                timeout_seconds=timeout_seconds,
                tags=tags or [],
                enabled=True,
            )
            logger.debug(f"Registered check: {name}")

    def register_startup_check(
        self,
        name: str,
        check_fn: HealthCheckFn,
        timeout_seconds: float = 10.0,
    ) -> None:
        """
        Register a startup health check.

        Startup checks are performed during application startup to
        verify critical components are initialized correctly.

        Args:
            name: Unique identifier for the check
            check_fn: Health check function
            timeout_seconds: Timeout for check execution

        Example:
            >>> def db_initialized() -> HealthCheckResult:
            ...     if db.is_connected:
            ...         return HealthCheckResult.healthy("db_init", "Connected")
            ...     return HealthCheckResult.unhealthy("db_init", "Not connected")
            >>> checker.register_startup_check("database_init", db_initialized)
        """
        with self._lock:
            self.register_check(name, check_fn, tags=["startup"], timeout_seconds=timeout_seconds)
            self._startup_checks.append(name)
            logger.debug(f"Registered startup check: {name}")

    def register_readiness_check(
        self,
        name: str,
        check_fn: HealthCheckFn,
        timeout_seconds: float = 5.0,
    ) -> None:
        """
        Register a readiness health check.

        Readiness checks determine if the service is ready to serve
        traffic. Unlike liveness, readiness can temporarily fail
        during maintenance or high load.

        Args:
            name: Unique identifier for the check
            check_fn: Health check function
            timeout_seconds: Timeout for check execution

        Example:
            >>> def can_serve_traffic() -> HealthCheckResult:
            ...     if load < 0.9:
            ...         return HealthCheckResult.healthy("load", "OK")
            ...     return HealthCheckResult.degraded("load", "High load")
            >>> checker.register_readiness_check("load_check", can_serve_traffic)
        """
        with self._lock:
            self.register_check(name, check_fn, tags=["readiness"], timeout_seconds=timeout_seconds)
            self._readiness_checks.append(name)
            logger.debug(f"Registered readiness check: {name}")

    def mark_startup_complete(self) -> None:
        """
        Mark startup as complete.

        Call this after all startup checks have passed and the
        service is fully initialized.

        Example:
            >>> checker.register_startup_check("init", init_check)
            >>> result = await checker.check_startup()
            >>> if result.is_healthy:
            ...     checker.mark_startup_complete()
        """
        with self._lock:
            self._startup_complete = True
            logger.info("Startup complete marked")

    async def check_liveness(self) -> AggregatedHealthStatus:
        """
        Perform liveness health check.

        Liveness checks verify the service is running and responsive.
        A failing liveness check typically means the service should
        be restarted.

        Returns:
            AggregatedHealthStatus from all liveness-relevant checks

        Example:
            >>> status = await checker.check_liveness()
            >>> if not status.is_operational:
            ...     logger.error("Service not alive!")
        """
        start_time = time.perf_counter()

        # Liveness is typically all registered checks
        liveness_checks = [
            name for name, check in self._checks.items()
            if check.enabled and "liveness" not in check.tags or not check.tags
        ]

        # If no specific liveness checks, check all enabled
        if not liveness_checks:
            liveness_checks = [name for name, check in self._checks.items() if check.enabled]

        results = await self._execute_checks(liveness_checks)
        status = AggregatedHealthStatus.from_results(results)

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Record metric if collector available
        self._record_health_metric("liveness", status, elapsed_ms)

        logger.debug(
            f"Liveness check: {status.overall_status.name} "
            f"({status.healthy_checks}/{status.total_checks} healthy)",
        )

        return status

    async def check_readiness(self) -> AggregatedHealthStatus:
        """
        Perform readiness health check.

        Readiness checks verify the service is ready to serve traffic.
        A failing readiness check means the service should not receive
        new requests but doesn't need restarting.

        Returns:
            AggregatedHealthStatus from all readiness-relevant checks

        Example:
            >>> status = await checker.check_readiness()
            >>> if status.is_healthy:
            ...     # Service ready for traffic
            ...     pass
        """
        start_time = time.perf_counter()

        # Readiness includes all readiness checks plus general checks
        readiness_check_names = list(set(self._readiness_checks))

        # Also include general health if no specific readiness checks
        if not readiness_check_names:
            readiness_check_names = [
                name for name, check in self._checks.items()
                if check.enabled
            ]

        results = await self._execute_checks(readiness_check_names)
        status = AggregatedHealthStatus.from_results(results)

        # Update ready state
        with self._lock:
            self._ready = status.is_healthy

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        self._record_health_metric("readiness", status, elapsed_ms)

        logger.debug(
            f"Readiness check: {status.overall_status.name} "
            f"({status.healthy_checks}/{status.total_checks} healthy)",
        )

        return status

    async def check_startup(self) -> AggregatedHealthStatus:
        """
        Perform startup health check.

        Startup checks verify critical components initialized correctly
        during application startup. These checks run before the service
        accepts traffic.

        Returns:
            AggregatedHealthStatus from all startup checks

        Example:
            >>> status = await checker.check_startup()
            >>> if status.is_healthy:
            ...     checker.mark_startup_complete()
        """
        start_time = time.perf_counter()

        # Use registered startup checks or all checks if none specific
        startup_check_names = self._startup_checks if self._startup_checks else list(self._checks.keys())

        results = await self._execute_checks(startup_check_names)
        status = AggregatedHealthStatus.from_results(results)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        self._record_health_metric("startup", status, elapsed_ms)

        logger.debug(
            f"Startup check: {status.overall_status.name} "
            f"({status.healthy_checks}/{status.total_checks} healthy)",
        )

        return status

    async def check_component(self, name: str) -> HealthCheckResult:
        """
        Check a specific component by name.

        Args:
            name: Name of the component/check to verify

        Returns:
            HealthCheckResult for the specified component

        Raises:
            ValueError: If component not found

        Example:
            >>> result = await checker.check_component("memory_probe")
            >>> print(f"Memory: {result.status}")
        """
        with self._lock:
            check = self._checks.get(name)
            if check is None:
                raise ValueError(f"Unknown component: {name}")
            if not check.enabled:
                return HealthCheckResult(
                    check_name=name,
                    status=HealthStatus.UNKNOWN,
                    message="Check disabled",
                )

        # Execute the check
        result = await self._execute_single_check(check)

        # Update history
        self._update_history(name, result)

        return result

    async def get_aggregated_health(self) -> AggregatedHealthStatus:
        """
        Get aggregated health status across all checks.

        Combines results from all registered checks into a single
        overall health status.

        Returns:
            AggregatedHealthStatus representing overall health

        Example:
            >>> health = await checker.get_aggregated_health()
            >>> print(health.summary())
            Health Status: HEALTHY (100.0% healthy)
        """
        start_time = time.perf_counter()

        # Get all enabled checks
        check_names = [name for name, check in self._checks.items() if check.enabled]

        results = await self._execute_checks(check_names)
        status = AggregatedHealthStatus.from_results(results)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        self._record_health_metric("aggregated", status, elapsed_ms)

        logger.info(
            f"Aggregated health: {status.overall_status.name} "
            f"({status.health_percentage:.1f}% healthy)",
        )

        return status

    def get_health_status(self, name: str) -> Optional[HealthCheckResult]:
        """
        Get the last known health status for a component.

        Returns the cached result from the last check without
        executing a new check.

        Args:
            name: Name of the component

        Returns:
            Last HealthCheckResult or None if never checked

        Example:
            >>> result = checker.get_health_status("memory_probe")
            >>> if result and result.is_healthy:
            ...     print("Memory OK")
        """
        with self._lock:
            check = self._checks.get(name)
            return check.last_result if check else None

    def get_all_health_statuses(self) -> Dict[str, HealthCheckResult]:
        """
        Get last known health status for all components.

        Returns:
            Dictionary mapping component names to their last results

        Example:
            >>> statuses = checker.get_all_health_statuses()
            >>> for name, result in statuses.items():
            ...     print(f"{name}: {result.status}")
        """
        with self._lock:
            return {
                name: check.last_result
                for name, check in self._checks.items()
                if check.last_result is not None
            }

    async def _execute_checks(
        self,
        check_names: List[str],
    ) -> List[HealthCheckResult]:
        """
        Execute multiple health checks concurrently.

        Args:
            check_names: List of check names to execute

        Returns:
            List of HealthCheckResult from all checks
        """
        tasks = []
        for name in check_names:
            with self._lock:
                check = self._checks.get(name)
                if check and check.enabled:
                    tasks.append(self._execute_single_check(check))

        if not tasks:
            return []

        # Execute checks concurrently with timeout
        results = []
        for task in asyncio.as_completed(tasks, timeout=30.0):
            try:
                result = await task
                results.append(result)
            except asyncio.TimeoutError:
                results.append(HealthCheckResult(
                    check_name="unknown",
                    status=HealthStatus.UNHEALTHY,
                    message="Check timed out",
                    response_time_ms=30000,
                ))
            except Exception as e:
                results.append(HealthCheckResult(
                    check_name="unknown",
                    status=HealthStatus.UNHEALTHY,
                    message=f"Check failed: {str(e)}",
                ))

        return results

    async def _execute_single_check(
        self,
        check: RegisteredCheck,
    ) -> HealthCheckResult:
        """
        Execute a single health check.

        Args:
            check: RegisteredCheck to execute

        Returns:
            HealthCheckResult from the check
        """
        start_time = time.perf_counter()

        try:
            if check.is_probe and check.probe:
                # Execute probe
                if asyncio.iscoroutinefunction(check.probe.check):
                    result = await asyncio.wait_for(
                        check.probe.check(),
                        timeout=check.timeout_seconds,
                    )
                    result = result.to_health_check_result()
                else:
                    # Run sync probe in executor
                    loop = asyncio.get_event_loop()
                    probe_result = await asyncio.wait_for(
                        loop.run_in_executor(None, check.probe.check),
                        timeout=check.timeout_seconds,
                    )
                    result = probe_result.to_health_check_result()

            elif check.check_fn:
                # Execute custom check function
                if check.is_async:
                    result = await asyncio.wait_for(
                        check.check_fn(),  # type: ignore
                        timeout=check.timeout_seconds,
                    )
                else:
                    loop = asyncio.get_event_loop()
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, check.check_fn),  # type: ignore
                        timeout=check.timeout_seconds,
                    )
            else:
                result = HealthCheckResult(
                    check_name=check.name,
                    status=HealthStatus.UNKNOWN,
                    message="No check function registered",
                )

            # Ensure result has correct check name
            if result.check_name != check.name:
                result = HealthCheckResult(
                    check_name=check.name,
                    status=result.status,
                    message=result.message,
                    response_time_ms=result.response_time_ms,
                    metadata=result.metadata,
                )

        except asyncio.TimeoutError:
            result = HealthCheckResult(
                check_name=check.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Check timed out after {check.timeout_seconds}s",
                response_time_ms=check.timeout_seconds * 1000,
            )
        except Exception as e:
            result = HealthCheckResult(
                check_name=check.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {str(e)}",
                response_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        # Update check state
        with self._lock:
            check.last_result = result
            check.last_check_time = datetime.now(timezone.utc)

        return result

    def _update_history(
        self,
        name: str,
        result: HealthCheckResult,
    ) -> None:
        """
        Update check history.

        Args:
            name: Check name
            result: Check result to record
        """
        with self._lock:
            if name not in self._history:
                self._history[name] = []

            self._history[name].append(result)

            # Trim history if too long
            if len(self._history[name]) > self._max_history_size:
                self._history[name] = self._history[name][-self._max_history_size:]

    def _record_health_metric(
        self,
        check_type: str,
        status: AggregatedHealthStatus,
        elapsed_ms: float,
    ) -> None:
        """
        Record health check as metric.

        Args:
            check_type: Type of check (liveness, readiness, etc.)
            status: Aggregated health status
            elapsed_ms: Total check duration
        """
        if self._metrics_collector is None:
            return

        try:
            # Record health percentage as gauge
            self._metrics_collector.gauge(f"{self._service_name}_health_{check_type}_percent").set(
                status.health_percentage
            )

            # Record check duration as histogram
            self._metrics_collector.histogram(
                f"{self._service_name}_health_{check_type}_duration_ms"
            ).observe(elapsed_ms)

            # Record status as counter
            status_label = status.overall_status.name.lower()
            self._metrics_collector.counter(
                f"{self._service_name}_health_{check_type}_status",
                label_names=["status"],
            ).inc(labels={"status": status_label})

        except Exception as e:
            logger.warning(f"Failed to record health metric: {e}")

    def set_check_enabled(self, name: str, enabled: bool) -> None:
        """
        Enable or disable a health check.

        Args:
            name: Check name
            enabled: Whether to enable the check

        Raises:
            ValueError: If check not found

        Example:
            >>> checker.set_check_enabled("disk_probe", False)
        """
        with self._lock:
            if name not in self._checks:
                raise ValueError(f"Unknown check: {name}")
            self._checks[name].enabled = enabled
            logger.debug(f"Check {name} {'enabled' if enabled else 'disabled'}")

    def get_check_history(
        self,
        name: str,
        limit: int = 10,
    ) -> List[HealthCheckResult]:
        """
        Get recent check history.

        Args:
            name: Check name
            limit: Maximum results to return

        Returns:
            List of recent HealthCheckResult

        Example:
            >>> history = checker.get_check_history("memory_probe", limit=5)
            >>> for result in history:
            ...     print(f"{result.timestamp}: {result.status}")
        """
        with self._lock:
            history = self._history.get(name, [])
            return history[-limit:]

    async def shutdown(self) -> None:
        """
        Graceful shutdown of health checker.

        Performs final health check and cleans up resources.

        Example:
            >>> await checker.shutdown()
        """
        logger.info("Shutting down HealthChecker")

        # Perform final health check
        try:
            final_status = await self.get_aggregated_health()
            logger.info(f"Final health status: {final_status.overall_status.name}")
        except Exception as e:
            logger.warning(f"Final health check failed: {e}")

        # Clear history
        with self._lock:
            self._history.clear()
            self._checks.clear()
            self._probes.clear()

        logger.info("HealthChecker shutdown complete")


# Global health checker instance
_default_health_checker: Optional[HealthChecker] = None
_health_checker_lock = threading.Lock()


def get_health_checker(service_name: str = "gaia") -> HealthChecker:
    """
    Get the global health checker instance.

    Creates a new instance if one doesn't exist.

    Args:
        service_name: Service name for the checker

    Returns:
        Global HealthChecker instance

    Example:
        >>> checker = get_health_checker("my-service")
        >>> checker.register_probe(MemoryProbe())
    """
    global _default_health_checker
    with _health_checker_lock:
        if _default_health_checker is None:
            _default_health_checker = HealthChecker(service_name=service_name)
        return _default_health_checker


def reset_health_checker() -> None:
    """
    Reset the global health checker instance.

    Primarily for testing purposes.

    Example:
        >>> reset_health_checker()
    """
    global _default_health_checker
    with _health_checker_lock:
        if _default_health_checker:
            asyncio.run(_default_health_checker.shutdown())
            _default_health_checker = None
