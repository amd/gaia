# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pick a slice of the evaluation set without copying files around.

The per-use-case directories are convenient for reading and for running one set.
They are the wrong tool for an experiment that wants "every hard record in the
build track, pool only" — that is a query, not a directory. This reads the
canonical ``records.jsonl`` and writes the matching subset.

Usage::

    python -m gaia.factory.dataset.select --data DIR --use-case bug_fix
    python -m gaia.factory.dataset.select --data DIR --track build --difficulty hard
    python -m gaia.factory.dataset.select --data DIR --partition pool --out slice.jsonl

With no filter it prints the available values and counts, which is usually what
you want first.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Sequence

#: Fields that can be filtered on, mapped to whether a record holds a scalar or
#: a list. Anything not listed here is deliberately not filterable — a filter on
#: free text would silently match things the caller did not intend.
FILTERABLE: Dict[str, str] = {
    "use_case": "scalar",
    "track": "scalar",
    "difficulty": "scalar",
    "partition": "scalar",
    "activity": "scalar",
    "domain": "scalar",
    "grading_polarity": "scalar",
    "capability_axes": "list",
}


def load(data: Path) -> List[dict]:
    path = data / "records.jsonl"
    if not path.exists():
        raise SystemExit(
            f"no canonical records at {path}. Point --data at the dataset root "
            "produced by gaia.factory.dataset.usecase_split."
        )
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def apply_filters(records: Sequence[dict], filters: Dict[str, List[str]]) -> List[dict]:
    """Records matching every filter. Within one filter, any value matches."""
    out = list(records)
    for field, wanted in filters.items():
        kind = FILTERABLE[field]
        if kind == "list":
            out = [r for r in out if set(r.get(field) or []) & set(wanted)]
        else:
            out = [r for r in out if r.get(field) in wanted]
    return out


def describe(records: Sequence[dict]) -> str:
    """What is available to filter on, and how much of each."""
    lines = [f"{len(records)} records\n"]
    for field, kind in FILTERABLE.items():
        if kind == "list":
            c = Counter(v for r in records for v in (r.get(field) or []))
        else:
            c = Counter(r.get(field) for r in records)
        shown = ", ".join(f"{k}={n}" for k, n in c.most_common(8) if k is not None)
        more = "" if len(c) <= 8 else f", (+{len(c) - 8} more)"
        lines.append(f"  --{field.replace('_', '-')}  {shown}{more}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, required=True, help="Dataset root.")
    ap.add_argument(
        "--out", type=Path, default=None, help="Write here (default stdout)."
    )
    ap.add_argument(
        "--count", action="store_true", help="Print the matching count and exit."
    )
    for field in FILTERABLE:
        ap.add_argument(
            f"--{field.replace('_', '-')}",
            action="append",
            default=None,
            help=f"Filter on {field}; repeat to allow several values.",
        )
    args = ap.parse_args()

    records = load(args.data)
    filters = {
        f: getattr(args, f)
        for f in FILTERABLE
        if getattr(args, f)  # only fields the caller actually passed
    }
    if not filters:
        print(describe(records))
        return

    unknown = {
        f: [v for v in vals if v not in {r.get(f) for r in records}]
        for f, vals in filters.items()
        if FILTERABLE[f] == "scalar"
    }
    bad = {f: v for f, v in unknown.items() if v}
    if bad:
        # A typo'd filter silently returning zero records is how an experiment
        # reports "0% pass" on a set that was never loaded.
        raise SystemExit(
            "no such value(s): "
            + "; ".join(f"{f}={v}" for f, v in bad.items())
            + "\n\n"
            + describe(records)
        )

    matched = apply_filters(records, filters)
    if args.count:
        print(len(matched))
        return
    if not matched:
        raise SystemExit("filters matched 0 records; nothing written")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as fh:
            for rec in matched:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{len(matched)} records -> {args.out}")
    else:
        for rec in matched:
            sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
