"""
GAIA Production Monitor

Real-time monitoring and alerting for GAIA pipeline production deployments.
Tracks success rates, latency, memory, and error counts with configurable
alert thresholds and callback-based notification.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable
import logging

from gaia.utils.logging import get_logger


logger = get_logger(__name__)


@dataclass
class ProductionMetrics:
    """
    Runtime metrics for a production GAIA pipeline deployment.

    Tracks execution counts, latency, memory, and errors across all
    pipeline loops. All counters are updated via ProductionMonitor's
    record_loop_execution() method.

    Attributes:
        loops_executed: Total number of loop executions attempted
        loops_successful: Number of loops that completed without error
        loops_failed: Number of loops that failed
        total_latency_ms: Cumulative latency across all successful loops (ms)
        peak_memory_mb: Peak memory usage observed (MB)
        errors: List of error description strings from failed loops
    """

    loops_executed: int = 0
    loops_successful: int = 0
    loops_failed: int = 0
    total_latency_ms: float = 0.0
    peak_memory_mb: float = 0.0
    errors: List[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """
        Compute success rate as a fraction (0.0-1.0).

        Returns 1.0 when no executions have been recorded (no-failure
        assumption for an idle system).

        Returns:
            Fraction of successful loops over total executions.
        """
        if self.loops_executed == 0:
            return 1.0
        return self.loops_successful / self.loops_executed

    @property
    def avg_latency_ms(self) -> float:
        """
        Compute average latency per executed loop in milliseconds.

        Returns 0.0 when no executions have been recorded.

        Returns:
            Average latency in milliseconds.
        """
        if self.loops_executed == 0:
            return 0.0
        return self.total_latency_ms / self.loops_executed


class ProductionMonitor:
    """
    Background monitor for GAIA pipeline production health.

    Periodically evaluates ProductionMetrics against configurable alert
    thresholds and fires an optional alert callback when thresholds are
    exceeded. Designed for use with asyncio event loops.

    Alert Thresholds (Production Defaults from P3 Validation):
    - success_rate < 0.99 triggers WARNING (when loops_executed > 0)
    - len(errors) > 10 triggers WARNING

    Example:
        >>> monitor = ProductionMonitor(
        ...     metrics=ProductionMetrics(),
        ...     alert_thresholds={"min_success_rate": 0.99, "max_errors": 10},
        ...     alert_callback=lambda alert: notify_oncall(alert)
        ... )
        >>> monitor.record_loop_execution(success=True, latency_ms=62.0)
        >>> await monitor.start_monitoring()
    """

    def __init__(
        self,
        metrics: Optional[ProductionMetrics] = None,
        alert_thresholds: Optional[Dict[str, float]] = None,
        alert_callback: Optional[Callable[[Dict], None]] = None,
        check_interval_seconds: float = 60.0,
    ):
        """
        Initialize the production monitor.

        Supports two calling conventions:

        1. Explicit (new, preferred by RUNBOOK and production smoke tests)::

            ProductionMonitor(
                metrics=ProductionMetrics(),
                alert_thresholds={"min_success_rate": 0.99, "max_errors": 10},
                alert_callback=my_callback,
            )

        2. Legacy (original API, retained for backwards compatibility)::

            ProductionMonitor(
                check_interval_seconds=60.0,
                alert_callback=my_callback,
            )

        Args:
            metrics: Optional pre-created ProductionMetrics instance. When
                omitted a fresh instance is created automatically.
            alert_thresholds: Dict of threshold values. Supported keys:
                ``min_success_rate`` (default 0.99) and ``max_errors``
                (default 10). When omitted the P3-validated production
                defaults are used.
            alert_callback: Optional callable invoked with an alert dict when
                a threshold is breached. Signature: callback(alert: dict) -> None
            check_interval_seconds: How often to evaluate thresholds in the
                background monitoring loop (default: 60.0).
        """
        self.metrics = metrics if metrics is not None else ProductionMetrics()
        self.alert_thresholds = alert_thresholds if alert_thresholds is not None else {
            "min_success_rate": 0.99,
            "max_errors": 10,
        }
        self.alert_callback = alert_callback
        # Retain underscore alias so legacy internal references still resolve
        self._alert_callback = alert_callback
        self._check_interval = check_interval_seconds
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None

        logger.info(
            "ProductionMonitor initialized",
            extra={"check_interval_seconds": check_interval_seconds},
        )

    async def start_monitoring(self) -> None:
        """
        Start background monitoring loop.

        Runs _check_thresholds() every check_interval_seconds until
        stop_monitoring() is called. This coroutine runs indefinitely
        and should be scheduled as an asyncio Task.
        """
        self._monitoring = True
        logger.info("ProductionMonitor: monitoring started")

        while self._monitoring:
            await self._check_thresholds()
            await asyncio.sleep(self._check_interval)

    def stop_monitoring(self) -> None:
        """
        Signal the monitoring loop to stop after the current sleep cycle.

        Does not cancel an in-flight _check_thresholds() call; the loop
        exits cleanly after the current sleep completes.
        """
        self._monitoring = False
        logger.info("ProductionMonitor: monitoring stopped")

    def record_loop_execution(
        self,
        success: bool,
        latency_ms: float,
        error_description: Optional[str] = None,
    ) -> None:
        """
        Record the outcome of a single pipeline loop execution.

        Updates loops_executed, loops_successful or loops_failed,
        total_latency_ms, and (on failure) appends to errors.

        Args:
            success: True if the loop completed without error
            latency_ms: Execution duration in milliseconds
            error_description: Optional error description (appended to
                metrics.errors on failure; auto-generated if not provided)
        """
        self.metrics.loops_executed += 1
        self.metrics.total_latency_ms += latency_ms

        if success:
            self.metrics.loops_successful += 1
        else:
            self.metrics.loops_failed += 1
            description = error_description or f"Loop execution failed at {datetime.now(timezone.utc).isoformat()}"
            self.metrics.errors.append(description)

    def record_execution(
        self,
        latency_ms: float,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """
        Alternate API for recording execution (matches task specification).

        Delegates to record_loop_execution with re-ordered parameters.

        Args:
            latency_ms: Execution duration in milliseconds
            success: True if the loop completed without error
            error: Optional error string to record on failure
        """
        self.record_loop_execution(
            success=success,
            latency_ms=latency_ms,
            error_description=error,
        )

    async def _check_thresholds(self) -> None:
        """
        Evaluate alert thresholds and fire callback if any are breached.

        Threshold conditions (both can trigger independently):
        1. success_rate < min_success_rate AND loops_executed > 0 -> WARNING
        2. len(errors) > max_errors -> WARNING

        Threshold values are read from ``self.alert_thresholds`` with
        safe defaults of 0.99 and 10 respectively.

        Alerts are dicts with at minimum ``type`` and ``message`` keys.
        Each alert is passed individually to ``self.alert_callback`` when set.
        """
        alerts = []
        min_success_rate = self.alert_thresholds.get("min_success_rate", 0.99)
        max_errors = self.alert_thresholds.get("max_errors", 10)

        if self.metrics.loops_executed > 0 and self.metrics.success_rate < min_success_rate:
            alert = {
                "level": "WARNING",
                "type": "success_rate",
                "message": (
                    f"ALERT: Success rate {self.metrics.success_rate:.2%} below threshold"
                ),
                "success_rate": self.metrics.success_rate,
                "loops_executed": self.metrics.loops_executed,
                "loops_failed": self.metrics.loops_failed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            alerts.append(alert)
            logger.warning(alert["message"])

        if len(self.metrics.errors) > max_errors:
            alert = {
                "level": "WARNING",
                "type": "error_count",
                "message": (
                    f"ALERT: Error count {len(self.metrics.errors)} exceeds threshold"
                ),
                "error_count": len(self.metrics.errors),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            alerts.append(alert)
            logger.warning(alert["message"])

        callback = self.alert_callback
        if alerts and callback:
            for alert in alerts:
                try:
                    callback(alert)
                except Exception as e:
                    logger.error(f"Alert callback raised an exception: {e}")

    def get_summary(self) -> Dict:
        """
        Return all current metrics as a dictionary.

        Returns:
            Dictionary with all ProductionMetrics fields plus computed
            properties (success_rate, avg_latency_ms).
        """
        return {
            "loops_executed": self.metrics.loops_executed,
            "loops_successful": self.metrics.loops_successful,
            "loops_failed": self.metrics.loops_failed,
            "success_rate": self.metrics.success_rate,
            "total_latency_ms": self.metrics.total_latency_ms,
            "avg_latency_ms": self.metrics.avg_latency_ms,
            "peak_memory_mb": self.metrics.peak_memory_mb,
            "error_count": len(self.metrics.errors),
            "errors": list(self.metrics.errors),
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        }

    def reset(self) -> None:
        """
        Reset all metrics counters to zero.

        Useful for beginning a new monitoring window without creating
        a new ProductionMonitor instance.
        """
        self.metrics = ProductionMetrics()
        logger.info("ProductionMonitor: metrics reset")
