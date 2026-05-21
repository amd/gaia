# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Validate .github/dependabot.yml: no soft-disabled entries, no missing groups.

Per #1157, every ecosystem entry MUST be live (open-pull-requests-limit != 0) and every
npm entry MUST have a `groups:` stanza (without grouping, removing limit:0 produces
one PR per outdated package and floods the queue).

Runs as part of `python util/lint.py --all`. Run directly with
`python util/check_dependabot.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Anchor to the repo root so the script works regardless of CWD — matches the
# convention in util/check_doc_versions.py and util/check_agent_conventions.py.
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / ".github" / "dependabot.yml"


def run_check() -> int:
    """Validate .github/dependabot.yml. Returns 0 on success, 1 on any error."""
    if not CONFIG.exists():
        print(f"[!] {CONFIG} not found", file=sys.stderr)
        return 1

    try:
        cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"[!] {CONFIG} failed to parse: {exc}", file=sys.stderr)
        return 1

    if not isinstance(cfg, dict) or "updates" not in cfg:
        print(f"[!] {CONFIG} has no top-level 'updates' list", file=sys.stderr)
        return 1

    errors: list[str] = []

    for entry in cfg["updates"]:
        directory = entry.get("directory", "<unknown>")
        ecosystem = entry.get("package-ecosystem", "<unknown>")
        # Absent key means Dependabot uses its own positive default — only an explicit 0 soft-disables.
        limit = entry.get("open-pull-requests-limit", 5)

        if limit == 0:
            errors.append(
                f"{ecosystem} {directory}: open-pull-requests-limit is 0 "
                f"(soft-disabled). Per #1157, all entries must be live."
            )

        if ecosystem == "npm" and "groups" not in entry:
            errors.append(
                f"{ecosystem} {directory}: missing `groups:` stanza. Without "
                f"grouping, Dependabot opens one PR per outdated package — "
                f"see #1157."
            )

    if errors:
        print("[!] Dependabot configuration issues found:", file=sys.stderr)
        for err in errors:
            print(f"    - {err}", file=sys.stderr)
        return 1

    print(f"[OK] {len(cfg['updates'])} dependabot.yml entries validated.")
    return 0


if __name__ == "__main__":
    sys.exit(run_check())
