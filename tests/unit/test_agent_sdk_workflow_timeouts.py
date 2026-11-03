# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The Agent SDK job on the self-hosted Windows pool must fail a hang in minutes,
at a named step, not after GitHub's anonymous 6-hour ceiling (issue #3339).

A hung run on PR #2930 held a scarce Strix Halo runner for 6h5m and died with
no indication of which step was stuck. Two invariants prevent that:

- every step carries its own ``timeout-minutes``, so the step that hangs is the
  one that fails and is named in the run summary;
- the job cap is at least the sum of the step caps, so no step can be cut off
  by the job-level timeout before its own cap fires.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test_agent_sdk.yml"
JOB = "test-agent-sdk-windows"
# GitHub's default (and hard ceiling) for a job is 360 minutes.
GITHUB_DEFAULT_MINUTES = 360


@pytest.fixture(scope="module")
def job() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert JOB in workflow["jobs"], f"{WORKFLOW.name} no longer has job {JOB!r}"
    return workflow["jobs"][JOB]


def _label(step: dict) -> str:
    return step.get("name") or step.get("uses") or "<unnamed step>"


def test_job_has_an_explicit_integer_timeout(job):
    cap = job.get("timeout-minutes")
    assert isinstance(cap, int) and not isinstance(cap, bool), (
        f"{JOB} needs a literal integer timeout-minutes; without it a hang runs "
        f"to GitHub's {GITHUB_DEFAULT_MINUTES}-minute ceiling (#3339). Got {cap!r}."
    )
    assert 0 < cap < GITHUB_DEFAULT_MINUTES


def test_every_step_has_its_own_timeout(job):
    missing = [_label(s) for s in job["steps"] if "timeout-minutes" not in s]
    assert not missing, (
        f"steps without timeout-minutes would hang to the job cap and fail "
        f"anonymously: {missing}"
    )
    for step in job["steps"]:
        cap = step["timeout-minutes"]
        assert isinstance(cap, int) and not isinstance(cap, bool) and cap > 0, (
            f"{_label(step)!r}: timeout-minutes must be a positive integer, got "
            f"{cap!r}"
        )


def test_job_cap_never_preempts_a_step_cap(job):
    """If the step caps summed past the job cap, a late step could hang into the
    job-level timeout first, which is the anonymous cancel this issue is about."""
    total = sum(step["timeout-minutes"] for step in job["steps"])
    assert (
        job["timeout-minutes"] >= total
    ), f"job timeout {job['timeout-minutes']} < sum of step timeouts {total}"


def test_job_does_not_tolerate_its_own_failure(job):
    assert job.get("continue-on-error", False) is False
