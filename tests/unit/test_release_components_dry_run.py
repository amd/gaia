# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A dry run of release_components.yml must publish nothing.

The workflow runs from a feature branch with no environment and no approval
when dry_run is on, so the only thing standing between a dry run and a live
upload is each step's ``dry_run == 'false'`` guard. This reads the workflow and
fails if any step that touches a secret, uploads to a release or triggers
another workflow loses that guard, and if the publish job's environment stops
resolving to the approval-gated one on a real run.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release_components.yml"
GUARD = "needs.version.outputs.dry_run == 'false'"
PUBLISH_ENVIRONMENT = "agent-publish"

# Anything a step can do that leaves the runner.
_SIDE_EFFECTS = (
    re.compile(r"\$\{\{\s*secrets\."),
    re.compile(r"\bgh\s+release\s+upload\b"),
    re.compile(r"\bgh\s+workflow\s+run\b"),
    re.compile(r"\bwrangler\s+deploy\b"),
    re.compile(r"publish_to_r2\.py"),
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _text(value) -> str:
    if isinstance(value, dict):
        return "\n".join(_text(v) for v in value.values())
    return "" if value is None else str(value)


def _side_effect(step: dict) -> str | None:
    text = "\n".join(_text(step.get(k)) for k in ("run", "env", "with", "uses"))
    for pattern in _SIDE_EFFECTS:
        if pattern.search(text):
            return pattern.pattern
    return None


def unguarded_steps(workflow: dict) -> list[str]:
    """Every side-effecting step whose step and job conditions both lack the
    dry-run guard, as ``job/step: reason``."""
    found = []
    for job_id, job in workflow["jobs"].items():
        if GUARD in str(job.get("if", "")):
            continue
        for i, step in enumerate(job.get("steps", [])):
            reason = _side_effect(step)
            if reason and GUARD not in str(step.get("if", "")):
                found.append(f"{job_id}/{step.get('name', f'step {i}')}: {reason}")
    return found


def resolve_environment(job: dict, dry_run: str) -> str:
    """Evaluate the job's environment the way Actions would for one dry_run.

    Only the operators the expression uses are translated; anything else
    raises, so a rewrite into a shape this cannot read fails the test instead
    of passing it.
    """
    env = job.get("environment", "")
    if isinstance(env, dict):
        env = env.get("name", "")
    m = re.fullmatch(r"\$\{\{(.*)\}\}", str(env).strip())
    if not m:
        return str(env)
    expr = m.group(1).replace("needs.version.outputs.dry_run", repr(dry_run))
    if not re.fullmatch(r"[\s'a-z\-=&|()]*", expr):
        raise AssertionError(f"cannot evaluate environment expression: {env}")
    expr = expr.replace("&&", " and ").replace("||", " or ")
    return eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - vetted above


def test_the_guard_scan_still_finds_the_steps_it_guards(workflow):
    """A scan that matched nothing would pass vacuously."""
    hits = [
        f"{job_id}/{step.get('name')}"
        for job_id, job in workflow["jobs"].items()
        for step in job.get("steps", [])
        if _side_effect(step)
    ]
    assert any(h.startswith("terminal-hub/") for h in hits), hits
    assert any(h.startswith("github-release-installers/") for h in hits), hits


def test_every_side_effecting_step_is_off_on_a_dry_run(workflow):
    assert unguarded_steps(workflow) == []


def test_removing_a_guard_is_caught(workflow):
    for job_id, step_name in (
        ("terminal-hub", "Publish to the Agent Hub (POST /publish)"),
        ("github-release-installers", "Attach the installers and their checksums"),
        ("github-release-installers", "Redeploy the website to show them"),
    ):
        broken = copy.deepcopy(workflow)
        step = next(
            s for s in broken["jobs"][job_id]["steps"] if s.get("name") == step_name
        )
        step["if"] = str(step.get("if", "")).replace(GUARD, "true")
        assert any(
            u.startswith(f"{job_id}/{step_name}") for u in unguarded_steps(broken)
        ), f"stripping the guard from {job_id}/{step_name} went unnoticed"


@pytest.mark.parametrize("job_id", ["terminal-hub", "github-release-installers"])
def test_publish_jobs_never_start_unguarded_on_a_dry_run(workflow, job_id):
    """The job condition may not force these jobs to publish on a dry run."""
    assert "dry_run == 'true'" not in str(workflow["jobs"][job_id].get("if", ""))


def test_a_real_run_publishes_under_the_approval_gated_environment(workflow):
    job = workflow["jobs"]["terminal-hub"]
    assert resolve_environment(job, "false") == PUBLISH_ENVIRONMENT
    # A dry run takes no environment: no approval prompt, no ref allowlist.
    assert resolve_environment(job, "true") == ""


def test_the_environment_check_reads_a_weakened_expression(workflow):
    broken = copy.deepcopy(workflow["jobs"]["terminal-hub"])
    broken["environment"] = (
        "${{ needs.version.outputs.dry_run == 'true' && 'agent-publish' || '' }}"
    )
    assert resolve_environment(broken, "false") != PUBLISH_ENVIRONMENT


def test_a_dispatch_never_waits_hours_for_a_missing_release(workflow):
    steps = workflow["jobs"]["github-release-installers"]["steps"]
    find = next(s for s in steps if s.get("name") == "Find the GitHub release")
    assert 'if [ "${EVENT}" = "workflow_dispatch" ]' in find["run"]
    dispatch_branch = find["run"].split('if [ "${EVENT}" = "workflow_dispatch" ]')[1]
    dispatch_branch = dispatch_branch.split("exit 0", 1)[0]
    assert "sleep" not in dispatch_branch
    attach = next(
        s for s in steps if s.get("name") == "Attach the installers and their checksums"
    )
    assert "steps.release.outputs.exists == 'true'" in attach["if"]
