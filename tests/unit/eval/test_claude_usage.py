# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Real Claude usage is recorded per scenario and added up per category and run.

Before this, a successful scenario kept only the driver's own ``cost_estimate``
guess and dropped the token counts ``claude -p`` reports, so nobody could say
what a sweep cost or where the tokens went.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.eval import runner
from gaia.eval.claude_usage import (
    aggregate_usage,
    merge_scenario_usage,
    scenario_total,
    usage_from_cli,
)
from gaia.eval.lane_summary import main as summary_main
from gaia.eval.scorecard import build_scorecard, write_summary_md


def _cli(
    *,
    inp=9,
    out=2428,
    cache_read=329205,
    cache_write=35093,
    turns=14,
    cost=0.3457665,
    api_ms=39155,
    is_error=False,
    **extra,
):
    """A ``claude -p --output-format json`` payload, shaped like the real one."""
    return {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "duration_api_ms": api_ms,
        "num_turns": turns,
        "total_cost_usd": cost,
        "usage": {
            "input_tokens": inp,
            "cache_creation_input_tokens": cache_write,
            "cache_read_input_tokens": cache_read,
            "output_tokens": out,
            "output_tokens_details": {"thinking_tokens": 1486},
            "cache_creation": {"ephemeral_1h_input_tokens": cache_write},
        },
        "modelUsage": {"claude-sonnet-4-6": {"inputTokens": inp}},
        **extra,
    }


ZERO = _cli(inp=0, out=0, cache_read=0, cache_write=0, turns=1, cost=0, api_ms=0)


# --- reading one payload -----------------------------------------------------


def test_every_counter_is_read_from_a_real_payload():
    usage = usage_from_cli(_cli())
    assert usage == {
        "input_tokens": 9,
        "output_tokens": 2428,
        "cache_read_input_tokens": 329205,
        "cache_creation_input_tokens": 35093,
        "calls": 14,
        "cost_usd": 0.3457665,
        "duration_api_ms": 39155,
        "models": ["claude-sonnet-4-6"],
    }


def test_a_run_that_never_reached_claude_made_no_calls():
    # The CLI counts the failed attempt as a turn; no API call was answered.
    usage = usage_from_cli(ZERO)
    assert usage["calls"] == 0
    assert all(usage[k] == 0 for k in ("input_tokens", "output_tokens"))


@pytest.mark.parametrize("raw", [None, "text", [], {}, {"usage": "nope"}])
def test_no_usage_block_is_none_not_zero(raw):
    assert usage_from_cli(raw) is None


def test_garbage_counts_are_not_summed():
    usage = usage_from_cli(
        {"usage": {"input_tokens": "12", "output_tokens": -5}, "num_turns": 2}
    )
    assert usage["input_tokens"] == 0 and usage["output_tokens"] == 0


# --- the runner records it ---------------------------------------------------


def _run(tmp_path, monkeypatch, stdout, returncode=0):
    monkeypatch.setattr(
        runner,
        "_run_driver",
        lambda *a, **k: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=""
        ),
    )
    scenario = {
        "id": "s1",
        "category": "rag_quality",
        "setup": {"index_documents": []},
        "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
    }
    return runner.run_scenario_subprocess(
        tmp_path / "s1.yaml",
        scenario,
        tmp_path,
        "http://localhost:4200",
        "claude-sonnet-4-6",
        "1.00",
        30,
    )


def _judged(**extra):
    return {
        "scenario_id": "s1",
        "status": "PASS",
        "overall_score": 9,
        "turns": [],
        **extra,
    }


def test_a_passing_scenario_keeps_its_real_usage(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, json.dumps(_cli(structured_output=_judged())))
    assert result["status"] == "PASS"
    driver = result["claude_usage"]["roles"]["driver"]
    assert driver["cache_creation_input_tokens"] == 35093
    assert driver["calls"] == 14
    # The trace on disk carries it too, so resume and re-scoring can read it.
    trace = json.loads((tmp_path / "traces" / "s1.json").read_text("utf-8"))
    assert trace["claude_usage"] == result["claude_usage"]


def test_the_driver_cannot_report_its_own_usage(tmp_path, monkeypatch):
    forged = {"roles": {"driver": {"input_tokens": 1}}}
    stdout = json.dumps({"structured_output": _judged(claude_usage=forged)})
    result = _run(tmp_path, monkeypatch, stdout)
    assert "claude_usage" not in result


def test_a_failed_driver_keeps_its_usage(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, json.dumps(ZERO), returncode=1)
    assert result["status"] == "ERRORED"
    assert result["claude_usage"]["roles"]["driver"]["calls"] == 0
    # The integrity gate's existing field is unchanged.
    assert result["driver_usage"]["input_tokens"] == 0


def test_a_budget_stop_keeps_its_usage(tmp_path, monkeypatch):
    stdout = json.dumps(_cli(subtype="error_max_budget_usd", cost=2.01))
    result = _run(tmp_path, monkeypatch, stdout)
    assert result["status"] == "BUDGET_EXCEEDED"
    assert result["claude_usage"]["roles"]["driver"]["cost_usd"] == 2.01


def test_unparseable_output_records_nothing(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, "not json")
    assert result["status"] == "ERRORED"
    assert "claude_usage" not in result


# --- adding it up ------------------------------------------------------------


def _usage(**counts):
    base = usage_from_cli(_cli())
    base.update(counts)
    return {"roles": {"driver": base}}


def test_iterations_sum_every_attempt_not_just_the_representative():
    attempts = [
        {"status": "PASS", "overall_score": 9, "claude_usage": _usage(calls=10)},
        {"status": "FAIL", "overall_score": 3, "claude_usage": _usage(calls=4)},
        {"status": "TIMEOUT", "overall_score": None},
    ]
    result = runner.summarize_attempts(attempts)
    assert result["status"] == "FAIL"
    assert result["claude_usage"]["roles"]["driver"]["calls"] == 14
    assert result["claude_usage"]["attempts_without_usage"] == 1


def test_attempts_with_no_usage_at_all_record_none():
    assert merge_scenario_usage([None, {}]) is None


def test_scenario_total_sums_roles():
    usage = {
        "roles": {
            "driver": {"input_tokens": 5, "calls": 2},
            "judge": {"input_tokens": 7, "calls": 1},
        }
    }
    total = scenario_total(usage)
    assert total["input_tokens"] == 12 and total["calls"] == 3


def test_scorecard_totals_by_category_and_names_the_gap():
    results = [
        {
            "scenario_id": "a",
            "category": "x",
            "status": "PASS",
            "claude_usage": _usage(),
        },
        {
            "scenario_id": "b",
            "category": "x",
            "status": "FAIL",
            "claude_usage": _usage(),
        },
        {
            "scenario_id": "c",
            "category": "y",
            "status": "PASS",
            "claude_usage": _usage(),
        },
        {"scenario_id": "d", "category": "y", "status": "TIMEOUT"},
    ]
    card = build_scorecard("run", results, {"model": "claude-sonnet-4-6"})
    usage = card["claude_usage"]
    assert usage["total"]["cache_creation_input_tokens"] == 3 * 35093
    assert usage["by_category"]["x"]["scenarios"] == 2
    assert usage["by_category"]["y"]["calls"] == 14
    assert usage["by_role"]["driver"]["calls"] == 42
    assert usage["scenarios_with_usage"] == 3
    assert usage["scenarios_without_usage"] == 1

    md = write_summary_md(card)
    assert "## Claude usage" in md
    assert "| x | 2 | 28 |" in md
    assert "1 scenario(s) recorded no usage" in md


def test_a_scorecard_without_usage_has_no_usage_section():
    card = build_scorecard("run", [{"scenario_id": "a", "status": "PASS"}], {})
    assert card["claude_usage"]["scenarios_with_usage"] == 0
    assert "## Claude usage" not in write_summary_md(card)


def test_aggregate_is_empty_for_no_results():
    usage = aggregate_usage([])
    assert usage["total"]["calls"] == 0 and usage["by_category"] == {}


def test_the_lane_summary_copy_of_the_fields_matches():
    from gaia.eval import claude_usage, lane_summary

    assert lane_summary.USAGE_FIELDS == claude_usage.USAGE_FIELDS
    assert lane_summary.empty_usage() == claude_usage.empty_usage()


def _lane_artifacts(tmp_path):
    lanes = {
        "lanes": [
            {"lane": "one", "categories": ["x"]},
            {"lane": "two", "categories": ["y"]},
        ]
    }
    (tmp_path / "lanes.json").write_text(json.dumps(lanes), encoding="utf-8")
    for lane, cat in (("one", "x"), ("two", "y")):
        card = build_scorecard(
            "r",
            [
                {
                    "scenario_id": cat,
                    "category": cat,
                    "status": "PASS",
                    "claude_usage": _usage(),
                }
            ],
            {},
        )
        target = tmp_path / "art" / f"eval-{lane}" / cat
        target.mkdir(parents=True)
        (target / "scorecard.json").write_text(json.dumps(card), encoding="utf-8")
    return [
        "--lanes-file",
        str(tmp_path / "lanes.json"),
        "--artifacts-dir",
        str(tmp_path / "art"),
    ]


TOTAL_ROW = "| **total** | 2 | 28 | 18 | 70,186 | 658,410 | 4,856 | $0.69 |"


def test_the_lane_summary_runs_as_ci_runs_it(tmp_path):
    """eval_flagship.yml runs the file directly on a runner without gaia."""
    from gaia.eval import lane_summary

    # No gaia on the path, and the workflow's own PYTHONIOENCODING.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [
            sys.executable,
            "-S",
            str(Path(lane_summary.__file__)),
            *_lane_artifacts(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert TOTAL_ROW in proc.stdout


def test_run_summary_adds_usage_across_lanes(tmp_path, capsys):
    assert summary_main(_lane_artifacts(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "### Claude usage" in out
    assert TOTAL_ROW in out
