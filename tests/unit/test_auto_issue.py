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
import os
import re
import shutil
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
    def __init__(
        self, issues=None, list_error=None, list_output=None, write_error=None
    ):
        self.issues = issues or []
        self.write_error = write_error
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
        if self.write_error:
            raise self.write_error
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


@pytest.mark.parametrize(
    "open_issues,expected",
    [([], "9999"), ([_issue(12, "anything", key="wf/failure")], "12")],
    ids=["filed", "existing"],
)
def test_report_outputs_the_issue_number(tmp_path, monkeypatch, open_issues, expected):
    """A later step links the issue, so the number is a step output either way."""
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert _report(FakeGh(open_issues), tmp_path) == 0
    assert output.read_text(encoding="utf-8") == f"issue={expected}\n"


def test_an_issue_ref_without_a_number_is_refused():
    with pytest.raises(ValueError, match="no issue number"):
        auto_issue.issue_number("https://github.com/amd/gaia/issues/new")


def test_a_filed_issue_without_a_number_fails_the_step_cleanly(
    tmp_path, monkeypatch, capsys
):
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    class NoNumberGh(FakeGh):
        def __call__(self, argv):
            out = super().__call__(argv)
            return "filed\n" if list(argv)[:2] == ["issue", "create"] else out

    assert _report(NoNumberGh(), tmp_path) == 1
    assert "::error::no issue number in 'filed'" in capsys.readouterr().out
    assert output.read_text(encoding="utf-8") == ""


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
    assert edit[edit.index("--title") + 1] == "WF failed"


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


def _eval_issues():
    return [
        _issue(1, "Flagship eval did not pass on v0.25.0-rc15"),
        _issue(2, "Flagship eval did not pass on v0.25.0", key="eval-flagship/v0.25.0"),
        _issue(3, "Flagship eval did not pass on v0.25.2", key="eval-flagship/v0.25.2"),
        _issue(4, "Flagship eval did not pass on v0.26.0-rc1"),
        _issue(5, "Flagship eval did not pass on v0.24.3", key="eval-flagship/v0.24.3"),
        _issue(6, "Flagship eval did not pass on main"),
        _issue(7, "Nightly flagship eval did not pass"),
        _issue(8, "Agent release eval did not pass on x", key="eval-agent-gaia/x"),
    ]


def _resolve_eval(gh, version):
    return _resolve(
        gh,
        "--key-prefix",
        "eval-flagship/",
        "--legacy-title-prefix",
        "Flagship eval did not pass on ",
        f"--up-to-version={version}",
    )


def test_a_passing_tag_closes_only_itself_and_earlier_tags_on_its_line():
    gh = FakeGh(_eval_issues())
    _resolve_eval(gh, "v0.25.1")
    # Not v0.25.2 (newer), v0.26.0-rc1 or v0.24.3 (other lines), or an unparseable title.
    assert sorted(c[2] for c in gh.writes()) == ["1", "2"]


def test_a_final_release_supersedes_its_own_rcs_but_an_rc_not_the_final():
    gh = FakeGh(_eval_issues())
    _resolve_eval(gh, "v0.25.0-rc16")
    assert [c[2] for c in gh.writes()] == ["1"]

    gh = FakeGh(_eval_issues())
    _resolve_eval(gh, "v0.25.0")
    assert sorted(c[2] for c in gh.writes()) == ["1", "2"]


def test_a_green_rerun_of_the_same_tag_closes_that_tags_issue():
    gh = FakeGh(_eval_issues())
    _resolve_eval(gh, "v0.26.0-rc1")
    assert [c[2] for c in gh.writes()] == ["4"]


def test_agent_release_versions_are_read_from_the_package_tag():
    gh = FakeGh(
        [
            _issue(1, "t", key="eval-agent-gaia/agent-pkg-gaia-v0.3.0"),
            _issue(2, "t", key="eval-agent-gaia/agent-pkg-gaia-v0.3.2"),
            _issue(
                3, "Agent release eval did not pass on agent-pkg-gaia-v0.3.0 (dispatch)"
            ),
        ]
    )
    _resolve(
        gh,
        "--key-prefix",
        "eval-agent-gaia/",
        "--legacy-title-prefix",
        "Agent release eval did not pass on ",
        "--up-to-version=agent-pkg-gaia-v0.3.1",
    )
    assert sorted(c[2] for c in gh.writes()) == ["1", "3"]


def test_an_unparseable_ceiling_is_refused():
    gh = FakeGh(_eval_issues())
    with pytest.raises(SystemExit):
        _resolve_eval(gh, "main")
    assert gh.writes() == []


def test_release_check_closes_only_the_guards_that_ran_and_passed():
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
    # The run exited at guard 5 (CI red): guards 1b and 4 passed, 6 never ran.
    _resolve(
        gh,
        "--key=\nrelease-check/hotfix-tag\nrelease-check/staged-not-tagged"
        "\nrelease-check/version-mismatch",
        "--legacy-title=\nRelease check: latest tag is hotfix-shaped, next version is "
        "ambiguous\nRelease check: v0.25.1 already staged on main, not tagged"
        "\nRelease check: version.py on main doesn't match any expected version",
    )
    assert sorted(c[2] for c in gh.writes()) == ["4888", "5000"]


def test_a_failing_gh_write_reports_its_stderr_not_the_body(tmp_path, capsys):
    err = subprocess.CalledProcessError(
        1,
        ["gh", "issue", "comment", "12", "--body", "SECRET BODY"],
        "",
        "HTTP 403: nope",
    )
    gh = FakeGh([_issue(12, "t", key="wf/failure")], write_error=err)
    assert _report(gh, tmp_path, body="SECRET BODY") == 1
    out = capsys.readouterr().out
    assert "::error::gh issue comment failed (exit 1): HTTP 403: nope" in out
    assert "SECRET BODY" not in out


def test_a_failing_close_is_reported_and_exits_non_zero(capsys):
    err = subprocess.CalledProcessError(4, ["gh", "issue", "close", "8"], "", "gone")
    gh = FakeGh([_issue(8, "t", key="wf/failure")], write_error=err)
    assert _resolve(gh, "--key", "wf/failure") == 1
    assert "::error::gh issue close failed (exit 4): gone" in capsys.readouterr().out


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


def _nightly_scope_run():
    wf = yaml.safe_load(
        (WORKFLOWS / "nightly-patch-release.yml").read_text(encoding="utf-8")
    )
    steps = wf["jobs"]["check"]["steps"]
    return next(s for s in steps if s.get("id") == "scope")["run"], steps


def test_every_release_check_guard_records_both_outcomes():
    run, _ = _nightly_scope_run()
    raised = re.findall(r"record_condition ([a-z-]+)", run)
    passed = re.findall(r"guard_passed ([a-z-]+)", run)
    assert sorted(raised) == sorted(passed) and len(raised) == 5
    # Each guard is marked passed only after its own condition block.
    for key in raised:
        assert run.index(f"record_condition {key}") < run.index(f"guard_passed {key}")


def test_release_check_resolve_closes_only_passed_guards():
    _, steps = _nightly_scope_run()
    (resolve,) = [s for s in steps if s.get("with", {}).get("mode") == "resolve"]
    w = resolve["with"]
    assert "key-prefix" not in w and "legacy-title-prefix" not in w
    assert w["key"] == "${{ steps.scope.outputs.cleared_keys }}"
    assert w["legacy-title"] == "${{ steps.scope.outputs.cleared_titles }}"


def test_prefix_resolves_are_capped_at_the_passing_version():
    for name, _job, _i, _jobdef, step in _action_steps():
        w = step.get("with", {})
        if w.get("mode") == "resolve" and "key-prefix" in w:
            assert w.get(
                "up-to-version"
            ), f"{name} closes every version of {w['key-prefix']}"


# --- hotfix versions ----------------------------------------------------------


def test_a_hotfix_sorts_after_its_base_release():
    assert auto_issue.parse_version("v0.24.1.1") > auto_issue.parse_version("v0.24.1")
    assert auto_issue.parse_version("v0.24.1.1") < auto_issue.parse_version("v0.24.2")
    assert auto_issue.parse_version("v0.24.1.1-rc1") > auto_issue.parse_version(
        "v0.24.1"
    )
    assert auto_issue.parse_version("v0.24.1.2") > auto_issue.parse_version("v0.24.1.1")


def test_a_base_release_pass_leaves_a_later_hotfix_issue_open():
    issues = [
        _issue(1, "t", key="eval-flagship/v0.24.1.1"),
        _issue(2, "t", key="eval-flagship/v0.24.1"),
        _issue(3, "Flagship eval did not pass on v0.24.1.1"),
    ]
    gh = FakeGh(issues)
    _resolve_eval(gh, "v0.24.1")
    assert [c[2] for c in gh.writes()] == ["2"]

    gh = FakeGh(issues)
    _resolve_eval(gh, "v0.24.1.1")
    assert sorted(c[2] for c in gh.writes()) == ["1", "2", "3"]


# --- release evals: one issue per target, filed only for a measured regression --

#: (workflow, job, target) for each release eval that files `eval-failure`.
EVAL_ISSUE_JOBS = (
    ("eval_flagship.yml", "release-escalation", "flagship"),
    ("release_agent_gaia.yml", "eval-report", "agent-gaia"),
)


def _eval_job(workflow, job_id):
    wf = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    return wf["jobs"][job_id]


def _with_mode(job, mode):
    return next(
        s
        for s in job["steps"]
        if str(s.get("uses", "")).endswith(".github/actions/auto-issue")
        and s["with"]["mode"] == mode
    )


@pytest.mark.parametrize("workflow, job_id, target", EVAL_ISSUE_JOBS)
def test_an_eval_issue_is_filed_only_for_a_measured_regression(
    workflow, job_id, target
):
    job = _eval_job(workflow, job_id)
    notice = next(s for s in job["steps"] if s.get("id") == "notice")
    assert "util/eval_failure_notice.py" in notice["run"]
    assert f"--target {target}" in notice["run"]
    report = _with_mode(job, "report")
    assert report["if"] == "steps.notice.outputs.verdict == 'regressed'"
    w = report["with"]
    # One issue per target, edited in place: never one per tag.
    assert w["key"] == f"eval-regression/{target}"
    assert w["update"] == "edit"
    assert w["labels"] == "eval-failure"
    assert w["title"] == "${{ steps.notice.outputs.title }}"
    resolve = _with_mode(job, "resolve")
    assert resolve["if"] == "steps.notice.outputs.verdict == 'passed'"
    assert resolve["with"]["key"] == f"eval-regression/{target}"
    assert "up-to-version" not in resolve["with"]


@pytest.mark.parametrize("workflow, job_id, target", EVAL_ISSUE_JOBS)
def test_untrusted_eval_text_reaches_the_issue_only_through_a_file(
    workflow, job_id, target
):
    job = _eval_job(workflow, job_id)
    report = _with_mode(job, "report")["with"]
    assert "body" not in report
    assert report["body-file"] == "${{ github.workspace }}/notice/body.md"
    for step in job["steps"]:
        # Every value a script reads comes in through env, never an expression.
        assert "${{" not in step.get("run", ""), step.get("name")


@pytest.mark.parametrize("workflow, job_id, target", EVAL_ISSUE_JOBS)
def test_eval_issue_jobs_are_least_privilege_and_serialised(workflow, job_id, target):
    job = _eval_job(workflow, job_id)
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert job["concurrency"] == {
        "group": f"eval-failure-issue-{target}",
        "cancel-in-progress": False,
    }
    checkout = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["persist-credentials"] is False


def _close_on_pass(job, gh, version):
    """Run every resolve step a passing run takes, with a concrete tag."""
    resolves = [
        s
        for s in job["steps"]
        if s.get("with", {}).get("mode") == "resolve"
        and s.get("if") == "steps.notice.outputs.verdict == 'passed'"
    ]
    assert len(resolves) == 2
    for step in resolves:
        w = step["with"]
        args = []
        for name in ("key", "key-prefix", "legacy-title-prefix"):
            if name in w:
                args += [f"--{name}", w[name]]
        if "up-to-version" in w:
            args.append(f"--up-to-version={version}")
        _resolve(gh, *args)


def _all_eval_issues():
    return [
        _issue(1, "x", key="eval-regression/flagship"),
        _issue(2, "x", key="eval-flagship/smoke/v0.26.0-rc1"),
        _issue(3, "x", key="eval-flagship/full/v0.26.0-rc2"),
        _issue(4, "Flagship eval did not pass on v0.26.0-rc0"),
        _issue(5, "x", key="eval-flagship/smoke/v0.27.0-rc1"),
        _issue(6, "x", key="eval-regression/agent-gaia"),
        _issue(7, "x", key="eval-agent-gaia/agent-pkg-gaia-v0.3.3"),
        _issue(8, "Agent release eval did not pass on agent-pkg-gaia-v0.3.2"),
        _issue(9, "x", key="eval-agent-gaia/agent-pkg-gaia-v0.4.0"),
    ]


def test_a_flagship_pass_closes_its_issue_and_earlier_per_tag_ones():
    gh = FakeGh(_all_eval_issues())
    _close_on_pass(_eval_job("eval_flagship.yml", "release-escalation"), gh, "v0.26.0")
    # Not a later line's per-tag issue (#5), nor anything of the agent's.
    assert sorted(c[2] for c in gh.writes()) == ["1", "2", "3", "4"]


def test_an_agent_pass_closes_only_the_agents_issues():
    gh = FakeGh(_all_eval_issues())
    _close_on_pass(
        _eval_job("release_agent_gaia.yml", "eval-report"),
        gh,
        "agent-pkg-gaia-v0.3.4",
    )
    assert sorted(c[2] for c in gh.writes()) == ["6", "7", "8"]


def test_the_flagship_notice_reads_every_job_its_tiers_need():
    job = _eval_job("eval_flagship.yml", "release-escalation")
    assert set(job["needs"]) >= {"eval-gate", "lanes", "agent-eval", "verify-final"}
    env = next(s for s in job["steps"] if s.get("id") == "notice")["env"]
    assert env["LANES_RESULT"] == "${{ needs.agent-eval.result }}"
    assert env["VERIFY_RESULT"] == "${{ needs.verify-final.result }}"
    assert env["OUTCOMES_RESULT"] == "${{ needs.flagship-tasks.result }}"
    assert env["BASELINE_DIR"] == "${{ needs.lanes.outputs.baseline_dir }}"


def test_the_agent_notice_expects_what_the_eval_ran_against_its_baseline():
    release = yaml.safe_load(
        (WORKFLOWS / "release_agent_gaia.yml").read_text(encoding="utf-8")
    )
    callee = yaml.safe_load(
        (WORKFLOWS / "test_gaia_agent_eval.yml").read_text(encoding="utf-8")
    )
    env = next(
        s for s in release["jobs"]["eval-report"]["steps"] if s.get("id") == "notice"
    )["env"]
    # A category the notice does not expect is never reported as missing.
    assert env["EVAL_CATEGORIES"] == release["jobs"]["gaia-eval"]["with"]["categories"]
    assert env["BASELINE_DIR"] == callee["env"]["BASELINE_DIR"]
    # #5197: the flagship baseline, same pool, model and judge as this eval,
    # with a scorecard for every category the release runs.
    assert env["BASELINE_DIR"] == "tests/fixtures/eval_baselines/gaia-flagship"
    baseline = REPO / env["BASELINE_DIR"]
    meta = json.loads((baseline / "meta.json").read_text(encoding="utf-8"))
    assert meta["model"] == "Gemma-4-E4B-it-GGUF"
    assert meta["eval_model"]
    for category in env["EVAL_CATEGORIES"].split(","):
        assert (baseline / f"scorecard_{category}.json").is_file(), category


# --- the action's body-file input ---------------------------------------------


def _run_action_script(tmp_path, **env):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not on PATH")
    action = yaml.safe_load((ACTION_DIR / "action.yml").read_text(encoding="utf-8"))
    step = action["runs"]["steps"][0]
    assert step["env"]["AI_BODY_FILE"] == "${{ inputs.body-file }}"
    script = tmp_path / "run.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    argv_out = tmp_path / "argv.txt"
    full_env = {
        "PATH": os.environ.get("PATH", ""),
        "GITHUB_REPOSITORY": R,
        "GITHUB_ACTION_PATH": str(tmp_path),
        "RUNNER_TEMP": str(tmp_path),
        "ARGV_OUT": str(argv_out),
        "AI_MODE": "report",
        "AI_KEY": "eval-regression/flagship",
        "AI_TITLE": "t",
        "AI_UPDATE": "edit",
        **{k: "" for k in ("AI_LABELS", "AI_ASSIGNEES", "AI_BODY", "AI_BODY_FILE")},
        **{k: "" for k in ("AI_LEGACY_TITLE", "AI_LEGACY_TITLE_PREFIX")},
        **env,
    }
    # A shell function stands in for auto_issue.py and records its argv.
    shim = 'python3() { printf "%s\n" "$@" > "$ARGV_OUT"; }; . "$0"'
    proc = subprocess.run(
        [bash, "-c", shim, str(script)],
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )
    argv = (
        argv_out.read_text(encoding="utf-8").splitlines() if argv_out.exists() else []
    )
    return proc, argv


def test_the_action_files_the_body_file_as_given(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("| `x` | judge said $(whoami) |\n", encoding="utf-8")
    proc, argv = _run_action_script(tmp_path, AI_BODY_FILE=str(body))
    assert proc.returncode == 0, proc.stderr
    assert f"--body-file={body}" in argv


@pytest.mark.parametrize(
    "env, error",
    [
        ({"AI_BODY": "inline"}, "not both"),
        ({"AI_BODY": ""}, "missing or empty"),
    ],
)
def test_the_action_refuses_a_bad_body_file(tmp_path, env, error):
    body = tmp_path / "body.md"
    body.write_text("" if error == "missing or empty" else "text", encoding="utf-8")
    proc, argv = _run_action_script(tmp_path, AI_BODY_FILE=str(body), **env)
    assert proc.returncode != 0 and argv == []
    assert error in proc.stdout


# --- nightly-patch-release: a job's issue closes only when that job runs green --


def _nightly_notify_steps():
    wf = yaml.safe_load(
        (WORKFLOWS / "nightly-patch-release.yml").read_text(encoding="utf-8")
    )
    return wf["jobs"]["notify-on-failure"]["steps"]


def test_a_draft_failure_closes_only_on_a_green_draft():
    resolves = {
        s["with"]["key"]: s
        for s in _nightly_notify_steps()
        if s.get("with", {}).get("mode") == "resolve"
    }
    assert set(resolves) == {
        "nightly-patch-release/check-failure",
        "nightly-patch-release/draft-failure",
    }
    # A skipped draft (a guard stopped the night) must not close the draft issue.
    assert (
        resolves["nightly-patch-release/draft-failure"]["if"]
        == "needs.draft.result == 'success'"
    )
    assert (
        resolves["nightly-patch-release/check-failure"]["if"]
        == "needs.check.result == 'success'"
    )


def test_the_check_resolve_never_matches_a_draft_issue():
    gh = FakeGh(
        [
            _issue(1, "⚠️ Nightly patch-release draft failed (target v0.25.3)"),
            _issue(2, "⚠️ Nightly patch-release check failed (scope undetermined)"),
            _issue(3, "x", key="nightly-patch-release/draft-failure"),
            _issue(4, "x", key="nightly-patch-release/check-failure"),
        ]
    )
    _resolve(
        gh,
        "--key",
        "nightly-patch-release/check-failure",
        "--legacy-title-prefix",
        "⚠️ Nightly patch-release check failed",
    )
    assert sorted(c[2] for c in gh.writes()) == ["2", "4"]
