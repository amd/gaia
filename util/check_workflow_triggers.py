# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Reject `on.pull_request.branches:` filters in .github/workflows/*.yml.

Per #2767, `on.pull_request.branches` matches the PR's BASE branch, not its head.
`branches: [ main ]` therefore means "only when merging into main" — every stacked
PR (one opened against another feature branch) falls outside the filter and the
job is never created. Nothing shows failed or skipped; the checks page is simply
missing it. That is how #2599 merged email-agent code with zero email tests, zero
unit tests and zero lint behind a green checks page.

Use `paths:` to scope a workflow to the code it covers. Never `branches:`.

Runs as part of `python util/lint.py --all`. Run directly with
`python util/check_workflow_triggers.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import yaml

# Anchor to the repo root so the script works regardless of CWD — matches the
# convention in util/check_dependabot.py and util/check_doc_versions.py.
REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"


def _pull_request_trigger(workflow: Any) -> Dict[str, Any] | None:
    """Return the `on.pull_request` mapping, or None if there isn't one.

    PyYAML resolves the bare `on:` key to the boolean True (YAML 1.1), so both
    spellings have to be probed.
    """
    if not isinstance(workflow, dict):
        return None
    triggers = workflow.get("on", workflow.get(True))
    if not isinstance(triggers, dict):
        return None
    trigger = triggers.get("pull_request")
    return trigger if isinstance(trigger, dict) else None


def run_check() -> int:
    """Validate every workflow's pull_request trigger. 0 on success, 1 on error."""
    if not WORKFLOW_DIR.is_dir():
        print(f"[!] {WORKFLOW_DIR} not found", file=sys.stderr)
        return 1

    errors: list[str] = []
    checked = 0

    workflows = sorted(
        p for p in WORKFLOW_DIR.iterdir() if p.suffix in (".yml", ".yaml")
    )

    for path in workflows:
        try:
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: failed to parse: {exc}")
            continue

        checked += 1
        trigger = _pull_request_trigger(workflow)
        if trigger is not None and "branches" in trigger:
            errors.append(
                f"{path.name}: `on.pull_request.branches: "
                f"{trigger['branches']}` filters on the PR's BASE branch, so this "
                f"workflow never runs on a stacked PR. Drop the filter; scope the "
                f"workflow with `paths:` instead (#2767)."
            )

    if errors:
        print("[!] Workflow trigger issues found:", file=sys.stderr)
        for err in errors:
            print(f"    - {err}", file=sys.stderr)
        return 1

    print(f"[OK] {checked} workflow pull_request triggers validated.")
    return 0


if __name__ == "__main__":
    sys.exit(run_check())
