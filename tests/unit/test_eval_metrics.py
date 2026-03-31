# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for GAIA Agent Eval Metrics Collector."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gaia.eval.eval_metrics import (
    EvalMetricsCollector,
    EvalScenarioMetrics,
    get_all_collectors,
    get_eval_collector,
    remove_eval_collector,
)


class TestEvalScenarioMetrics:
    """Tests for EvalScenarioMetrics dataclass."""

    def test_create_metrics(self):
        """Test creating a metrics instance."""
        metrics = EvalScenarioMetrics(
            scenario_id="test_scenario",
            run_id="test_run",
        )
        assert metrics.scenario_id == "test_scenario"
        assert metrics.run_id == "test_run"
        assert metrics.duration_seconds == 0.0
        assert metrics.status == "PENDING"

    def test_start_records_timestamp(self):
        """Test that start() records the start time."""
        metrics = EvalScenarioMetrics(
            scenario_id="test_scenario",
            run_id="test_run",
        )
        before = datetime.now(timezone.utc)
        metrics.start()
        after = datetime.now(timezone.utc)

        assert metrics.start_time is not None
        assert before <= metrics.start_time <= after

    def test_end_calculates_duration(self):
        """Test that end() calculates duration correctly."""
        metrics = EvalScenarioMetrics(
            scenario_id="test_scenario",
            run_id="test_run",
        )
        metrics.start()
        time.sleep(0.1)
        metrics.end()

        assert metrics.duration_seconds >= 0.1
        assert metrics.end_time is not None

    def test_to_dict_serializes(self):
        """Test that to_dict() produces valid JSON-serializable dict."""
        metrics = EvalScenarioMetrics(
            scenario_id="test_scenario",
            run_id="test_run",
            duration_seconds=45.5,
            tokens_generated=1500,
            cost_estimate_usd=0.02,
            status="PASS",
        )

        result = metrics.to_dict()
        assert result["scenario_id"] == "test_scenario"
        assert result["run_id"] == "test_run"
        assert result["duration_seconds"] == 45.5
        assert result["tokens_generated"] == 1500
        assert result["cost_estimate_usd"] == 0.02
        assert result["status"] == "PASS"

        # Verify JSON serialization works
        json_str = json.dumps(result)
        assert json_str is not None

    def test_from_result_extract_metrics(self):
        """Test creating metrics from eval result dict."""
        result = {
            "scenario_id": "test_scenario",
            "status": "PASS",
            "overall_score": 8.5,
            "elapsed_s": 65.3,
            "cost_estimate": {
                "turns": 5,
                "estimated_usd": 0.025,
            },
        }

        metrics = EvalScenarioMetrics.from_result(
            run_id="test_run",
            scenario_id="test_scenario",
            result=result,
        )

        assert metrics.duration_seconds == 65.3
        assert metrics.cost_estimate_usd == 0.025
        assert metrics.tokens_generated == 500  # 5 turns * 100
        assert metrics.status == "PASS"

    def test_from_result_handles_missing_fields(self):
        """Test from_result handles missing fields gracefully."""
        result = {
            "scenario_id": "test_scenario",
            "status": "FAIL",
        }

        metrics = EvalScenarioMetrics.from_result(
            run_id="test_run",
            scenario_id="test_scenario",
            result=result,
        )

        assert metrics.duration_seconds == 0.0
        assert metrics.cost_estimate_usd == 0.0
        assert metrics.tokens_generated == 0
        assert metrics.status == "FAIL"


class TestEvalMetricsCollector:
    """Tests for EvalMetricsCollector class."""

    def test_create_collector(self):
        """Test creating a collector."""
        collector = EvalMetricsCollector(run_id="test_run")
        assert collector.run_id == "test_run"

    def test_start_run_records_timestamp(self):
        """Test that start_run() records the start time."""
        collector = EvalMetricsCollector(run_id="test_run")
        before = datetime.now(timezone.utc)
        collector.start_run()
        after = datetime.now(timezone.utc)

        assert collector._started_at is not None
        assert before <= collector._started_at <= after

    def test_end_run_records_timestamp(self):
        """Test that end_run() records the end time."""
        collector = EvalMetricsCollector(run_id="test_run")
        collector.start_run()
        time.sleep(0.05)
        collector.end_run()

        assert collector._ended_at is not None

    def test_start_scenario_creates_metrics(self):
        """Test that start_scenario creates and starts metrics."""
        collector = EvalMetricsCollector(run_id="test_run")
        before = datetime.now(timezone.utc)
        metrics = collector.start_scenario("scenario_001")
        after = datetime.now(timezone.utc)

        assert metrics is not None
        assert metrics.scenario_id == "scenario_001"
        assert metrics.run_id == "test_run"
        assert metrics.start_time is not None
        assert before <= metrics.start_time <= after

    def test_end_scenario_updates_metrics(self):
        """Test that end_scenario updates metrics from result."""
        collector = EvalMetricsCollector(run_id="test_run")
        collector.start_scenario("scenario_001")
        time.sleep(0.1)

        result = {
            "scenario_id": "scenario_001",
            "status": "PASS",
            "elapsed_s": 45.5,
            "cost_estimate": {
                "turns": 3,
                "estimated_usd": 0.015,
            },
        }

        metrics = collector.end_scenario("scenario_001", result)

        assert metrics.duration_seconds == 45.5
        assert metrics.cost_estimate_usd == 0.015
        assert metrics.status == "PASS"

    def test_end_scenario_without_start(self):
        """Test end_scenario works even if start_scenario wasn't called."""
        collector = EvalMetricsCollector(run_id="test_run")

        result = {
            "scenario_id": "scenario_001",
            "status": "FAIL",
            "elapsed_s": 120.5,
            "cost_estimate": {
                "turns": 10,
                "estimated_usd": 0.05,
            },
        }

        metrics = collector.end_scenario("scenario_001", result)

        assert metrics is not None
        assert metrics.duration_seconds == 120.5
        assert metrics.status == "FAIL"

    def test_get_metrics_returns_correct_scenario(self):
        """Test getting metrics for a specific scenario."""
        collector = EvalMetricsCollector(run_id="test_run")
        collector.start_scenario("scenario_001")
        collector.start_scenario("scenario_002")

        metrics_001 = collector.get_metrics("scenario_001")
        metrics_003 = collector.get_metrics("scenario_003")

        assert metrics_001 is not None
        assert metrics_001.scenario_id == "scenario_001"
        assert metrics_003 is None

    def test_get_all_metrics(self):
        """Test getting all metrics."""
        collector = EvalMetricsCollector(run_id="test_run")
        collector.start_scenario("scenario_001")
        collector.start_scenario("scenario_002")

        all_metrics = collector.get_all_metrics()

        assert len(all_metrics) == 2
        assert "scenario_001" in all_metrics
        assert "scenario_002" in all_metrics

    def test_get_run_summary(self):
        """Test getting run summary."""
        collector = EvalMetricsCollector(run_id="test_run")
        collector.start_run()

        # Add some scenarios
        collector.start_scenario("scenario_001")
        collector.end_scenario("scenario_001", {
            "status": "PASS",
            "elapsed_s": 50.0,
            "cost_estimate": {"turns": 5, "estimated_usd": 0.025},
        })

        collector.start_scenario("scenario_002")
        collector.end_scenario("scenario_002", {
            "status": "FAIL",
            "elapsed_s": 100.0,
            "cost_estimate": {"turns": 10, "estimated_usd": 0.05},
        })

        collector.end_run()
        summary = collector.get_run_summary()

        assert summary["run_id"] == "test_run"
        assert summary["total_scenarios"] == 2
        assert summary["total_duration_seconds"] == 150.0
        assert summary["avg_duration_seconds"] == 75.0
        assert abs(summary["total_cost_usd"] - 0.075) < 0.001
        assert summary["total_tokens"] == 1500

    def test_get_run_summary_empty(self):
        """Test summary with no scenarios."""
        collector = EvalMetricsCollector(run_id="test_run")
        collector.start_run()
        collector.end_run()
        summary = collector.get_run_summary()

        assert summary["total_scenarios"] == 0
        assert summary["total_duration_seconds"] == 0.0
        assert summary["avg_duration_seconds"] == 0.0

    def test_get_metrics_by_status(self):
        """Test grouping scenarios by status."""
        collector = EvalMetricsCollector(run_id="test_run")

        collector.start_scenario("pass_001")
        collector.end_scenario("pass_001", {"status": "PASS", "elapsed_s": 50.0})

        collector.start_scenario("pass_002")
        collector.end_scenario("pass_002", {"status": "PASS", "elapsed_s": 60.0})

        collector.start_scenario("fail_001")
        collector.end_scenario("fail_001", {"status": "FAIL", "elapsed_s": 100.0})

        by_status = collector.get_metrics_by_status()

        assert "PASS" in by_status
        assert "FAIL" in by_status
        assert len(by_status["PASS"]) == 2
        assert len(by_status["FAIL"]) == 1

    def test_get_slowest_scenarios(self):
        """Test getting slowest scenarios."""
        collector = EvalMetricsCollector(run_id="test_run")

        # Add scenarios with different durations
        for i, duration in enumerate([30.0, 90.0, 45.0, 120.0, 60.0]):
            collector.start_scenario(f"scenario_{i:03d}")
            collector.end_scenario(f"scenario_{i:03d}", {
                "status": "PASS",
                "elapsed_s": duration,
            })

        slowest = collector.get_slowest_scenarios(n=3)

        assert len(slowest) == 3
        assert slowest[0]["scenario_id"] == "scenario_003"  # 120s
        assert slowest[0]["duration_seconds"] == 120.0
        assert slowest[1]["scenario_id"] == "scenario_001"  # 90s
        assert slowest[2]["scenario_id"] == "scenario_004"  # 60s

    def test_clear(self):
        """Test clearing all metrics."""
        collector = EvalMetricsCollector(run_id="test_run")
        collector.start_run()
        collector.start_scenario("scenario_001")
        collector.start_scenario("scenario_002")

        collector.clear()

        assert len(collector.get_all_metrics()) == 0
        assert collector._started_at is None

    def test_thread_safety(self):
        """Test thread-safe concurrent access."""
        import threading

        collector = EvalMetricsCollector(run_id="test_run")

        def add_scenario(scenario_id):
            collector.start_scenario(scenario_id)
            time.sleep(0.01)
            collector.end_scenario(scenario_id, {
                "status": "PASS",
                "elapsed_s": 50.0,
            })

        threads = []
        for i in range(10):
            t = threading.Thread(target=add_scenario, args=(f"scenario_{i:03d}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        all_metrics = collector.get_all_metrics()
        assert len(all_metrics) == 10


class TestGlobalRegistry:
    """Tests for global collector registry."""

    def test_get_eval_collector_creates_new(self):
        """Test getting a collector creates it if not exists."""
        # Clean up any existing collector
        remove_eval_collector("test_registry_run")

        collector = get_eval_collector("test_registry_run")
        assert collector is not None
        assert collector.run_id == "test_registry_run"

    def test_get_eval_collector_returns_same_instance(self):
        """Test getting a collector returns the same instance."""
        remove_eval_collector("test_registry_run")

        collector1 = get_eval_collector("test_registry_run")
        collector2 = get_eval_collector("test_registry_run")

        assert collector1 is collector2

    def test_remove_eval_collector(self):
        """Test removing a collector."""
        remove_eval_collector("test_registry_run")
        get_eval_collector("test_registry_run")

        result = remove_eval_collector("test_registry_run")
        assert result is True

        # Getting it again should create a new one
        collector = get_eval_collector("test_registry_run")
        assert collector is not None

    def test_remove_nonexistent_collector(self):
        """Test removing a collector that doesn't exist."""
        result = remove_eval_collector("nonexistent_run")
        assert result is False

    def test_get_all_collectors(self):
        """Test getting all registered collectors."""
        # Clean up first
        for run_id in list(get_all_collectors().keys()):
            remove_eval_collector(run_id)

        get_eval_collector("run_001")
        get_eval_collector("run_002")
        get_eval_collector("run_003")

        all_collectors = get_all_collectors()
        assert len(all_collectors) == 3
        assert "run_001" in all_collectors
        assert "run_002" in all_collectors
        assert "run_003" in all_collectors


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
