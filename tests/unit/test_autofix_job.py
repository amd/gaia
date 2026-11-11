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


FAKE_GH = """#!/bin/sh
[ -n "$FAIL_GH" ] && { echo "HTTP 502" >&2; exit 1; }
case "$1" in
  pr) printf "%s" "$FAKE_PR" ;;
  issue) printf "%s" "$FAKE_LINKED" ;;
esac
"""


@pytest.fixture
def run_step(job, tmp_path):
    """Run one of the job's bash steps with a stand-in gh and an instant sleep."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("gh", FAKE_GH), ("sleep", "#!/bin/sh\nexit 0\n")):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    outcome = tmp_path / "outcome.md"
    summary = tmp_path / "summary.md"
    output = tmp_path / "output"

    def run(name, content=None, open_pr="", linked="", fail_gh=False):
        if content is not None:
            outcome.write_text(content, encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_PR": open_pr,
            "FAKE_LINKED": linked,
            "FAIL_GH": "1" if fail_gh else "",
            "ISSUE_NUMBER": "42",
            "REPO": "amd/gaia",
            "OUTCOME_FILE": str(outcome),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(output),
        }
        result = subprocess.run(
            ["bash", "-e", "-c", step(job, name)["run"]],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        written = summary.read_text(encoding="utf-8") if summary.exists() else ""
        outputs = output.read_text(encoding="utf-8") if output.exists() else ""
        return result, written, outputs

    return run


@pytest.fixture
def run_check(run_step):
    def run(content, open_pr="", linked="", fail_gh=False):
        result, summary, _ = run_step(
            "Check the auto-fix outcome", content, open_pr, linked, fail_gh
        )
        return result, summary

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


@needs_bash
def test_a_fix_pr_under_another_branch_counts(run_check):
    result, _ = run_check("Outcome: A\nFixed it.\n", open_pr="", linked="77")
    assert result.returncode == 0
    assert "#77" in result.stdout


@needs_bash
def test_a_failing_lookup_warns_without_failing_the_job(run_check):
    result, _ = run_check("Outcome: A\nFixed it.\n", fail_gh=True)
    assert result.returncode == 0
    assert "not verified" in result.stdout


@needs_bash
@pytest.mark.parametrize("open_pr, linked", [("12", ""), ("", "77")])
def test_an_open_fix_pr_skips_the_run(run_step, open_pr, linked):
    result, summary, outputs = run_step(
        "Skip if a fix PR is already open", open_pr=open_pr, linked=linked
    )
    assert result.returncode == 0
    assert f"pr={open_pr or linked}" in outputs
    assert "did not run" in summary


@needs_bash
def test_no_open_fix_pr_lets_the_run_start(run_step):
    result, _, outputs = run_step("Skip if a fix PR is already open")
    assert result.returncode == 0
    assert outputs.strip() == "pr="


@needs_bash
def test_a_failing_lookup_stops_the_run_before_it_can_push(run_step):
    result, _, _ = run_step("Skip if a fix PR is already open", fail_gh=True)
    assert result.returncode != 0


def test_a_stalled_fix_is_handed_to_a_maintainer(job):
    notify = step(job, "Notify on auto-fix failure")
    assert notify["if"] == "failure() || steps.outcome.outputs.outcome == 'FAILED'"
    assert step(job, "Check the auto-fix outcome")["id"] == "outcome"
    assert job["permissions"]["issues"] == "write"


@needs_bash
def test_the_outcome_is_published_for_the_notice(run_step):
    _, _, outputs = run_step("Check the auto-fix outcome", "Outcome: FAILED\nlint\n")
    assert "outcome=FAILED" in outputs


@pytest.fixture
def run_notify(job, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    (bin_dir / "gh").write_text(
        '#!/bin/sh\necho "$*" >> "$CALLS"\n'
        'case "$1 $2" in "label create"|"issue edit") exit "$LABEL_EXIT";; esac\n',
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)

    def run(exec_file, outcome, label_fails=False):
        env = {
            **os.environ,
            "LABEL_EXIT": "1" if label_fails else "0",
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "CALLS": str(calls),
            "ISSUE_NUMBER": "42",
            "REPO": "amd/gaia",
            "EXEC_FILE": exec_file,
            "OUTCOME": outcome,
        }
        result = subprocess.run(
            ["bash", "-e", "-c", step(job, "Notify on auto-fix failure")["run"]],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result, calls.read_text(encoding="utf-8") if calls.exists() else ""

    return run


@needs_bash
@pytest.mark.parametrize("outcome, comments", [("", True), ("FAILED", False)])
def test_a_stalled_fix_gets_needs_human(run_notify, outcome, comments):
    result, calls = run_notify("/tmp/exec.json", outcome)
    assert result.returncode == 0, result.stderr
    assert "issue edit 42 --repo amd/gaia --add-label needs-human" in calls
    assert ("issue comment 42" in calls) is comments


@needs_bash
@pytest.mark.parametrize("outcome, comments", [("", True), ("FAILED", False)])
def test_a_failed_label_still_comments_and_fails_loudly(run_notify, outcome, comments):
    result, calls = run_notify("/tmp/exec.json", outcome, label_fails=True)
    assert result.returncode != 0
    assert "::error title=needs-human label failed::" in result.stdout
    assert ("issue comment 42" in calls) is comments
    if comments:
        assert "could not be added" in calls


@needs_bash
def test_an_abort_before_claude_ran_stays_silent(run_notify):
    result, calls = run_notify("", "")
    assert result.returncode == 0
    assert calls == ""


def test_a_second_start_queues_instead_of_cancelling(job):
    assert job["concurrency"]["cancel-in-progress"] is False
    # The template's own `labeled` event starts the run; `opened` would be a second one.
    assert "'opened'" not in job["if"]
    assert "github.event.label.name == 'bug'" in job["if"]
