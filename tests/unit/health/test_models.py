"""
Unit tests for Health Models.

Covers:
- HealthStatus enum
- HealthCheckResult dataclass
- AggregatedHealthStatus dataclass
- ProbeResult dataclass
"""

import pytest
from datetime import datetime, timezone

from gaia.health.models import (
    HealthStatus,
    HealthCheckResult,
    AggregatedHealthStatus,
    ProbeResult,
)


class TestHealthStatus:
    """Test HealthStatus enum."""

    def test_health_status_values(self):
        """Should have correct status values."""
        assert HealthStatus.HEALTHY.name == "HEALTHY"
        assert HealthStatus.DEGRADED.name == "DEGRADED"
        assert HealthStatus.UNHEALTHY.name == "UNHEALTHY"
        assert HealthStatus.STARTING.name == "STARTING"
        assert HealthStatus.UNKNOWN.name == "UNKNOWN"

    def test_is_operational_healthy(self):
        """HEALTHY should be operational."""
        assert HealthStatus.HEALTHY.is_operational is True

    def test_is_operational_degraded(self):
        """DEGRADED should be operational."""
        assert HealthStatus.DEGRADED.is_operational is True

    def test_is_operational_unhealthy(self):
        """UNHEALTHY should not be operational."""
        assert HealthStatus.UNHEALTHY.is_operational is False

    def test_is_operational_starting(self):
        """STARTING should not be operational."""
        assert HealthStatus.STARTING.is_operational is False

    def test_is_operational_unknown(self):
        """UNKNOWN should not be operational."""
        assert HealthStatus.UNKNOWN.is_operational is False

    def test_is_healthy_healthy(self):
        """HEALTHY should be healthy."""
        assert HealthStatus.HEALTHY.is_healthy is True

    def test_is_healthy_degraded(self):
        """DEGRADED should not be healthy."""
        assert HealthStatus.DEGRADED.is_healthy is False

    def test_is_healthy_unhealthy(self):
        """UNHEALTHY should not be healthy."""
        assert HealthStatus.UNHEALTHY.is_healthy is False

    def test_to_dict(self):
        """Should convert to dictionary correctly."""
        result = HealthStatus.HEALTHY.to_dict()

        assert result["status"] == "HEALTHY"
        assert result["is_operational"] is True
        assert result["is_healthy"] is True

    def test_to_dict_degraded(self):
        """Should convert DEGRADED to dictionary correctly."""
        result = HealthStatus.DEGRADED.to_dict()

        assert result["status"] == "DEGRADED"
        assert result["is_operational"] is True
        assert result["is_healthy"] is False


class TestHealthCheckResult:
    """Test HealthCheckResult dataclass."""

    def test_init_basic(self):
        """Should initialize with basic values."""
        result = HealthCheckResult(
            check_name="test_check",
            status=HealthStatus.HEALTHY,
            message="OK",
        )

        assert result.check_name == "test_check"
        assert result.status == HealthStatus.HEALTHY
        assert result.message == "OK"
        assert result.response_time_ms == 0.0
        assert result.is_healthy is True

    def test_init_with_all_values(self):
        """Should initialize with all values."""
        now = datetime.now(timezone.utc)
        metadata = {"key": "value"}
        exception = Exception("test")

        result = HealthCheckResult(
            check_name="test_check",
            status=HealthStatus.UNHEALTHY,
            message="Failed",
            response_time_ms=25.5,
            timestamp=now,
            metadata=metadata,
            exception=exception,
        )

        assert result.check_name == "test_check"
        assert result.status == HealthStatus.UNHEALTHY
        assert result.message == "Failed"
        assert result.response_time_ms == 25.5
        assert result.timestamp == now
        assert result.metadata == metadata
        assert result.exception == exception

    def test_is_healthy_property(self):
        """Should correctly report is_healthy."""
        healthy = HealthCheckResult("test", HealthStatus.HEALTHY, "OK")
        degraded = HealthCheckResult("test", HealthStatus.DEGRADED, "Warning")
        unhealthy = HealthCheckResult("test", HealthStatus.UNHEALTHY, "Error")

        assert healthy.is_healthy is True
        assert degraded.is_healthy is False
        assert unhealthy.is_healthy is False

    def test_is_operational_property(self):
        """Should correctly report is_operational."""
        healthy = HealthCheckResult("test", HealthStatus.HEALTHY, "OK")
        degraded = HealthCheckResult("test", HealthStatus.DEGRADED, "Warning")
        unhealthy = HealthCheckResult("test", HealthStatus.UNHEALTHY, "Error")

        assert healthy.is_operational is True
        degraded.is_operational is True
        assert unhealthy.is_operational is False

    def test_with_status(self):
        """Should create new result with updated status."""
        original = HealthCheckResult("test", HealthStatus.HEALTHY, "OK")
        new = original.with_status(HealthStatus.DEGRADED)

        assert original.status == HealthStatus.HEALTHY
        assert new.status == HealthStatus.DEGRADED
        assert new.check_name == "test"

    def test_with_message(self):
        """Should create new result with updated message."""
        original = HealthCheckResult("test", HealthStatus.HEALTHY, "OK")
        new = original.with_message("Updated")

        assert original.message == "OK"
        assert new.message == "Updated"

    def test_with_metadata(self):
        """Should create new result with updated metadata."""
        original = HealthCheckResult("test", HealthStatus.HEALTHY, "OK")
        new = original.with_metadata(cpu=50, memory=75)

        assert original.metadata == {}
        assert new.metadata == {"cpu": 50, "memory": 75}

    def test_to_dict(self):
        """Should convert to dictionary correctly."""
        result = HealthCheckResult(
            check_name="test",
            status=HealthStatus.HEALTHY,
            message="OK",
            response_time_ms=10.5,
            metadata={"key": "value"},
        )

        data = result.to_dict()

        assert data["check_name"] == "test"
        assert data["status"] == "HEALTHY"
        assert data["message"] == "OK"
        assert data["response_time_ms"] == 10.5
        assert data["metadata"]["key"] == "value"
        assert data["is_healthy"] is True
        assert data["is_operational"] is True
        assert "timestamp" in data

    def test_healthy_classmethod(self):
        """Should create healthy result via classmethod."""
        result = HealthCheckResult.healthy(
            "test",
            message="All good",
            response_time_ms=15.0,
            extra="data",
        )

        assert result.status == HealthStatus.HEALTHY
        assert result.message == "All good"
        assert result.response_time_ms == 15.0
        assert result.metadata["extra"] == "data"

    def test_unhealthy_classmethod(self):
        """Should create unhealthy result via classmethod."""
        exception = ConnectionError("refused")
        result = HealthCheckResult.unhealthy(
            "test",
            message="Connection failed",
            response_time_ms=5000.0,
            exception=exception,
        )

        assert result.status == HealthStatus.UNHEALTHY
        assert result.message == "Connection failed"
        assert result.exception == exception

    def test_degraded_classmethod(self):
        """Should create degraded result via classmethod."""
        result = HealthCheckResult.degraded(
            "disk",
            message="Disk at 85%",
            disk_percent=85.0,
        )

        assert result.status == HealthStatus.DEGRADED
        assert result.message == "Disk at 85%"
        assert result.metadata["disk_percent"] == 85.0

    def test_default_timestamp(self):
        """Should have current timestamp by default."""
        before = datetime.now(timezone.utc)
        result = HealthCheckResult("test", HealthStatus.HEALTHY, "OK")
        after = datetime.now(timezone.utc)

        assert before <= result.timestamp <= after


class TestAggregatedHealthStatus:
    """Test AggregatedHealthStatus dataclass."""

    def test_from_results_all_healthy(self):
        """Should aggregate all healthy results."""
        results = [
            HealthCheckResult.healthy("check1", "OK"),
            HealthCheckResult.healthy("check2", "OK"),
            HealthCheckResult.healthy("check3", "OK"),
        ]

        aggregated = AggregatedHealthStatus.from_results(results)

        assert aggregated.overall_status == HealthStatus.HEALTHY
        assert aggregated.total_checks == 3
        assert aggregated.healthy_checks == 3
        assert aggregated.degraded_checks == 0
        assert aggregated.unhealthy_checks == 0
        assert aggregated.health_percentage == 100.0
        assert aggregated.is_healthy is True

    def test_from_results_with_degraded(self):
        """Should aggregate with degraded results."""
        results = [
            HealthCheckResult.healthy("check1", "OK"),
            HealthCheckResult.degraded("check2", "Warning"),
            HealthCheckResult.healthy("check3", "OK"),
        ]

        aggregated = AggregatedHealthStatus.from_results(results)

        assert aggregated.overall_status == HealthStatus.DEGRADED
        assert aggregated.total_checks == 3
        assert aggregated.healthy_checks == 2
        assert aggregated.degraded_checks == 1
        assert aggregated.is_healthy is False
        assert aggregated.is_operational is True

    def test_from_results_with_unhealthy(self):
        """Should aggregate with unhealthy results."""
        results = [
            HealthCheckResult.healthy("check1", "OK"),
            HealthCheckResult.unhealthy("check2", "Failed"),
            HealthCheckResult.healthy("check3", "OK"),
        ]

        aggregated = AggregatedHealthStatus.from_results(results)

        assert aggregated.overall_status == HealthStatus.UNHEALTHY
        assert aggregated.total_checks == 3
        assert aggregated.unhealthy_checks == 1
        assert aggregated.is_operational is False

    def test_from_results_empty(self):
        """Should handle empty results."""
        aggregated = AggregatedHealthStatus.from_results([])

        assert aggregated.overall_status == HealthStatus.UNKNOWN
        assert aggregated.total_checks == 0
        assert aggregated.health_percentage == 0.0

    def test_empty_classmethod(self):
        """Should create empty status."""
        empty = AggregatedHealthStatus.empty()

        assert empty.overall_status == HealthStatus.UNKNOWN
        assert empty.total_checks == 0
        assert empty.is_healthy is False

    def test_health_percentage(self):
        """Should calculate health percentage correctly."""
        results = [
            HealthCheckResult.healthy("check1"),
            HealthCheckResult.healthy("check2"),
            HealthCheckResult.degraded("check3"),
            HealthCheckResult.unhealthy("check4"),
        ]

        aggregated = AggregatedHealthStatus.from_results(results)

        # 2 healthy out of 4 = 50%
        assert aggregated.health_percentage == 50.0

    def test_health_percentage_with_unknown(self):
        """Should exclude unknown from percentage calculation."""
        results = [
            HealthCheckResult.healthy("check1"),
            HealthCheckResult.healthy("check2"),
            HealthCheckResult("check3", HealthStatus.UNKNOWN, "Unknown"),
        ]

        aggregated = AggregatedHealthStatus.from_results(results)

        # 2 healthy out of 2 known = 100%
        assert aggregated.health_percentage == 100.0
        assert aggregated.unknown_checks == 1

    def test_to_dict(self):
        """Should convert to dictionary correctly."""
        results = [
            HealthCheckResult.healthy("check1"),
            HealthCheckResult.degraded("check2"),
        ]
        aggregated = AggregatedHealthStatus.from_results(
            results,
            metadata={"env": "prod"},
        )

        data = aggregated.to_dict()

        assert data["overall_status"] == "DEGRADED"
        assert data["total_checks"] == 2
        assert data["healthy_checks"] == 1
        assert data["degraded_checks"] == 1
        assert data["health_percentage"] == 50.0
        assert data["is_healthy"] is False
        assert data["is_operational"] is True
        assert data["metadata"]["env"] == "prod"
        assert len(data["results"]) == 2

    def test_summary(self):
        """Should generate summary string."""
        results = [
            HealthCheckResult.healthy("check1"),
            HealthCheckResult.degraded("check2"),
        ]
        aggregated = AggregatedHealthStatus.from_results(results)

        summary = aggregated.summary()

        assert "DEGRADED" in summary
        assert "50.0%" in summary
        assert "1 healthy" in summary
        assert "1 degraded" in summary

    def test_with_metadata(self):
        """Should include metadata in aggregation."""
        results = [HealthCheckResult.healthy("check1")]
        aggregated = AggregatedHealthStatus.from_results(
            results,
            metadata={"version": "1.0"},
        )

        assert aggregated.metadata["version"] == "1.0"


class TestProbeResult:
    """Test ProbeResult dataclass."""

    def test_init_basic(self):
        """Should initialize with basic values."""
        result = ProbeResult(
            probe_name="memory_probe",
            status=HealthStatus.HEALTHY,
            message="Memory OK",
        )

        assert result.probe_name == "memory_probe"
        assert result.status == HealthStatus.HEALTHY
        assert result.message == "Memory OK"
        assert result.threshold_exceeded is False
        assert result.recommendation is None

    def test_init_with_all_values(self):
        """Should initialize with all values."""
        result = ProbeResult(
            probe_name="disk_probe",
            status=HealthStatus.DEGRADED,
            message="Disk at 85%",
            response_time_ms=15.5,
            metadata={"disk_percent": 85.0},
            threshold_exceeded=True,
            recommendation="Free disk space",
        )

        assert result.probe_name == "disk_probe"
        assert result.status == HealthStatus.DEGRADED
        assert result.message == "Disk at 85%"
        assert result.response_time_ms == 15.5
        assert result.metadata["disk_percent"] == 85.0
        assert result.threshold_exceeded is True
        assert result.recommendation == "Free disk space"

    def test_to_health_check_result(self):
        """Should convert to HealthCheckResult."""
        probe_result = ProbeResult(
            probe_name="test_probe",
            status=HealthStatus.HEALTHY,
            message="OK",
            response_time_ms=10.0,
            metadata={"key": "value"},
        )

        health_result = probe_result.to_health_check_result()

        assert health_result.check_name == "test_probe"
        assert health_result.status == HealthStatus.HEALTHY
        assert health_result.message == "OK"
        assert health_result.response_time_ms == 10.0
        assert health_result.metadata["key"] == "value"

    def test_to_health_check_result_custom_name(self):
        """Should use custom check name."""
        probe_result = ProbeResult(
            probe_name="probe",
            status=HealthStatus.HEALTHY,
            message="OK",
        )

        health_result = probe_result.to_health_check_result("custom_name")

        assert health_result.check_name == "custom_name"

    def test_to_dict(self):
        """Should convert to dictionary correctly."""
        result = ProbeResult(
            probe_name="test",
            status=HealthStatus.DEGRADED,
            message="Warning",
            response_time_ms=25.0,
            threshold_exceeded=True,
            recommendation="Action needed",
        )

        data = result.to_dict()

        assert data["probe_name"] == "test"
        assert data["status"] == "DEGRADED"
        assert data["message"] == "Warning"
        assert data["response_time_ms"] == 25.0
        assert data["threshold_exceeded"] is True
        assert data["recommendation"] == "Action needed"
        assert data["is_healthy"] is False

    def test_default_timestamp(self):
        """Should have current timestamp by default."""
        before = datetime.now(timezone.utc)
        result = ProbeResult("test", HealthStatus.HEALTHY, "OK")
        after = datetime.now(timezone.utc)

        assert before <= result.timestamp <= after
