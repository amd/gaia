# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Eval Metrics Collector

Lightweight metrics collection for GAIA Agent eval framework.
Captures performance metrics during eval scenario execution without
modifying the core eval architecture (claude -p subprocess pattern).

This module provides:
- EvalScenarioMetrics: Dataclass for per-scenario performance data
- EvalMetricsCollector: Thread-safe collector for eval metrics
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class EvalScenarioMetrics:
    """
    Performance metrics for a single eval scenario run.

    Attributes:
        scenario_id: Unique identifier for the scenario
        run_id: Eval run identifier
        start_time: When scenario execution started
        end_time: When scenario execution ended
        duration_seconds: Total execution time in seconds
        tokens_generated: Estimated token count (from cost_estimate)
        cost_estimate_usd: Estimated cost in USD
        status: Final scenario status (PASS, FAIL, etc.)
        metadata: Additional performance data
    """

    scenario_id: str
    run_id: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    tokens_generated: int = 0
    cost_estimate_usd: float = 0.0
    status: str = "PENDING"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def start(self) -> None:
        """Mark scenario execution as started."""
        self.start_time = datetime.now(timezone.utc)

    def end(self) -> None:
        """Mark scenario execution as ended and calculate duration."""
        if self.start_time:
            self.end_time = datetime.now(timezone.utc)
            self.duration_seconds = (self.end_time - self.start_time).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "scenario_id": self.scenario_id,
            "run_id": self.run_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.duration_seconds,
            "tokens_generated": self.tokens_generated,
            "cost_estimate_usd": self.cost_estimate_usd,
            "status": self.status,
            "metadata": self.metadata,
        }

    @classmethod
    def from_result(cls, run_id: str, scenario_id: str, result: Dict[str, Any]) -> "EvalScenarioMetrics":
        """
        Create metrics from eval result dictionary.

        Args:
            run_id: Eval run identifier
            scenario_id: Scenario identifier
            result: Result dict from run_scenario_subprocess

        Returns:
            EvalScenarioMetrics instance
        """
        metrics = cls(
            scenario_id=scenario_id,
            run_id=run_id,
        )

        # Extract duration from elapsed_s if present
        if "elapsed_s" in result:
            metrics.duration_seconds = result["elapsed_s"]

        # Extract cost estimate
        cost_est = result.get("cost_estimate", {})
        metrics.cost_estimate_usd = cost_est.get("estimated_usd", 0.0)
        metrics.tokens_generated = cost_est.get("turns", 0) * 100  # Rough estimate: 100 tokens/turn

        # Extract status
        metrics.status = result.get("status", "UNKNOWN")

        return metrics


class EvalMetricsCollector:
    """
    Thread-safe metrics collector for GAIA Agent eval framework.

    The EvalMetricsCollector captures performance metrics during eval
    scenario execution. It provides minimal overhead and does not
    modify the core eval architecture.

    Features:
    - Per-scenario timing capture
    - Token and cost estimation
    - Run-level aggregation
    - Thread-safe for parallel execution

    Example:
        >>> collector = EvalMetricsCollector(run_id="eval-20250101")
        >>> metrics = collector.start_scenario("scenario-001")
        >>> # ... run scenario ...
        >>> collector.end_scenario("scenario-001", result)
        >>> all_metrics = collector.get_all_metrics()
    """

    def __init__(self, run_id: str):
        """
        Initialize eval metrics collector.

        Args:
            run_id: Unique identifier for the eval run
        """
        self.run_id = run_id
        self._lock = threading.RLock()
        self._scenario_metrics: Dict[str, EvalScenarioMetrics] = {}
        self._started_at: Optional[datetime] = None
        self._ended_at: Optional[datetime] = None

    def start_run(self) -> None:
        """Mark the eval run as started."""
        with self._lock:
            self._started_at = datetime.now(timezone.utc)

    def end_run(self) -> None:
        """Mark the eval run as ended."""
        with self._lock:
            self._ended_at = datetime.now(timezone.utc)

    def start_scenario(self, scenario_id: str) -> EvalScenarioMetrics:
        """
        Start tracking a scenario execution.

        Args:
            scenario_id: Unique scenario identifier

        Returns:
            EvalScenarioMetrics instance for the scenario
        """
        with self._lock:
            metrics = EvalScenarioMetrics(
                scenario_id=scenario_id,
                run_id=self.run_id,
            )
            metrics.start()
            self._scenario_metrics[scenario_id] = metrics
            return metrics

    def end_scenario(self, scenario_id: str, result: Dict[str, Any]) -> EvalScenarioMetrics:
        """
        End tracking a scenario and capture result metrics.

        Args:
            scenario_id: Unique scenario identifier
            result: Result dictionary from scenario execution

        Returns:
            EvalScenarioMetrics instance with final values
        """
        with self._lock:
            if scenario_id not in self._scenario_metrics:
                # Scenario wasn't started, create from result
                metrics = EvalScenarioMetrics.from_result(self.run_id, scenario_id, result)
                self._scenario_metrics[scenario_id] = metrics
            else:
                metrics = self._scenario_metrics[scenario_id]
                metrics.end()
                # Extract additional data from result
                cost_est = result.get("cost_estimate", {})
                metrics.cost_estimate_usd = cost_est.get("estimated_usd", 0.0)
                metrics.tokens_generated = cost_est.get("turns", 0) * 100
                metrics.status = result.get("status", "UNKNOWN")
                # Store elapsed_s from result as more accurate duration
                if "elapsed_s" in result:
                    metrics.duration_seconds = result["elapsed_s"]

            return metrics

    def get_metrics(self, scenario_id: str) -> Optional[EvalScenarioMetrics]:
        """
        Get metrics for a specific scenario.

        Args:
            scenario_id: Scenario identifier

        Returns:
            EvalScenarioMetrics or None if not found
        """
        with self._lock:
            return self._scenario_metrics.get(scenario_id)

    def get_all_metrics(self) -> Dict[str, EvalScenarioMetrics]:
        """
        Get metrics for all scenarios in the run.

        Returns:
            Dictionary mapping scenario_id to EvalScenarioMetrics
        """
        with self._lock:
            return dict(self._scenario_metrics)

    def get_run_summary(self) -> Dict[str, Any]:
        """
        Get aggregate metrics summary for the entire run.

        Returns:
            Dictionary with aggregate statistics
        """
        with self._lock:
            if not self._scenario_metrics:
                return {
                    "run_id": self.run_id,
                    "total_scenarios": 0,
                    "total_duration_seconds": 0.0,
                    "avg_duration_seconds": 0.0,
                    "total_cost_usd": 0.0,
                    "total_tokens": 0,
                }

            total_duration = sum(m.duration_seconds for m in self._scenario_metrics.values())
            total_cost = sum(m.cost_estimate_usd for m in self._scenario_metrics.values())
            total_tokens = sum(m.tokens_generated for m in self._scenario_metrics.values())

            return {
                "run_id": self.run_id,
                "total_scenarios": len(self._scenario_metrics),
                "total_duration_seconds": total_duration,
                "avg_duration_seconds": total_duration / len(self._scenario_metrics),
                "total_cost_usd": total_cost,
                "total_tokens": total_tokens,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "ended_at": self._ended_at.isoformat() if self._ended_at else None,
            }

    def get_metrics_by_status(self) -> Dict[str, List[str]]:
        """
        Group scenario IDs by their status.

        Returns:
            Dictionary mapping status to list of scenario IDs
        """
        with self._lock:
            by_status: Dict[str, List[str]] = {}
            for scenario_id, metrics in self._scenario_metrics.items():
                status = metrics.status
                if status not in by_status:
                    by_status[status] = []
                by_status[status].append(scenario_id)
            return by_status

    def get_slowest_scenarios(self, n: int = 5) -> List[Dict[str, Any]]:
        """
        Get the N slowest scenarios by duration.

        Args:
            n: Number of scenarios to return

        Returns:
            List of dicts with scenario_id, duration_seconds, status
        """
        with self._lock:
            sorted_metrics = sorted(
                self._scenario_metrics.values(),
                key=lambda m: m.duration_seconds,
                reverse=True,
            )[:n]
            return [
                {
                    "scenario_id": m.scenario_id,
                    "duration_seconds": m.duration_seconds,
                    "status": m.status,
                }
                for m in sorted_metrics
            ]

    def clear(self) -> None:
        """Clear all collected metrics."""
        with self._lock:
            self._scenario_metrics.clear()
            self._started_at = None
            self._ended_at = None


# Global registry for eval metrics collectors
_eval_collectors: Dict[str, EvalMetricsCollector] = {}
_registry_lock = threading.Lock()


def get_eval_collector(run_id: str) -> EvalMetricsCollector:
    """
    Get or create an EvalMetricsCollector for an eval run.

    This is a convenience function for getting a collector from the
    global registry.

    Args:
        run_id: Unique eval run identifier

    Returns:
        EvalMetricsCollector instance
    """
    with _registry_lock:
        if run_id not in _eval_collectors:
            _eval_collectors[run_id] = EvalMetricsCollector(run_id=run_id)
        return _eval_collectors[run_id]


def remove_eval_collector(run_id: str) -> bool:
    """
    Remove an eval collector from the registry.

    Args:
        run_id: Eval run identifier to remove

    Returns:
        True if removed, False if not found
    """
    with _registry_lock:
        if run_id in _eval_collectors:
            del _eval_collectors[run_id]
            return True
        return False


def get_all_collectors() -> Dict[str, EvalMetricsCollector]:
    """
    Get all registered eval collectors.

    Returns:
        Dictionary mapping run IDs to collectors
    """
    with _registry_lock:
        return dict(_eval_collectors)
