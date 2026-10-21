# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Combine the per-lane scorecards of a parallel scenario-eval run into one report.

The eval runs as independent lanes on separate machines, so there is no single
job that saw the whole run. This reads what each lane uploaded and answers the
questions a reader of the run actually has: how much was measured, by which
lane, how long it took, and which lanes have nothing to say and why.

**It reports; it does not judge.** Each lane already owns its own red/green via
its integrity gate and its ``--compare`` step, and repeating that verdict here
would make one regression fail twice and read as two. What this adds is the
thing no individual lane can see — that a lane is *missing entirely*, which
looks like silence rather than failure and is otherwise indistinguishable from
a lane that had nothing to report.

The distinction the summary is built around:

* **errored** — the lane uploaded no artifact at all. Its machine died, or the
  job failed before the upload step. There is no measurement and no scorecard.
* **not measured** — the lane ran but a category produced no scorecard (the
  embedder would not load, the harness crashed part-way).
* **measured** — scenarios ran and scored. ``fail`` here is a real, comparable
  outcome, not an absence.

Usage::

    python -m gaia.eval.lane_summary \\
        --lanes-file eval/ci_lanes.json \\
        --artifacts-dir lane-artifacts \\
        --baseline-dir tests/fixtures/eval_baselines/gaia-flagship

A targeted dispatch passes ``--matrix-json`` (the lanes job's matrix) and
``--selection``. The report then covers only the lanes and categories that were
asked for, under a banner saying the run was not a full sweep, so unselected
lanes are neither reported as dead nor mistaken for measured.

Exit codes:
    0 — a report was produced (whatever it says).
    1 — the inputs are unreadable, so no report could be produced at all.

Notably NOT an exit code: "a lane failed". That verdict belongs to the lane.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gaia.eval.claude_usage import add_usage, empty_usage

# A scenario counted here ran but produced no score, so it is an absence rather
# than a result. Same list as the integrity gate's, and for the same reason:
# `skipped` (SKIPPED_NO_DOCUMENT) keeps its scenario id, so it slips past every
# check that works by comparing id sets.
NO_MEASUREMENT_COUNTERS = (
    "infra_error",
    "errored",
    "timeout",
    "blocked",
    "budget_exceeded",
    "skipped",
    "not_run",
)


def _read_json(path: Path) -> dict | None:
    # utf-8-sig: the lane files are written on Windows, where a stray BOM would
    # otherwise read as "the lane said nothing".
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def collect_lane(lane: dict, artifacts_dir: Path, prefix: str) -> dict:
    """Read one lane's uploaded artifact into a row of the summary."""
    name = lane["lane"]
    root = artifacts_dir / f"{prefix}{name}"
    if not root.is_dir():
        # download-artifact unpacks a LONE artifact straight into the target
        # directory, with no per-artifact folder: a one-lane targeted run.
        flat = _read_json(artifacts_dir / "lane-status.json") or {}
        if flat.get("lane") == name:
            root = artifacts_dir
    row = {
        "lane": name,
        "categories": list(lane["categories"]),
        "present": root.is_dir(),
        "job_status": None,
        "embedder_ok": None,
        "minutes": None,
        "measured": 0,
        "passed": 0,
        "failed": 0,
        "unmeasured": 0,
        "missing_categories": [],
        "claude": empty_usage(),
        "claude_scenarios": 0,
    }
    if not root.is_dir():
        row["missing_categories"] = list(lane["categories"])
        return row

    status = _read_json(root / "lane-status.json") or {}
    row["job_status"] = status.get("job_status")
    row["embedder_ok"] = status.get("embedder_ok")

    timing = _read_json(root / "lane-timing.json") or {}
    if isinstance(timing.get("total_minutes"), (int, float)):
        row["minutes"] = round(float(timing["total_minutes"]), 1)

    for category in lane["categories"]:
        scorecard = _read_json(root / category / "scorecard.json")
        if not scorecard:
            row["missing_categories"].append(category)
            continue
        summary = scorecard.get("summary") or {}
        row["measured"] += int(summary.get("total_scenarios") or 0)
        row["passed"] += int(summary.get("passed") or 0)
        row["failed"] += int(summary.get("failed") or 0)
        row["unmeasured"] += sum(
            int(summary.get(key) or 0) for key in NO_MEASUREMENT_COUNTERS
        )
        usage = scorecard.get("claude_usage") or {}
        if usage.get("scenarios_with_usage"):
            add_usage(row["claude"], usage.get("total"))
            row["claude_scenarios"] += int(usage["scenarios_with_usage"])
    return row


def _lane_state(row: dict) -> str:
    if not row["present"]:
        return "ERRORED"
    if row["missing_categories"] and not row["measured"]:
        return "NOT MEASURED"
    if row["missing_categories"]:
        return "PARTIAL"
    return "measured"


def render(rows: list[dict], has_baseline: bool, selection: str | None = None) -> str:
    """Render the whole run as markdown for the job summary."""
    out: list[str] = ["## Scenario eval — all lanes", ""]

    if selection:
        out += [
            f"> **{selection}.** This is not a full sweep: only the lanes, "
            "categories and scenarios below ran. Totals cover the selection, and "
            "nothing here says anything about the scenarios left out.",
            "",
        ]

    if not has_baseline:
        out += [
            "> **No regression verdict.** No flagship baseline is committed, so "
            "every lane below measured quality and compared it to nothing. A "
            "green run here does **not** mean no regression — it means the "
            "scenarios ran. These scorecards are the raw material for the first "
            "baseline, not a pass against one.",
            "",
        ]

    out += [
        "| lane | categories | scenarios | pass | fail | no measurement | minutes | state |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    totals = {"measured": 0, "passed": 0, "failed": 0, "unmeasured": 0, "minutes": 0.0}
    for row in rows:
        state = _lane_state(row)
        minutes = "—" if row["minutes"] is None else f"{row['minutes']:.1f}"
        out.append(
            f"| `{row['lane']}` | {', '.join(row['categories'])} | {row['measured']} | "
            f"{row['passed']} | {row['failed']} | {row['unmeasured']} | {minutes} | {state} |"
        )
        for key in ("measured", "passed", "failed", "unmeasured"):
            totals[key] += row[key]
        if row["minutes"]:
            totals["minutes"] = max(totals["minutes"], row["minutes"])
    out.append(
        f"| **total** | | **{totals['measured']}** | **{totals['passed']}** | "
        f"**{totals['failed']}** | **{totals['unmeasured']}** | "
        f"**{totals['minutes']:.1f}** | |"
    )
    out += [
        "",
        "Minutes are the **slowest lane**, not the sum: the lanes run at the same "
        "time on separate machines, so that column is the run's wall clock.",
        "",
    ]

    errored = [r for r in rows if not r["present"]]
    if errored:
        out += ["### Lanes that produced nothing", ""]
        for row in errored:
            out.append(
                f"- **`{row['lane']}`** uploaded no artifact. The job died before "
                f"its upload step, so {', '.join(row['categories'])} were not "
                "measured and left no logs here. Open that lane's job log."
            )
        out.append("")

    partial = [r for r in rows if r["present"] and r["missing_categories"]]
    if partial:
        out += ["### Categories with no scorecard", ""]
        for row in partial:
            why = ""
            if row["embedder_ok"] is False:
                why = " (the lane reported the RAG embedder would not load)"
            out.append(
                f"- **`{row['lane']}`**: {', '.join(row['missing_categories'])}{why}. "
                "See that lane's integrity gate for the per-category reason."
            )
        out.append("")

    out += _claude_usage_section(rows)
    return "\n".join(out) + "\n"


def _claude_usage_section(rows: list[dict]) -> list[str]:
    """Claude tokens per lane and for the whole run, from each scorecard."""
    used = [r for r in rows if r.get("claude_scenarios")]
    if not used:
        return []

    def line(label, usage, scenarios):
        return (
            f"| {label} | {scenarios} | {usage.get('calls', 0):,} | "
            f"{usage.get('input_tokens', 0):,} | "
            f"{usage.get('cache_creation_input_tokens', 0):,} | "
            f"{usage.get('cache_read_input_tokens', 0):,} | "
            f"{usage.get('output_tokens', 0):,} | "
            f"${float(usage.get('cost_usd') or 0):.2f} |"
        )

    out = [
        "### Claude usage",
        "",
        "Tokens `claude -p` reported, over the scenarios that recorded usage. "
        "Cost is at list price.",
        "",
        "| lane | scenarios | API calls | input | cache write | cache read | output | cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    total = empty_usage()
    scenarios = 0
    for row in used:
        out.append(line(f"`{row['lane']}`", row["claude"], row["claude_scenarios"]))
        add_usage(total, row["claude"])
        scenarios += row["claude_scenarios"]
    out.append(line("**total**", total, scenarios))
    out.append("")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lanes-file", required=True, type=Path)
    parser.add_argument("--artifacts-dir", required=True, type=Path)
    parser.add_argument(
        "--artifact-prefix",
        default="eval-",
        help="Prefix the lane jobs used when naming their artifact.",
    )
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        help="Committed flagship baselines. Absent or empty means no verdict.",
    )
    parser.add_argument(
        "--matrix-json",
        help="A targeted run's lane matrix; report only these lanes and categories.",
    )
    parser.add_argument(
        "--selection",
        help="One line describing a targeted run, shown as the report's banner.",
    )
    args = parser.parse_args(argv)

    lanes_doc = _read_json(args.lanes_file)
    if not lanes_doc or "lanes" not in lanes_doc:
        print(
            f"::error::Could not read the lane map at {args.lanes_file}. Without "
            "it there is no list of lanes to report on, so no summary can be "
            "produced. Check the file parses as JSON and has a `lanes` key.",
            file=sys.stderr,
        )
        return 1

    has_baseline = bool(
        args.baseline_dir
        and args.baseline_dir.is_dir()
        and any(args.baseline_dir.glob("scorecard_*.json"))
    )
    lanes = lanes_doc["lanes"]
    if args.matrix_json:
        try:
            include = json.loads(args.matrix_json)["include"]
        except (ValueError, KeyError, TypeError) as exc:
            print(
                f"::error::--matrix-json is not the lanes job's matrix ({exc}), so "
                "this targeted run's lanes are unknown and no summary can be produced.",
                file=sys.stderr,
            )
            return 1
        lanes = [
            {"lane": entry["lane"], "categories": entry["categories"].split()}
            for entry in include
        ]
    rows = [
        collect_lane(lane, args.artifacts_dir, args.artifact_prefix) for lane in lanes
    ]
    print(render(rows, has_baseline, args.selection))

    for row in rows:
        if not row["present"]:
            print(
                f"::warning::Lane `{row['lane']}` uploaded no artifact, so "
                f"{', '.join(row['categories'])} went unmeasured and left no logs "
                "to read. Open that lane's job log for the failure."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
