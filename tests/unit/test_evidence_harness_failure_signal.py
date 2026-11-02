# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
A broken evidence harness must be loud at merge time, not just a line of prose
inside a review comment (issue #3855).

Three PRs merged on "static review only" after their evidence stage failed: the
stage wrote a warning into ``evidence-bundle.md``, the review job still went
green, and nothing a merger scans showed the gap. A clean exit that wrote no
bundle produced no warning at all. These tests pin the fix in
``claude-run.yml``:

- one check step classifies the outcome, names the cause in the bundle and the
  job summary, and covers the clean-exit-no-bundle shape;
- a final step turns the job red when the harness failed;
- a bundle committed in the PR's own tree is dropped before the stage runs, so
  a PR (a fork's included) cannot supply its own "evidence".

The behaviour tests run the check step's real ``run:`` script under bash.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude-run.yml"
CHECK = "Check the evidence harness produced a bundle"
GATE = "Evidence harness failed — this PR's surface was not exercised"
DROP = "Drop any evidence-bundle.md the PR tree carries"
SENTINEL = "EVIDENCE HARNESS FAILED TO RUN"


@pytest.fixture(scope="module")
def job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["run"]


@pytest.fixture(scope="module")
def steps(job) -> list[dict]:
    return job["steps"]


def _idx(steps, name=None, step_id=None) -> int:
    for i, s in enumerate(steps):
        if (name and s.get("name") == name) or (step_id and s.get("id") == step_id):
            return i
    raise AssertionError(f"claude-run.yml has no step {name or step_id!r}")


# --- structure --------------------------------------------------------------


def test_gate_is_the_last_step_and_cannot_be_tolerated(steps):
    assert steps[-1].get("name") == GATE, "the red gate must run after the review posts"
    gate = steps[-1]
    assert gate.get("continue-on-error", False) is False
    assert "steps.evidence_check.outputs.failed == 'true'" in gate["if"]
    assert "!cancelled()" in gate["if"]
    assert "exit 1" in gate["run"] and "::error" in gate["run"]


def test_check_runs_after_every_evidence_step(steps):
    check = _idx(steps, CHECK)
    for step_id in (
        "evidence_setup",
        "evidence_install",
        "evidence",
        "evidence_quota",
        "evidence_secondary",
    ):
        assert _idx(steps, step_id=step_id) < check, step_id
    assert (
        _idx(steps, name="Run Claude (attempt 1)") > check
    ), "the review must read the sentinel, so the check runs before it"


def test_check_is_not_skipped_when_an_earlier_step_failed(steps):
    cond = steps[_idx(steps, CHECK)]["if"]
    assert "!cancelled()" in cond
    assert "steps.surface.outputs.testable == 'true'" in cond
    assert (
        "outcome == 'failure'" not in cond
    ), "gating on a failed outcome misses the clean-exit-no-bundle shape"


def test_stale_bundle_is_dropped_before_the_evidence_stage(steps):
    drop = _idx(steps, DROP)
    assert steps[drop]["run"].strip() == "rm -f evidence-bundle.md"
    assert drop < _idx(steps, step_id="evidence")
    assert drop > _idx(steps, name="Checkout PR head")
    assert "fork" not in steps[drop].get("if", ""), "forks need the drop most"


def test_evidence_steps_still_never_run_fork_code(steps):
    """The fork gate is the security boundary; this fix must not move it."""
    surface = steps[_idx(steps, step_id="surface")]
    assert "github.event.pull_request.head.repo.fork == false" in surface["if"]
    for step_id in ("evidence_setup", "evidence_install", "evidence"):
        assert (
            "steps.surface.outputs.testable == 'true'"
            in steps[_idx(steps, step_id=step_id)]["if"]
        )


# --- behaviour --------------------------------------------------------------


def _run(
    job, steps, tmp_path, *, log=None, bundle=None, has_secondary="true", **outcomes
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    work = tmp_path / "ws"
    temp = tmp_path / "rt"
    work.mkdir()
    temp.mkdir()
    if log is not None:
        (temp / "evidence-primary.log").write_text(log, encoding="utf-8")
    if bundle is not None:
        (work / "evidence-bundle.md").write_text(bundle, encoding="utf-8")
    out = tmp_path / "out"
    summary = tmp_path / "summary"
    env = {
        **os.environ,
        "RUNNER_TEMP": str(temp),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
        "HAS_SECONDARY": has_secondary,
        "QUOTA_RE": job["env"]["QUOTA_RE"],
        "CREDENTIAL_RE": job["env"]["CREDENTIAL_RE"],
        "RUN_URL": "https://github.com/amd/gaia/actions/runs/1",
        "SETUP_OUTCOME": outcomes.get("setup", "success"),
        "INSTALL_OUTCOME": outcomes.get("install", "success"),
        "PRIMARY_OUTCOME": outcomes.get("primary", "success"),
        "QUOTA": outcomes.get("quota", ""),
        "SECONDARY_OUTCOME": outcomes.get("secondary", "skipped"),
    }
    res = subprocess.run(
        [bash, "-c", steps[_idx(steps, CHECK)]["run"]],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    outputs = dict(
        line.split("=", 1)
        for line in (
            out.read_text(encoding="utf-8").splitlines() if out.exists() else []
        )
    )
    bundle_path = work / "evidence-bundle.md"
    return {
        "outputs": outputs,
        "bundle": (
            bundle_path.read_text(encoding="utf-8") if bundle_path.exists() else None
        ),
        "summary": summary.read_text(encoding="utf-8") if summary.exists() else "",
        "stdout": res.stdout,
    }


@pytest.mark.parametrize(
    "bundle",
    [
        "## Real-world evidence\n```\n$ gaia --version\n```\n",
        "N/A — no CLI/API/MCP surface changed in this PR.",
    ],
)
def test_a_written_bundle_passes_untouched(job, steps, tmp_path, bundle):
    r = _run(job, steps, tmp_path, log="ok", bundle=bundle)
    assert r["outputs"]["failed"] == "false"
    assert r["bundle"] == bundle
    assert r["summary"] == ""


def test_secondary_success_counts(job, steps, tmp_path):
    r = _run(
        job,
        steps,
        tmp_path,
        primary="failure",
        quota="true",
        secondary="success",
        bundle="evidence",
    )
    assert r["outputs"]["failed"] == "false"


CASES = {
    "install": (dict(install="failure", bundle="looks fine"), "failed to install"),
    "setup": (dict(setup="failure"), "failed to install"),
    "clean-exit-no-bundle": (dict(log="done"), "wrote no evidence-bundle.md"),
    "clean-exit-empty-bundle": (
        dict(log="done", bundle=""),
        "wrote no evidence-bundle.md",
    ),
    "quota-both": (
        dict(
            primary="failure",
            quota="true",
            secondary="failure",
            log="You've hit your limit",
        ),
        "both Claude accounts are out of quota",
    ),
    "quota-no-secondary": (
        dict(primary="failure", quota="true", has_secondary="false", log="usage limit"),
        "CLAUDE_CODE_OAUTH_TOKEN_SECONDARY is not set",
    ),
    "credential": (
        dict(
            primary="failure", quota="false", log="API Error: 401 authentication_error"
        ),
        "rejected the credential",
    ),
    "cli-never-started": (dict(primary="failure", quota="false"), "never started"),
    "generic": (
        dict(primary="failure", quota="false", log="Traceback: boom"),
        "errored",
    ),
}


@pytest.mark.parametrize("case", list(CASES))
def test_every_failure_shape_is_named_and_flagged(job, steps, tmp_path, case):
    kwargs, cause = CASES[case]
    r = _run(job, steps, tmp_path, **kwargs)
    assert r["outputs"]["failed"] == "true"
    assert cause in r["outputs"]["cause"]
    assert r["bundle"].startswith("⚠️ " + SENTINEL)
    assert f"Cause: {r['outputs']['cause']}." in r["bundle"]
    assert "Evidence harness failed" in r["summary"] and cause in r["summary"]


def test_install_failure_overrides_a_bundle_written_without_gaia(job, steps, tmp_path):
    r = _run(job, steps, tmp_path, install="failure", bundle="All good!")
    assert SENTINEL in r["bundle"] and "All good!" not in r["bundle"]


def test_the_log_is_matched_never_echoed(job, steps, tmp_path):
    secret = "sk-ant-oat01-SECRET authentication_error"
    r = _run(job, steps, tmp_path, primary="failure", quota="false", log=secret)
    for text in (r["bundle"], r["summary"], r["stdout"], r["outputs"]["cause"]):
        assert "sk-ant" not in text
