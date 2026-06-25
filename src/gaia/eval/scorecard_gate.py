# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Standalone release gate: blocks packaging when the candidate scorecard is missing
or when its aggregate score strictly regressed below the prior version's.

**Distinct from** ``src/gaia/eval/scorecard.py`` — that module aggregates per-run
scenario PASS/FAIL for internal CI. This gate checks the *outward-facing* release
artifact produced by ``release_scorecard.py``.

Usage::

    python -m gaia.eval.scorecard_gate \\
        --scorecards-dir hub/agents/npm/agent-email/scorecards \\
        --manifest hub/agents/python/email/gaia-agent.yaml

    python -m gaia.eval.scorecard_gate \\
        --scorecards-dir hub/agents/npm/agent-email/scorecards \\
        --version 0.2.4

Exit codes:
    0 — Passed (presence-only first adoption, equal score, or score improved).
    1 — Failed (missing/invalid candidate card, strict regression, or prior card invalid).

The ``--allow-regression`` flag overrides a regression: prints a ``::warning::``
GHA annotation and both version/score pairs, then exits 0.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from gaia.eval.release_scorecard import (
    _assert_safe_path,
    latest_version_below,
    parse_scorecard,
    validate_scorecard,
)


def _read_version_from_manifest(manifest_path: Path) -> str:
    """Read the ``version:`` field from a ``gaia-agent.yaml`` manifest.

    Args:
        manifest_path: Path to the YAML manifest file.

    Returns:
        The version string.

    Raises:
        ValueError: If the file cannot be read or ``version:`` is absent.
    """
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            f"Cannot read manifest {manifest_path}: {exc}"
        ) from exc

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Invalid YAML in manifest {manifest_path}: {exc}"
        ) from exc

    version = data.get("version")
    if not version:
        raise ValueError(
            f"Manifest {manifest_path} has no 'version:' field."
        )
    return str(version)


def main(argv=None) -> int:
    """Run the scorecard gate.

    Args:
        argv: Argument list (``sys.argv[1:]`` if None).

    Returns:
        0 on pass, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Release gate: ensures a valid scorecard exists for the candidate version "
            "and that its aggregate score has not strictly regressed vs the prior version."
        ),
        prog="python -m gaia.eval.scorecard_gate",
    )
    parser.add_argument(
        "--scorecards-dir",
        required=False,
        help="Directory containing per-version scorecard .md files.",
    )
    version_group = parser.add_mutually_exclusive_group()
    version_group.add_argument(
        "--version",
        help="Candidate version string (e.g. 0.2.4).",
    )
    version_group.add_argument(
        "--manifest",
        help="Path to gaia-agent.yaml; the 'version:' field is used as the candidate version.",
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

    # Validate required arguments
    if not args.scorecards_dir:
        print(
            "ERROR: --scorecards-dir is required.\n"
            "Usage: python -m gaia.eval.scorecard_gate --scorecards-dir DIR "
            "--version V (or --manifest PATH)"
        )
        return 1

    if not args.version and not args.manifest:
        print(
            "ERROR: Either --version or --manifest is required.\n"
            "Usage: python -m gaia.eval.scorecard_gate --scorecards-dir DIR "
            "--version V (or --manifest PATH)"
        )
        return 1

    scorecards_dir = Path(args.scorecards_dir)

    # Resolve the candidate version
    if args.manifest:
        try:
            version = _read_version_from_manifest(Path(args.manifest))
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1
    else:
        version = args.version

    # --- Step 1: Presence check ---
    try:
        candidate_path = _assert_safe_path(scorecards_dir, version)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    if not candidate_path.exists():
        print(
            f"ERROR: Scorecard missing for version {version}.\n"
            f"  Expected: {candidate_path}\n"
            f"  Run 'python gen_scorecard.py' (or 'carry_forward') to generate it, "
            f"then commit the file before releasing."
        )
        return 1

    try:
        candidate_parsed = parse_scorecard(candidate_path)
    except ValueError as exc:
        print(f"ERROR: Cannot parse candidate scorecard {candidate_path}: {exc}")
        return 1

    errors = validate_scorecard(candidate_parsed)
    if errors:
        print(
            f"ERROR: Candidate scorecard {candidate_path} is invalid:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
        return 1

    # --- Step 2: Locate prior version ---
    try:
        prev_version = latest_version_below(scorecards_dir, version)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    if prev_version is None:
        print(
            f"PASS: No prior scorecard found for versions below {version}. "
            f"First adoption — presence check only."
        )
        return 0

    # --- Step 3: Parse prior and regression check ---
    try:
        prev_path = _assert_safe_path(scorecards_dir, prev_version)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    try:
        prev_parsed = parse_scorecard(prev_path)
    except ValueError as exc:
        print(
            f"ERROR: Cannot parse prior scorecard {prev_path}: {exc}\n"
            f"  The prior scorecard is corrupt or missing a valid front matter. "
            f"Fix it before releasing {version}."
        )
        return 1

    prev_errors = validate_scorecard(prev_parsed)
    if prev_errors:
        print(
            f"ERROR: Prior scorecard {prev_path} is invalid:\n"
            + "\n".join(f"  - {e}" for e in prev_errors)
            + f"\n  Fix the prior scorecard before releasing {version}."
        )
        return 1

    candidate_score = candidate_parsed.get("aggregate", {}).get("value")
    prev_score = prev_parsed.get("aggregate", {}).get("value")

    if candidate_score is None:
        print(
            f"ERROR: Candidate scorecard {candidate_path} has no 'aggregate.value' field."
        )
        return 1

    if prev_score is None:
        print(
            f"ERROR: Prior scorecard {prev_path} has no 'aggregate.value' field."
        )
        return 1

    if float(candidate_score) < float(prev_score):
        # Strict regression detected
        if args.allow_regression:
            print(
                f"::warning::Scorecard regression allowed by --allow-regression: "
                f"{prev_version}={prev_score} → {version}={candidate_score}"
            )
            print(
                f"WARNING: Regression override active. "
                f"Prior version {prev_version} scored {prev_score}; "
                f"candidate {version} scored {candidate_score}. "
                f"This regression has been explicitly acknowledged."
            )
            return 0
        print(
            f"ERROR: Scorecard regression detected.\n"
            f"  Prior version {prev_version}: aggregate.value = {prev_score}\n"
            f"  Candidate {version}: aggregate.value = {candidate_score}\n"
            f"  The candidate score is strictly lower than the prior. "
            f"Investigate the regression or use --allow-regression to override intentionally."
        )
        return 1

    print(
        f"PASS: Scorecard gate passed.\n"
        f"  Candidate {version}: aggregate.value = {candidate_score} "
        f"(prior {prev_version}: {prev_score})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
