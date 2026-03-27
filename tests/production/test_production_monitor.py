"""
Tests for GAIA Production Monitor.

Tests cover:
- ProductionMetrics dataclass properties (success_rate, avg_latency_ms)
- ProductionMonitor record_execution / record_loop_execution
- get_summary dictionary structure
- reset() zeroing all counters
- Threshold checking (success_rate < 0.99 triggers alert; error count > 10 triggers alert)
- Alert callback invocation
- start_monitoring / stop_monitoring async lifecycle
"""

import asyncio
import pytest
from unittest.mock import MagicMock, patch
from typing import Dict, List

from gaia.metrics.production_monitor import ProductionMonitor, ProductionMetrics


# ---------------------------------------------------------------------------
# ProductionMetrics unit tests
# ---------------------------------------------------------------------------

class TestProductionMetrics:
    """Tests for ProductionMetrics dataclass."""

    def test_initial_state(self):
        """Test default values on construction."""
        m = ProductionMetrics()
        assert m.loops_executed == 0
        assert m.loops_successful == 0
        assert m.loops_failed == 0
        assert m.total_latency_ms == 0.0
        assert m.peak_memory_mb == 0.0
        assert m.errors == []

    def test_success_rate_no_executions(self):
        """Success rate should be 1.0 when no executions recorded (idle assumption)."""
        m = ProductionMetrics()
        assert m.success_rate == 1.0

    def test_success_rate_all_success(self):
        """Success rate should be 1.0 when all executions succeeded."""
        m = ProductionMetrics(loops_executed=10, loops_successful=10, loops_failed=0)
        assert m.success_rate == 1.0

    def test_success_rate_mixed(self):
        """Success rate computed correctly with mixed outcomes."""
        m = ProductionMetrics(loops_executed=100, loops_successful=98, loops_failed=2)
        assert abs(m.success_rate - 0.98) < 1e-9

    def test_success_rate_all_failed(self):
        """Success rate should be 0.0 when all executions failed."""
        m = ProductionMetrics(loops_executed=5, loops_successful=0, loops_failed=5)
        assert m.success_rate == 0.0

    def test_avg_latency_no_executions(self):
        """Average latency should be 0.0 when no executions recorded."""
        m = ProductionMetrics()
        assert m.avg_latency_ms == 0.0

    def test_avg_latency_computed(self):
        """Average latency computed correctly."""
        m = ProductionMetrics(loops_executed=4, total_latency_ms=400.0)
        assert abs(m.avg_latency_ms - 100.0) < 1e-9

    def test_avg_latency_single_execution(self):
        """Average latency with a single execution."""
        m = ProductionMetrics(loops_executed=1, total_latency_ms=62.5)
        assert abs(m.avg_latency_ms - 62.5) < 1e-9


# ---------------------------------------------------------------------------
# ProductionMonitor – record_loop_execution / record_execution
# ---------------------------------------------------------------------------

class TestProductionMonitorRecording:
    """Tests for recording executions into ProductionMonitor."""

    def test_record_successful_loop(self):
        """Successful execution increments loops_executed and loops_successful."""
        monitor = ProductionMonitor()
        monitor.record_loop_execution(success=True, latency_ms=50.0)

        assert monitor.metrics.loops_executed == 1
        assert monitor.metrics.loops_successful == 1
        assert monitor.metrics.loops_failed == 0
        assert monitor.metrics.total_latency_ms == 50.0
        assert monitor.metrics.errors == []

    def test_record_failed_loop_with_description(self):
        """Failed execution increments loops_failed and appends error description."""
        monitor = ProductionMonitor()
        monitor.record_loop_execution(
            success=False,
            latency_ms=120.0,
            error_description="Timeout in phase DEVELOPMENT",
        )

        assert monitor.metrics.loops_executed == 1
        assert monitor.metrics.loops_successful == 0
        assert monitor.metrics.loops_failed == 1
        assert "Timeout in phase DEVELOPMENT" in monitor.metrics.errors

    def test_record_failed_loop_auto_description(self):
        """Failed execution without description generates a timestamp-based error string."""
        monitor = ProductionMonitor()
        monitor.record_loop_execution(success=False, latency_ms=10.0)

        assert len(monitor.metrics.errors) == 1
        # Auto-generated description should contain the word "failed"
        assert "failed" in monitor.metrics.errors[0].lower()

    def test_record_execution_alternate_api(self):
        """record_execution() with alternate parameter order delegates correctly."""
        monitor = ProductionMonitor()
        monitor.record_execution(latency_ms=75.0, success=True)

        assert monitor.metrics.loops_executed == 1
        assert monitor.metrics.loops_successful == 1
        assert monitor.metrics.total_latency_ms == 75.0

    def test_record_execution_failure_alternate_api(self):
        """record_execution() failure path forwards error string."""
        monitor = ProductionMonitor()
        monitor.record_execution(latency_ms=200.0, success=False, error="Agent crash")

        assert monitor.metrics.loops_failed == 1
        assert "Agent crash" in monitor.metrics.errors

    def test_multiple_executions_accumulate(self):
        """Multiple recordings accumulate counters correctly."""
        monitor = ProductionMonitor()
        for _ in range(5):
            monitor.record_loop_execution(success=True, latency_ms=100.0)
        for i in range(2):
            monitor.record_loop_execution(
                success=False, latency_ms=50.0, error_description=f"error-{i}"
            )

        assert monitor.metrics.loops_executed == 7
        assert monitor.metrics.loops_successful == 5
        assert monitor.metrics.loops_failed == 2
        assert monitor.metrics.total_latency_ms == 600.0
        assert len(monitor.metrics.errors) == 2


# ---------------------------------------------------------------------------
# ProductionMonitor – get_summary
# ---------------------------------------------------------------------------

class TestProductionMonitorSummary:
    """Tests for get_summary() output."""

    def test_get_summary_keys(self):
        """get_summary() must return all required keys."""
        monitor = ProductionMonitor()
        summary = monitor.get_summary()

        required_keys = {
            "loops_executed",
            "loops_successful",
            "loops_failed",
            "success_rate",
            "total_latency_ms",
            "avg_latency_ms",
            "peak_memory_mb",
            "error_count",
            "errors",
            "snapshot_at",
        }
        assert required_keys.issubset(summary.keys())

    def test_get_summary_values_after_recording(self):
        """get_summary() values match recorded data."""
        monitor = ProductionMonitor()
        monitor.record_loop_execution(success=True, latency_ms=80.0)
        monitor.record_loop_execution(success=False, latency_ms=20.0, error_description="oops")

        summary = monitor.get_summary()

        assert summary["loops_executed"] == 2
        assert summary["loops_successful"] == 1
        assert summary["loops_failed"] == 1
        assert abs(summary["total_latency_ms"] - 100.0) < 1e-9
        assert abs(summary["avg_latency_ms"] - 50.0) < 1e-9
        assert summary["error_count"] == 1
        assert "oops" in summary["errors"]

    def test_get_summary_snapshot_at_is_iso_string(self):
        """snapshot_at value should be a non-empty ISO-formatted string."""
        monitor = ProductionMonitor()
        summary = monitor.get_summary()
        assert isinstance(summary["snapshot_at"], str)
        assert len(summary["snapshot_at"]) > 0
        # Should parse without error
        from datetime import datetime
        datetime.fromisoformat(summary["snapshot_at"].replace("Z", "+00:00"))

    def test_get_summary_errors_is_copy(self):
        """Modifying returned errors list must not affect internal state."""
        monitor = ProductionMonitor()
        monitor.record_loop_execution(success=False, latency_ms=10.0, error_description="e1")
        summary = monitor.get_summary()
        summary["errors"].append("injected")
        assert len(monitor.metrics.errors) == 1


# ---------------------------------------------------------------------------
# ProductionMonitor – reset
# ---------------------------------------------------------------------------

class TestProductionMonitorReset:
    """Tests for reset() behaviour."""

    def test_reset_clears_all_counters(self):
        """reset() returns metrics to initial zero state."""
        monitor = ProductionMonitor()
        for _ in range(10):
            monitor.record_loop_execution(success=True, latency_ms=50.0)
        monitor.record_loop_execution(success=False, latency_ms=10.0, error_description="err")

        monitor.reset()

        assert monitor.metrics.loops_executed == 0
        assert monitor.metrics.loops_successful == 0
        assert monitor.metrics.loops_failed == 0
        assert monitor.metrics.total_latency_ms == 0.0
        assert monitor.metrics.errors == []
        assert monitor.metrics.success_rate == 1.0
        assert monitor.metrics.avg_latency_ms == 0.0

    def test_reset_then_record(self):
        """After reset, new recordings work correctly."""
        monitor = ProductionMonitor()
        for _ in range(5):
            monitor.record_loop_execution(success=True, latency_ms=100.0)
        monitor.reset()
        monitor.record_loop_execution(success=True, latency_ms=40.0)

        assert monitor.metrics.loops_executed == 1
        assert monitor.metrics.total_latency_ms == 40.0


# ---------------------------------------------------------------------------
# ProductionMonitor – threshold checks (alert logic)
# ---------------------------------------------------------------------------

class TestProductionMonitorThresholds:
    """Tests for alert threshold evaluation."""

    @pytest.mark.asyncio
    async def test_no_alert_when_success_rate_above_threshold(self):
        """No alert fired when success rate is at or above 0.99."""
        alerts: List[Dict] = []
        monitor = ProductionMonitor(
            check_interval_seconds=0.0,
            alert_callback=lambda a: alerts.append(a),
        )

        # 99 successes, 1 failure => 0.99 success rate (exactly at threshold, not below)
        for _ in range(99):
            monitor.record_loop_execution(success=True, latency_ms=50.0)
        monitor.record_loop_execution(success=False, latency_ms=50.0, error_description="e")

        await monitor._check_thresholds()
        success_rate_alerts = [a for a in alerts if a["type"] == "success_rate"]
        assert len(success_rate_alerts) == 0

    @pytest.mark.asyncio
    async def test_alert_when_success_rate_below_threshold(self):
        """Alert fired when success rate drops below 0.99."""
        alerts: List[Dict] = []
        monitor = ProductionMonitor(
            check_interval_seconds=0.0,
            alert_callback=lambda a: alerts.append(a),
        )

        # 98 successes, 2 failures => 0.98 success rate (below 0.99)
        for _ in range(98):
            monitor.record_loop_execution(success=True, latency_ms=50.0)
        for _ in range(2):
            monitor.record_loop_execution(success=False, latency_ms=50.0, error_description="fail")

        await monitor._check_thresholds()

        success_rate_alerts = [a for a in alerts if a["type"] == "success_rate"]
        assert len(success_rate_alerts) == 1
        assert success_rate_alerts[0]["level"] == "WARNING"
        assert "success_rate" in success_rate_alerts[0]

    @pytest.mark.asyncio
    async def test_no_alert_when_no_executions(self):
        """No alert when loops_executed == 0 (idle system)."""
        alerts: List[Dict] = []
        monitor = ProductionMonitor(
            check_interval_seconds=0.0,
            alert_callback=lambda a: alerts.append(a),
        )

        await monitor._check_thresholds()
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_no_alert_when_error_count_at_threshold(self):
        """No alert when error count is exactly 10 (threshold is > 10)."""
        alerts: List[Dict] = []
        monitor = ProductionMonitor(
            check_interval_seconds=0.0,
            alert_callback=lambda a: alerts.append(a),
        )

        # Add exactly 10 errors but keep success rate above threshold
        for _ in range(1000):
            monitor.record_loop_execution(success=True, latency_ms=50.0)
        for i in range(10):
            monitor.record_loop_execution(success=False, latency_ms=50.0, error_description=f"err-{i}")

        await monitor._check_thresholds()
        error_alerts = [a for a in alerts if a["type"] == "error_count"]
        assert len(error_alerts) == 0

    @pytest.mark.asyncio
    async def test_alert_when_error_count_exceeds_threshold(self):
        """Alert fired when error count exceeds 10."""
        alerts: List[Dict] = []
        monitor = ProductionMonitor(
            check_interval_seconds=0.0,
            alert_callback=lambda a: alerts.append(a),
        )

        # Add 11 errors; success rate kept >= 0.99 to isolate error count trigger
        for _ in range(10000):
            monitor.record_loop_execution(success=True, latency_ms=50.0)
        for i in range(11):
            monitor.record_loop_execution(success=False, latency_ms=50.0, error_description=f"err-{i}")

        await monitor._check_thresholds()

        error_alerts = [a for a in alerts if a["type"] == "error_count"]
        assert len(error_alerts) == 1
        assert error_alerts[0]["level"] == "WARNING"
        assert error_alerts[0]["error_count"] == 11

    @pytest.mark.asyncio
    async def test_both_alerts_can_fire_independently(self):
        """Both success_rate and error_count alerts can fire in the same check."""
        alerts: List[Dict] = []
        monitor = ProductionMonitor(
            check_interval_seconds=0.0,
            alert_callback=lambda a: alerts.append(a),
        )

        # 88 successes, 12 failures => success_rate = 0.88 (<0.99) and errors > 10
        for _ in range(88):
            monitor.record_loop_execution(success=True, latency_ms=50.0)
        for i in range(12):
            monitor.record_loop_execution(success=False, latency_ms=50.0, error_description=f"err-{i}")

        await monitor._check_thresholds()

        alert_types = {a["type"] for a in alerts}
        assert "success_rate" in alert_types
        assert "error_count" in alert_types


# ---------------------------------------------------------------------------
# ProductionMonitor – alert callback
# ---------------------------------------------------------------------------

class TestProductionMonitorAlertCallback:
    """Tests for alert callback invocation."""

    @pytest.mark.asyncio
    async def test_callback_receives_alert_dict(self):
        """Callback is invoked with a dict containing expected keys."""
        received: List[Dict] = []

        def callback(alert: Dict) -> None:
            received.append(alert)

        monitor = ProductionMonitor(
            check_interval_seconds=0.0,
            alert_callback=callback,
        )

        # Trigger success_rate alert
        for _ in range(97):
            monitor.record_loop_execution(success=True, latency_ms=50.0)
        for _ in range(3):
            monitor.record_loop_execution(success=False, latency_ms=50.0, error_description="x")

        await monitor._check_thresholds()

        assert len(received) >= 1
        alert = received[0]
        assert "level" in alert
        assert "type" in alert
        assert "message" in alert
        assert "timestamp" in alert

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_propagate(self):
        """An exception raised inside the callback must not propagate to the monitor."""
        def bad_callback(alert: Dict) -> None:
            raise RuntimeError("callback exploded")

        monitor = ProductionMonitor(
            check_interval_seconds=0.0,
            alert_callback=bad_callback,
        )

        # Trigger alert
        for _ in range(97):
            monitor.record_loop_execution(success=True, latency_ms=50.0)
        for _ in range(3):
            monitor.record_loop_execution(success=False, latency_ms=50.0, error_description="x")

        # Should not raise
        await monitor._check_thresholds()

    @pytest.mark.asyncio
    async def test_no_callback_no_error(self):
        """Monitor works correctly without an alert callback configured."""
        monitor = ProductionMonitor(check_interval_seconds=0.0, alert_callback=None)

        for _ in range(97):
            monitor.record_loop_execution(success=True, latency_ms=50.0)
        for _ in range(3):
            monitor.record_loop_execution(success=False, latency_ms=50.0, error_description="x")

        # Should not raise even though threshold is exceeded
        await monitor._check_thresholds()


# ---------------------------------------------------------------------------
# ProductionMonitor – start / stop monitoring lifecycle
# ---------------------------------------------------------------------------

class TestProductionMonitorLifecycle:
    """Tests for start_monitoring / stop_monitoring."""

    @pytest.mark.asyncio
    async def test_stop_monitoring_halts_loop(self):
        """stop_monitoring() causes start_monitoring() to exit cleanly."""
        monitor = ProductionMonitor(check_interval_seconds=0.01)

        task = asyncio.create_task(monitor.start_monitoring())
        await asyncio.sleep(0.05)
        monitor.stop_monitoring()

        # Give the task time to exit cleanly
        await asyncio.wait_for(task, timeout=1.0)
        assert not monitor._monitoring

    @pytest.mark.asyncio
    async def test_monitoring_calls_check_thresholds(self):
        """start_monitoring() invokes _check_thresholds at least once per interval."""
        call_count = 0
        original_check = None

        async def patched_check():
            nonlocal call_count
            call_count += 1

        monitor = ProductionMonitor(check_interval_seconds=0.01)
        monitor._check_thresholds = patched_check

        task = asyncio.create_task(monitor.start_monitoring())
        await asyncio.sleep(0.05)
        monitor.stop_monitoring()
        await asyncio.wait_for(task, timeout=1.0)

        assert call_count >= 1


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestProductionMonitorIntegration:
    """Integration tests for ProductionMonitor."""

    @pytest.mark.asyncio
    async def test_full_monitoring_workflow(self):
        """Full recording, threshold check, and reset workflow."""
        alerts: List[Dict] = []
        monitor = ProductionMonitor(
            check_interval_seconds=0.0,
            alert_callback=lambda a: alerts.append(a),
        )

        # Record mixed executions below the alert threshold
        for _ in range(99):
            monitor.record_loop_execution(success=True, latency_ms=60.0)
        monitor.record_loop_execution(success=False, latency_ms=60.0, error_description="single-err")

        # Threshold check – success_rate == 0.99, no alert expected
        await monitor._check_thresholds()
        assert len([a for a in alerts if a["type"] == "success_rate"]) == 0

        # Push below threshold
        for _ in range(2):
            monitor.record_loop_execution(success=False, latency_ms=60.0, error_description="extra-err")

        await monitor._check_thresholds()
        assert len([a for a in alerts if a["type"] == "success_rate"]) == 1

        # Verify summary
        summary = monitor.get_summary()
        assert summary["loops_executed"] == 102
        assert summary["loops_failed"] == 3

        # Reset and verify clean slate
        monitor.reset()
        assert monitor.metrics.loops_executed == 0
        assert monitor.metrics.errors == []

    def test_peak_memory_mb_field_accessible(self):
        """peak_memory_mb field can be set externally."""
        monitor = ProductionMonitor()
        monitor.metrics.peak_memory_mb = 512.0
        summary = monitor.get_summary()
        assert summary["peak_memory_mb"] == 512.0
