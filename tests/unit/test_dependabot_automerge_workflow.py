# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
dependabot-automerge.yml must actually enable auto-merge, and only when the
base branch has required checks that would gate it (issue #3260).

The old guard compared ``gh --jq '.autoMergeRequest'`` to the literal string
``null``. gh prints an empty line for a JSON null, so the guard always took the
"already enabled" branch and the run went green without ever merging anything.
Fixing that alone would be worse: GitHub's auto-merge waits only for *required*
checks, and with none it merges immediately.

The behaviour tests run the step's real ``run:`` script under bash with a ``gh``
stub that pipes canned API JSON through ``jq``, so the jq expressions in the
workflow are exercised, not just the shell around them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dependabot-automerge.yml"
STEP_NAME = "Enable auto-merge for patch and dev-only minor updates"
PR_URL = "https://github.com/amd/gaia/pull/1"


@pytest.fixture(scope="module")
def step() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [
        s for s in workflow["jobs"]["auto-merge"]["steps"] if s.get("name") == STEP_NAME
    ]
    assert steps, f"{WORKFLOW.name} no longer has a {STEP_NAME!r} step"
    return steps[0]


def test_guard_never_compares_to_the_literal_null(step):
    run = step["run"]
    assert not re.search(r"""!?=\s*["']null["']""", run), (
        "gh --jq prints an empty line for a JSON null, not the word 'null'; "
        "comparing to it makes the guard always fire (#3260)."
    )
    assert ".autoMergeRequest // empty" in run


def test_required_checks_are_read_before_merging(step):
    run = step["run"]
    merge_at = run.index("gh pr merge --auto")
    for probe in ('gh api "repos/$REPO/branches/', 'gh api "repos/$REPO/rules/'):
        assert probe in run, f"step no longer reads required checks via {probe!r}"
        assert run.index(probe) < merge_at


def test_step_fails_on_errors_and_never_tolerates_them(step):
    assert "set -euo pipefail" in step["run"]
    assert step.get("continue-on-error", False) is False


def test_base_ref_and_repo_come_from_env_not_inline(step):
    assert "${{" not in step["run"]
    assert step["env"]["BASE_REF"] == "${{ github.event.pull_request.base.ref }}"
    assert step["env"]["REPO"] == "${{ github.repository }}"


# --- behaviour: run the real script against a stubbed gh -------------------

_STUB = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
expr=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--jq" ]; then expr="$a"; fi
  prev="$a"
done
case "$1 $2" in
  "api repos/amd/gaia/branches/"*) body_file="$STUB_BRANCH" ;;
  "api repos/amd/gaia/rules/"*) body_file="$STUB_RULES" ;;
  "pr view") body_file="$STUB_PR" ;;
  "pr merge") exit "${STUB_MERGE_EXIT:-0}" ;;
  *) echo "unexpected gh call: $*" >&2; exit 99 ;;
esac
if [ "$(cat "$body_file")" = "FAIL" ]; then
  echo "gh: HTTP 403" >&2
  exit 1
fi
jq -r "$expr" "$body_file"
"""

PROTECTED = {
    "protection": {
        "enabled": True,
        "required_status_checks": {
            "enforcement_level": "non_admins",
            "contexts": ["lint"],
            "checks": [{"context": "lint", "app_id": 1}, {"context": "unit"}],
        },
    }
}
UNGATED = {
    "protection": {
        "enabled": True,
        "required_status_checks": {
            "enforcement_level": "non_admins",
            "contexts": [],
            "checks": [],
        },
    }
}
RULESET_CHECKS = [
    {"type": "pull_request", "parameters": {}},
    {
        "type": "required_status_checks",
        "parameters": {"required_status_checks": [{"context": "unit"}]},
    },
]


def _run(tmp_path, step, *, branch, rules, pr, merge_exit=0):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    if shutil.which("jq") is None:
        pytest.skip("jq not available (it ships on GitHub's ubuntu runners)")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(_STUB, encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    files = {}
    for name, body in (("branch", branch), ("rules", rules), ("pr", pr)):
        f = tmp_path / f"{name}.json"
        f.write_text(body if isinstance(body, str) else json.dumps(body), "utf-8")
        files[name] = str(f)
    log = tmp_path / "gh.log"
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "STUB_BRANCH": files["branch"],
        "STUB_RULES": files["rules"],
        "STUB_PR": files["pr"],
        "STUB_MERGE_EXIT": str(merge_exit),
        "PR_URL": PR_URL,
        "REPO": "amd/gaia",
        "BASE_REF": "main",
    }
    res = subprocess.run(
        [bash, "-c", step["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return res, calls


def _merges(calls):
    return [c for c in calls if c.startswith("pr merge")]


def test_enables_auto_merge_when_it_is_off_and_checks_are_required(tmp_path, step):
    res, calls = _run(
        tmp_path, step, branch=PROTECTED, rules=[], pr={"autoMergeRequest": None}
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert _merges(calls) == [f"pr merge --auto --squash {PR_URL}"]
    assert "branch protection=2" in res.stdout


def test_already_enabled_is_a_no_op(tmp_path, step):
    res, calls = _run(
        tmp_path,
        step,
        branch=PROTECTED,
        rules=[],
        pr={"autoMergeRequest": {"mergeMethod": "SQUASH"}},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "already enabled" in res.stdout
    assert _merges(calls) == []


def test_ruleset_required_checks_alone_are_enough(tmp_path, step):
    res, calls = _run(
        tmp_path,
        step,
        branch=UNGATED,
        rules=RULESET_CHECKS,
        pr={"autoMergeRequest": None},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert len(_merges(calls)) == 1


@pytest.mark.parametrize(
    "branch",
    [
        UNGATED,
        {"protection": {"enabled": False}},
        {"name": "main"},
        {
            "protection": {
                "required_status_checks": {
                    "enforcement_level": "off",
                    "contexts": ["lint"],
                    "checks": [],
                }
            }
        },
    ],
    ids=["empty-lists", "protection-disabled", "no-protection-key", "enforcement-off"],
)
def test_refuses_when_nothing_would_gate_the_merge(tmp_path, step, branch):
    res, calls = _run(
        tmp_path,
        step,
        branch=branch,
        rules=[{"type": "pull_request", "parameters": {}}],
        pr={"autoMergeRequest": None},
    )
    assert res.returncode == 1
    assert "::error" in res.stdout and "#3260" in res.stdout
    assert _merges(calls) == []
    assert not any(c.startswith("pr view") for c in calls)


@pytest.mark.parametrize("failing", ["branch", "rules", "pr"])
def test_an_api_failure_fails_the_step_without_merging(tmp_path, step, failing):
    bodies = {"branch": PROTECTED, "rules": [], "pr": {"autoMergeRequest": None}}
    bodies[failing] = "FAIL"
    res, calls = _run(tmp_path, step, **bodies)
    assert res.returncode != 0
    assert _merges(calls) == []


def test_merge_failure_fails_the_step(tmp_path, step):
    res, calls = _run(
        tmp_path,
        step,
        branch=PROTECTED,
        rules=[],
        pr={"autoMergeRequest": None},
        merge_exit=1,
    )
    assert len(_merges(calls)) == 1
    assert res.returncode != 0
