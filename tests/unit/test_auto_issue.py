# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
`.github/actions/auto-issue` is what keeps a workflow alert to one issue and
closes it when the alert clears. Before it, alert workflows matched "their"
issue by fuzzy title search and never closed it, so #3920 and #4462 stayed open
long after the condition cleared and three mutually exclusive "Release check:"
states sat open at once.

Every test drives ``main()`` with a fake ``gh`` that records argv, and asserts
the argv shape against the real gh CLI flags — a stub that only proves "we
called something" is how a broken alert path stays green.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
ACTION_DIR = REPO / ".github" / "actions" / "auto-issue"
WORKFLOWS = REPO / ".github" / "workflows"

_spec = importlib.util.spec_from_file_location(
    "auto_issue", ACTION_DIR / "auto_issue.py"
)
auto_issue = importlib.util.module_from_spec(_spec)
sys.modules["auto_issue"] = auto_issue
_spec.loader.exec_module(auto_issue)

R = "amd/gaia"


class FakeGh:
    def __init__(self, issues=None, list_error=None, list_output=None):
        self.issues = issues or []
        self.list_error = list_error
        self.list_output = list_output
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ["issue", "list"]:
            assert argv[argv.index("--author") + 1] == "github-actions[bot]"
            assert argv[argv.index("--state") + 1] == "open"
            assert argv[argv.index("--repo") + 1] == R
            if self.list_error:
                raise self.list_error
            if self.list_output is not None:
                return self.list_output
            return json.dumps(self.issues)
        if argv[:2] == ["issue", "create"]:
            return "https://github.com/amd/gaia/issues/9999\n"
        return ""

    def writes(self):
        return [c for c in self.calls if c[:2] != ["issue", "list"]]


def _issue(number, title, key=None, body="text"):
    if key:
        body = f"{body}\n\n<!-- gaia-auto-issue: {key} -->\n"
    return {"number": number, "title": title, "body": body}


def _report(gh, tmp_path, *extra, key="wf/failure", title="WF failed", body="boom"):
    f = tmp_path / "body.md"
    f.write_text(body, encoding="utf-8")
    argv = [
        "report",
        f"--repo={R}",
        f"--key={key}",
        f"--title={title}",
        f"--body-file={f}",
    ]
    return auto_issue.main([*argv, *extra], run=gh)


def _resolve(gh, *extra):
    return auto_issue.main(
        ["resolve", "--repo", R, "--note", "Recovered in run X", *extra], run=gh
    )


# --- report -------------------------------------------------------------------


def test_report_files_a_marked_issue_when_none_is_open(tmp_path):
    gh = FakeGh()
    assert (
        _report(gh, tmp_path, "--label", "bug\ndevops", "--assignee", "kovtcharov-amd")
        == 0
    )
    (create,) = gh.writes()
    assert create[:2] == ["issue", "create"]
    assert create[create.index("--title") + 1] == "WF failed"
    body = create[create.index("--body") + 1]
    assert body.startswith("boom")
    assert body.rstrip().endswith("<!-- gaia-auto-issue: wf/failure -->")
    labels = [create[i + 1] for i, a in enumerate(create) if a == "--label"]
    assert labels == ["bug", "devops"]
    assert create[create.index("--assignee") + 1] == "kovtcharov-amd"
    assert create[create.index("--repo") + 1] == R


def test_rerun_comments_on_the_marked_issue_instead_of_filing(tmp_path):
    gh = FakeGh([_issue(12, "anything", key="wf/failure")])
    assert _report(gh, tmp_path, title="a reworded title") == 0
    (comment,) = gh.writes()
    assert comment[:3] == ["issue", "comment", "12"]
    assert comment[comment.index("--body") + 1] == "boom"


def test_oldest_of_several_matches_is_the_one_updated(tmp_path):
    gh = FakeGh([_issue(40, "x", key="wf/failure"), _issue(7, "y", key="wf/failure")])
    _report(gh, tmp_path)
    assert gh.writes()[0][:3] == ["issue", "comment", "7"]


def test_a_different_key_is_never_matched_even_with_the_same_title(tmp_path):
    gh = FakeGh([_issue(5, "WF failed", key="other/failure")])
    _report(gh, tmp_path)
    assert gh.writes()[0][:2] == ["issue", "create"]


def test_key_prefix_lookalike_is_not_a_match(tmp_path):
    gh = FakeGh([_issue(5, "t", key="wf/failure-extra")])
    _report(gh, tmp_path)
    assert gh.writes()[0][:2] == ["issue", "create"]


def test_unmarked_legacy_issue_is_adopted_by_exact_title_or_prefix(tmp_path):
    gh = FakeGh([_issue(3, "WF failed")])
    _report(gh, tmp_path)
    assert gh.writes()[0][:3] == ["issue", "comment", "3"]

    gh = FakeGh(
        [_issue(4, "⚠️ Nightly patch-release check failed (scope undetermined)")]
    )
    _report(gh, tmp_path, "--legacy-title-prefix", "⚠️ Nightly patch-release")
    assert gh.writes()[0][:3] == ["issue", "comment", "4"]


def test_edit_mode_rewrites_the_body_and_keeps_exactly_one_marker(tmp_path):
    gh = FakeGh([_issue(9, "Release check: x", key="release-check/ci-not-green")])
    _report(
        gh,
        tmp_path,
        "--update",
        "edit",
        key="release-check/ci-not-green",
        body="new state",
    )
    (edit,) = gh.writes()
    assert edit[:3] == ["issue", "edit", "9"]
    body = edit[edit.index("--body") + 1]
    assert body.startswith("new state")
    assert body.count("gaia-auto-issue") == 1


def test_edit_mode_stamps_a_marker_on_an_adopted_legacy_issue(tmp_path):
    gh = FakeGh([_issue(9, "Release check: x")])
    _report(
        gh,
        tmp_path,
        "--update",
        "edit",
        key="release-check/a",
        title="Release check: x",
    )
    body = gh.writes()[0][gh.writes()[0].index("--body") + 1]
    assert "<!-- gaia-auto-issue: release-check/a -->" in body


def test_an_issue_marked_for_another_key_is_not_matched_by_its_title(tmp_path):
    gh = FakeGh([_issue(2, "WF failed", key="unrelated/key")])
    _report(gh, tmp_path)
    assert gh.writes()[0][:2] == ["issue", "create"]


def test_failed_lookup_still_files_the_alert_and_exits_non_zero(tmp_path, capsys):
    err = subprocess.CalledProcessError(1, ["gh"], "", "HTTP 502")
    gh = FakeGh(list_error=err)
    assert _report(gh, tmp_path) == 1
    assert gh.writes()[0][:2] == ["issue", "create"]
    assert "::error::" in capsys.readouterr().out


@pytest.mark.parametrize("output", ["not json", '{"a": 1}'])
def test_garbled_lookup_output_is_a_lookup_failure(tmp_path, output):
    gh = FakeGh(list_output=output)
    assert _report(gh, tmp_path) == 1
    assert gh.writes()[0][:2] == ["issue", "create"]


def test_a_lookup_at_the_limit_counts_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_issue, "LOOKUP_LIMIT", 2)
    gh = FakeGh([_issue(1, "a"), _issue(2, "b")])
    assert _report(gh, tmp_path) == 1


@pytest.mark.parametrize("key", ["", "has space", "a\nb", "-leading-dash"])
def test_bad_keys_are_rejected_before_anything_is_written(tmp_path, key):
    gh = FakeGh()
    with pytest.raises(ValueError):
        _report(gh, tmp_path, key=key)
    assert gh.writes() == []


def test_empty_title_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        _report(FakeGh(), tmp_path, title="  ")


# --- resolve ------------------------------------------------------------------


def test_resolve_closes_the_matching_issue_as_completed_with_the_note():
    gh = FakeGh([_issue(8, "t", key="wf/failure"), _issue(9, "t", key="other")])
    assert _resolve(gh, "--key", "wf/failure") == 0
    (close,) = gh.writes()
    assert close[:3] == ["issue", "close", "8"]
    assert close[close.index("--reason") + 1] == "completed"
    assert close[close.index("--comment") + 1] == "Recovered in run X"
    assert close[close.index("--repo") + 1] == R


def test_resolve_with_nothing_open_writes_nothing():
    gh = FakeGh([_issue(9, "t", key="other")])
    assert _resolve(gh, "--key", "wf/failure") == 0
    assert gh.writes() == []


def test_a_passing_tag_closes_every_earlier_tags_issue_marked_or_legacy():
    gh = FakeGh(
        [
            _issue(1, "Flagship eval did not pass on v0.25.0-rc15"),
            _issue(
                2, "Flagship eval did not pass on v0.25.0", key="eval-flagship/v0.25.0"
            ),
            _issue(3, "Nightly flagship eval did not pass"),
            _issue(4, "Agent release eval did not pass on x", key="eval-agent-gaia/x"),
        ]
    )
    _resolve(
        gh,
        "--key-prefix",
        "eval-flagship/",
        "--legacy-title-prefix",
        "Flagship eval did not pass on ",
    )
    assert sorted(c[2] for c in gh.writes()) == ["1", "2"]


def test_release_check_closes_the_other_conditions_but_never_the_current_one():
    gh = FakeGh(
        [
            _issue(4175, "Release check: minor/major-shaped changes pending on main"),
            _issue(
                4355,
                "Release check: main's CI is not green, nightly release drafting is paused",
            ),
            _issue(
                4888,
                "Release check: version.py on main doesn't match any expected version",
            ),
            _issue(5000, "Release check: x", key="release-check/hotfix-tag"),
        ]
    )
    current_title = (
        "Release check: main's CI is not green, nightly release drafting is paused"
    )
    _resolve(
        gh,
        "--key-prefix",
        "release-check/",
        "--except-key",
        "release-check/ci-not-green",
        "--legacy-title-prefix",
        "Release check: ",
        "--except-title",
        current_title,
    )
    assert sorted(c[2] for c in gh.writes()) == ["4175", "4888", "5000"]


def test_release_check_with_no_condition_closes_all_of_them():
    gh = FakeGh([_issue(1, "Release check: a"), _issue(2, "z", key="release-check/b")])
    _resolve(
        gh, "--key-prefix", "release-check/", "--legacy-title-prefix", "Release check: "
    )
    assert sorted(c[2] for c in gh.writes()) == ["1", "2"]


def test_failed_lookup_closes_nothing_and_exits_non_zero(capsys):
    gh = FakeGh(list_error=OSError("gh not found"))
    assert _resolve(gh, "--key", "wf/failure") == 1
    assert gh.writes() == []
    assert "::error::" in capsys.readouterr().out


def test_resolve_with_no_selector_is_refused():
    with pytest.raises(SystemExit):
        _resolve(FakeGh())


def test_lookup_only_ever_reads_bot_filed_issues():
    # FakeGh asserts --author github-actions[bot] on every list call; a human issue
    # quoting a marker is therefore never even fetched.
    gh = FakeGh([_issue(1, "t", key="wf/failure")])
    _resolve(gh, "--key", "wf/failure")
    assert any(c[:2] == ["issue", "list"] for c in gh.calls)


# --- wiring -------------------------------------------------------------------


def _action_steps():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (wf.get("jobs") or {}).items():
            for i, step in enumerate(job.get("steps") or []):
                if str(step.get("uses", "")).endswith(".github/actions/auto-issue"):
                    yield path.name, job_id, i, job, step


def test_every_alert_workflow_uses_the_action_and_hand_rolls_no_issue_filing():
    users = {name for name, *_ in _action_steps()}
    assert users >= {
        "claude-auth-canary.yml",
        "claude.yml",
        "eval_flagship.yml",
        "lemonade-candidate-smoke.yml",
        "lemonade-version-bump.yml",
        "nightly-patch-release.yml",
        "release_agent_gaia.yml",
    }
    for name in users:
        wf = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
        for job_id, job in wf["jobs"].items():
            for step in job.get("steps") or []:
                run = step.get("run", "")
                assert (
                    "gh issue create" not in run
                ), f"{name}:{job_id} files an issue by hand"
                assert (
                    "gh issue close" not in run
                ), f"{name}:{job_id} closes an issue by hand"


def test_every_report_has_a_matching_resolve_in_the_same_workflow():
    by_wf: dict[str, dict[str, list]] = {}
    for name, _job, _i, _jobdef, step in _action_steps():
        w = step.get("with", {})
        by_wf.setdefault(name, {"report": [], "resolve": []})[w["mode"]].append(w)
    for name, modes in by_wf.items():
        assert modes["report"], name
        assert modes["resolve"], f"{name} files an issue but never closes it"


def test_jobs_using_the_action_check_it_out_and_can_write_issues():
    for name, job_id, idx, job, step in _action_steps():
        steps = job["steps"]
        checkouts = [
            s
            for s in steps[:idx]
            if str(s.get("uses", "")).startswith("actions/checkout@")
        ]
        assert checkouts, f"{name}:{job_id} uses the action without checking it out"
        if step["uses"].startswith("./.auto-issue/"):
            assert any(
                s.get("with", {}).get("path") == ".auto-issue" for s in checkouts
            )
        perms = job.get("permissions")
        if perms is not None:
            assert perms.get("issues") == "write", f"{name}:{job_id}"
        else:
            wf = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
            top = wf.get("permissions", {})
            assert (
                top == "write-all" or top.get("issues") == "write"
            ), f"{name}:{job_id}"


def test_action_inputs_cover_everything_the_workflows_pass():
    action = yaml.safe_load((ACTION_DIR / "action.yml").read_text(encoding="utf-8"))
    known = set(action["inputs"])
    for name, job_id, _i, _job, step in _action_steps():
        unknown = set(step.get("with", {})) - known
        assert not unknown, f"{name}:{job_id} passes unknown inputs {unknown}"
        assert step["with"]["mode"] in ("report", "resolve")
