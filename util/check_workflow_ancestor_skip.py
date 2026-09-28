# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Flag job `if:` conditions that silently inherit GitHub Actions' implicit success().

A job's `if:` expression is implicitly ANDed with `success()` unless the
expression itself calls one of the status-check functions (`success()`,
`failure()`, `cancelled()`, `always()`). So a condition like

    if: needs.version.outputs.dry_run == 'false'

does not mean "run when dry_run is false" — it means "run when dry_run is
false AND every job in the transitive `needs:` chain succeeded". A job
anywhere upstream that reports `'skipped'` (a conditional path skipping on
purpose, not a failure) silently skips this job too, with nothing in the run
log to say why — the condition just never became true.

This is not hypothetical: it fully blocked v0.24.0's GitHub Release for three
retags (#3915-#3917), and a second, independent instance was found and fixed
in `release_components.yml`'s `redeploy-website` job (#3929) by manually
auditing every job in the file. This check automates that audit so the
pattern can't recur silently a third time.

The fix is always the same shape: prefix the expression with `!cancelled()`
(to opt out of the implicit success() ANDing while still refusing a genuine
failure) or `always()`, then handle any now-visible 'skipped' ancestor
explicitly if that is an acceptable path for this job.

Only flags a job whose `if:` references `needs.` at all — a condition with no
cross-job reference cannot be affected by an ancestor's skip, so it is not
flagged even without a status function.

Runs as part of `python util/lint.py --all`. Run directly with
`python util/check_workflow_ancestor_skip.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

# Anchor to the repo root so the script works regardless of CWD — matches the
# convention in util/check_workflow_triggers.py.
REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

_STATUS_FUNCTIONS = ("success(", "failure(", "cancelled(", "always(")
_NEEDS_RE = re.compile(r"\bneeds\.")


def _job_if_conditions(workflow: Any) -> List[tuple]:
    """Every (job_id, if_expression) pair with a non-empty `if:` string."""
    if not isinstance(workflow, dict):
        return []
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return []

    found = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        condition = job.get("if")
        if isinstance(condition, str) and condition.strip():
            found.append((job_id, condition))
    return found


def run_check() -> int:
    """Validate every job's `if:` condition. 0 on success, 1 on error."""
    if not WORKFLOW_DIR.is_dir():
        print(f"[!] {WORKFLOW_DIR} not found", file=sys.stderr)
        return 1

    errors: List[str] = []
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

        for job_id, condition in _job_if_conditions(workflow):
            checked += 1
            if not _NEEDS_RE.search(condition):
                continue
            if any(fn in condition for fn in _STATUS_FUNCTIONS):
                continue
            errors.append(
                f"{path.name}: job `{job_id}` has `if: {condition.strip()}` — "
                f"this references needs.* with no success()/failure()/"
                f"cancelled()/always() call, so GitHub Actions implicitly ANDs "
                f"it with success(). A 'skipped' job anywhere in the transitive "
                f"needs: chain silently skips this job too, even when the "
                f"written condition would otherwise be true. Prefix with "
                f"`!cancelled() &&` (or `always() &&`) and handle 'skipped' "
                f"ancestors explicitly if that is an acceptable path (#3929)."
            )

    if errors:
        print("[!] Ancestor-skip risk found:", file=sys.stderr)
        for err in errors:
            print(f"    - {err}", file=sys.stderr)
        return 1

    print(f"[OK] {checked} job `if:` condition(s) checked for ancestor-skip risk.")
    return 0


if __name__ == "__main__":
    sys.exit(run_check())
