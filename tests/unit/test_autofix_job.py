# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The auto-fix job either opens a PR or says why not; it never ends silently.

Runs that ended green with no PR, no comment and no record were the failure seen on
main, so these tests run the outcome check itself against a stand-in ``gh``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CLAUDE_YML = ROOT / ".github" / "workflows" / "claude.yml"


@pytest.fixture(scope="module")
def job():
    return yaml.safe_load(CLAUDE_YML.read_text(encoding="utf-8"))["jobs"]["auto-fix"]


def step(job, name):
    return next(s for s in job["steps"] if s.get("name") == name)


def prompt(job):
    return next(s for s in job["steps"] if s.get("id") == "claude")["with"]["prompt"]


def test_an_open_fix_pr_stops_every_step_that_could_push(job):
    steps = job["steps"]
    assert steps[0].get("id") == "existing"
    assert "--state open" in steps[0]["run"]
    assert "autofix/issue-$ISSUE_NUMBER" in steps[0]["run"]
    for s in steps[1:]:
        if s["name"] in ("Check the auto-fix outcome", "Notify on auto-fix failure"):
            continue
        assert s.get("if") == "steps.existing.outputs.pr == ''", s["name"]


def test_the_outcome_is_checked_before_the_failure_notice(job):
    names = [s["name"] for s in job["steps"]]
    assert names.index("Check the auto-fix outcome") < names.index(
        "Notify on auto-fix failure"
    )
    check = step(job, "Check the auto-fix outcome")
    assert check["if"] == "steps.claude.outcome == 'success'"
    assert check["env"]["OUTCOME_FILE"] in prompt(job)


def test_the_prompt_runs_no_command_that_writes_git_config(job):
    text = prompt(job)
    assert "git branch -D autofix" not in text
    assert "Run no git command that writes `.git/config`" in text


def test_issue_text_never_reaches_a_run_script(job):
    for s in job["steps"]:
        assert "github.event.issue.title" not in s.get("run", "")
        assert "github.event.issue.body" not in s.get("run", "")


needs_bash = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="runs the step's bash with a stand-in gh on PATH",
)


@pytest.fixture
def run_check(job, tmp_path):
    script = step(job, "Check the auto-fix outcome")["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text('#!/bin/sh\nprintf "%s" "$FAKE_PR"\n', encoding="utf-8")
    fake_gh.chmod(0o755)
    outcome = tmp_path / "outcome.md"
    summary = tmp_path / "summary.md"

    def run(content, open_pr=""):
        if content is not None:
            outcome.write_text(content, encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_PR": open_pr,
            "ISSUE_NUMBER": "42",
            "REPO": "amd/gaia",
            "OUTCOME_FILE": str(outcome),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        result = subprocess.run(
            ["bash", "-e", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        written = summary.read_text(encoding="utf-8") if summary.exists() else ""
        return result, written

    return run


@needs_bash
def test_no_outcome_file_fails(run_check):
    result, _ = run_check(None)
    assert result.returncode == 1
    assert "without an outcome" in result.stdout


@needs_bash
def test_a_decline_passes_and_says_why_in_the_summary(run_check):
    result, summary = run_check("Outcome: C\nA feature request, not a bug.\n")
    assert result.returncode == 0
    assert "A feature request, not a bug." in summary


@needs_bash
def test_a_claimed_pr_that_does_not_exist_fails(run_check):
    result, _ = run_check("Outcome: A\nFixed it.\n", open_pr="")
    assert result.returncode == 1
    assert "PR missing" in result.stdout


@needs_bash
def test_a_claimed_pr_that_exists_passes(run_check):
    result, _ = run_check("Outcome: A\nFixed it.\n", open_pr="123")
    assert result.returncode == 0
    assert "#123" in result.stdout


@needs_bash
def test_an_abandoned_fix_warns_without_a_second_comment(run_check):
    result, summary = run_check("Outcome: FAILED\ntests broke\n")
    assert result.returncode == 0
    assert "::warning" in result.stdout
    assert "tests broke" in summary


@needs_bash
@pytest.mark.parametrize("content", ["Outcome: maybe\n", "A\n", "Outcome: A B\n"])
def test_an_unreadable_outcome_fails(run_check, content):
    result, _ = run_check(content)
    assert result.returncode == 1
    assert "unreadable" in result.stdout


@needs_bash
def test_crlf_outcome_line_is_read(run_check):
    result, _ = run_check("Outcome: C\r\nduplicate\r\n")
    assert result.returncode == 0


@needs_bash
def test_model_text_never_reaches_stdout(run_check):
    result, summary = run_check("Outcome: C\n::error::injected\n")
    assert result.returncode == 0
    assert "injected" not in result.stdout
    assert "::error::injected" in summary


def test_a_bug_label_from_triage_reaches_auto_fix():
    handoff = yaml.safe_load(CLAUDE_YML.read_text(encoding="utf-8"))["jobs"][
        "triage-autofix-handoff"
    ]
    assert handoff["needs"] == "issue-triage"
    assert handoff["permissions"] == {"issues": "read", "actions": "write"}
    # The bug template's label already started auto-fix from the `opened` event.
    assert "!contains(github.event.issue.labels.*.name, 'bug')" in handoff["if"]
    assert "gh workflow run claude.yml" in handoff["steps"][0]["run"]


@pytest.fixture
def run_handoff(tmp_path):
    jobs = yaml.safe_load(CLAUDE_YML.read_text(encoding="utf-8"))["jobs"]
    script = jobs["triage-autofix-handoff"]["steps"][0]["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        '#!/bin/sh\necho "$*" >> "$CALLS"\n'
        'case "$1" in issue) printf "%s" "$FAKE_LABELS" ;; esac\n',
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    def run(labels):
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "CALLS": str(calls),
            "FAKE_LABELS": labels,
            "ISSUE_NUMBER": "42",
            "REPO": "amd/gaia",
            "REF": "main",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
        result = subprocess.run(
            ["bash", "-e", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result, calls.read_text(encoding="utf-8") if calls.exists() else ""

    return run


@needs_bash
def test_triage_bug_label_dispatches_auto_fix_for_that_issue(run_handoff):
    result, calls = run_handoff("p2,bug,tests")
    assert result.returncode == 0
    assert (
        "workflow run claude.yml --repo amd/gaia --ref main -f issue_number=42" in calls
    )


@needs_bash
@pytest.mark.parametrize("labels", ["", "enhancement", "bugfix,debug"])
def test_no_bug_label_dispatches_nothing(run_handoff, labels):
    result, calls = run_handoff(labels)
    assert result.returncode == 0
    assert "workflow run" not in calls
