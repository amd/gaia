#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pick the one proactive Claude audit that runs tonight.

Running all three audits every night spent the Claude subscription's weekly cap
before the week was out: both daily audits failed 5 of 10 nights in October 2026
on "You've hit your weekly limit". One audit a night, in a fixed rotation, keeps
each one on a steady cadence (every ``len(ROTATION)`` nights) at a third of the
nightly spend.

The slot is ``days since 1970-01-01 (UTC) mod len(ROTATION)``: deterministic,
the same on any runner, and continuous across month and year ends, which a
day-of-month or day-of-week rule is not. Adding an audit means appending it to
``ROTATION``; every audit's cadence becomes ``len(ROTATION)`` nights.

A night lost to the usage limit is not retried on the next night (that night
belongs to another audit). It is covered instead: each audit scopes itself from
its own last SUCCESSFUL rotation run (scripts/audit/audit_history.py), so its
next turn picks up everything the lost night would have.

stdlib only.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone

#: Order is the rotation. The walkthrough is first only by convention; any order
#: gives each audit one night in len(ROTATION).
ROTATION: tuple[tuple[str, str], ...] = (
    ("claude-nightly-audit.yml", "code audit (correctness, docs, tests, features)"),
    ("claude-security-audit.yml", "security audit"),
    ("claude-weekly-doc-walkthrough.yml", "doc walkthrough"),
)

EPOCH = date(1970, 1, 1)


def night_number(day: date) -> int:
    return (day - EPOCH).days


def pick(day: date) -> tuple[str, str, int]:
    """Return ``(workflow file, description, night number)`` for a UTC date."""
    night = night_number(day)
    workflow, description = ROTATION[night % len(ROTATION)]
    return workflow, description, night


def schedule(start: date, nights: int) -> list[tuple[date, str]]:
    """The next ``nights`` assignments from ``start``, for the job summary."""
    out = []
    for offset in range(nights):
        day = date.fromordinal(start.toordinal() + offset)
        out.append((day, pick(day)[1]))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--date", default="", help="UTC date YYYY-MM-DD (default: today, UTC)"
    )
    parser.add_argument(
        "--summary", default="", help="Append tonight's pick and the next nights here"
    )
    args = parser.parse_args(argv)
    day = (
        date.fromisoformat(args.date)
        if args.date
        else datetime.now(timezone.utc).date()
    )
    workflow, description, night = pick(day)
    if args.summary:
        rows = [f"| {d.isoformat()} | {what} |" for d, what in schedule(day, 6)]
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(
                "\n".join(
                    [
                        f"### Tonight ({day.isoformat()} UTC): {description}",
                        "",
                        f"`{workflow}`, rotation night {night}. One audit a night; "
                        f"each runs every {len(ROTATION)} nights.",
                        "",
                        "| Night (UTC) | Audit |",
                        "|---|---|",
                        *rows,
                        "",
                    ]
                )
            )
    print(f"workflow={workflow}")
    print(f"description={description}")
    print(f"night={night}")
    print(f"date={day.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
