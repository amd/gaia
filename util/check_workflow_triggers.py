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

Per #2939, a workflow that gates its jobs on the `ready_for_ci` label must list
`labeled` in its PR `types:`. Without it, applying the label to a draft starts
nothing, so the condition that checks for it is never evaluated.

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
READY_LABEL = "ready_for_ci"


def _triggers(workflow: Any) -> Dict[Any, Any]:
    """Return the `on:` mapping, or {} if there isn't one.

    PyYAML resolves the bare `on:` key to the boolean True (YAML 1.1), so both
    spellings have to be probed.
    """
    if not isinstance(workflow, dict):
        return {}
    triggers = workflow.get("on", workflow.get(True))
    return triggers if isinstance(triggers, dict) else {}


def _pull_request_trigger(workflow: Any, event: str) -> Dict[str, Any] | None:
    """Return the requested PR event mapping, or None if there isn't one."""
    trigger = _triggers(workflow).get(event)
    return trigger if isinstance(trigger, dict) else None


def _ready_label_errors(name: str, workflow: Any, text: str) -> list[str]:
    """Flag PR triggers that can never react to the `ready_for_ci` label (#2939)."""
    if READY_LABEL not in text:
        return []
    errors = []
    triggers = _triggers(workflow)
    for event in PR_EVENTS:
        if event not in triggers:
            continue
        trigger = triggers[event]
        # A bare `pull_request:` uses GitHub's default types, which omit `labeled`.
        types = trigger.get("types") if isinstance(trigger, dict) else None
        if types is None:
            types = ["opened", "synchronize", "reopened"]
        if isinstance(types, str):
            types = [types]
        if "labeled" not in types:
            errors.append(
                f"{name}: checks the `{READY_LABEL}` label but `on.{event}.types` "
                f"{types} omits `labeled`, so applying the label to a draft PR "
                f"starts nothing. Add `labeled` to the types list (#2939)."
            )
    return errors


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
        text = path.read_text(encoding="utf-8")
        try:
            workflow = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            errors.append(f"{path.name}: failed to parse: {exc}")
            continue

        checked += 1
        errors.extend(_ready_label_errors(path.name, workflow, text))
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
