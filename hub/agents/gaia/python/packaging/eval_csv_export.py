# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Export a ``gaia eval agent`` run to CSV for spreadsheet analysis.

Writes two files next to the run (or to ``--output-dir``):

``results.csv``
    One row per **scenario**, with the experiment/config fields repeated on
    every row. Repetition is deliberate: it is what makes PivotTables work and
    lets several runs be concatenated into one sheet and analysed as a time
    series.

``summary.csv``
    One row per **category** — the sheet to skim before pivoting the detail.

Granularity note: rows are per scenario, not per scenario x iteration. When
``--iterations N`` is used the runner folds the attempts into one result (worst
judged attempt represents the scenario) and keeps the spread in a ``stability``
block, which is exported as columns here. Per-iteration KPI values are not
retained by the runner, so emitting an iteration-level row would mean columns
that are mostly empty; the per-attempt statuses are exported instead.

Two rules that keep the sheet honest:

* Column order is **stable and append-only** — new columns go at the end so
  CSVs from different commits stack cleanly.
* An unmeasured value writes an **empty cell, never 0**. A TTFT that was never
  measured and a TTFT of 0.0s must not look the same in a spreadsheet.

Usage::

    python eval_csv_export.py --run-dir eval/results/<run-id> [--output-dir eval-out]
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

# Stable, append-only. Never insert; append at the end.
RESULT_COLUMNS = [
    # -- experiment identity / config (repeated on every row) ----------------
    "run_id",
    "timestamp_utc",
    "gaia_commit",
    "agent_type",
    "agent_model",
    "judge_model",
    "backend_url",
    "budget_usd_per_scenario",
    # -- scenario identity ---------------------------------------------------
    "scenario_id",
    "category",
    "status",
    "overall_score",
    "elapsed_s",
    "root_cause",
    # -- judge dimensions (mean across the scenario's turns) -----------------
    "correctness",
    "tool_selection",
    "context_retention",
    "completeness",
    "efficiency",
    "personality",
    "error_recovery",
    # -- stability (populated when --iterations > 1) -------------------------
    "runs",
    "judged",
    "pass_count",
    "pass_rate",
    "stability",
    "score_avg",
    "score_min",
    "score_max",
    "score_stdev",
    "attempt_statuses",
    # -- KPIs ----------------------------------------------------------------
    "turns_run",
    "total_tool_calls",
    "max_tool_calls_in_a_turn",
    "input_tokens",
    "output_tokens",
    "ttft_avg_s",
    "ttft_min_s",
    "ttft_max_s",
    "tps_avg",
    "tps_min",
    "tps_max",
    "turns_measured",
    "perf_flags",
]

SUMMARY_COLUMNS = [
    "run_id",
    "gaia_commit",
    "agent_type",
    "agent_model",
    "category",
    "scenarios",
    "passed",
    "failed",
    "pass_rate",
    "avg_score",
    "flaky_count",
]

_DIMENSIONS = [
    "correctness",
    "tool_selection",
    "context_retention",
    "completeness",
    "efficiency",
    "personality",
    "error_recovery",
]


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Provenance we cannot establish is left blank, never guessed.
        return ""


def _mean(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 3) if vals else None


def _dimension_means(scenario: dict) -> dict:
    turns = scenario.get("turns") or []
    out = {}
    for dim in _DIMENSIONS:
        out[dim] = _mean([(t.get("scores") or {}).get(dim) for t in turns])
    return out


def scenario_rows(scorecard: dict, commit: str) -> list:
    """One row per scenario, config repeated so the file is self-describing."""
    config = scorecard.get("config") or {}
    base = {
        "run_id": scorecard.get("run_id"),
        "timestamp_utc": scorecard.get("timestamp"),
        "gaia_commit": commit,
        "agent_type": config.get("agent_type"),
        # The scorecard's config.model is the JUDGE model; the agent model is
        # whatever the backend ran and is recorded on the card, not here.
        "judge_model": config.get("model"),
        "agent_model": config.get("agent_model"),
        "backend_url": config.get("backend_url"),
        "budget_usd_per_scenario": config.get("budget_per_scenario_usd"),
    }

    rows = []
    for sc in scorecard.get("scenarios") or []:
        perf = sc.get("performance_summary") or {}
        tools = sc.get("tool_usage") or {}
        stab = sc.get("stability") or {}
        row = dict(base)
        row.update(
            {
                "scenario_id": sc.get("scenario_id"),
                "category": sc.get("category"),
                "status": sc.get("status"),
                "overall_score": sc.get("overall_score"),
                "elapsed_s": sc.get("elapsed_s"),
                "root_cause": sc.get("root_cause"),
                "runs": stab.get("runs"),
                "judged": stab.get("judged"),
                "pass_count": stab.get("pass_count"),
                "pass_rate": stab.get("pass_rate"),
                "stability": stab.get("stability"),
                "score_avg": stab.get("score_avg"),
                "score_min": stab.get("score_min"),
                "score_max": stab.get("score_max"),
                "score_stdev": stab.get("score_stdev"),
                "attempt_statuses": ";".join(stab.get("statuses") or []) or None,
                "turns_run": len(sc.get("turns") or []) or None,
                "total_tool_calls": tools.get("total_tool_calls"),
                "max_tool_calls_in_a_turn": tools.get("max_tool_calls_in_a_turn"),
                "input_tokens": perf.get("total_input_tokens"),
                "output_tokens": perf.get("total_output_tokens"),
                "ttft_avg_s": perf.get("avg_time_to_first_token"),
                "ttft_min_s": perf.get("min_time_to_first_token"),
                "ttft_max_s": perf.get("max_time_to_first_token"),
                "tps_avg": perf.get("avg_tokens_per_second"),
                "tps_min": perf.get("min_tokens_per_second"),
                "tps_max": perf.get("max_tokens_per_second"),
                "turns_measured": perf.get("turns_measured"),
                "perf_flags": ";".join(perf.get("flags") or []) or None,
            }
        )
        row.update(_dimension_means(sc))
        rows.append(row)
    return rows


def category_rows(scorecard: dict, commit: str) -> list:
    config = scorecard.get("config") or {}
    by_cat = (scorecard.get("summary") or {}).get("by_category") or {}
    flaky = {}
    for sc in scorecard.get("scenarios") or []:
        if (sc.get("stability") or {}).get("stability") == "flaky":
            flaky[sc.get("category")] = flaky.get(sc.get("category"), 0) + 1

    rows = []
    for cat, stats in sorted(by_cat.items()):
        passed = stats.get("passed", 0)
        failed = stats.get("failed", 0)
        judged = passed + failed
        rows.append(
            {
                "run_id": scorecard.get("run_id"),
                "gaia_commit": commit,
                "agent_type": config.get("agent_type"),
                "agent_model": config.get("agent_model"),
                "category": cat,
                "scenarios": judged,
                "passed": passed,
                "failed": failed,
                "pass_rate": round(passed / judged, 4) if judged else None,
                "avg_score": stats.get("avg_score"),
                "flaky_count": flaky.get(cat, 0),
            }
        )
    return rows


def write_csv(path: Path, columns: list, rows: list) -> None:
    """UTF-8 **with BOM** so Excel on Windows opens it in the right encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=columns, extrasaction="ignore", restval=""
        )
        writer.writeheader()
        for row in rows:
            # None -> empty cell. Never 0: an unmeasured metric and a measured
            # zero must not be indistinguishable in a spreadsheet.
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--output-dir", type=Path, default=None)
    args = ap.parse_args()

    scorecard_path = args.run_dir / "scorecard.json"
    if not scorecard_path.is_file():
        print(
            f"No scorecard.json in {args.run_dir} — pass the run directory that "
            "`gaia eval agent` printed as its Output: line.",
            file=sys.stderr,
        )
        return 1

    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))
    commit = _git_commit()
    out_dir = args.output_dir or args.run_dir

    results = scenario_rows(scorecard, commit)
    summary = category_rows(scorecard, commit)
    write_csv(out_dir / "results.csv", RESULT_COLUMNS, results)
    write_csv(out_dir / "summary.csv", SUMMARY_COLUMNS, summary)

    print(f"Wrote {out_dir / 'results.csv'} ({len(results)} scenario rows)")
    print(f"Wrote {out_dir / 'summary.csv'} ({len(summary)} category rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
