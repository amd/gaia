"""
Health Monitoring Data Models for GAIA.

This module defines the core data structures for health monitoring,
including health status enumeration, health check results, and
aggregated health status.

All models are designed for immutability and thread-safety.

Example:
    >>> from gaia.health.models import HealthStatus, HealthCheckResult
    >>> from datetime import datetime, timezone
    >>> result = HealthCheckResult(
    ...     check_name="llm_connectivity",
    ...     status=HealthStatus.HEALTHY,
    ...     message="LLM server responding",
    ...     response_time_ms=25.5
    ... )
    >>> print(result.is_healthy)
    True
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class HealthStatus(Enum):
    """
    Enumeration of health status values.

    Each status represents the health state of a component or check:

    - HEALTHY: All checks passing, component fully operational
    - DEGRADED: Some non-critical checks failing, component operational
    - UNHEALTHY: Critical checks failing, component impaired
    - STARTING: Component still initializing
    - UNKNOWN: Health check not yet run or status indeterminate

    Example:
        >>> HealthStatus.HEALTHY.value
        'healthy'
        >>> HealthStatus.UNHEALTHY.value
        'unhealthy'
    """

    HEALTHY = auto()
    DEGRADED = auto()
    UNHEALTHY = auto()
    STARTING = auto()
    UNKNOWN = auto()

    @property
    def is_operational(self) -> bool:
        """
        Check if status indicates operational state.

        Returns:
            True if HEALTHY or DEGRADED, False otherwise

        Example:
            >>> HealthStatus.HEALTHY.is_operational
            True
            >>> HealthStatus.UNHEALTHY.is_operational
            False
        """
        return self in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)

    @property
    def is_healthy(self) -> bool:
        """
        Check if status indicates healthy state.

        Returns:
            True only if HEALTHY

        Example:
            >>> HealthStatus.HEALTHY.is_healthy
            True
            >>> HealthStatus.DEGRADED.is_healthy
            False
        """
        return self == HealthStatus.HEALTHY

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert status to dictionary for serialization.

        Returns:
            Dictionary with status name and operational flag

        Example:
            >>> HealthStatus.HEALTHY.to_dict()
            {'status': 'HEALTHY', 'is_operational': True, 'is_healthy': True}
        """
        return {
            "status": self.name,
            "is_operational": self.is_operational,
            "is_healthy": self.is_healthy,
        }


@dataclass(frozen=True)
class HealthCheckResult:
    """
    Immutable result of a single health check.

    A HealthCheckResult captures the outcome of executing a health check
    for a specific component or probe at a specific point in time.

    The frozen=True ensures results cannot be modified after creation,
    providing immutability for thread-safe operations and historical accuracy.

    Attributes:
        check_name: Unique identifier for the health check
        status: Health status result (HEALTHY, DEGRADED, UNHEALTHY, etc.)
        message: Human-readable description of the check result
        response_time_ms: Time taken to perform the check in milliseconds
        timestamp: When the check was performed (UTC timezone)
        metadata: Additional contextual information about the check
        exception: Optional exception if check failed

    Example:
        >>> result = HealthCheckResult(
        ...     check_name="memory_probe",
        ...     status=HealthStatus.HEALTHY,
        ...     message="Memory usage at 45%",
        ...     response_time_ms=12.5,
        ...     metadata={"used_mb": 4500, "total_mb": 10000}
        ... )
        >>> print(result.check_name)
        memory_probe
        >>> print(result.is_healthy)
        True
    """

    check_name: str
    status: HealthStatus
    message: str
    response_time_ms: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    exception: Optional[Exception] = None

    @property
    def is_healthy(self) -> bool:
        """
        Check if result indicates healthy state.

        Returns:
            True if status is HEALTHY

        Example:
            >>> result = HealthCheckResult(
            ...     check_name="test",
            ...     status=HealthStatus.HEALTHY,
            ...     message="OK"
            ... )
            >>> result.is_healthy
            True
        """
        return self.status.is_healthy

    @property
    def is_operational(self) -> bool:
        """
        Check if result indicates operational state.

        Returns:
            True if status is HEALTHY or DEGRADED

        Example:
            >>> result = HealthCheckResult(
            ...     check_name="test",
            ...     status=HealthStatus.DEGRADED,
            ...     message="Warning"
            ... )
            >>> result.is_operational
            True
        """
        return self.status.is_operational

    def with_status(self, status: HealthStatus) -> "HealthCheckResult":
        """
        Create a new result with updated status.

        Since HealthCheckResult is immutable (frozen), this creates a copy
        with the specified status updated.

        Args:
            status: New health status

        Returns:
            New HealthCheckResult with updated status

        Example:
            >>> new_result = result.with_status(HealthStatus.DEGRADED)
        """
        return HealthCheckResult(
            check_name=self.check_name,
            status=status,
            message=self.message,
            response_time_ms=self.response_time_ms,
            timestamp=self.timestamp,
            metadata=self.metadata,
            exception=self.exception,
        )

    def with_message(self, message: str) -> "HealthCheckResult":
        """
        Create a new result with updated message.

        Args:
            message: New message string

        Returns:
            New HealthCheckResult with updated message

        Example:
            >>> new_result = result.with_message("Updated message")
        """
        return HealthCheckResult(
            check_name=self.check_name,
            status=self.status,
            message=message,
            response_time_ms=self.response_time_ms,
            timestamp=self.timestamp,
            metadata=self.metadata,
            exception=self.exception,
        )

    def with_metadata(self, **kwargs: Any) -> "HealthCheckResult":
        """
        Create a new result with updated metadata.

        Args:
            **kwargs: Metadata fields to update

        Returns:
            New HealthCheckResult with updated metadata

        Example:
            >>> new_result = result.with_metadata(cpu_percent=75.5)
        """
        new_metadata = {**self.metadata, **kwargs}
        return HealthCheckResult(
            check_name=self.check_name,
            status=self.status,
            message=self.message,
            response_time_ms=self.response_time_ms,
            timestamp=self.timestamp,
            metadata=new_metadata,
            exception=self.exception,
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert result to dictionary for serialization.

        Returns:
            Dictionary representation with ISO format timestamp

        Example:
            >>> data = result.to_dict()
            >>> assert "check_name" in data
            >>> assert "status" in data
        """
        return {
            "check_name": self.check_name,
            "status": self.status.name,
            "message": self.message,
            "response_time_ms": self.response_time_ms,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "is_healthy": self.is_healthy,
            "is_operational": self.is_operational,
        }

    @classmethod
    def healthy(
        cls,
        check_name: str,
        message: str = "OK",
        response_time_ms: float = 0.0,
        **metadata: Any,
    ) -> "HealthCheckResult":
        """
        Create a healthy result.

        Convenience constructor for creating HEALTHY results.

        Args:
            check_name: Name of the health check
            message: Optional message
            response_time_ms: Optional response time
            **metadata: Optional metadata

        Returns:
            HealthCheckResult with HEALTHY status

        Example:
            >>> result = HealthCheckResult.healthy(
            ...     "llm_probe",
            ...     "LLM responding",
            ...     response_time_ms=25.0
            ... )
        """
        return cls(
            check_name=check_name,
            status=HealthStatus.HEALTHY,
            message=message,
            response_time_ms=response_time_ms,
            metadata=metadata,
        )

    @classmethod
    def unhealthy(
        cls,
        check_name: str,
        message: str = "Check failed",
        response_time_ms: float = 0.0,
        exception: Optional[Exception] = None,
        **metadata: Any,
    ) -> "HealthCheckResult":
        """
        Create an unhealthy result.

        Convenience constructor for creating UNHEALTHY results.

        Args:
            check_name: Name of the health check
            message: Optional message describing the failure
            response_time_ms: Optional response time
            exception: Optional exception that caused failure
            **metadata: Optional metadata

        Returns:
            HealthCheckResult with UNHEALTHY status

        Example:
            >>> result = HealthCheckResult.unhealthy(
            ...     "db_probe",
            ...     "Connection refused",
            ...     exception=ConnectionError("refused")
            ... )
        """
        return cls(
            check_name=check_name,
            status=HealthStatus.UNHEALTHY,
            message=message,
            response_time_ms=response_time_ms,
            exception=exception,
            metadata=metadata,
        )

    @classmethod
    def degraded(
        cls,
        check_name: str,
        message: str = "Degraded performance",
        response_time_ms: float = 0.0,
        **metadata: Any,
    ) -> "HealthCheckResult":
        """
        Create a degraded result.

        Convenience constructor for creating DEGRADED results.

        Args:
            check_name: Name of the health check
            message: Optional message describing degradation
            response_time_ms: Optional response time
            **metadata: Optional metadata

        Returns:
            HealthCheckResult with DEGRADED status

        Example:
            >>> result = HealthCheckResult.degraded(
            ...     "disk_probe",
            ...     "Disk usage at 85%",
            ...     disk_percent=85.0
            ... )
        """
        return cls(
            check_name=check_name,
            status=HealthStatus.DEGRADED,
            message=message,
            response_time_ms=response_time_ms,
            metadata=metadata,
        )


@dataclass(frozen=True)
class AggregatedHealthStatus:
    """
    Aggregated health status across multiple checks.

    AggregatedHealthStatus combines results from multiple health checks
    into an overall status, using the worst status as the aggregate.

    Status Aggregation Rules:
        - If ANY check is UNHEALTHY -> Overall is UNHEALTHY
        - If ANY check is DEGRADED (none UNHEALTHY) -> Overall is DEGRADED
        - If ALL checks are HEALTHY -> Overall is HEALTHY
        - If ALL checks are UNKNOWN/STARTING -> Overall is STARTING

    Attributes:
        overall_status: Aggregated health status
        total_checks: Total number of checks performed
        healthy_checks: Number of healthy checks
        degraded_checks: Number of degraded checks
        unhealthy_checks: Number of unhealthy checks
        unknown_checks: Number of unknown checks
        results: List of individual check results
        timestamp: When aggregation was performed (UTC timezone)
        metadata: Additional contextual information

    Example:
        >>> results = [
        ...     HealthCheckResult.healthy("check1", "OK"),
        ...     HealthCheckResult.degraded("check2", "Warning"),
        ... ]
        >>> aggregated = AggregatedHealthStatus.from_results(results)
        >>> print(aggregated.overall_status)
        HealthStatus.DEGRADED
        >>> print(aggregated.health_percentage)
        50.0
    """

    overall_status: HealthStatus
    total_checks: int
    healthy_checks: int
    degraded_checks: int
    unhealthy_checks: int
    unknown_checks: int
    results: List[HealthCheckResult] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def health_percentage(self) -> float:
        """
        Calculate health percentage (0-100).

        Returns:
            Percentage of healthy checks out of total known checks

        Example:
            >>> results = [
            ...     HealthCheckResult.healthy("check1"),
            ...     HealthCheckResult.healthy("check2"),
            ...     HealthCheckResult.degraded("check3"),
            ... ]
            >>> aggregated = AggregatedHealthStatus.from_results(results)
            >>> print(aggregated.health_percentage)
            66.666...
        """
        known_checks = self.total_checks - self.unknown_checks
        if known_checks == 0:
            return 0.0
        return (self.healthy_checks / known_checks) * 100.0

    @property
    def is_healthy(self) -> bool:
        """
        Check if aggregated status is healthy.

        Returns:
            True if overall status is HEALTHY

        Example:
            >>> aggregated.is_healthy
            True
        """
        return self.overall_status.is_healthy

    @property
    def is_operational(self) -> bool:
        """
        Check if aggregated status is operational.

        Returns:
            True if overall status is HEALTHY or DEGRADED

        Example:
            >>> aggregated.is_operational
            True
        """
        return self.overall_status.is_operational

    @classmethod
    def from_results(
        cls,
        results: List[HealthCheckResult],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AggregatedHealthStatus":
        """
        Create aggregated status from list of results.

        Analyzes all results and determines the worst overall status.

        Args:
            results: List of health check results to aggregate
            metadata: Optional additional metadata

        Returns:
            AggregatedHealthStatus with combined status

        Example:
            >>> results = [
            ...     HealthCheckResult.healthy("llm", "OK"),
            ...     HealthCheckResult.healthy("mcp", "OK"),
            ...     HealthCheckResult.degraded("disk", "85% used"),
            ... ]
            >>> aggregated = AggregatedHealthStatus.from_results(results)
            >>> print(aggregated.overall_status)
            HealthStatus.DEGRADED
        """
        healthy = sum(1 for r in results if r.status == HealthStatus.HEALTHY)
        degraded = sum(1 for r in results if r.status == HealthStatus.DEGRADED)
        unhealthy = sum(1 for r in results if r.status == HealthStatus.UNHEALTHY)
        starting = sum(1 for r in results if r.status == HealthStatus.STARTING)
        unknown = sum(1 for r in results if r.status == HealthStatus.UNKNOWN)

        total = len(results)

        # Determine overall status (worst wins)
        if unhealthy > 0:
            overall = HealthStatus.UNHEALTHY
        elif degraded > 0:
            overall = HealthStatus.DEGRADED
        elif healthy == total and total > 0:
            overall = HealthStatus.HEALTHY
        elif starting > 0:
            overall = HealthStatus.STARTING
        else:
            overall = HealthStatus.UNKNOWN

        return cls(
            overall_status=overall,
            total_checks=total,
            healthy_checks=healthy,
            degraded_checks=degraded,
            unhealthy_checks=unhealthy,
            unknown_checks=unknown + starting,  # Treat STARTING as unknown for stats
            results=results,
            metadata=metadata or {},
        )

    @classmethod
    def empty(cls) -> "AggregatedHealthStatus":
        """
        Create an empty aggregated status.

        Returns a status with no checks performed (UNKNOWN state).

        Returns:
            AggregatedHealthStatus with UNKNOWN status

        Example:
            >>> empty = AggregatedHealthStatus.empty()
            >>> print(empty.overall_status)
            HealthStatus.UNKNOWN
        """
        return cls(
            overall_status=HealthStatus.UNKNOWN,
            total_checks=0,
            healthy_checks=0,
            degraded_checks=0,
            unhealthy_checks=0,
            unknown_checks=0,
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert aggregated status to dictionary.

        Returns:
            Dictionary representation with all status details

        Example:
            >>> data = aggregated.to_dict()
            >>> assert "overall_status" in data
            >>> assert "health_percentage" in data
        """
        return {
            "overall_status": self.overall_status.name,
            "total_checks": self.total_checks,
            "healthy_checks": self.healthy_checks,
            "degraded_checks": self.degraded_checks,
            "unhealthy_checks": self.unhealthy_checks,
            "unknown_checks": self.unknown_checks,
            "health_percentage": self.health_percentage,
            "is_healthy": self.is_healthy,
            "is_operational": self.is_operational,
            "timestamp": self.timestamp.isoformat(),
            "results": [r.to_dict() for r in self.results],
            "metadata": self.metadata,
        }

    def summary(self) -> str:
        """
        Generate human-readable summary.

        Returns:
            Formatted summary string

        Example:
            >>> print(aggregated.summary())
            Health Status: DEGRADED (66.7% healthy)
            Checks: 2 healthy, 1 degraded, 0 unhealthy, 0 unknown
        """
        status_str = self.overall_status.name
        pct = self.health_percentage
        return (
            f"Health Status: {status_str} ({pct:.1f}% healthy)\n"
            f"Checks: {self.healthy_checks} healthy, {self.degraded_checks} degraded, "
            f"{self.unhealthy_checks} unhealthy, {self.unknown_checks} unknown"
        )


@dataclass
class ProbeResult:
    """
    Result from a probe execution.

    ProbeResult extends HealthCheckResult with additional probe-specific
    information including performance thresholds and recommendations.

    Attributes:
        probe_name: Name of the probe
        status: Health status result
        message: Human-readable description
        response_time_ms: Execution time in milliseconds
        timestamp: When probe was executed (UTC timezone)
        metadata: Additional probe-specific data
        threshold_exceeded: Whether any threshold was exceeded
        recommendation: Optional recommendation for remediation

    Example:
        >>> result = ProbeResult(
        ...     probe_name="memory",
        ...     status=HealthStatus.DEGRADED,
        ...     message="Memory at 85%",
        ...     response_time_ms=10.5,
        ...     threshold_exceeded=True,
        ...     recommendation="Consider increasing memory allocation"
        ... )
    """

    probe_name: str
    status: HealthStatus
    message: str
    response_time_ms: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    threshold_exceeded: bool = False
    recommendation: Optional[str] = None

    def to_health_check_result(self, check_name: Optional[str] = None) -> HealthCheckResult:
        """
        Convert to HealthCheckResult.

        Args:
            check_name: Optional override for check name

        Returns:
            HealthCheckResult equivalent

        Example:
            >>> result = probe_result.to_health_check_result()
        """
        return HealthCheckResult(
            check_name=check_name or self.probe_name,
            status=self.status,
            message=self.message,
            response_time_ms=self.response_time_ms,
            timestamp=self.timestamp,
            metadata=self.metadata,
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert probe result to dictionary.

        Returns:
            Dictionary representation

        Example:
            >>> data = result.to_dict()
            >>> assert "probe_name" in data
            >>> assert "threshold_exceeded" in data
        """
        return {
            "probe_name": self.probe_name,
            "status": self.status.name,
            "message": self.message,
            "response_time_ms": self.response_time_ms,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "threshold_exceeded": self.threshold_exceeded,
            "recommendation": self.recommendation,
            "is_healthy": self.status.is_healthy,
        }
