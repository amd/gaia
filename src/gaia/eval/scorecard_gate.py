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

    if float(candidate_score) < float(prev_score):
        # Strict regression detected
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
            f"ERROR: Scorecard regression detected.\n"
            f"  Prior version v{prev_version}: aggregate.value = {prev_score}\n"
            f"  Candidate v{candidate_version}: aggregate.value = {candidate_score}\n"
            f"  The candidate score is strictly lower than the prior. "
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
