# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The account switch must survive the primary probe being out of quota.

Actions runs ``run:`` blocks with ``bash -eo pipefail``. The classifier exits 2
for a quota rejection, which is exactly when the switch is needed, so the
classify step has to record that code instead of dying on it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "claude-account-switch.yml"
)


def _classify_step_script() -> str:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    for job in jobs.values():
        for step in job["steps"]:
            if step.get("id") == "primary_verdict":
                return step["run"]
    raise AssertionError("no primary_verdict step in claude-account-switch.yml")


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="runs the step under a POSIX bash, as Actions does",
)
def test_a_quota_verdict_is_recorded_not_fatal(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "echo '::error::QUOTA EXHAUSTED (402/429-class): weekly limit · resets Oct 14 | WHAT TO DO: wait'\n"
        "exit 2\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_python.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(_classify_step_script(), encoding="utf-8", newline="\n")
    log = tmp_path / "probe.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CLAUDE_CONCLUSION": "failure",
        "CLAUDE_LOG": str(log),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    recorded = output.read_text(encoding="utf-8")
    assert "code=2" in recorded
    assert "QUOTA EXHAUSTED" in recorded


# --- when the switch runs ------------------------------------------------------

WORKFLOWS = WORKFLOW.parent


def _load(name):
    return yaml.load(
        (WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def test_the_switch_runs_on_claude_failures_not_a_clock():
    triggers = _load("claude-account-switch.yml")["on"]
    assert "schedule" not in triggers
    assert "workflow_dispatch" in triggers
    names = {
        _load(p.name).get("name")
        for p in WORKFLOWS.glob("*.yml")
        if isinstance(_load(p.name), dict)
    }
    watched = triggers["workflow_run"]["workflows"]
    # A name that matches no workflow never fires and nothing says so.
    assert set(watched) <= names, set(watched) - names
    # claude.yml completes ~1000 times a week; it dispatches the switch instead.
    assert "Claude AI Assistant" not in watched


def test_claude_yml_asks_for_a_switch_from_every_claude_run_caller():
    jobs = _load("claude.yml")["jobs"]
    callers = {
        job_id
        for job_id, job in jobs.items()
        if job.get("uses") == "./.github/workflows/claude-run.yml"
    }
    requester = jobs["request-account-switch"]
    assert set(requester["needs"]) == callers
    for job_id in callers:
        assert f"needs.{job_id}.outputs.quota_suspected == 'true'" in requester["if"]
    # The only job holding actions: write runs one fixed command, no checkout.
    assert requester["permissions"] == {"actions": "write"}
    assert all("uses" not in step for step in requester["steps"])
    assert "gh workflow run claude-account-switch.yml" in requester["steps"][0]["run"]


def test_claude_run_exposes_the_quota_verdict():
    workflow = _load("claude-run.yml")
    output = workflow["on"]["workflow_call"]["outputs"]["quota_suspected"]
    assert output["value"] == "${{ jobs.run.outputs.quota_suspected }}"
    assert (
        workflow["jobs"]["run"]["outputs"]["quota_suspected"]
        == "${{ steps.harness.outputs.quota_suspected }}"
    )


def _harness_script() -> str:
    for step in _load("claude-run.yml")["jobs"]["run"]["steps"]:
        if step.get("id") == "harness":
            return step["run"]
    raise AssertionError("no harness step in claude-run.yml")


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or not shutil.which("jq"),
    reason="runs the step under a POSIX bash with jq, as Actions does",
)
@pytest.mark.parametrize(
    "result, expected",
    [
        # Refused before any model work: budget or credential, the probe decides.
        ({"type": "result", "is_error": True, "modelUsage": {}}, "true"),
        (
            {
                "type": "result",
                "is_error": True,
                "modelUsage": {"claude-opus-5-5": {}},
                "total_cost_usd": 0.4,
                "result": "Claude AI usage limit reached",
            },
            "true",
        ),
        # A real run that went wrong is not a quota event.
        (
            {
                "type": "result",
                "is_error": True,
                "modelUsage": {"claude-opus-5-5": {}},
                "total_cost_usd": 0.4,
                "result": "tool failed",
            },
            "false",
        ),
        # A transient 429 mid-run is throttling, not an exhausted account.
        (
            {
                "type": "result",
                "is_error": True,
                "modelUsage": {"claude-opus-5-5": {}},
                "total_cost_usd": 0.4,
                "result": "API Error: 429 rate limit exceeded, retry later",
            },
            "false",
        ),
        (
            {
                "type": "result",
                "is_error": True,
                "modelUsage": {"claude-opus-5-5": {}},
                "total_cost_usd": 0.4,
                "result": "You've hit your session limit - limit resets 4pm",
            },
            "true",
        ),
        ({"type": "result", "is_error": False, "modelUsage": {"m": {}}}, "false"),
    ],
)
def test_harness_flags_only_refusals_and_budget_errors(tmp_path, result, expected):
    import json

    log = tmp_path / "exec.json"
    log.write_text(json.dumps(result) + "\n", encoding="utf-8")
    script = tmp_path / "step.sh"
    script.write_text(_harness_script(), encoding="utf-8", newline="\n")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {**os.environ, "EXEC_FILE": str(log), "GITHUB_OUTPUT": str(output)}
    run = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert f"quota_suspected={expected}" in output.read_text(encoding="utf-8")
