# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The parallel eval's cross-lane report.

The case worth pinning is the silent one: a lane whose machine died uploads
nothing, which from the outside looks the same as a lane with nothing to say.
These assert it is reported as an absence, and that a run with no committed
baseline says out loud that it has no regression verdict to give.
"""

import json

import pytest

from gaia.eval.lane_summary import collect_lane, main, render

LANES = {
    "lanes": [
        {"lane": "tools", "categories": ["tool_selection", "error_recovery"]},
        {"lane": "memory", "categories": ["memory"]},
    ],
    "excluded": {"vision": "reads the screen"},
}


def _scorecard(total, passed, failed, **counters):
    summary = {"total_scenarios": total, "passed": passed, "failed": failed}
    summary.update(counters)
    return {"summary": summary}


@pytest.fixture
def artifacts(tmp_path):
    return tmp_path / "lane-artifacts"


def _write(root, rel, payload):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_lane_with_no_artifact_is_reported_as_errored(artifacts):
    artifacts.mkdir(parents=True)
    row = collect_lane(LANES["lanes"][0], artifacts, "eval-")
    assert row["present"] is False
    assert row["missing_categories"] == ["tool_selection", "error_recovery"]
    assert row["measured"] == 0
    assert "ERRORED" in render([row], has_baseline=True)


def test_scores_are_summed_across_a_lanes_categories(artifacts):
    _write(artifacts, "eval-tools/tool_selection/scorecard.json", _scorecard(5, 4, 1))
    _write(artifacts, "eval-tools/error_recovery/scorecard.json", _scorecard(3, 3, 0))
    _write(artifacts, "eval-tools/lane-timing.json", {"total_minutes": 61.25})

    row = collect_lane(LANES["lanes"][0], artifacts, "eval-")
    assert (row["measured"], row["passed"], row["failed"]) == (8, 7, 1)
    assert row["minutes"] == 61.2


def test_unscored_statuses_count_as_no_measurement_not_as_failures(artifacts):
    _write(
        artifacts,
        "eval-memory/memory/scorecard.json",
        _scorecard(10, 2, 1, infra_error=4, skipped=3),
    )
    row = collect_lane(LANES["lanes"][1], artifacts, "eval-")
    assert row["failed"] == 1
    assert row["unmeasured"] == 7


def test_a_lane_that_ran_but_scored_nothing_is_not_called_measured(artifacts):
    _write(artifacts, "eval-memory/lane-status.json", {"embedder_ok": False})
    row = collect_lane(LANES["lanes"][1], artifacts, "eval-")
    out = render([row], has_baseline=True)
    assert "NOT MEASURED" in out
    assert "embedder would not load" in out


def test_missing_baseline_says_there_is_no_regression_verdict(artifacts):
    artifacts.mkdir(parents=True)
    row = collect_lane(LANES["lanes"][1], artifacts, "eval-")
    assert "does **not** mean no regression" in render([row], has_baseline=False)
    assert "does **not** mean no regression" not in render([row], has_baseline=True)


def test_wall_clock_is_the_slowest_lane_not_the_sum(artifacts):
    _write(artifacts, "eval-tools/tool_selection/scorecard.json", _scorecard(1, 1, 0))
    _write(artifacts, "eval-tools/error_recovery/scorecard.json", _scorecard(1, 1, 0))
    _write(artifacts, "eval-tools/lane-timing.json", {"total_minutes": 40})
    _write(artifacts, "eval-memory/memory/scorecard.json", _scorecard(1, 1, 0))
    _write(artifacts, "eval-memory/lane-timing.json", {"total_minutes": 110})

    rows = [collect_lane(lane, artifacts, "eval-") for lane in LANES["lanes"]]
    out = render(rows, has_baseline=True)
    assert "**110.0**" in out
    assert "**150.0**" not in out


def test_unreadable_lane_map_fails_loudly_rather_than_printing_an_empty_table(
    tmp_path, capsys
):
    bad = tmp_path / "lanes.json"
    bad.write_text("{not json", encoding="utf-8")
    code = main(["--lanes-file", str(bad), "--artifacts-dir", str(tmp_path)])
    assert code == 1
    assert "Could not read the lane map" in capsys.readouterr().err


def test_a_reported_run_exits_zero_even_when_a_lane_errored(tmp_path, capsys):
    lanes_file = tmp_path / "lanes.json"
    lanes_file.write_text(json.dumps(LANES), encoding="utf-8")
    code = main(
        [
            "--lanes-file",
            str(lanes_file),
            "--artifacts-dir",
            str(tmp_path / "none"),
            "--baseline-dir",
            str(tmp_path / "no-baseline"),
        ]
    )
    # The lane owns its own red/green; this step reports and must not double-fail.
    assert code == 0
    out = capsys.readouterr().out
    assert "ERRORED" in out
    assert "uploaded no artifact" in out
