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

A targeted run (``gaia eval agent --scenario``) records its selection in the
scorecard's ``config.scenario_filter``. Then only the selected scenarios are
expected, and every one of them must have run.

``--fail-on-zero-token-errors`` adds a third check that blocks whatever
``--enforce`` says: scenarios that ERRORED without Claude spending a single
token. That is the signature of an exhausted Claude quota (v0.25.1-rc2: 68
scenarios, ~3 s each), and a run that hit it measured nothing from that point on.

``skipped`` (``SKIPPED_NO_DOCUMENT`` / ``SKIPPED_NO_MODEL``) is the subtle one and the reason this list
is not just the obvious errors: those scenarios keep their ids, so the missing
check stays clean, and ``compare_scorecards`` files them under ``corpus_changed``
= "not a quality signal". A runner whose corpus never materialised would
otherwise go green having measured nothing.

Usage::

    python -m gaia.eval.integrity_gate \\
        --baseline-dir tests/fixtures/eval_baselines/gaia-flagship \\
        --results-dir eval-out \\
        --category tool_selection \\
        --not-measured rag_quality --not-measured context_retention \\
        --not-measured-reason "the RAG embedder will not load on this runner (#3016)" \\
        --enforce

Exit codes:
    0 — complete, OR incomplete in report mode (``--enforce`` absent).
    1 — incomplete and ``--enforce`` was passed, or zero-token errors were found
        with ``--fail-on-zero-token-errors``.

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
import re
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
    "not_run",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario_ids(scorecard: dict) -> list:
    return [
        s.get("scenario_id")
        for s in scorecard.get("scenarios") or []
        if s.get("scenario_id")
    ]


def scenario_filter(scorecard: dict) -> list | None:
    """The ids a targeted run was limited to, or ``None`` for a full run."""
    selected = (scorecard.get("config") or {}).get("scenario_filter")
    return list(selected) if selected else None


_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
# Scorecards without `driver_usage` keep only the first 500 characters of the
# driver's JSON, which still carry these fields when Claude was never reached.
_ZERO_API_TEXT = re.compile(r'"duration_api_ms"\s*:\s*0\s*[,}]')
_ZERO_TOKENS_TEXT = (
    re.compile(r'"input_tokens"\s*:\s*0\s*[,}]'),
    re.compile(r'"output_tokens"\s*:\s*0\s*[,}]'),
)


def _spent_no_tokens(result: dict) -> bool:
    # The runner now names the outage itself: the scenario Claude cut off, and
    # every one it then never started.
    if result.get("claude_outage") or result.get("status") == "NOT_RUN":
        return True
    if result.get("status") != "ERRORED":
        return False
    usage = result.get("driver_usage")
    if isinstance(usage, dict):
        keys = _TOKEN_KEYS + ("duration_api_ms",)
        return all(not usage.get(key) for key in keys)
    text = str(result.get("error") or "")
    return bool(_ZERO_API_TEXT.search(text)) and all(
        pattern.search(text) for pattern in _ZERO_TOKENS_TEXT
    )


def zero_token_errors(scorecard: dict) -> list:
    """Scenario ids Claude never measured: quota or credential outage, not the agent."""
    return [
        s.get("scenario_id")
        for s in scorecard.get("scenarios") or []
        if _spent_no_tokens(s)
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

    current = _load(current_path)
    got = _scenario_ids(current)
    selected = scenario_filter(current)

    if selected:
        # A targeted run is complete when every SELECTED scenario ran. The
        # baseline's others were never asked for, so they are not "missing".
        absent = sorted(set(selected) - set(got))
        if absent:
            problems.append(
                f"{category}: {len(absent)} selected scenario(s) missing from the run: "
                f"{', '.join(absent)}"
            )
        expected = f"targeted run of {len(selected)} selected"
        if baseline_path.exists():
            others = set(_scenario_ids(_load(baseline_path))) - set(selected)
            expected += f", {len(others)} other baseline scenario(s) not run"
    # Without a baseline only the missing-scenario check goes dark; everything
    # below still answers "did this produce a measurement". Reporting the absent
    # baseline and moving on beats returning early, which would drop the one
    # check that still works and read as a clean category.
    elif baseline_path.exists():
        want = _scenario_ids(_load(baseline_path))
        absent = sorted(set(want) - set(got))
        if absent:
            problems.append(
                f"{category}: {len(absent)} baseline scenario(s) missing from the run: "
                f"{', '.join(absent)}"
            )
        expected = f"{len(want)} in baseline"
    else:
        problems.append(
            f"{category}: no baseline at {baseline_path}, so a scenario that "
            "silently stopped running cannot be detected"
        )
        expected = "no baseline to compare the scenario set against"

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
        f"{category}: {len(got)} scenario(s) run, {expected}, unmeasured={shown}"
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
    parser.add_argument(
        "--fail-on-zero-token-errors",
        action="store_true",
        help=(
            "Exit 1, whatever --enforce says, when any scenario ERRORED without "
            "Claude spending a token: the driver's quota ran out."
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
    no_tokens = []
    for category in args.categories:
        current_path = results_dir / category / "scorecard.json"
        cat_problems, status_line = check_category(
            baseline_dir / f"scorecard_{category}.json",
            current_path,
            category,
        )
        print(status_line)
        problems.extend(cat_problems)
        if args.fail_on_zero_token_errors and current_path.exists():
            no_tokens += zero_token_errors(_load(current_path))

    if no_tokens:
        message = (
            f"{len(no_tokens)} scenario(s) were cut off or never started because "
            f"Claude was unavailable: {', '.join(no_tokens)}. These are "
            "not results. Usually the quota ran out: wait for the window to reset "
            "(or switch CLAUDE_ACCOUNT), then re-dispatch just these with the "
            "`scenarios` input. If the quota is fine, the driver failed before its "
            "first call (credential or CLI flags): read the error in each trace."
        )
        print(f"::error::Integrity: {message}")
        _write_summary(["", "### Eval integrity: CLAUDE NEVER REACHED", "", message])

    # A category the environment made impossible is still an unmeasured category.
    # Named with its cause rather than silently dropped.
    for category in args.not_measured:
        problems.append(f"{category}: NOT MEASURED - {args.not_measured_reason}")

    if not problems:
        if no_tokens:
            # Counted from the scenarios themselves, so a stale summary block
            # cannot turn an outage green.
            print("Claude was unavailable - failing the build.")
            return 1
        print("")
        print("Integrity OK - every expected scenario ran and produced a measurement.")
        _write_summary(
            [
                "",
                "### Eval integrity: OK",
                "",
                f"Every expected scenario ran and produced a measurement "
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

    if args.enforce or no_tokens:
        reason = "enforce=true" if args.enforce else "Claude was never reached"
        print(f"{reason} - failing the build.")
        summary.append(f"**{reason} — this failed the build.**")
        _write_summary(summary)
        return 1

    print(
        "enforce=false - reported as warnings, build stays green. Re-dispatch with "
        "enforce=true to make this blocking."
    )
    summary.append(
        "**enforce=false — reported only; the build stays green.** Re-dispatch with "
        "`enforce: true` to make this blocking."
    )
    _write_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
