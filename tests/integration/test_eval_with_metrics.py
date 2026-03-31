# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for GAIA Agent Eval with Metrics Collection.

These tests verify that the metrics collection integrates correctly
with the eval runner and produces expected outputs.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.eval.eval_metrics import EvalMetricsCollector
from gaia.eval.runner import run_scenario_subprocess


class TestEvalMetricsIntegration:
    """Integration tests for eval metrics collection."""

    @pytest.fixture
    def mock_scenario_data(self):
        """Sample scenario data for testing."""
        return {
            "id": "test_scenario_001",
            "category": "knowledge_qa",
            "description": "Test scenario for metrics integration",
            "persona": "casual_user",
            "setup": {
                "index_documents": [],
            },
            "turns": [
                {
                    "turn": 1,
                    "objective": "Test objective",
                    "success_criteria": "Test criteria",
                }
            ],
        }

    @pytest.fixture
    def temp_run_dir(self):
        """Create a temporary run directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "traces").mkdir()
            yield run_dir

    def test_metrics_collector_with_mock_result(self, temp_run_dir):
        """Test metrics collector captures mock scenario result."""
        collector = EvalMetricsCollector(run_id="test_run_001")

        # Simulate scenario execution
        collector.start_scenario("test_scenario_001")

        # Mock result
        result = {
            "scenario_id": "test_scenario_001",
            "status": "PASS",
            "overall_score": 8.5,
            "elapsed_s": 45.5,
            "cost_estimate": {
                "turns": 5,
                "estimated_usd": 0.025,
            },
            "turns": [],
        }

        metrics = collector.end_scenario("test_scenario_001", result)

        assert metrics.duration_seconds == 45.5
        assert metrics.cost_estimate_usd == 0.025
        assert metrics.status == "PASS"

        # Verify summary
        summary = collector.get_run_summary()
        assert summary["total_scenarios"] == 1
        assert summary["total_duration_seconds"] == 45.5

    def test_metrics_saved_to_file(self, temp_run_dir):
        """Test metrics summary is saved to file."""
        collector = EvalMetricsCollector(run_id="test_run_001")
        collector.start_run()

        collector.start_scenario("test_scenario_001")
        collector.end_scenario("test_scenario_001", {
            "status": "PASS",
            "elapsed_s": 30.0,
            "cost_estimate": {"turns": 3, "estimated_usd": 0.015},
        })

        collector.end_run()

        # Save metrics to file
        metrics_path = temp_run_dir / "metrics_summary.json"
        metrics_path.write_text(
            json.dumps(collector.get_run_summary(), indent=2),
            encoding="utf-8",
        )

        # Verify file exists and contents
        assert metrics_path.exists()
        loaded = json.loads(metrics_path.read_text())
        assert loaded["run_id"] == "test_run_001"
        assert loaded["total_scenarios"] == 1

    def test_run_scenario_subprocess_accepts_metrics_collector(self, mock_scenario_data, temp_run_dir):
        """Test that run_scenario_subprocess accepts metrics_collector parameter."""
        # This test verifies the function signature accepts the new parameter
        # without actually running the claude subprocess

        collector = EvalMetricsCollector(run_id="test_run_002")

        # Mock the subprocess call to avoid actual claude invocation
        with patch("gaia.eval.runner.subprocess.run") as mock_proc:
            mock_proc.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({
                    "result": json.dumps({
                        "scenario_id": "test_scenario_001",
                        "status": "PASS",
                        "overall_score": 7.5,
                        "turns": [{
                            "turn": 1,
                            "scores": {
                                "correctness": 7.0,
                                "tool_selection": 7.0,
                                "context_retention": 7.0,
                                "completeness": 7.0,
                                "efficiency": 7.0,
                                "personality": 7.0,
                                "error_recovery": 7.0,
                            },
                            "overall_score": 7.0,
                            "pass": True,
                        }],
                    })
                }),
                stderr="",
            )

            # Mock MANIFEST.read_text by patching the module-level variable
            with patch("gaia.eval.runner.MANIFEST") as mock_manifest:
                mock_manifest.exists.return_value = True
                mock_manifest.read_text.return_value = '{"documents": []}'

                with patch("gaia.eval.runner.build_scenario_prompt", return_value="test prompt"):
                    # This should not raise TypeError about unexpected argument
                    try:
                        result = run_scenario_subprocess(
                            _scenario_path=Path("/tmp/test.yaml"),
                            scenario_data=mock_scenario_data,
                            run_dir=temp_run_dir,
                            backend_url="http://localhost:4200",
                            model="test-model",
                            budget="1.00",
                            timeout=60,
                            metrics_collector=collector,
                        )
                    except (json.JSONDecodeError, KeyError, AttributeError) as e:
                        # Expected for mock response parsing
                        pass

            # Verify metrics were captured
            metrics = collector.get_metrics("test_scenario_001")
            assert metrics is not None

    def test_multiple_scenarios_tracking(self, temp_run_dir):
        """Test tracking multiple scenarios in a single run."""
        collector = EvalMetricsCollector(run_id="test_run_003")
        collector.start_run()

        scenarios = [
            ("scenario_001", "PASS", 45.0, 0.02),
            ("scenario_002", "FAIL", 120.0, 0.06),
            ("scenario_003", "PASS", 30.0, 0.015),
            ("scenario_004", "TIMEOUT", 300.0, 0.0),
        ]

        for sid, status, duration, cost in scenarios:
            collector.start_scenario(sid)
            collector.end_scenario(sid, {
                "status": status,
                "elapsed_s": duration,
                "cost_estimate": {"turns": int(duration / 10), "estimated_usd": cost},
            })

        collector.end_run()

        # Verify all scenarios tracked
        all_metrics = collector.get_all_metrics()
        assert len(all_metrics) == 4

        # Verify summary calculations
        summary = collector.get_run_summary()
        assert summary["total_scenarios"] == 4
        assert summary["total_duration_seconds"] == 495.0
        assert abs(summary["total_cost_usd"] - 0.095) < 0.001

        # Verify by-status grouping
        by_status = collector.get_metrics_by_status()
        assert len(by_status["PASS"]) == 2
        assert len(by_status["FAIL"]) == 1
        assert len(by_status["TIMEOUT"]) == 1

        # Verify slowest scenarios
        slowest = collector.get_slowest_scenarios(n=2)
        assert len(slowest) == 2
        assert slowest[0]["scenario_id"] == "scenario_004"  # 300s
        assert slowest[1]["scenario_id"] == "scenario_002"  # 120s

    def test_metrics_backward_compatibility(self, temp_run_dir):
        """Test that metrics don't break existing result processing."""
        # Simulate old-style result without elapsed_s
        old_style_result = {
            "scenario_id": "test_scenario",
            "status": "PASS",
            "overall_score": 8.0,
            "cost_estimate": {"turns": 5, "estimated_usd": 0.025},
        }

        collector = EvalMetricsCollector(run_id="test_run_compat")
        collector.start_scenario("test_scenario")
        metrics = collector.end_scenario("test_scenario", old_style_result)

        # Should handle missing elapsed_s gracefully
        assert metrics.duration_seconds >= 0  # Will be time-based or 0
        assert metrics.cost_estimate_usd == 0.025

    def test_skipped_scenario_metrics(self, temp_run_dir):
        """Test metrics for skipped scenarios (no document)."""
        collector = EvalMetricsCollector(run_id="test_run_skip")

        # Skipped scenario result
        skipped_result = {
            "scenario_id": "real_world_001",
            "category": "real_world",
            "status": "SKIPPED_NO_DOCUMENT",
            "overall_score": None,
            "turns": [],
            "elapsed_s": 0.0,
            "cost_estimate": {"turns": 0, "estimated_usd": 0.0},
        }

        collector.start_scenario("real_world_001")
        metrics = collector.end_scenario("real_world_001", skipped_result)

        assert metrics.status == "SKIPPED_NO_DOCUMENT"
        assert metrics.duration_seconds == 0.0
        assert metrics.cost_estimate_usd == 0.0


class TestScorecardPerformanceField:
    """Tests for performance field in scorecard."""

    def test_performance_field_added(self):
        """Test that performance field is added to results."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "test_001",
                "status": "PASS",
                "overall_score": 8.5,
                "elapsed_s": 45.5,
                "cost_estimate": {"turns": 5, "estimated_usd": 0.025},
            }
        ]

        scorecard = build_scorecard("test_run", results, {"model": "test"})

        # Verify performance field was added
        assert len(scorecard["scenarios"]) == 1
        assert "performance" in scorecard["scenarios"][0]
        perf = scorecard["scenarios"][0]["performance"]
        assert perf["duration_seconds"] == 45.5
        assert perf["cost_estimate_usd"] == 0.025

    def test_performance_field_preserves_existing(self):
        """Test that existing performance fields are preserved."""
        from gaia.eval.scorecard import build_scorecard

        results = [
            {
                "scenario_id": "test_001",
                "status": "PASS",
                "overall_score": 8.5,
                "elapsed_s": 45.5,
                "cost_estimate": {"turns": 5, "estimated_usd": 0.025},
                "performance": {
                    "duration_seconds": 50.0,
                    "custom_field": "preserved",
                },
            }
        ]

        scorecard = build_scorecard("test_run", results, {"model": "test"})

        # Existing performance field should be preserved
        perf = scorecard["scenarios"][0]["performance"]
        assert perf["duration_seconds"] == 50.0
        assert perf["custom_field"] == "preserved"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x"])
