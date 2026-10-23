#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Propose install-time thresholds from the installer journeys' recent runs.

Reads the ``install-timings`` artifact of the last N completed runs of
.github/workflows/test_installers_headless.yml, takes every phase that PASSED,
and computes per row and metric: the sample count, median and p95, and a
threshold of

    threshold = ceil(max(p95 * 1.5, p95 + 60))   seconds

The 1.5x term absorbs runner-to-runner variance on the slow phases; the +60 s
floor keeps a phase that normally takes 10 s from failing on one slow runner.
p95 is linearly interpolated, so with a handful of samples it sits just under
the slowest one -- re-measure as runs accumulate.

    python installer/headless/propose_thresholds.py --runs 5            # print
    python installer/headless/propose_thresholds.py --runs 5 --write    # update the file
    python installer/headless/propose_thresholds.py --run-id 123 --run-id 456 --write

Needs the GitHub CLI (`gh`), authenticated. Nothing here is estimated: every
number traces to the run ids recorded beside it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from journey import METRICS, metrics_of  # noqa: E402

REPO = "amd/gaia"
WORKFLOW = "test_installers_headless.yml"
THRESHOLDS = HERE.parent / "install_time_thresholds.json"
GATED = ["install", "launch_to_healthy", "upgrade", "uninstall"]
REPORT_ONLY = {
    "download": (
        "Network-bound: it measures GitHub's release CDN and the runner's link, "
        "not the installer, so it is reported in the table but never gated."
    )
}
FORMULA = (
    "threshold = ceil(max(p95 * 1.5, p95 + 60)) seconds; p95 linearly interpolated"
)


def p95(values: List[float]) -> float:
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * 0.95
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def threshold(p: float) -> int:
    return int(math.ceil(max(p * 1.5, p + 60)))


def _gh(*args: str) -> str:
    gh = shutil.which("gh")
    if not gh:
        raise SystemExit(
            "error: the GitHub CLI `gh` is not on PATH. Install it and run `gh auth login`."
        )
    return subprocess.run(
        [gh, *args], check=True, capture_output=True, text=True
    ).stdout


def recent_run_ids(n: int, release: Optional[str]) -> List[int]:
    out = _gh(
        "run",
        "list",
        "-R",
        REPO,
        "--workflow",
        WORKFLOW,
        "--status",
        "completed",
        "-L",
        str(max(n * 4, 20)),
        "--json",
        "databaseId,displayTitle,event",
    )
    return [r["databaseId"] for r in json.loads(out)]


def load_run(run_id: int, scratch: Path) -> Optional[List[Dict[str, Any]]]:
    dest = scratch / str(run_id)
    try:
        _gh(
            "run",
            "download",
            str(run_id),
            "-R",
            REPO,
            "-n",
            "install-timings",
            "-D",
            str(dest),
        )
    except subprocess.CalledProcessError:
        return None  # a run that never reached the summary job has no timings
    data = json.loads((dest / "install-timings.json").read_text(encoding="utf-8"))
    return data.get("results", [])


def collect(
    run_ids: List[int], n: int, release: Optional[str]
) -> Tuple[Dict[str, Dict[str, List[float]]], Dict[str, Dict[str, Any]], List[int]]:
    samples: Dict[str, Dict[str, List[float]]] = {}
    meta: Dict[str, Dict[str, Any]] = {}
    used: List[int] = []
    with tempfile.TemporaryDirectory() as tmp:
        for rid in run_ids:
            if len(used) >= n:
                break
            results = load_run(rid, Path(tmp))
            if not results:
                continue
            if release and results[0].get("release") != release:
                continue
            used.append(rid)
            for r in results:
                row = r["row"]
                m = meta.setdefault(row, {"runner_images": set(), "runs": []})
                m["runner_images"].add(r.get("runner_image", "?"))
                m["runs"].append(rid)
                for metric, secs in metrics_of(r).items():
                    samples.setdefault(row, {}).setdefault(metric, []).append(secs)
    return samples, meta, used


def build(
    samples: Dict[str, Dict[str, List[float]]],
    meta: Dict[str, Dict[str, Any]],
    used: List[int],
    release: Optional[str],
) -> Dict[str, Any]:
    rows: Dict[str, Any] = {}
    for row in sorted(samples):
        metrics = {}
        for metric in METRICS:
            vals = samples[row].get(metric)
            if not vals:
                continue
            p = p95(vals)
            entry: Dict[str, Any] = {
                "samples": len(vals),
                "median": round(statistics.median(vals), 1),
                "p95": round(p, 1),
                "min": round(min(vals), 1),
                "max": round(max(vals), 1),
            }
            if metric in GATED:
                entry["threshold"] = threshold(p)
            metrics[metric] = entry
        rows[row] = {
            "runner_images": sorted(meta[row]["runner_images"]),
            "source_runs": sorted(set(meta[row]["runs"])),
            "metrics": metrics,
        }
    return {
        "about": (
            "Install-time thresholds for .github/workflows/test_installers_headless.yml. "
            "Generated by installer/headless/propose_thresholds.py from real runs; do not "
            "hand-edit a number. A row or metric missing here reports 'no threshold' as a warning."
        ),
        "formula": FORMULA,
        "gated_metrics": GATED,
        "report_only": REPORT_ONLY,
        "measured": dt.date.today().isoformat(),
        "release_measured": release or "mixed",
        "source_runs": sorted(used),
        "rows": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--runs", type=int, default=5, help="use the last N runs that produced timings"
    )
    ap.add_argument(
        "--run-id", type=int, action="append", default=[], help="use exactly these runs"
    )
    ap.add_argument("--release", help="only runs against this release tag")
    ap.add_argument(
        "--min-samples",
        type=int,
        default=3,
        help="refuse a metric with fewer samples than this",
    )
    ap.add_argument("--write", action="store_true", help=f"write {THRESHOLDS.name}")
    args = ap.parse_args(argv)

    run_ids = args.run_id or recent_run_ids(args.runs, args.release)
    n = len(args.run_id) if args.run_id else args.runs
    samples, meta, used = collect(run_ids, n, args.release)
    if not used:
        print(
            "error: none of those runs produced an install-timings artifact.",
            file=sys.stderr,
        )
        return 1
    proposal = build(samples, meta, used, args.release)

    thin = [
        f"{row}/{metric} ({e['samples']})"
        for row, r in proposal["rows"].items()
        for metric, e in r["metrics"].items()
        if metric in GATED and e["samples"] < args.min_samples
    ]
    for row, r in proposal["rows"].items():
        for metric, e in list(r["metrics"].items()):
            if metric in GATED and e["samples"] < args.min_samples:
                e.pop("threshold", None)

    old = (
        json.loads(THRESHOLDS.read_text(encoding="utf-8"))
        if THRESHOLDS.exists()
        else {"rows": {}}
    )
    print(f"runs used: {', '.join(map(str, used))}")
    print(
        f"{'row':32} {'metric':18} {'n':>3} {'median':>8} {'p95':>8} {'old':>8} {'new':>8}"
    )
    for row, r in proposal["rows"].items():
        for metric, e in r["metrics"].items():
            prev = (
                old.get("rows", {})
                .get(row, {})
                .get("metrics", {})
                .get(metric, {})
                .get("threshold", "-")
            )
            print(
                f"{row:32} {metric:18} {e['samples']:>3} {e['median']:>8} {e['p95']:>8} {prev!s:>8} {e.get('threshold', '-')!s:>8}"
            )
    if thin:
        print(
            f"\nno threshold proposed (fewer than {args.min_samples} samples): {', '.join(thin)}"
        )

    if args.write:
        THRESHOLDS.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {THRESHOLDS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
