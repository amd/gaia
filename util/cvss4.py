# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Score a CVSS 4.0 vector to a base score + severity.

A thin, deterministic wrapper over the ``cvss`` library, whose 4.0 scores match
the FIRST v4 calculator (https://www.first.org/cvss/calculator/4.0) — verified in
``tests/unit/test_cvss4.py`` against known anchors. The security-audit workflow
uses this so a finding's severity comes from arithmetic on a reviewed *vector*,
never an LLM's guess at a number (the AI triage on a real ticket estimated 7.3 for
a vector that actually scores 8.7 — see the tests).

Usage:
    python util/cvss4.py "CVSS:4.0/AV:N/AC:L/AT:P/PR:N/UI:P/VC:N/VI:H/VA:N/SC:N/SI:N/SA:N"
    -> {"vector": "...", "base_score": 6.0, "severity": "Medium"}

The *vector* is the judgment call (which requires human review); this tool only
does the math on it.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict


def score(vector: str) -> Dict[str, Any]:
    """Return ``{vector, base_score, severity}`` for a CVSS 4.0 vector string.

    Raises ValueError (with the offending vector) if it is malformed — a bad
    vector is a loud error, never a silent 0.0.
    """
    try:
        from cvss import CVSS4
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ImportError(
            "The 'cvss' package is required to score CVSS 4.0 vectors. "
            'Install it with `pip install -e ".[dev]"` (it is in the dev extra).'
        ) from exc

    try:
        c = CVSS4(vector)
    except Exception as exc:  # cvss raises its own CVSSError subclasses
        raise ValueError(f"Invalid CVSS 4.0 vector {vector!r}: {exc}") from exc

    return {
        "vector": c.clean_vector(),
        "base_score": c.base_score,
        "severity": c.severities()[0],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a CVSS 4.0 vector.")
    parser.add_argument("vector", help="e.g. 'CVSS:4.0/AV:N/AC:L/.../SA:N'")
    args = parser.parse_args(argv)
    try:
        result = score(args.vector)
    except (ValueError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
