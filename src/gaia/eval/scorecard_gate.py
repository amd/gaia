# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Standalone release gate: blocks packaging when the candidate SCORECARD.md is
missing, invalid, or when its aggregate score strictly regressed below the prior
version's.

**Distinct from** ``src/gaia/eval/scorecard.py`` — that module aggregates per-run
scenario PASS/FAIL for internal CI. This gate checks the *outward-facing* release
artifact produced by ``release_scorecard.py``.

Storage convention: one ``SCORECARD.md`` per agent package (updated in place,
versioned via the publish snapshot — the same way README.md works).

Usage::

    # Presence-only (first adoption):
    python -m gaia.eval.scorecard_gate \\
        --scorecard hub/agents/npm/agent-email/SCORECARD.md

    # With a baseline from a file (unit tests):
    python -m gaia.eval.scorecard_gate \\
        --scorecard hub/agents/npm/agent-email/SCORECARD.md \\
        --baseline-file /tmp/prev-SCORECARD.md

    # With a baseline resolved from a git ref (CI):
    python -m gaia.eval.scorecard_gate \\
        --scorecard hub/agents/npm/agent-email/SCORECARD.md \\
        --baseline-ref agent-pkg-email-v0.2.3

Exit codes:
    0 — Passed (presence-only first adoption, equal score, or score improved).
    1 — Failed (missing/invalid candidate, strict regression, invalid baseline).

The ``--allow-regression`` flag overrides a regression: prints a ``::warning::``
GHA annotation and both version/score pairs, then exits 0.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from gaia.eval.release_scorecard import (
    parse_scorecard,
    validate_scorecard,
)


def _metric_value(parsed: dict, name: str) -> float | None:
    """Read a displayed metric's value by name from a parsed scorecard.

    Looks in ``results.metrics`` first, then ``aggregate.components`` — both carry
    the secondary (weight-0) metrics. Returns ``None`` if absent or non-numeric.
    """
    for m in parsed.get("results", {}).get("metrics", []) or []:
        if m.get("name") == name and isinstance(m.get("value"), (int, float)):
            return float(m["value"])
    for c in parsed.get("aggregate", {}).get("components", []) or []:
        if c.get("metric") == name and isinstance(c.get("value"), (int, float)):
            return float(c["value"])
    return None


def _within_one_stdev(parsed: dict) -> float | None:
    """Recorded run-to-run stdev of the within-one aggregate (#1894), [0,1] scale.

    Lives in ``recipe.config.acceptance_variance.within_one_bucket_accuracy.stdev``.
    Returns ``None`` when the card carries no variance (single-run / older cards) —
    the gate then falls back to a strict ``<`` regression check.
    """
    av = parsed.get("recipe", {}).get("config", {}).get("acceptance_variance")
    if not isinstance(av, dict):
        return None
    w = av.get("within_one_bucket_accuracy")
    if isinstance(w, dict) and isinstance(w.get("stdev"), (int, float)):
        return float(w["stdev"])
    return None


def _parse_baseline_ref(scorecard_path: Path, ref: str) -> str | None:
    """Resolve ``<ref>:<scorecard-path>`` via ``git show`` and return the content.

    The path used in the git command is the path of ``scorecard_path`` relative
    to the repository root (discovered by ``git rev-parse --show-toplevel``).

    Returns the file content as a string, or None if the file does not exist at
    that ref (treated as first adoption — presence-only pass).

    Raises:
        ValueError: If ``git`` cannot be called or the ref is otherwise invalid
            (the caller treats this as an actionable error, not first adoption).
    """
    # Discover repo root so we can form a root-relative path for git show.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ValueError(
            f"Cannot determine git repository root: {exc}. "
            "Run from inside a git repository, or use --baseline-file instead."
        ) from exc

    repo_root = Path(result.stdout.strip())
    scorecard_path = Path(scorecard_path).resolve()
    try:
        rel = scorecard_path.relative_to(repo_root)
    except ValueError:
        raise ValueError(
            f"SCORECARD path {scorecard_path} is not inside the git repo root "
            f"{repo_root}. Use an absolute path inside the repo, or use "
            "--baseline-file instead."
        )

    git_path = rel.as_posix()
    try:
        result = subprocess.run(  # noqa: S603 (git is trusted here)
            ["git", "show", f"{ref}:{git_path}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"git not found: {exc}") from exc

    if result.returncode != 0:
        # File absent at that ref → first adoption (presence-only pass).
        return None

    return result.stdout


def main(argv=None) -> int:
    """Run the scorecard gate.

    Args:
        argv: Argument list (``sys.argv[1:]`` if None).

    Returns:
        0 on pass, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Release gate: ensures a valid SCORECARD.md exists and that its "
            "aggregate score has not strictly regressed vs the prior version."
        ),
        prog="python -m gaia.eval.scorecard_gate",
    )
    parser.add_argument(
        "--scorecard",
        required=True,
        help="Path to the candidate SCORECARD.md (e.g. hub/agents/npm/agent-email/SCORECARD.md).",
    )
    baseline_group = parser.add_mutually_exclusive_group()
    baseline_group.add_argument(
        "--baseline-file",
        help=(
            "Path to the prior version's SCORECARD.md for regression comparison "
            "(for unit tests; no git access needed)."
        ),
    )
    baseline_group.add_argument(
        "--baseline-ref",
        help=(
            "Git ref (tag or commit) of the prior release to use as baseline. "
            "Resolves via 'git show <ref>:<scorecard-path>'. If the file does not "
            "exist at that ref, a presence-only pass is applied (first adoption)."
        ),
    )
    parser.add_argument(
        "--allow-regression",
        action="store_true",
        default=False,
        help=(
            "Override a regression: prints a GHA ::warning:: annotation and both "
            "version/score pairs, then exits 0. Use only when a regression is intentional."
        ),
    )
    parser.add_argument(
        "--min-aggregate",
        type=float,
        default=None,
        help=(
            "Absolute acceptance bar (#1437): fail if aggregate.value is below this "
            "(e.g. 80 for the 80%% within-one-bucket target). Applies to every path, "
            "including first adoption. Omit to skip the absolute check (report mode)."
        ),
    )
    parser.add_argument(
        "--min-urgent-recall",
        type=float,
        default=None,
        help=(
            "Anti-gaming floor: fail if the card's 'urgent_recall' secondary metric "
            "is below this (e.g. 0.70) — a high aggregate must not come with buried "
            "urgent mail. Fails loud if the metric is absent. Omit to skip."
        ),
    )
    parser.add_argument(
        "--regression-k",
        type=float,
        default=1.0,
        help=(
            "Variance-aware regression band (#1894): when the BASELINE card records a "
            "within-one stdev, flag a regression only if the candidate falls below "
            "baseline − k·stdev (default k=1). With no recorded stdev, a strict '<' "
            "check is used."
        ),
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 1

    candidate_path = Path(args.scorecard)

    # --- Step 1: Presence check ---
    if not candidate_path.exists():
        print(
            f"ERROR: SCORECARD.md missing at {candidate_path}.\n"
            f"  Run 'python gen_scorecard.py' (or 'carry_forward') to generate it, "
            f"then commit the file before releasing.\n"
            f"  See https://amd-gaia.ai/docs/reference/eval-scorecard and "
            f".claude/skills/adding-eval-scorecard/SKILL.md"
        )
        return 1

    try:
        candidate_parsed = parse_scorecard(candidate_path)
    except ValueError as exc:
        print(f"ERROR: Cannot parse candidate SCORECARD.md at {candidate_path}: {exc}")
        return 1

    errors = validate_scorecard(candidate_parsed)
    if errors:
        print(
            f"ERROR: Candidate SCORECARD.md at {candidate_path} is invalid:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
        return 1

    # --- Step 1b: Absolute acceptance bar + URGENT floor (#1437) ---
    # These are candidate-only checks (no baseline needed) and apply to EVERY
    # path including first adoption, so a sub-bar release is blocked even when no
    # prior version exists.
    cand_version = candidate_parsed.get("agent", {}).get("version", "?")
    cand_aggregate = candidate_parsed.get("aggregate", {}).get("value")
    if args.min_aggregate is not None:
        if cand_aggregate is None:
            print(
                f"ERROR: Candidate SCORECARD.md at {candidate_path} has no "
                f"'aggregate.value'; cannot enforce --min-aggregate."
            )
            return 1
        if float(cand_aggregate) < float(args.min_aggregate):
            print(
                f"ERROR: Acceptance bar not met (#1437).\n"
                f"  Candidate v{cand_version}: aggregate.value = {cand_aggregate}\n"
                f"  Required: >= {args.min_aggregate}\n"
                f"  Improve the agent's within-one-bucket accuracy before releasing."
            )
            return 1
    if args.min_urgent_recall is not None:
        urgent_recall = _metric_value(candidate_parsed, "urgent_recall")
        if urgent_recall is None:
            print(
                f"ERROR: --min-urgent-recall set but the candidate SCORECARD.md at "
                f"{candidate_path} has no 'urgent_recall' metric.\n"
                f"  Re-generate the scorecard with the current adapter (it records "
                f"urgent_recall as a secondary metric)."
            )
            return 1
        if float(urgent_recall) < float(args.min_urgent_recall):
            print(
                f"ERROR: URGENT recall floor not met (anti-gaming).\n"
                f"  Candidate v{cand_version}: urgent_recall = {urgent_recall}\n"
                f"  Required: >= {args.min_urgent_recall}\n"
                f"  Urgent mail is being buried — fix before releasing even if the "
                f"aggregate passes."
            )
            return 1

    # --- Step 2: Resolve baseline ---
    baseline_text: str | None = None

    if args.baseline_file:
        baseline_path = Path(args.baseline_file)
        if not baseline_path.exists():
            print(
                f"ERROR: --baseline-file not found: {baseline_path}.\n"
                f"  Provide a valid path to a prior SCORECARD.md, or omit --baseline-file "
                f"for a presence-only pass."
            )
            return 1
        try:
            baseline_text = baseline_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: Cannot read --baseline-file {baseline_path}: {exc}")
            return 1

    elif args.baseline_ref:
        try:
            baseline_text = _parse_baseline_ref(candidate_path, args.baseline_ref)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1
        # None means the file doesn't exist at that ref → first adoption
        if baseline_text is None:
            print(
                f"PASS: No SCORECARD.md found at ref '{args.baseline_ref}'. "
                f"First adoption — presence check only."
            )
            return 0

    if baseline_text is None:
        # No baseline specified at all → presence-only pass.
        candidate_version = candidate_parsed.get("agent", {}).get("version", "?")
        candidate_score = candidate_parsed.get("aggregate", {}).get("value")
        if candidate_score is None:
            print(
                f"ERROR: Candidate SCORECARD.md at {candidate_path} has no "
                f"'aggregate.value' field.\n"
                f"  Fix the scorecard front matter before releasing."
            )
            return 1
        print(
            f"PASS: No baseline provided. Presence check only.\n"
            f"  Candidate v{candidate_version}: aggregate.value = {candidate_score}"
        )
        return 0

    # --- Step 3: Parse baseline and regression check ---
    try:
        prev_parsed = parse_scorecard(baseline_text)
    except ValueError as exc:
        print(
            f"ERROR: Cannot parse baseline SCORECARD.md: {exc}\n"
            f"  The baseline is corrupt or missing a valid front matter. "
            f"Fix it before releasing."
        )
        return 1

    prev_errors = validate_scorecard(prev_parsed)
    if prev_errors:
        print(
            "ERROR: Baseline SCORECARD.md is invalid:\n"
            + "\n".join(f"  - {e}" for e in prev_errors)
            + "\n  Fix the baseline scorecard before releasing."
        )
        return 1

    candidate_score = candidate_parsed.get("aggregate", {}).get("value")
    prev_score = prev_parsed.get("aggregate", {}).get("value")

    if candidate_score is None:
        print(
            f"ERROR: Candidate SCORECARD.md at {candidate_path} has no "
            "'aggregate.value' field.\n"
            "  Fix the scorecard front matter before releasing."
        )
        return 1

    if prev_score is None:
        print(
            "ERROR: Baseline SCORECARD.md has no 'aggregate.value' field.\n"
            "  Fix the baseline scorecard before releasing."
        )
        return 1

    candidate_version = candidate_parsed.get("agent", {}).get("version", "?")
    prev_version = prev_parsed.get("agent", {}).get("version", "?")

    # Variance-aware regression band (#1894): if the baseline recorded a within-one
    # stdev, a single noisy draw shouldn't trip the gate — only flag a regression
    # when the candidate falls below baseline − k·stdev. stdev is on the [0,1]
    # scale; aggregate.value is ×100, so scale the band to match. With no recorded
    # stdev, fall back to a strict '<' (back-compat with older single-run cards).
    prev_stdev = _within_one_stdev(prev_parsed)
    regression_threshold = float(prev_score)
    band_note = ""
    if prev_stdev is not None and args.regression_k > 0:
        band = args.regression_k * prev_stdev * 100.0
        regression_threshold = float(prev_score) - band
        band_note = (
            f" [variance-aware: {prev_score} − {args.regression_k}×stdev"
            f"({round(prev_stdev * 100, 2)}) = {round(regression_threshold, 2)}]"
        )

    if float(candidate_score) < regression_threshold:
        # Regression detected (beyond the noise band, if one is recorded)
        if args.allow_regression:
            print(
                f"::warning::Scorecard regression allowed by --allow-regression: "
                f"v{prev_version}={prev_score} → v{candidate_version}={candidate_score}"
            )
            print(
                f"WARNING: Regression override active. "
                f"Prior version v{prev_version} scored {prev_score}; "
                f"candidate v{candidate_version} scored {candidate_score}. "
                f"This regression has been explicitly acknowledged."
            )
            return 0
        print(
            f"ERROR: Scorecard regression detected.{band_note}\n"
            f"  Prior version v{prev_version}: aggregate.value = {prev_score}\n"
            f"  Candidate v{candidate_version}: aggregate.value = {candidate_score}\n"
            f"  The candidate score is below the regression threshold. "
            f"Investigate the regression or use --allow-regression to override intentionally."
        )
        return 1

    print(
        f"PASS: Scorecard gate passed.\n"
        f"  Candidate v{candidate_version}: aggregate.value = {candidate_score} "
        f"(prior v{prev_version}: {prev_score})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
