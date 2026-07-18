#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Discover docs in scope for the weekly doc walkthrough.

Emits a JSON array of {"path": ..., "slug": ...} for every doc the
walkthrough should walk. Glob-based (not a hardcoded list) so a new guide is
picked up automatically -- same extensibility principle as
claude-weekly-audit.yml's dimension matrix. stdlib only: this runs before any
venv exists.
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Explicit exclude list for docs that are structurally out of scope for a CLI
# walkthrough (not "hard to test" -- those still get walked with a per-step
# "not verifiable" flag; these have no command-line surface at all).
EXCLUDE = {
    # Needs a browser driver (Playwright) to verify for real -- stretch goal,
    # see docs/plans/weekly-doc-walkthrough-audit.md "Non-goals (v1)".
    "docs/guides/agent-ui.mdx",
}

GLOBS = [
    "docs/guides/*.mdx",
    "docs/quickstart.mdx",
    "docs/setup.mdx",
    "docs/reference/cli.mdx",
]


def slugify(rel_path: str) -> str:
    stem = re.sub(r"\.mdx?$", "", rel_path)
    return re.sub(r"[^a-zA-Z0-9]+", "-", stem).strip("-").lower()


def discover() -> list:
    seen = set()
    docs = []
    for pattern in GLOBS:
        for match in sorted(REPO_ROOT.glob(pattern)):
            rel = match.relative_to(REPO_ROOT).as_posix()
            if rel in EXCLUDE or rel in seen:
                continue
            seen.add(rel)
            docs.append({"path": rel, "slug": slugify(rel)})
    return docs


def main() -> int:
    docs = discover()
    if not docs:
        print(
            "no docs discovered -- EXCLUDE list or GLOBS is misconfigured",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(docs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
