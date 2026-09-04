"""Aggregate TUI-eval run + judge results into one matrix for the report.

Reads every ``<sid>.json`` under one or more run dirs (a later dir overrides an
earlier one for the same scenario), applies the ``<sid>.judged.json`` verdict
when present, and prints a category-grouped PASS/FAIL/NEEDS_JUDGE/ERROR table
plus totals. Judge verdict wins over the deterministic verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_runs(dirs: list[Path]) -> dict:
    runs: dict[str, dict] = {}
    for d in dirs:
        for p in sorted(d.glob("*.json")):
            if p.name.endswith(".judged.json") or p.name == "summary.json":
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            sid = data["scenario"]
            judged = p.with_name(f"{sid}.judged.json")
            if judged.is_file():
                jv = json.loads(judged.read_text(encoding="utf-8"))
                data["verdict"] = jv["judged_verdict"]
                data["judge_summary"] = jv.get("judge", {}).get("summary", "")
            runs[sid] = data  # later dir overrides
    return runs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True, type=Path)
    args = ap.parse_args()

    runs = load_runs(args.runs)
    by_cat: dict[str, list] = defaultdict(list)
    for sid, data in runs.items():
        by_cat[data.get("category", "?")].append((sid, data))

    totals: Counter = Counter()
    for cat in sorted(by_cat):
        rows = sorted(by_cat[cat])
        cat_counts = Counter(d["verdict"] for _, d in rows)
        for v in cat_counts:
            totals[v] += cat_counts[v]
        line = " ".join(f"{v}={cat_counts[v]}" for v in sorted(cat_counts))
        print(f"\n## {cat} ({line})")
        for sid, d in rows:
            mark = {"PASS": "PASS", "FAIL": "FAIL", "NEEDS_JUDGE": "JUDGE?", "ERROR": "ERR"}
            note = d.get("judge_summary", "")[:90]
            print(f"  {mark.get(d['verdict'], d['verdict']):<6} {sid}  {note}")

    print("\n== TOTALS ==")
    for v in sorted(totals):
        print(f"  {v}: {totals[v]}")
    print(f"  scenarios run: {len(runs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
