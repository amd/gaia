# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Reject base-branch filters on PR events in .github/workflows/*.yml.

Per #2767, `on.pull_request.branches` matches the PR's BASE branch, not its head.
`branches: [ main ]` therefore means "only when merging into main" — every stacked
PR (one opened against another feature branch) falls outside the filter and the
job is never created. Nothing shows failed or skipped; the checks page is simply
missing it. That is how #2599 merged email-agent code with zero email tests, zero
unit tests and zero lint behind a green checks page.

This also applies to `pull_request_target` and `branches-ignore`. Use `paths:`
to scope a workflow to the code it covers, rather than base-branch filters.

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
PR_EVENTS = ("pull_request", "pull_request_target")
FILTER_KEYS = ("branches", "branches-ignore")


def _pull_request_trigger(workflow: Any, event: str) -> Dict[str, Any] | None:
    """Return the requested PR event mapping, or None if there isn't one.

    PyYAML resolves the bare `on:` key to the boolean True (YAML 1.1), so both
    spellings have to be probed.
    """
    if not isinstance(workflow, dict):
        return None
    triggers = workflow.get("on", workflow.get(True))
    if not isinstance(triggers, dict):
        return None
    trigger = triggers.get(event)
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
        for event in PR_EVENTS:
            trigger = _pull_request_trigger(workflow, event)
            if trigger is None:
                continue
            for key in FILTER_KEYS:
                if key in trigger:
                    errors.append(
                        f"{path.name}: `on.{event}.{key}: {trigger[key]}` "
                        f"filters on the PR's BASE branch, so this workflow can "
                        f"skip stacked PRs. Drop the filter; scope the workflow "
                        f"with `paths:` instead (#2767)."
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
