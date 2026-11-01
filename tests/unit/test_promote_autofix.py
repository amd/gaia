# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A new auto-fixable audit issue reaches auto-fix, and the wiring can actually fire.

The label is not enough: GITHUB_TOKEN events start no workflows, so these tests pin
the dispatch and the permission and trigger it depends on, not just that gh was called.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
AUDITS = ("claude-nightly-audit.yml", "claude-weekly-doc-walkthrough.yml")


@pytest.fixture(scope="module")
def promote():
    path = ROOT / "scripts" / "audit" / "promote_autofix.py"
    spec = importlib.util.spec_from_file_location("promote_autofix", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def issue(number, body="**Auto-fixable:** yes", login="app/github-actions", labels=()):
    return {
        "number": number,
        "title": f"issue {number}",
        "body": body,
        "author": {"login": login},
        "labels": [{"name": name} for name in labels],
    }


def workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


@pytest.fixture
def gh(promote, monkeypatch):
    """Record gh calls; ``fail_on`` makes matching calls fail as the real CLI would."""

    class Fake:
        calls: list = []
        issues: list = []
        fail_on: str = ""

        def __call__(self, *args):
            self.calls.append(args)
            if self.fail_on and self.fail_on in " ".join(args):
                raise RuntimeError("HTTP 403")
            if args[:2] == ("issue", "list"):
                return json.dumps(self.issues)
            return ""

    fake = Fake()
    fake.calls = []
    monkeypatch.setattr(promote, "_gh", fake)
    return fake


def test_select_takes_only_new_bot_filed_auto_fixable_issues(promote):
    issues = [
        issue(9, body="**Auto-fixable:** yes"),  # filed before this run
        issue(11, body="**Auto-fixable:** no — needs a human"),
        issue(12, login="some-human"),
        issue(13, labels=["bug"]),
        issue(14),
    ]
    assert [i["number"] for i in promote.select(issues, watermark=10)] == [14]


def test_select_matches_the_line_the_audits_write(promote):
    for name in AUDITS:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if "**Auto-fixable:** yes" in ln)
        assert promote.select([issue(5, body=line.strip())], watermark=0)


def test_red_findings_go_first(promote):
    issues = [
        issue(5, body="**Severity:** 🟠 medium\n**Auto-fixable:** yes"),
        issue(6, body="**Severity:** 🔴 high\n**Auto-fixable:** yes"),
    ]
    assert [i["number"] for i in promote.select(issues, 0)] == [6, 5]


def test_each_issue_is_labelled_and_dispatched(promote, gh, tmp_path):
    gh.issues = [issue(20)]
    summary = tmp_path / "summary.md"
    code = promote.main(
        ["--repo", "amd/gaia", "--watermark", "10", "--summary", str(summary)]
    )
    assert code == 0
    assert (
        "issue",
        "edit",
        "20",
        "--repo",
        "amd/gaia",
        "--add-label",
        "bug",
    ) in gh.calls
    dispatch = next(c for c in gh.calls if c[:2] == ("workflow", "run"))
    assert dispatch[2] == "claude.yml"
    assert "--ref" in dispatch and "main" in dispatch
    assert "issue_number=20" in dispatch
    assert "#20" in summary.read_text(encoding="utf-8")


def test_the_cap_leaves_the_rest_unlabelled_and_names_them(promote, gh, tmp_path):
    gh.issues = [issue(n) for n in range(20, 24)]
    summary = tmp_path / "summary.md"
    code = promote.main(
        ["--repo", "r/r", "--watermark", "1", "--max", "2", "--summary", str(summary)]
    )
    assert code == 0
    assert sum(c[:2] == ("workflow", "run") for c in gh.calls) == 2
    assert not any(c[:3] == ("issue", "edit", "23") for c in gh.calls)
    text = summary.read_text(encoding="utf-8")
    assert "#22" in text and "#23" in text and "cap" in text


def test_a_failed_dispatch_fails_the_step_but_tries_the_rest(promote, gh, capsys):
    gh.issues = [issue(30), issue(31)]
    gh.fail_on = "issue_number=30"
    assert promote.main(["--repo", "r/r", "--watermark", "1"]) == 1
    assert "issue_number=31" in {a for c in gh.calls for a in c}
    assert "#30" in capsys.readouterr().err
    assert not any(c[:3] == ("issue", "edit", "30") for c in gh.calls)


def test_a_failed_label_warns_because_the_fix_already_started(promote, gh, capsys):
    gh.issues = [issue(30)]
    gh.fail_on = "--add-label"
    assert promote.main(["--repo", "r/r", "--watermark", "1"]) == 0
    assert "has no `bug` label" in capsys.readouterr().err


def test_a_stalled_gh_call_is_a_normal_failure(promote, monkeypatch):
    def stall(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(promote.subprocess, "run", stall)
    with pytest.raises(RuntimeError, match="timed out"):
        promote._gh("workflow", "run")


def test_no_candidates_is_a_clean_pass(promote, gh, tmp_path):
    gh.issues = [issue(5)]
    summary = tmp_path / "summary.md"
    assert (
        promote.main(["--repo", "r/r", "--watermark", "9", "--summary", str(summary)])
        == 0
    )
    assert not any(c[:2] == ("workflow", "run") for c in gh.calls)
    assert "No new auto-fixable" in summary.read_text(encoding="utf-8")


def test_a_dispatch_can_start_auto_fix_and_nothing_else():
    wf = workflow("claude.yml")
    triggers = wf[True]  # PyYAML reads the `on:` key as a boolean
    assert triggers["workflow_dispatch"]["inputs"]["issue_number"]["required"] is True

    condition = wf["jobs"]["auto-fix"]["if"]
    assert "github.event_name == 'workflow_dispatch'" in condition
    for name, job in wf["jobs"].items():
        if name in ("auto-fix", "request-account-switch"):
            continue
        assert "github.event_name ==" in job["if"], name
        assert "workflow_dispatch" not in job["if"], name


def test_auto_fix_accepts_the_bot_that_dispatches_it():
    steps = workflow("claude.yml")["jobs"]["auto-fix"]["steps"]
    fix = next(s for s in steps if s.get("id") == "claude")
    assert "github-actions" in fix["with"]["allowed_bots"]


def test_auto_fix_reads_the_dispatched_issue_number():
    job = workflow("claude.yml")["jobs"]["auto-fix"]
    assert "inputs.issue_number" in job["env"]["ISSUE_NUMBER"]
    assert "inputs.issue_number" in job["concurrency"]["group"]
    assert "github.event.issue.number" not in json.dumps(job["steps"])


@pytest.mark.parametrize("name", AUDITS)
def test_each_audit_can_dispatch_and_runs_the_step(name):
    job = workflow(name)["jobs"]["synthesize"]
    assert job["permissions"]["actions"] == "write"
    step = next(
        s for s in job["steps"] if "promote_autofix.py" in str(s.get("run", ""))
    )
    assert "always()" in step["if"]
    assert "dry_run != 'true'" in step["if"]
    watermark = next(s for s in job["steps"] if s.get("id") == "watermark")
    assert "if" not in watermark, "a real run needs the watermark, not only a dry run"
    names = [s.get("name", "") for s in job["steps"]]
    assert names.index(step["name"]) > names.index(watermark["name"])
