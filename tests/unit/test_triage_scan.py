# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The triage worklist must put outside contributors' bug fixes first and never
call a PR ready while a blocker is visible."""

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "util/triage_scan.py"
spec = importlib.util.spec_from_file_location("triage_scan", SCRIPT)
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)

TEAM = {"kovtcharov", "kovtcharov-amd"}


BOT_APPROVE = {
    "author": {"login": "github-actions"},
    "createdAt": "2026-10-20T11:00:00Z",
    "body": "**Verdict: Approve** - no blocking issues.",
}


def make_pr(**over):
    pr = {
        "number": 1,
        "title": "fix(ui): something",
        "url": "https://github.com/amd/gaia/pull/1",
        "headRefName": "fix/x",
        "headRefOid": "abc123",
        "headRepository": {"nameWithOwner": "outsider/gaia"},
        "reviewThreads": {"nodes": []},
        "isDraft": False,
        "additions": 10,
        "deletions": 5,
        "mergeable": "MERGEABLE",
        "reviewDecision": None,
        "maintainerCanModify": True,
        "author": {"login": "outsider"},
        "headRepositoryOwner": {"login": "outsider"},
        "labels": {"nodes": []},
        "milestone": None,
        "closingIssuesReferences": {"nodes": []},
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "committedDate": "2026-10-20T10:00:00Z",
                        "statusCheckRollup": {
                            "contexts": {
                                "pageInfo": {"hasNextPage": False},
                                "nodes": [
                                    {
                                        "__typename": "CheckRun",
                                        "name": name,
                                        "conclusion": "SUCCESS",
                                        "status": "COMPLETED",
                                    }
                                    for name in (
                                        "Unit Tests (Linux, py3.12)",
                                        "Run Code Quality Checks",
                                    )
                                ],
                            }
                        },
                    }
                }
            ]
        },
        "comments": {"nodes": [BOT_APPROVE]},
        "reviews": {"nodes": []},
    }
    pr.update(over)
    return pr


def with_check(pr, **check):
    base = {
        "__typename": "CheckRun",
        "name": "X",
        "conclusion": None,
        "status": "COMPLETED",
    }
    pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
        "nodes"
    ].append({**base, **check})
    return pr


def test_clean_third_party_pr_is_a_ready_candidate():
    row = ts.assess_pr(make_pr(), TEAM)
    assert row["third_party"] and row["ready"] and row["kind"] == "bugfix"


@pytest.mark.parametrize(
    "login",
    ["dependabot", "github-actions[bot]", "claude", "kovtcharov", "KOVTCHAROV-AMD"],
)
def test_bots_and_maintainers_are_not_third_party(login):
    assert not ts.is_third_party({"login": login}, TEAM)


def test_fork_ci_awaiting_approval_blocks_ready():
    pr = with_check(make_pr(), conclusion="ACTION_REQUIRED")
    row = ts.assess_pr(pr, TEAM)
    assert not row["ready"]
    assert any("awaiting maintainer approval" in b for b in row["blockers"])


@pytest.mark.parametrize("conclusion", ["FAILURE", "TIMED_OUT"])
def test_failed_check_blocks_ready(conclusion):
    row = ts.assess_pr(with_check(make_pr(), conclusion=conclusion, name="Lint"), TEAM)
    assert any("CI red: Lint" in b for b in row["blockers"])


def test_skipped_and_cancelled_checks_do_not_count_as_red():
    for conclusion in ("SKIPPED", "NEUTRAL"):
        row = ts.assess_pr(with_check(make_pr(), conclusion=conclusion), TEAM)
        assert row["ready"], conclusion


def test_running_check_blocks_ready():
    row = ts.assess_pr(with_check(make_pr(), status="IN_PROGRESS"), TEAM)
    assert any("pending" in b for b in row["blockers"])


def test_no_checks_at_all_is_not_green():
    pr = make_pr()
    pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
    assert "no CI results" in ts.assess_pr(pr, TEAM)["blockers"]


def test_conflict_and_draft_block():
    row = ts.assess_pr(make_pr(mergeable="CONFLICTING", isDraft=True), TEAM)
    assert {"draft", "conflict"} <= set(row["blockers"])


def test_comment_after_last_commit_blocks_but_authors_own_does_not():
    later = "2026-10-21T10:00:00Z"

    def note(login):
        return {"author": {"login": login}, "createdAt": later, "body": "hm"}

    other = make_pr(comments={"nodes": [BOT_APPROVE, note("reviewer")]})
    own = make_pr(comments={"nodes": [BOT_APPROVE, note("outsider")]})
    assert any("comment" in b for b in ts.assess_pr(other, TEAM)["blockers"])
    assert ts.assess_pr(own, TEAM)["ready"]


def test_comment_before_last_commit_is_addressed():
    old = "2026-10-19T10:00:00Z"
    pr = make_pr(reviews={"nodes": [{"author": {"login": "bot"}, "submittedAt": old}]})
    assert ts.assess_pr(pr, TEAM)["ready"]


def test_large_pr_and_locked_fork_need_a_human():
    big = ts.assess_pr(make_pr(additions=5000, deletions=1), TEAM)
    assert any("large" in b for b in big["blockers"])
    locked = make_pr(maintainerCanModify=False)
    assert any(
        "maintainer edits off" in b for b in ts.assess_pr(locked, TEAM)["blockers"]
    )


def test_same_repo_branch_is_not_flagged_as_locked_fork():
    pr = make_pr(maintainerCanModify=False, headRepositoryOwner={"login": "amd"})
    assert ts.assess_pr(pr, TEAM)["ready"]


@pytest.mark.parametrize(
    "title,kind",
    [
        ("fix(ui): x", "bugfix"),
        ("test(hub): stabilize", "bugfix"),
        ("chore(deps): bump foo", "bugfix"),
        ("feat(ui): x", "other"),
        ("refactor: x", "other"),
        ("docs(plans): x", "other"),
        ("fixture cleanup", "other"),
    ],
)
def test_kind_from_title(title, kind):
    assert ts.classify_kind(title, []) == kind


def test_bug_label_makes_a_bugfix():
    assert ts.classify_kind("Preserve query errors", ["bug"]) == "bugfix"


def test_ranking_third_party_bugfix_beats_maintainer_bugfix_beats_feature():
    rows = [
        {"number": 4, "third_party": False, "kind": "other", "ready": True},
        {"number": 3, "third_party": False, "kind": "bugfix", "ready": True},
        {"number": 2, "third_party": True, "kind": "other", "ready": True},
        {"number": 1, "third_party": True, "kind": "bugfix", "ready": False},
    ]
    assert [r["number"] for r in sorted(rows, key=ts.pr_rank)] == [1, 2, 3, 4]


def test_maintainers_adds_personal_account_behind_amd_login(monkeypatch):
    done = SimpleNamespace(
        returncode=0, stdout="kovtcharov-amd\nitomek-amd\nsomeone\n", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)
    team = ts.maintainers("gh", "amd/gaia", {"extra"})
    assert {"kovtcharov", "kovtcharov-amd", "itomek", "someone", "extra"} <= team


def test_maintainers_fails_loudly_without_push_access(monkeypatch):
    done = SimpleNamespace(returncode=1, stdout="", stderr="HTTP 403")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)
    with pytest.raises(SystemExit) as exc:
        ts.maintainers("gh", "amd/gaia", set())
    assert "push access" in str(exc.value)


def test_issue_unanswered_ranks_before_answered():
    now = ts.datetime.now(ts.timezone.utc)

    def issue(n, replier):
        return {
            "number": n,
            "title": "t",
            "createdAt": "2026-10-01T00:00:00Z",
            "author": {"login": "outsider"},
            "labels": {"nodes": []},
            "milestone": None,
            "comments": {"nodes": [{"author": {"login": replier}}] if replier else []},
            "closedByPullRequestsReferences": {"nodes": []},
        }

    rows = [
        ts.assess_issue(issue(1, "kovtcharov-amd"), TEAM, now),
        ts.assess_issue(issue(2, None), TEAM, now),
    ]
    assert [r["number"] for r in sorted(rows, key=ts.issue_rank)] == [2, 1]


def test_pr_with_no_commits_does_not_crash_and_is_not_ready():
    pr = make_pr()
    pr["commits"]["nodes"] = []
    assert "no CI results" in ts.assess_pr(pr, TEAM)["blockers"]


def test_expected_status_that_never_reported_is_pending_not_green():
    pr = make_pr()
    pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
        "nodes"
    ].append(
        {"__typename": "StatusContext", "context": "required", "state": "EXPECTED"}
    )
    assert any("pending" in b for b in ts.assess_pr(pr, TEAM)["blockers"])


@pytest.mark.parametrize("state", ["UNKNOWN", None])
def test_unknown_mergeability_is_not_clean(state):
    row = ts.assess_pr(make_pr(mergeable=state), TEAM)
    assert not row["ready"]


def test_deleted_commenter_still_counts_as_a_comment():
    pr = make_pr(
        comments={"nodes": [{"author": None, "createdAt": "2026-10-22T00:00:00Z"}]}
    )
    assert not ts.assess_pr(pr, TEAM)["ready"]


def test_required_checks_missing_means_not_ready():
    pr = make_pr()
    nodes = pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
        "nodes"
    ]
    nodes[:] = [
        {
            "__typename": "CheckRun",
            "name": "label",
            "conclusion": "SUCCESS",
            "status": "COMPLETED",
        }
    ]
    row = ts.assess_pr(pr, TEAM)
    assert any("required CI not run" in b for b in row["blockers"])


def test_stalled_fork_runs_block_even_with_no_check_runs():
    row = ts.assess_pr(make_pr(), TEAM, stalled=[1, 2, 3])
    assert any("awaiting maintainer approval (3 runs)" in b for b in row["blockers"])


def test_cancelled_check_is_red_not_green():
    row = ts.assess_pr(with_check(make_pr(), conclusion="CANCELLED", name="Lint"), TEAM)
    assert any("CI red: Lint" in b for b in row["blockers"])


def test_truncated_check_list_is_never_trusted():
    pr = make_pr()
    pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["pageInfo"] = {
        "hasNextPage": True
    }
    assert any("truncated" in b for b in ts.assess_pr(pr, TEAM)["blockers"])


def test_missing_bot_verdict_is_a_note_not_a_blocker():
    row = ts.assess_pr(make_pr(comments={"nodes": []}), TEAM)
    assert row["ready"] and "no review-bot verdict" in row["notes"]


def test_bot_request_changes_blocks():
    c = {**BOT_APPROVE, "body": "**Verdict: Request changes**"}
    row = ts.assess_pr(make_pr(comments={"nodes": [c]}), TEAM)
    assert any("request changes" in b for b in row["blockers"])


def test_stale_approve_is_a_note_and_a_fresh_one_after_a_stale_reject_clears():
    stale = {**BOT_APPROVE, "createdAt": "2026-10-19T00:00:00Z"}
    row = ts.assess_pr(make_pr(comments={"nodes": [stale]}), TEAM)
    assert row["ready"] and any("predates" in n for n in row["notes"])
    reject = {
        **BOT_APPROVE,
        "createdAt": "2026-10-19T00:00:00Z",
        "body": "**Verdict: Request changes**",
    }
    assert ts.assess_pr(make_pr(comments={"nodes": [reject, BOT_APPROVE]}), TEAM)[
        "ready"
    ]


def test_a_commit_after_request_changes_does_not_clear_it():
    reject = {
        **BOT_APPROVE,
        "createdAt": "2026-10-19T00:00:00Z",
        "body": "**Verdict: Request changes**",
    }
    row = ts.assess_pr(make_pr(comments={"nodes": [reject]}), TEAM)
    assert not row["ready"] and any("request changes" in b for b in row["blockers"])


def test_bot_verdict_is_not_counted_as_an_unaddressed_comment():
    assert ts.comments_after_last_commit(make_pr()) == 0


def test_a_verdict_phrase_from_a_human_is_not_a_bot_verdict():
    human = {**BOT_APPROVE, "author": {"login": "outsider"}}
    row = ts.assess_pr(make_pr(comments={"nodes": [human]}), TEAM)
    assert "no review-bot verdict" in row["notes"]


def test_unresolved_review_thread_blocks_but_outdated_or_resolved_do_not():
    open_t = {"isResolved": False, "isOutdated": False}
    done = [
        {"isResolved": True, "isOutdated": False},
        {"isResolved": False, "isOutdated": True},
    ]
    assert any(
        "unresolved" in b
        for b in ts.assess_pr(make_pr(reviewThreads={"nodes": [open_t]}), TEAM)[
            "blockers"
        ]
    )
    assert ts.assess_pr(make_pr(reviewThreads={"nodes": done}), TEAM)["ready"]


def test_deleted_account_is_third_party():
    assert ts.is_third_party(None, TEAM)
    assert ts.assess_pr(make_pr(author=None), TEAM)["third_party"]


def test_unknown_mergeability_is_resolved_via_rest(monkeypatch):
    answers = iter(["null", "true"])
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=next(answers), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ts.time, "sleep", lambda s: None)
    nodes = [make_pr(mergeable="UNKNOWN"), make_pr(number=2, mergeable="CONFLICTING")]
    ts.resolve_unknown_mergeability("gh", "amd/gaia", nodes)
    assert nodes[0]["mergeable"] == "MERGEABLE"
    assert nodes[1]["mergeable"] == "CONFLICTING"
    assert len(calls) == 2


def test_mergeability_that_never_resolves_stays_unknown(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="null", stderr=""),
    )
    monkeypatch.setattr(ts.time, "sleep", lambda s: None)
    nodes = [make_pr(mergeable="UNKNOWN")]
    ts.resolve_unknown_mergeability("gh", "amd/gaia", nodes, attempts=2)
    assert nodes[0]["mergeable"] == "UNKNOWN"
    assert not ts.assess_pr(nodes[0], TEAM)["ready"]


def test_a_conflict_found_by_rest_blocks(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="false", stderr=""),
    )
    nodes = [make_pr(mergeable="UNKNOWN")]
    ts.resolve_unknown_mergeability("gh", "amd/gaia", nodes)
    assert "conflict" in ts.assess_pr(nodes[0], TEAM)["blockers"]


def test_a_cancelled_duplicate_does_not_mask_the_run_that_passed():
    pr = make_pr()
    nodes = pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
        "nodes"
    ]
    nodes += [
        {
            "__typename": "CheckRun",
            "name": "Skill Audit",
            "conclusion": "CANCELLED",
            "status": "COMPLETED",
            "startedAt": "2026-10-26T10:00:00Z",
        },
        {
            "__typename": "CheckRun",
            "name": "Skill Audit",
            "conclusion": "SUCCESS",
            "status": "COMPLETED",
            "startedAt": "2026-10-26T10:00:05Z",
        },
    ]
    assert ts.assess_pr(pr, TEAM)["ready"]


def test_a_newer_failure_is_not_hidden_by_an_older_pass():
    pr = make_pr()
    nodes = pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
        "nodes"
    ]
    nodes += [
        {
            "__typename": "CheckRun",
            "name": "Lint",
            "conclusion": "SUCCESS",
            "status": "COMPLETED",
            "startedAt": "2026-10-26T10:00:00Z",
        },
        {
            "__typename": "CheckRun",
            "name": "Lint",
            "conclusion": "FAILURE",
            "status": "COMPLETED",
            "startedAt": "2026-10-26T10:05:00Z",
        },
    ]
    assert any("CI red: Lint" in b for b in ts.assess_pr(pr, TEAM)["blockers"])
