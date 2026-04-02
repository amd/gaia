# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the CLI-level iteration aggregation and reliability summary."""

import io
import json
import subprocess
import sys
from unittest.mock import patch

from gaia.cli import _print_reliability_summary


def _make_scorecard(scenario_statuses):
    """Create a minimal scorecard dict with the given scenario statuses.

    Args:
        scenario_statuses: dict mapping scenario_id -> status string
    """
    return {
        "run_id": "test-run",
        "scenarios": [
            {"scenario_id": sid, "status": status}
            for sid, status in scenario_statuses.items()
        ],
    }


class TestPrintReliabilitySummary:
    """Tests for _print_reliability_summary()."""

    def test_all_pass(self, capsys, tmp_path):
        """All scenarios passing across iterations produces GO signal."""
        sc1 = _make_scorecard({"s1": "PASS", "s2": "PASS"})
        sc2 = _make_scorecard({"s1": "PASS", "s2": "PASS"})

        with patch("gaia.eval.runner.RESULTS_DIR", tmp_path):
            _print_reliability_summary([sc1, sc2], pass_threshold=0.9)

        output = capsys.readouterr().out
        assert "GO" in output
        assert "NO_GO" not in output

    def test_partial_failure(self, capsys, tmp_path):
        """Scenario failing in some iterations produces NO_GO when below threshold."""
        sc1 = _make_scorecard({"s1": "PASS", "s2": "FAIL"})
        sc2 = _make_scorecard({"s1": "PASS", "s2": "FAIL"})

        with patch("gaia.eval.runner.RESULTS_DIR", tmp_path):
            _print_reliability_summary([sc1, sc2], pass_threshold=0.9)

        output = capsys.readouterr().out
        assert "NO_GO" in output

    def test_mixed_results_computes_rate(self, capsys, tmp_path):
        """Pass rate is computed correctly across iterations."""
        # s1: passes 2/3 = 66%, s2: passes 3/3 = 100%
        sc1 = _make_scorecard({"s1": "PASS", "s2": "PASS"})
        sc2 = _make_scorecard({"s1": "FAIL", "s2": "PASS"})
        sc3 = _make_scorecard({"s1": "PASS", "s2": "PASS"})

        with patch("gaia.eval.runner.RESULTS_DIR", tmp_path):
            _print_reliability_summary([sc1, sc2, sc3], pass_threshold=0.9)

        output = capsys.readouterr().out
        assert "2/3" in output  # s1 pass rate
        assert "3/3" in output  # s2 pass rate
        assert "3 iterations" in output

    def test_empty_scorecards(self, capsys):
        """Empty or None scorecards are handled gracefully."""
        _print_reliability_summary([None, None], pass_threshold=0.9)
        output = capsys.readouterr().out
        assert "No scenario results" in output

    def test_writes_report_json(self, tmp_path):
        """A reliability_report.json file is written to RESULTS_DIR."""
        sc1 = _make_scorecard({"s1": "PASS"})
        sc2 = _make_scorecard({"s1": "PASS"})

        with patch("gaia.eval.runner.RESULTS_DIR", tmp_path):
            _print_reliability_summary([sc1, sc2], pass_threshold=0.9)

        report_path = tmp_path / "reliability_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["iterations"] == 2
        assert report["readiness"] == "GO"
        assert len(report["scenarios"]) == 1
        assert report["scenarios"][0]["iteration_pass_rate"] == 1.0

    def test_threshold_boundary(self, capsys, tmp_path):
        """Exactly meeting the threshold should produce PASS/GO."""
        # 9/10 passes = 90% with threshold 0.9 -> GO
        scorecards = []
        for i in range(10):
            status = "PASS" if i < 9 else "FAIL"
            scorecards.append(_make_scorecard({"s1": status}))

        with patch("gaia.eval.runner.RESULTS_DIR", tmp_path):
            _print_reliability_summary(scorecards, pass_threshold=0.9)

        output = capsys.readouterr().out
        assert "GO" in output
        assert "NO_GO" not in output


class TestIterationsCLIValidation:
    """Tests for CLI-level iterations validation."""

    def test_iterations_zero_rejected(self):
        """--iterations 0 should print error and exit."""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--iterations", "0"],
            capture_output=True,
            text=True,
        )
        assert "must be >= 1" in result.stderr

    def test_fix_with_iterations_rejected(self):
        """--fix combined with --iterations > 1 should print error."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gaia.cli",
                "eval",
                "agent",
                "--fix",
                "--iterations",
                "3",
            ],
            capture_output=True,
            text=True,
        )
        assert "incompatible" in result.stderr

    def test_iterations_negative_rejected(self):
        """--iterations -1 should be rejected."""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--iterations", "-1"],
            capture_output=True,
            text=True,
        )
        assert "must be >= 1" in result.stderr
