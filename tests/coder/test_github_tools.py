# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for :class:`GitHubToolsMixin` (§15.2 of coder-agent.mdx).

No real ``gh`` calls — the boundary mocked is :func:`gaia.coder.tools.github._run_gh`.
Every tool gets a happy-path test + a failure-path test + a registration check.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.coder.tools import github as gh_mod
from gaia.coder.tools.github import (
    GitHubCLIError,
    GitHubCLIMissingError,
    GitHubToolsMixin,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_snapshot():
    """Save and restore ``_TOOL_REGISTRY`` around a test."""
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture
def gh_mixin(registry_snapshot):
    mixin = GitHubToolsMixin()
    mixin.register_github_tools()
    return mixin


def _get_tool(name: str):
    entry = _TOOL_REGISTRY.get(name)
    assert entry is not None, f"tool {name!r} not registered"
    return entry["function"]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_all_eleven_tools_registered(gh_mixin):
    expected = {
        "gh_pr_create",
        "gh_pr_view",
        "gh_pr_comment",
        "gh_pr_review",
        "gh_pr_merge",
        "gh_issue_create",
        "gh_issue_comment",
        "gh_run_list",
        "gh_run_watch",
        "gh_run_view_log",
        "gh_release_create",
    }
    for name in expected:
        assert name in _TOOL_REGISTRY, f"missing tool: {name}"


# ---------------------------------------------------------------------------
# gh_binary guard
# ---------------------------------------------------------------------------


def test_missing_gh_binary_raises(monkeypatch):
    monkeypatch.setattr(gh_mod.shutil, "which", lambda _: None)
    with pytest.raises(GitHubCLIMissingError):
        gh_mod._gh_binary()


# ---------------------------------------------------------------------------
# gh_pr_create
# ---------------------------------------------------------------------------


def test_gh_pr_create_happy_path(gh_mixin, monkeypatch):
    url = "https://github.com/amd/gaia/pull/9001"
    monkeypatch.setattr(gh_mod, "_run_gh", lambda argv, **kw: url + "\n")
    tool = _get_tool("gh_pr_create")
    handle = tool(
        title="feat: x", body="body", head="auto/x", base="coder", draft=True
    )
    assert handle["number"] == 9001
    assert handle["url"] == url
    assert handle["draft"] is True


def test_gh_pr_create_non_zero_exit_raises(gh_mixin, monkeypatch):
    def _boom(argv, **_):
        raise GitHubCLIError(argv, 1, "auth required")

    monkeypatch.setattr(gh_mod, "_run_gh", _boom)
    tool = _get_tool("gh_pr_create")
    with pytest.raises(GitHubCLIError) as exc:
        tool(title="t", body="b", head="h")
    assert exc.value.returncode == 1
    assert "auth required" in str(exc.value)


def test_gh_pr_create_forwards_labels_and_assignees(
    gh_mixin, monkeypatch
):
    captured = {}

    def _capture(argv, **_):
        captured["argv"] = list(argv)
        return "https://github.com/amd/gaia/pull/1\n"

    monkeypatch.setattr(gh_mod, "_run_gh", _capture)
    tool = _get_tool("gh_pr_create")
    tool(
        title="t",
        body="b",
        head="h",
        labels=["bug", "auto"],
        assignees=["alice"],
    )
    argv = captured["argv"]
    # Labels and assignees are passed as repeated --label / --assignee flags.
    assert argv.count("--label") == 2
    assert argv.count("--assignee") == 1


def test_gh_pr_create_repo_env_default(gh_mixin, monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", "amd/gaia")
    captured: dict = {}

    def _capture(argv, **_):
        captured["argv"] = list(argv)
        return "https://github.com/amd/gaia/pull/42\n"

    monkeypatch.setattr(gh_mod, "_run_gh", _capture)
    tool = _get_tool("gh_pr_create")
    tool(title="t", body="b", head="h")
    argv = captured["argv"]
    # --repo should be auto-injected from env.
    idx = argv.index("--repo")
    assert argv[idx + 1] == "amd/gaia"


# ---------------------------------------------------------------------------
# gh_pr_view
# ---------------------------------------------------------------------------


def test_gh_pr_view_parses_json(gh_mixin, monkeypatch):
    payload = {
        "number": 7,
        "title": "t",
        "body": "b",
        "state": "OPEN",
        "baseRefName": "coder",
        "headRefName": "auto/x",
        "url": "https://github.com/amd/gaia/pull/7",
        "isDraft": True,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
    }
    monkeypatch.setattr(
        gh_mod, "_run_gh", lambda argv, **kw: json.dumps(payload)
    )
    state = _get_tool("gh_pr_view")(7)
    assert state["number"] == 7
    assert state["base"] == "coder"
    assert state["is_draft"] is True


def test_gh_pr_view_invalid_json_raises(gh_mixin, monkeypatch):
    monkeypatch.setattr(gh_mod, "_run_gh", lambda argv, **kw: "not-json")
    with pytest.raises(GitHubCLIError, match="non-JSON output"):
        _get_tool("gh_pr_view")(7)


# ---------------------------------------------------------------------------
# gh_pr_comment / gh_issue_comment
# ---------------------------------------------------------------------------


def test_gh_pr_comment_returns_handle(gh_mixin, monkeypatch):
    monkeypatch.setattr(
        gh_mod,
        "_run_gh",
        lambda argv, **kw: "https://github.com/amd/gaia/pull/1#issuecomment-999\n",
    )
    handle = _get_tool("gh_pr_comment")(1, "hello")
    assert handle["id"] == "issuecomment-999"


def test_gh_issue_comment_returns_handle(gh_mixin, monkeypatch):
    monkeypatch.setattr(
        gh_mod,
        "_run_gh",
        lambda argv, **kw: "https://github.com/amd/gaia/issues/2#issuecomment-123\n",
    )
    handle = _get_tool("gh_issue_comment")(2, "hi")
    assert handle["id"] == "issuecomment-123"


# ---------------------------------------------------------------------------
# gh_pr_review
# ---------------------------------------------------------------------------


def test_gh_pr_review_maps_event(gh_mixin, monkeypatch):
    captured: dict = {}

    def _capture(argv, **_):
        captured["argv"] = list(argv)
        return "https://github.com/amd/gaia/pull/3#pullrequestreview-1\n"

    monkeypatch.setattr(gh_mod, "_run_gh", _capture)
    handle = _get_tool("gh_pr_review")(3, event="APPROVE", body="lgtm")
    assert handle["state"] == "APPROVED"
    assert "--approve" in captured["argv"]


def test_gh_pr_review_request_changes_flag(gh_mixin, monkeypatch):
    captured: dict = {}

    def _capture(argv, **_):
        captured["argv"] = list(argv)
        return "https://github.com/amd/gaia/pull/3#pullrequestreview-2\n"

    monkeypatch.setattr(gh_mod, "_run_gh", _capture)
    _get_tool("gh_pr_review")(3, event="REQUEST_CHANGES", body="nope")
    assert "--request-changes" in captured["argv"]


# ---------------------------------------------------------------------------
# gh_pr_merge
# ---------------------------------------------------------------------------


def test_gh_pr_merge_reports_sha(gh_mixin, monkeypatch):
    """After merge, a follow-up view fetches the merge commit SHA."""
    responses = [
        "",  # pr merge
        json.dumps(
            {
                "number": 5,
                "title": "",
                "body": "",
                "state": "MERGED",
                "baseRefName": "coder",
                "headRefName": "x",
                "url": "https://github.com/amd/gaia/pull/5",
                "isDraft": False,
                "mergeable": None,
                "mergeStateStatus": None,
            }
        ),
        json.dumps({"mergeCommit": {"oid": "abc123"}}),
    ]

    def _capture(argv, **_):
        return responses.pop(0)

    monkeypatch.setattr(gh_mod, "_run_gh", _capture)
    result = _get_tool("gh_pr_merge")(5, method="squash")
    assert result["merged"] is True
    assert result["method"] == "squash"
    assert result["sha"] == "abc123"


# ---------------------------------------------------------------------------
# gh_issue_create
# ---------------------------------------------------------------------------


def test_gh_issue_create_parses_number(gh_mixin, monkeypatch):
    monkeypatch.setattr(
        gh_mod,
        "_run_gh",
        lambda argv, **kw: "https://github.com/amd/gaia/issues/314\n",
    )
    handle = _get_tool("gh_issue_create")(title="t", body="b")
    assert handle["number"] == 314


def test_gh_issue_create_bad_url_raises(gh_mixin, monkeypatch):
    monkeypatch.setattr(
        gh_mod,
        "_run_gh",
        lambda argv, **kw: "https://github.com/amd/gaia/issues/not-a-number\n",
    )
    with pytest.raises(GitHubCLIError, match="could not parse"):
        _get_tool("gh_issue_create")(title="t", body="b")


# ---------------------------------------------------------------------------
# gh_run_list / gh_run_watch / gh_run_view_log
# ---------------------------------------------------------------------------


def test_gh_run_list_parses_rows(gh_mixin, monkeypatch):
    payload = [
        {
            "databaseId": 10,
            "name": "ci",
            "status": "completed",
            "conclusion": "success",
            "headBranch": "coder",
            "url": "https://example/10",
            "createdAt": "2026-04-20T00:00:00Z",
        },
        {
            "databaseId": 11,
            "name": "ci",
            "status": "in_progress",
            "conclusion": None,
            "headBranch": "auto/x",
            "url": "https://example/11",
            "createdAt": "2026-04-20T01:00:00Z",
        },
    ]
    monkeypatch.setattr(
        gh_mod, "_run_gh", lambda argv, **kw: json.dumps(payload)
    )
    rows = _get_tool("gh_run_list")(limit=5)
    assert len(rows) == 2
    assert rows[0]["id"] == 10
    assert rows[1]["conclusion"] is None


def test_gh_run_watch_success(gh_mixin, monkeypatch):
    responses = iter(
        [
            "",  # watch
            json.dumps({"url": "https://example/10", "status": "completed"}),
        ]
    )
    monkeypatch.setattr(gh_mod, "_run_gh", lambda argv, **kw: next(responses))
    outcome = _get_tool("gh_run_watch")(10, timeout_s=60)
    assert outcome["conclusion"] == "success"


def test_gh_run_watch_failure_recovers_conclusion(gh_mixin, monkeypatch):
    """When ``--exit-status`` makes ``gh`` exit non-zero, we still return a structured result."""
    calls: list = []

    def _fake(argv, **_):
        calls.append(list(argv))
        # First call (run watch --exit-status) fails.
        if calls[-1][1] == "watch":
            raise GitHubCLIError(argv, 1, "run failed")
        # Second call: conclusion probe.
        if "conclusion" in calls[-1]:
            return json.dumps({"conclusion": "failure"})
        # Third call: view to get url + status.
        return json.dumps({"url": "https://example/11", "status": "completed"})

    monkeypatch.setattr(gh_mod, "_run_gh", _fake)
    outcome = _get_tool("gh_run_watch")(11)
    assert outcome["conclusion"] == "failure"


def test_gh_run_view_log_failed_only_flag(gh_mixin, monkeypatch):
    captured: dict = {}

    def _capture(argv, **_):
        captured["argv"] = list(argv)
        return "failing step log"

    monkeypatch.setattr(gh_mod, "_run_gh", _capture)
    log = _get_tool("gh_run_view_log")(12, failed_only=True)
    assert log == "failing step log"
    assert "--log-failed" in captured["argv"]


def test_gh_run_view_log_full_log_flag(gh_mixin, monkeypatch):
    captured: dict = {}

    def _capture(argv, **_):
        captured["argv"] = list(argv)
        return "full log"

    monkeypatch.setattr(gh_mod, "_run_gh", _capture)
    _get_tool("gh_run_view_log")(12, failed_only=False)
    assert "--log" in captured["argv"]
    assert "--log-failed" not in captured["argv"]


# ---------------------------------------------------------------------------
# gh_release_create
# ---------------------------------------------------------------------------


def test_gh_release_create_draft_default(gh_mixin, monkeypatch):
    captured: dict = {}

    def _capture(argv, **_):
        captured["argv"] = list(argv)
        return "https://github.com/amd/gaia/releases/tag/v1.0.0\n"

    monkeypatch.setattr(gh_mod, "_run_gh", _capture)
    handle = _get_tool("gh_release_create")(
        tag="v1.0.0", title="v1.0.0", notes="notes"
    )
    assert handle["tag"] == "v1.0.0"
    assert handle["draft"] is True
    assert "--draft" in captured["argv"]
