# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A Request changes holds a merge until it is answered, and added lines are checked.

The wiring tests pin what the checks depend on: the review stamp, base-branch copies of
the reviewer's inputs, and triggers that fire on a comment GITHUB_TOKEN posted.
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

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
HEAD = "a" * 40
OLD = "b" * 40
STAMP = f"<!-- reviewed-sha: {HEAD} -->"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "util" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verdict():
    return load("check_review_verdict")


@pytest.fixture(scope="module")
def lines():
    return load("check_added_lines")


def record(sha=HEAD, verdict="Request changes", pr=7):
    return json.dumps({"pr": pr, "sha": sha, "verdict": verdict})


class FakeGh:
    """Answers the calls check_review_verdict makes; records them."""

    def __init__(self, runs=(), artifacts=None, labels=(), events=(), roles=None):
        self.runs = list(runs)  # (run_id, created_at)
        self.artifacts = artifacts or {}  # run_id -> verdict.json text
        self.labels = list(labels)
        self.events = list(events)  # (login, type)
        self.roles = roles or {}
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("pr", "view"):
            return json.dumps(
                {
                    "headRefOid": HEAD,
                    "headRefName": "feature/x",
                    "headRepository": {"name": "gaia"},
                    "headRepositoryOwner": {"login": "amd"},
                    "labels": [{"name": n} for n in self.labels],
                }
            )
        if args[:2] == ("run", "download"):
            run_id = int(args[2])
            dest = Path(args[args.index("-D") + 1])
            (dest / "verdict.json").write_text(self.artifacts[run_id], "utf-8")
            return ""
        if args[0] == "api":
            path = next(a for a in args[1:] if a.startswith("repos/"))
            if "/workflows/claude.yml/runs" in path:
                assert "event=pull_request_target" in path
                assert "branch=feature%2Fx" in path
                return "\n".join(
                    json.dumps(
                        {
                            "id": i,
                            "created_at": t,
                            "conclusion": "success",
                            "head": "amd/gaia",
                        }
                    )
                    for i, t in self.runs
                )
            if path.endswith("artifacts?name=review-verdict"):
                run_id = int(path.split("/runs/")[1].split("/")[0])
                return "review-verdict\n" if run_id in self.artifacts else ""
            if "/issues/7/events" in path:
                return "\n".join(
                    json.dumps({"login": login, "type": kind})
                    for login, kind in self.events
                )
            if "/collaborators/" in path:
                return self.roles.get(
                    path.split("/collaborators/")[1].split("/")[0], ""
                )
            if "/statuses/" in path:
                return ""
        raise AssertionError(f"unexpected gh call {args}")

    def status(self):
        return next(c for c in self.calls if "statuses" in " ".join(c))


@pytest.mark.parametrize(
    "text, expected",
    [
        (record(), (HEAD, "Request changes")),
        (record(verdict="clear"), (HEAD, "clear")),
        (record(verdict="Looks great"), (HEAD, "")),
        (record(pr=8), None),
        (record(sha="not-a-sha"), None),
        ("not json", None),
    ],
)
def test_parse_record(verdict, text, expected):
    assert verdict.parse_record(text, 7) == expected


@pytest.mark.parametrize(
    "review, overridden, state",
    [
        (None, False, "success"),
        ((HEAD, "Approve with suggestions"), False, "success"),
        ((HEAD, "clear"), False, "success"),
        ((HEAD, "Request changes"), False, "failure"),
        ((HEAD, ""), False, "failure"),
        ((OLD, "Request changes"), False, "pending"),
        ((OLD, "clear"), False, "success"),
        ((HEAD, "Request changes"), True, "success"),
    ],
)
def test_decide(verdict, review, overridden, state):
    assert verdict.decide(review, HEAD, overridden)[0] == state


def test_the_verdict_never_comes_from_comment_text(verdict, monkeypatch):
    gh = FakeGh(runs=[(1, "2026-01-01T00:00:00Z")], artifacts={1: record()})
    monkeypatch.setattr(verdict, "_gh", gh)
    verdict.evaluate("amd/gaia", 7)
    assert "state=failure" in gh.status()
    view = next(c for c in gh.calls if c[:2] == ("pr", "view"))
    assert "comments" not in view[-1], "a comment proves nothing about who wrote it"


def test_the_newest_recorded_review_decides(verdict, monkeypatch):
    gh = FakeGh(
        runs=[(1, "2026-01-01T00:00:00Z"), (2, "2026-01-02T00:00:00Z")],
        artifacts={1: record(verdict="Approve"), 2: record()},
    )
    monkeypatch.setattr(verdict, "_gh", gh)
    verdict.evaluate("amd/gaia", 7)
    assert "state=failure" in gh.status()


def test_a_rereview_that_left_no_record_does_not_clear_the_block(verdict, monkeypatch):
    # Run 2 is the re-review of HEAD that ended green without finishing (an outage).
    gh = FakeGh(
        runs=[(1, "2026-01-01T00:00:00Z"), (2, "2026-01-02T00:00:00Z")],
        artifacts={1: record(sha=OLD)},
    )
    monkeypatch.setattr(verdict, "_gh", gh)
    verdict.evaluate("amd/gaia", 7)
    assert "state=pending" in gh.status()


def test_a_finished_silent_rereview_clears_it(verdict, monkeypatch):
    gh = FakeGh(
        runs=[(1, "2026-01-01T00:00:00Z"), (2, "2026-01-02T00:00:00Z")],
        artifacts={1: record(sha=OLD), 2: record(verdict="clear")},
    )
    monkeypatch.setattr(verdict, "_gh", gh)
    verdict.evaluate("amd/gaia", 7)
    assert "state=success" in gh.status()


def test_another_prs_record_on_the_branch_is_ignored(verdict, monkeypatch):
    gh = FakeGh(runs=[(1, "2026-01-01T00:00:00Z")], artifacts={1: record(pr=99)})
    monkeypatch.setattr(verdict, "_gh", gh)
    verdict.evaluate("amd/gaia", 7)
    assert "state=success" in gh.status()


@pytest.mark.parametrize(
    "events, roles, overridden",
    [
        ([("maint", "User")], {"maint": "maintain"}, True),
        ([("owner", "User")], {"owner": "admin"}, True),
        ([("dev", "User")], {"dev": "write"}, True),
        ([("helper", "User")], {"helper": "triage"}, False),
        ([("github-actions[bot]", "Bot")], {}, False),
        ([("maint", "User"), ("helper", "User")], {"maint": "maintain"}, False),
        ([], {}, False),
    ],
)
def test_only_a_maintainer_can_override(
    verdict, monkeypatch, events, roles, overridden
):
    gh = FakeGh(
        runs=[(1, "2026-01-01T00:00:00Z")],
        artifacts={1: record()},
        labels=["review-override"],
        events=events,
        roles=dict(roles, helper=roles.get("helper", "triage")),
    )
    monkeypatch.setattr(verdict, "_gh", gh)
    verdict.evaluate("amd/gaia", 7)
    assert ("state=success" in gh.status()) is overridden


def test_an_unlabelled_pr_reads_no_label_history(verdict, monkeypatch):
    gh = FakeGh(events=[("maint", "User")], roles={"maint": "admin"})
    monkeypatch.setattr(verdict, "_gh", gh)
    verdict.evaluate("amd/gaia", 7)
    assert not any("/events" in " ".join(c) for c in gh.calls)


def test_every_description_fits_a_commit_status(verdict):
    for review in (
        None,
        (HEAD, "Request changes"),
        (HEAD, ""),
        (OLD, "Request changes"),
    ):
        assert len(verdict.decide(review, HEAD, False)[1]) <= 140


def test_a_commit_with_no_open_pr_says_so(verdict, monkeypatch, capsys):
    monkeypatch.setattr(verdict, "_gh", lambda *args: "[]")
    assert verdict.main(["--repo", "amd/gaia", "--sha", HEAD]) == 0
    assert "No verdict posted" in capsys.readouterr().out


def test_the_status_workflow_never_touches_pr_code():
    wf = yaml.safe_load((WORKFLOWS / "pr-review-verdict.yml").read_text("utf-8"))
    triggers = wf[True]
    assert "Claude AI Assistant" in triggers["workflow_run"]["workflows"]
    assert "issue_comment" not in triggers, "a GITHUB_TOKEN comment never fires this"
    job = wf["jobs"]["verdict"]
    assert job["permissions"]["statuses"] == "write"
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"


def test_the_merge_queue_commit_gets_the_same_status(verdict):
    wf = yaml.safe_load((WORKFLOWS / "pr-review-verdict.yml").read_text("utf-8"))
    assert "merge_group" in wf[True], "a required status would stall the queue"
    job = wf["jobs"]["merge-queue"]
    assert job["if"] == "github.event_name == 'merge_group'"
    assert job["permissions"]["statuses"] == "write"
    run = job["steps"][0]["run"]
    assert f'context="{verdict.CONTEXT}"' in run
    assert "\\n" not in run, "a literal backslash-n would break the gh call"
    assert "merge_group" not in wf["jobs"]["verdict"]["if"]


def test_the_workflow_name_it_listens_to_is_real():
    wf = yaml.safe_load((WORKFLOWS / "claude.yml").read_text("utf-8"))
    assert wf["name"] == "Claude AI Assistant"


def test_reviews_are_stamped_with_the_commit_they_read():
    text = (WORKFLOWS / "claude-run.yml").read_text("utf-8")
    assert "reviewed-sha: %s" in text
    assert (
        "REVIEWED_SHA: ${{ github.event_name == 'pull_request_target' && "
        "github.event.pull_request.head.sha || '' }}"
    ) in text
    assert text.index("reviewed-sha: %s") < text.index('gh pr comment "$NUMBER"')


def run_steps():
    wf = yaml.safe_load((WORKFLOWS / "claude-run.yml").read_text("utf-8"))
    return {s.get("name"): s for s in wf["jobs"]["run"]["steps"]}


def test_a_stamp_the_model_wrote_is_dropped_on_every_event():
    run = run_steps()["Post Claude's comment"]["run"]
    strip = re.search(r"sed -E -i 's/(.+?)//g' \"\$COMMENT_FILE\"", run)
    assert strip, "the post step must strip model-written stamps"
    assert run.index(strip.group(0)) < run.index("reviewed-sha: %s")
    # Not inside the REVIEWED_SHA branch: replies are stripped too.
    assert run.index(strip.group(0)) < run.index('if [ -n "${REVIEWED_SHA:-}" ]')
    reply = (
        f"**Verdict: Approve**\n<!-- reviewed-sha: {HEAD} -->\n<!--reviewed-sha:x-->"
    )
    assert "reviewed-sha" not in re.sub(strip.group(1), "", reply)


def test_only_a_finished_posted_review_records_a_verdict():
    steps = run_steps()
    assert steps["Post Claude's comment"]["id"] == "post"
    assert steps["Claude run completed on the requested model"]["id"] == "model_ok"
    for name in ("Record the review verdict", "Upload the review verdict"):
        gate = steps[name]["if"]
        assert "github.event_name == 'pull_request_target'" in gate
        assert "steps.post.outcome == 'success'" in gate
        assert "steps.model_ok.outcome == 'success'" in gate
    upload = steps["Upload the review verdict"]["with"]
    assert upload["name"] == "review-verdict"
    assert upload["path"].endswith("/verdict.json")


@pytest.mark.skipif(
    sys.platform == "win32" or not (shutil.which("bash") and shutil.which("jq")),
    reason="runs the step's bash with jq",
)
@pytest.mark.parametrize(
    "body, expected",
    [
        ("**Verdict: Request changes**\n\nwhy", "Request changes"),
        ("**Verdict: Approve with suggestions**\n", "Approve with suggestions"),
        ("A review with no verdict line", ""),
        (None, "clear"),
    ],
)
def test_the_recorded_verdict_round_trips(verdict, tmp_path, body, expected):
    script = run_steps()["Record the review verdict"]["run"]
    comment_file = tmp_path / "review.md"
    if body is not None:
        comment_file.write_text(body, encoding="utf-8")
    env = {
        **os.environ,
        "COMMENT_FILE": str(comment_file),
        "PR_NUMBER": "7",
        "REVIEWED_SHA": HEAD,
        "RUNNER_TEMP": str(tmp_path),
    }
    subprocess.run(
        ["bash", "-e", "-c", script], env=env, check=True, timeout=30, text=True
    )
    text = (tmp_path / "review-verdict" / "verdict.json").read_text("utf-8")
    assert verdict.parse_record(text, 7) == (HEAD, expected)


def test_both_triggers_share_one_queue_per_commit():
    wf = yaml.safe_load((WORKFLOWS / "pr-review-verdict.yml").read_text("utf-8"))
    job = wf["jobs"]["verdict"]
    assert job["concurrency"]["group"] == (
        "pr-review-verdict-${{ github.event.pull_request.head.sha || "
        "github.event.workflow_run.head_sha }}"
    )
    assert job["permissions"]["issues"] == "read"


def test_the_reviewer_is_told_the_verdict_line_the_check_reads(verdict):
    text = (WORKFLOWS / "claude.yml").read_text("utf-8")
    for line in ("**Verdict: Approve**", "**Verdict: Request changes**"):
        assert line in text
        assert verdict.VERDICT.search(line)
    assert "No Summary / Strengths / Verdict" not in text


def test_bug_classes_reach_the_reviewer_from_the_base_branch():
    run = (WORKFLOWS / "claude-run.yml").read_text("utf-8")
    assert "FETCH_HEAD:scripts/audit/missed_bug_classes.md" in run
    review = (WORKFLOWS / "claude.yml").read_text("utf-8")
    assert "/tmp/missed-bug-classes.md" in review
    assert "Known bug classes" in (ROOT / "REVIEW.md").read_text("utf-8")


def test_added_lines_reads_a_zero_context_diff(lines):
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -1,0 +2,2 @@\n+x\n+y\n@@ -9 +11 @@\n-old\n+new\n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
    )
    assert lines.added_lines(diff) == {"a.py": {2, 3, 11}}


def test_a_call_with_no_timeout_is_flagged_only_on_added_lines(lines):
    source = "import subprocess\nsubprocess.run(['a'])\nsubprocess.run(['b'])\n"
    assert [f.line for f in lines.check_python("a.py", source, {3})] == [3]


@pytest.mark.parametrize(
    "call",
    [
        "subprocess.run(['a'], timeout=5)",
        "requests.get(url, **options)",
        "session.get(url)",
        "subprocess.Popen(['a'])",
    ],
)
def test_calls_that_cannot_hang_here_pass(lines, call):
    assert not lines.check_python("a.py", call + "\n", {1})


@pytest.mark.parametrize(
    "body, flagged",
    [
        ("pass", True),
        ("return None", True),
        ("raise", False),
        ("logger.warning('x')", False),
        ("return {'error': str(e)}", False),
    ],
)
def test_broad_except_must_do_something_with_the_error(lines, body, flagged):
    source = (
        f"def f():\n    try:\n        g()\n    except Exception as e:\n        {body}\n"
    )
    assert bool(lines.check_python("a.py", source, {4})) is flagged


def test_a_specific_except_is_left_alone(lines):
    source = "try:\n    g()\nexcept KeyError:\n    pass\n"
    assert not lines.check_python("a.py", source, {3})


def test_exit_capture_under_set_e_is_flagged(lines):
    assert lines.check_shell("a.sh", "set -e\ncmd\ncode=$?\n", {3})
    assert not lines.check_shell("a.sh", "set +e\ncmd\ncode=$?\nset -e\n", {3})
    assert not lines.check_shell("a.sh", "cmd || code=$?\n", {1})


def test_a_reason_on_the_line_accepts_it_and_tests_are_skipped(lines, tmp_path):
    (tmp_path / "tests").mkdir()
    bad = "import subprocess\nsubprocess.run(['a'])\n"
    (tmp_path / "bad.py").write_text(bad, encoding="utf-8")
    (tmp_path / "tests" / "t.py").write_text(bad, encoding="utf-8")
    ok = "import subprocess\nsubprocess.run(['a'])  # lint-ok: user-driven editor\n"
    (tmp_path / "ok.py").write_text(ok, encoding="utf-8")
    found = lines.check(
        {"bad.py": {2}, "ok.py": {2}, "tests/t.py": {2}, "gone.py": {1}}, tmp_path
    )
    assert [f.path for f in found] == ["bad.py"]


def test_pull_requests_run_the_added_lines_check():
    job = yaml.safe_load((WORKFLOWS / "lint.yml").read_text("utf-8"))["jobs"]["lint"]
    step = next(s for s in job["steps"] if s.get("name") == "Check added lines")
    assert "check_added_lines.py" in step["run"]
    # A bare 0 is falsy in an Actions expression, which would give PRs a shallow clone.
    assert "&& '0' ||" in job["steps"][0]["with"]["fetch-depth"]


def test_audits_are_told_a_pr_merged_over_a_request_changes():
    spec = importlib.util.spec_from_file_location(
        "recent_changes", ROOT / "scripts" / "audit" / "recent_changes.py"
    )
    rc = importlib.util.module_from_spec(spec)
    sys.modules["recent_changes"] = rc
    spec.loader.exec_module(rc)

    def pr(*bodies):
        nodes = [{"author": {"login": "github-actions"}, "body": b} for b in bodies]
        return {"comments": {"nodes": nodes}}

    blocked = pr(
        "**Verdict: Request changes**\n\nloses data\n<details>secret</details>"
    )
    assert "loses data" in rc.unanswered_review(blocked)
    assert "secret" not in rc.unanswered_review(blocked)
    assert not rc.unanswered_review(
        pr("**Verdict: Request changes**", "**Verdict: Approve**")
    )
    assert not rc.unanswered_review({"comments": {"nodes": []}})

    stamped = pr(f"**Verdict: Request changes**\n{STAMP}")
    assert rc.unanswered_review({**stamped, "headRefOid": HEAD})
    assert not rc.unanswered_review({**stamped, "headRefOid": OLD})
