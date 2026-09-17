# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the CI eval completeness gate (``gaia.eval.integrity_gate``).

The gate's whole contract is that ``--enforce`` moves the EXIT CODE and nothing
else â€” the findings stay visible in both modes. Both halves are asserted here:
report mode must exit 0 while still emitting the annotation and the job-summary
block, and enforce mode must exit 1 on the same input.
"""

import json
import os
import subprocess
import sys

import pytest

from gaia.eval.integrity_gate import main
from gaia.eval.scorecard import build_scorecard


@pytest.fixture(autouse=True)
def _isolate_job_summary(monkeypatch):
    """Never append fabricated gate findings to the CI job summary."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _result(scenario_id, status, score=9.0):
    return {
        "scenario_id": scenario_id,
        "status": status,
        "overall_score": score,
        "category": "tool_selection",
        "cost_estimate": {"estimated_usd": 0},
    }


@pytest.fixture
def workspace(tmp_path):
    """A baseline dir + results dir shaped like the workflow's `eval-out`."""
    baseline_dir = tmp_path / "baselines"
    results_dir = tmp_path / "eval-out"
    baseline_dir.mkdir()
    (results_dir / "tool_selection").mkdir(parents=True)

    def write(kind, results):
        card = build_scorecard("run", results, {})
        if kind == "baseline":
            path = baseline_dir / "scorecard_tool_selection.json"
        else:
            path = results_dir / "tool_selection" / "scorecard.json"
        path.write_text(json.dumps(card), encoding="utf-8")

    write(
        "baseline",
        [_result("known_path_read", "PASS"), _result("multi_step_plan", "PASS")],
    )
    return baseline_dir, results_dir, write


def _argv(baseline_dir, results_dir, *extra):
    return [
        "--baseline-dir",
        str(baseline_dir),
        "--results-dir",
        str(results_dir),
        "--category",
        "tool_selection",
        *extra,
    ]


def test_complete_run_passes_in_both_modes(workspace):
    baseline_dir, results_dir, write = workspace
    write(
        "current",
        [_result("known_path_read", "PASS"), _result("multi_step_plan", "FAIL", 3.0)],
    )

    # FAIL is a measurement, not a gap â€” completeness is indifferent to quality.
    assert main(_argv(baseline_dir, results_dir)) == 0
    assert main(_argv(baseline_dir, results_dir, "--enforce")) == 0


@pytest.mark.parametrize("status", ["INFRA_ERROR", "SETUP_ERROR", "TIMEOUT"])
def test_unmeasured_scenario_blocks_only_under_enforce(workspace, status, capsys):
    baseline_dir, results_dir, write = workspace
    write(
        "current",
        [_result("known_path_read", "PASS"), _result("multi_step_plan", status, 0.0)],
    )

    assert main(_argv(baseline_dir, results_dir)) == 0
    reported = capsys.readouterr().out
    assert "::warning::Integrity:" in reported
    assert "without a measurement" in reported
    assert "::error::" not in reported

    assert main(_argv(baseline_dir, results_dir, "--enforce")) == 1
    enforced = capsys.readouterr().out
    assert "::error::Integrity:" in enforced


def test_missing_baseline_scenario_blocks_only_under_enforce(workspace):
    baseline_dir, results_dir, write = workspace
    write("current", [_result("known_path_read", "PASS")])

    assert main(_argv(baseline_dir, results_dir)) == 0
    assert main(_argv(baseline_dir, results_dir, "--enforce")) == 1


def test_missing_scorecard_blocks_only_under_enforce(workspace, capsys):
    baseline_dir, results_dir, _ = workspace  # never write a current scorecard

    assert main(_argv(baseline_dir, results_dir)) == 0
    assert "no scorecard produced" in capsys.readouterr().out
    assert main(_argv(baseline_dir, results_dir, "--enforce")) == 1


def test_not_measured_category_is_reported_with_its_cause(workspace, capsys):
    baseline_dir, results_dir, write = workspace
    write(
        "current",
        [_result("known_path_read", "PASS"), _result("multi_step_plan", "PASS")],
    )

    argv = _argv(
        baseline_dir,
        results_dir,
        "--not-measured",
        "rag_quality",
        "--not-measured-reason",
        "the RAG embedder will not load on this runner (#3016)",
    )
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "rag_quality: NOT MEASURED" in out
    assert "#3016" in out

    assert main(argv + ["--enforce"]) == 1


def test_findings_reach_the_job_summary_in_report_mode(
    workspace, tmp_path, monkeypatch
):
    """Report mode must leave a durable record, not just a console line."""
    baseline_dir, results_dir, write = workspace
    write(
        "current",
        [
            _result("known_path_read", "PASS"),
            _result("multi_step_plan", "INFRA_ERROR", 0.0),
        ],
    )
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert main(_argv(baseline_dir, results_dir)) == 0

    written = summary.read_text(encoding="utf-8")
    assert "Eval integrity: INCOMPLETE" in written
    assert "without a measurement" in written
    assert "the build stays green" in written


def test_no_categories_is_a_miswired_workflow(tmp_path):
    """Checking nothing is a configuration error, never a pass."""
    assert main(["--baseline-dir", str(tmp_path), "--results-dir", str(tmp_path)]) == 1


@pytest.mark.parametrize("require_complete,expected", [(False, 0), (True, 2)])
@pytest.mark.parametrize(
    "status",
    [
        "INFRA_ERROR",
        "ERROR",
        "TIMEOUT",
        "BLOCKED_BY_ARCHITECTURE",
        "BUDGET_EXCEEDED",
        "SKIPPED_NO_DOCUMENT",
        "MISSING",
    ],
)
def test_cli_compare_completeness_opt_in(tmp_path, require_complete, expected, status):
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    baseline.write_text(
        json.dumps(
            build_scorecard(
                "baseline",
                [_result("one", "PASS" if status == "MISSING" else status, None)],
                {},
            )
        )
    )
    current.write_text(
        json.dumps(
            build_scorecard(
                "current",
                [] if status == "MISSING" else [_result("one", status, None)],
                {},
            )
        )
    )
    env = dict(os.environ, USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    command = [
        sys.executable,
        "-m",
        "gaia.cli",
        "eval",
        "agent",
        "--compare",
        str(baseline),
        str(current),
    ]
    if require_complete:
        command.append("--require-complete")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    if require_complete:
        assert (
            "missing from the run" in result.stdout
            if status == "MISSING"
            else "without a measurement" in result.stdout
        )
