# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Append this week's backlog results to the running trend and render it.

    python util/eval_backlog_trend.py --artifacts-dir lane-artifacts \\
        --previous prev/trend.json --out trend/trend.json \\
        --run-url URL --sha SHA --date 2026-10-25 --model Qwen3.6-35B-A3B-GGUF

The weekly backlog lane (eval/ci_tiers.json, tier ``backlog``) measures the
scenarios that always fail or flip on most runs. One run says little about
them; the trend is the point. Each run downloads the previous run's
``flagship-backlog-trend`` artifact, appends its own statuses, and uploads the
result, so the history rolls forward one artifact at a time. ``--previous`` may
be omitted only for the first run, or after a gap longer than the artifact's
retention; the workflow says which.

Prints the trend as markdown for the job summary. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Weeks kept in the artifact; a year of history.
KEEP = 52
#: Weeks shown per scenario in the summary table.
SHOW = 8
MARK = {"PASS": "P", "FAIL": "F"}


def backlog_ids(tiers: dict) -> dict:
    """``{id: leaves_full}`` for the backlog tier, in file order."""
    out = {}
    for machine in tiers["tiers"]["backlog"]["machines"]:
        for entry in machine["scenarios"]:
            out[entry["id"]] = bool(entry.get("leaves_full"))
    return out


def statuses(artifacts_dir: Path, wanted: set) -> dict:
    """``{id: status}`` from every scorecard under the downloaded artifacts."""
    found = {}
    for path in sorted(artifacts_dir.rglob("scorecard.json")):
        try:
            card = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
        for scenario in card.get("scenarios") or []:
            sid = scenario.get("scenario_id")
            if sid in wanted:
                found[sid] = str(scenario.get("status") or "UNKNOWN").upper()
    return found


def entry_for(ids: dict, found: dict, run_url: str, sha: str, date: str, model: str):
    run = {sid: found.get(sid, "NOT_RUN") for sid in ids}
    return {
        "date": date,
        "sha": sha,
        "run": run_url,
        "model": model,
        "passed": sum(s == "PASS" for s in run.values()),
        "total": len(run),
        "statuses": run,
    }


def render(trend: list, ids: dict) -> str:
    latest = trend[-1]
    out = [
        "## Weekly backlog trend",
        "",
        f"This week: **{latest['passed']} of {latest['total']}** backlog scenarios passed "
        f"on {latest['model']}. P = pass, F = fail, E = no measurement, - = not run.",
        "",
        "| date | commit | model | passed |",
        "| --- | --- | --- | ---: |",
    ]
    for week in reversed(trend[-SHOW:]):
        out.append(
            f"| {week['date']} | `{week['sha'][:9]}` | {week['model']} | "
            f"{week['passed']} / {week['total']} |"
        )
    weeks = trend[-SHOW:]
    out += [
        "",
        "| scenario | in full sweep | "
        + " | ".join(w["date"][5:] for w in weeks)
        + " |",
    ]
    out.append("| --- | --- | " + " | ".join("---" for _ in weeks) + " |")
    for sid, gone in ids.items():
        cells = []
        for week in weeks:
            status = week["statuses"].get(sid, "NOT_RUN")
            cells.append("-" if status == "NOT_RUN" else MARK.get(status, "E"))
        out.append(
            f"| `{sid}` | {'no' if gone else 'yes'} | " + " | ".join(cells) + " |"
        )
    back = [
        s for s, gone in ids.items() if gone and latest["statuses"].get(s) == "PASS"
    ]
    if back:
        out += [
            "",
            "Passed this week but left the full sweep: "
            + ", ".join(f"`{s}`" for s in back)
            + ". Once one passes reliably, move it back (drop its `leaves_full` in "
            "eval/ci_tiers.json).",
        ]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifacts-dir", required=True, type=Path)
    parser.add_argument("--tiers", default="eval/ci_tiers.json", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)

    ids = backlog_ids(json.loads(args.tiers.read_text(encoding="utf-8")))
    trend = []
    if args.previous:
        trend = json.loads(args.previous.read_text(encoding="utf-8"))
        if not isinstance(trend, list):
            print(f"::error::{args.previous} is not a trend list.", file=sys.stderr)
            return 1
    found = statuses(args.artifacts_dir, set(ids))
    if not found:
        print(
            f"::error::No backlog scenario has a result under {args.artifacts_dir}. "
            "Every backlog lane failed before scoring; this week adds nothing to the "
            "trend, and the previous trend is re-uploaded unchanged.",
            file=sys.stderr,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(trend, indent=1), encoding="utf-8")
        return 1
    trend = (
        trend + [entry_for(ids, found, args.run_url, args.sha, args.date, args.model)]
    )[-KEEP:]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(trend, indent=1), encoding="utf-8")
    print(render(trend, ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
