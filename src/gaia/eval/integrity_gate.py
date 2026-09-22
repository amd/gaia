# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
CI completeness gate: did the agent eval actually MEASURE what it claims to?

**Distinct from** ``compare_scorecards`` (``gaia.eval.runner``), which answers
"did quality get worse". That comparison scores only scenarios present on both
sides, so a run whose scenarios vanished or died in the harness exits 0 and reads
as "no regression". This gate answers the other question — "is there a
measurement at all" — and is the only thing standing between an empty run and a
green check.

Two failure classes, both reported:

* **Missing** — a scenario in the baseline never appeared in the run.
* **Unmeasured** — a scenario ran but produced no score (``infra_error``,
  ``errored``, ``timeout``, ``blocked``, ``budget_exceeded``, ``skipped``), plus
  any category named via ``--not-measured`` because the environment could not run
  it at all.

``skipped`` (``SKIPPED_NO_DOCUMENT``) is the subtle one and the reason this list
is not just the obvious errors: those scenarios keep their ids, so the missing
check stays clean, and ``compare_scorecards`` files them under ``corpus_changed``
= "not a quality signal". A runner whose corpus never materialised would
otherwise go green having measured nothing.

Usage::

    python -m gaia.eval.integrity_gate \\
        --baseline-dir tests/fixtures/eval_baselines/gemma-4-e4b-d71cd914 \\
        --results-dir eval-out \\
        --category tool_selection \\
        --not-measured rag_quality --not-measured context_retention \\
        --not-measured-reason "the RAG embedder will not load on this runner (#3016)" \\
        --enforce

Exit codes:
    0 — complete, OR incomplete in report mode (``--enforce`` absent).
    1 — incomplete and ``--enforce`` was passed.

``--enforce`` is what makes the gate BLOCKING. Without it the same findings are
emitted as ``::warning::`` annotations and written to the job summary, and the
job stays green — because the workflow that calls this advertises report mode by
default, and a gate that is red on every PR for a known environment defect gates
nothing. The signal is never suppressed in either mode; only the exit code moves.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Summary counters meaning "a scenario produced no measurement", as distinct
# from `failed` (FAIL is a legitimate, comparable outcome). Unlike the runner's
# regression comparison, completeness also treats blocked/skipped as unmeasured.
# Keys come from scorecard.py::build_scorecard.
NO_MEASUREMENT_COUNTERS = (
    "infra_error",
    "errored",
    "timeout",
    "blocked",
    "budget_exceeded",
    "skipped",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario_ids(scorecard: dict) -> list:
    return [
        s.get("scenario_id")
        for s in scorecard.get("scenarios") or []
        if s.get("scenario_id")
    ]


def check_category(baseline_path: Path, current_path: Path, category: str):
    """Return ``(problems, status_line)`` for one category.

    ``problems`` is a list of human-readable strings; empty means complete.
    """
    problems = []
    if not current_path.exists():
        return (
            [f"{category}: no scorecard produced ({current_path})"],
            f"{category}: no scorecard at {current_path}",
        )
    if not baseline_path.exists():
        return (
            [f"{category}: baseline scorecard not found ({baseline_path})"],
            f"{category}: no baseline at {baseline_path}",
        )

    current = _load(current_path)
    baseline = _load(baseline_path)

    got = _scenario_ids(current)
    want = _scenario_ids(baseline)
    absent = sorted(set(want) - set(got))
    if absent:
        problems.append(
            f"{category}: {len(absent)} baseline scenario(s) missing from the run: "
            f"{', '.join(absent)}"
        )

    summary = current.get("summary") or {}
    unmeasured = [
        f"{key}={summary[key]}" for key in NO_MEASUREMENT_COUNTERS if summary.get(key)
    ]
    if unmeasured:
        problems.append(
            f"{category}: scenarios without a measurement: {', '.join(unmeasured)}"
        )

    shown = ", ".join(unmeasured) if unmeasured else "none"
    status_line = (
        f"{category}: {len(got)} scenario(s) run, {len(want)} in baseline, "
        f"unmeasured={shown}"
    )
    return problems, status_line


def _write_summary(lines) -> None:
    """Append to the GitHub step summary. Visible in BOTH modes on purpose."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gaia.eval.integrity_gate",
        description="Fail a CI eval run that did not actually measure what it compares.",
    )
    parser.add_argument(
        "--baseline-dir",
        required=True,
        help="Directory holding scorecard_<category>.json baselines.",
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        help="Directory holding <category>/scorecard.json from this run.",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=[],
        dest="categories",
        help="Category that was run and must be complete. Repeatable.",
    )
    parser.add_argument(
        "--not-measured",
        action="append",
        default=[],
        dest="not_measured",
        help=(
            "Category the environment could not run at all. Reported as "
            "unmeasured with --not-measured-reason. Repeatable."
        ),
    )
    parser.add_argument(
        "--not-measured-reason",
        default="the environment could not run this category",
        help="Why the --not-measured categories could not run (goes in the annotation).",
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help=(
            "Block on an incomplete run (exit 1). Without it the same findings "
            "are emitted as ::warning:: annotations and the job stays green."
        ),
    )
    args = parser.parse_args(argv)

    if not args.categories and not args.not_measured:
        # Nothing to check is not "complete" — it is a miswired workflow.
        print(
            "::error::integrity_gate: no --category and no --not-measured given; "
            "nothing was checked. Pass the categories this run was supposed to measure.",
        )
        return 1

    baseline_dir = Path(args.baseline_dir)
    results_dir = Path(args.results_dir)

    problems = []
    for category in args.categories:
        cat_problems, status_line = check_category(
            baseline_dir / f"scorecard_{category}.json",
            results_dir / category / "scorecard.json",
            category,
        )
        print(status_line)
        problems.extend(cat_problems)

    # A category the environment made impossible is still an unmeasured category.
    # Named with its cause rather than silently dropped.
    for category in args.not_measured:
        problems.append(f"{category}: NOT MEASURED - {args.not_measured_reason}")

    if not problems:
        print("")
        print("Integrity OK - every baseline scenario ran and produced a measurement.")
        _write_summary(
            [
                "",
                "### Eval integrity: OK",
                "",
                f"Every baseline scenario ran and produced a measurement "
                f"({', '.join(args.categories)}).",
            ]
        )
        return 0

    level = "error" if args.enforce else "warning"
    for problem in problems:
        print(f"::{level}::Integrity: {problem}")

    print("")
    print(
        "The eval did not produce a complete set of measurements, so the baseline "
        "comparison is not trustworthy for the categories named above."
    )

    # The summary block is written in BOTH modes. In report mode it is the only
    # durable record that the run measured less than it appears to.
    summary = [
        "",
        f"### Eval integrity: INCOMPLETE ({len(problems)} finding(s))",
        "",
    ]
    summary += [f"- {problem}" for problem in problems]
    summary += [""]

    if args.enforce:
        print("enforce=true - failing the build.")
        summary.append("**enforce=true — this failed the build.**")
        _write_summary(summary)
        return 1

    print(
        "enforce=false - reported as warnings, build stays green. Re-run with "
        "enforce=true (or put [eval-enforce] in the PR title) to make this blocking."
    )
    summary.append(
        "**enforce=false — reported only; the build stays green.** Re-run with "
        "`enforce: true` (or put `[eval-enforce]` in the PR title) to make this blocking."
    )
    _write_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
