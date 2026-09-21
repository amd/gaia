# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for the hub-agent release drift check (#3895).

Two of these encode bugs that were actually made while building the check, and
would silently re-report ~5 weeks of drift where there are ~2 if reintroduced:
the tag glob must not require a leading ``v``, and versions must sort by parsed
semver rather than lexically.

The end-to-end tests build a real throwaway git repo rather than mocking
``git``, because the thing under test is precisely whether the git queries are
spelled correctly — a mock of ``git log`` proves the function was called, not
that the revision range is right.
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

UTIL_DIR = Path(__file__).resolve().parents[2] / "util"
sys.path.insert(0, str(UTIL_DIR))

from check_agent_release_drift import (  # noqa: E402
    DEFAULT_MAX_AGE_DAYS,
    Finding,
    check_agent,
    commits_since,
    discover_agents,
    format_report,
    latest_release,
    main,
    manifest_version,
    parse_git_date,
    parse_version,
    run_check,
    tag_version,
)

# Transcribed from `git tag --list 'agent-pkg-*'` on main. Hardcoded rather than
# read from the repo so the test still asserts the real-world shape in a shallow
# CI checkout that fetched no tags.
REAL_TAGS = [
    "agent-pkg-email-0.6.0",
    "agent-pkg-email-v0.1.0",
    "agent-pkg-email-v0.2.0",
    "agent-pkg-email-v0.2.1",
    "agent-pkg-email-v0.2.2",
    "agent-pkg-email-v0.2.3",
    "agent-pkg-email-v0.2.4",
    "agent-pkg-email-v0.2.5",
    "agent-pkg-email-v0.3.0",
    "agent-pkg-email-v0.4.0",
    "agent-pkg-email-v0.5.0",
    "agent-pkg-gaia-v0.1.1",
    "agent-pkg-gaia-v0.2.0",
]

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# parse_version
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0.6.0", (0, 6, 0)),
        ("1.0.0", (1, 0, 0)),
        ("0.10.0", (0, 10, 0)),
        ("10.20.30", (10, 20, 30)),
        ("  0.6.0  ", (0, 6, 0)),
        ("1.2.3-rc1", (1, 2, 3)),
        ("1.2.3+build5", (1, 2, 3)),
    ],
)
def test_parse_version_accepts(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    "text", ["", "1.2", "1.2.3.4", "v1.2.3", "abc", "1.2.x", "-1.2.3"]
)
def test_parse_version_rejects(text):
    assert parse_version(text) is None


# --------------------------------------------------------------------------
# tag_version — the optional leading `v`
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("agent-pkg-email-v0.5.0", "0.5.0"),
        ("agent-pkg-email-0.6.0", "0.6.0"),
        ("agent-pkg-gaia-v0.2.0", "0.2.0"),
    ],
)
def test_tag_version_accepts_both_spellings(tag, expected):
    agent_id = tag.split("-")[2]
    assert tag_version(tag, agent_id) == expected


@pytest.mark.parametrize(
    "tag",
    [
        "agent-pkg-gaia-v0.2.0",  # another agent
        "v0.6.0",  # release of the repo, not an agent
        "agent-pkg-email-latest",  # not a version
        "agent-pkg-email-",  # empty remainder
        "agent-pkg-emailer-v0.1.0",  # prefix is not a word-boundary match
        "",
    ],
)
def test_tag_version_rejects_non_email_releases(tag):
    assert tag_version(tag, "email") is None


# --------------------------------------------------------------------------
# latest_release
# --------------------------------------------------------------------------


def test_latest_release_finds_the_v_less_tag():
    """The regression that motivated the test: 0.6.0 carries no `v`.

    A checker globbing ``agent-pkg-email-v*`` anchors on v0.5.0 and reports a
    month of drift that does not exist.
    """
    assert latest_release(REAL_TAGS, "email") == ("0.6.0", "agent-pkg-email-0.6.0")


def test_latest_release_sorts_by_semver_not_lexically():
    tags = ["agent-pkg-email-v0.9.0", "agent-pkg-email-v0.10.0"]
    assert latest_release(tags, "email")[0] == "0.10.0"


def test_latest_release_sorts_across_v_and_v_less():
    tags = ["agent-pkg-email-0.6.0", "agent-pkg-email-v0.7.0"]
    assert latest_release(tags, "email") == ("0.7.0", "agent-pkg-email-v0.7.0")


def test_latest_release_per_agent():
    assert latest_release(REAL_TAGS, "gaia") == ("0.2.0", "agent-pkg-gaia-v0.2.0")


@pytest.mark.parametrize("agent_id", ["chat", "hello-world", "word-count"])
def test_latest_release_none_for_unreleased_agents(agent_id):
    assert latest_release(REAL_TAGS, agent_id) is None


def test_latest_release_none_for_empty_tag_list():
    assert latest_release([], "email") is None


def test_latest_release_ignores_malformed_versions():
    tags = ["agent-pkg-email-v0.6.0", "agent-pkg-email-nightly", "agent-pkg-email-v1.2"]
    assert latest_release(tags, "email") == ("0.6.0", "agent-pkg-email-v0.6.0")


# --------------------------------------------------------------------------
# manifest_version / discover_agents
# --------------------------------------------------------------------------


def _write_manifest(agents_dir: Path, agent_id: str, body: str) -> None:
    manifest = agents_dir / agent_id / "python" / "gaia-agent.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    "line,expected",
    [
        ("version: 0.7.0", "0.7.0"),
        ('version: "0.7.0"', "0.7.0"),
        ("version: '0.7.0'", "0.7.0"),
        ("version:    0.7.0   # bumped", "0.7.0"),
    ],
)
def test_manifest_version_parses(tmp_path, line, expected):
    _write_manifest(tmp_path, "email", f"id: email\n{line}\nname: Email\n")
    assert manifest_version("email", tmp_path) == expected


def test_manifest_version_ignores_nested_version_keys(tmp_path):
    _write_manifest(
        tmp_path, "email", "id: email\ndeps:\n  version: 9.9.9\nversion: 0.7.0\n"
    )
    assert manifest_version("email", tmp_path) == "0.7.0"


def test_manifest_version_none_when_absent(tmp_path):
    _write_manifest(tmp_path, "email", "id: email\nname: Email\n")
    assert manifest_version("email", tmp_path) is None


def test_manifest_version_none_when_no_manifest(tmp_path):
    assert manifest_version("nope", tmp_path) is None


def test_discover_agents_requires_a_manifest(tmp_path):
    _write_manifest(tmp_path, "email", "version: 0.7.0\n")
    _write_manifest(tmp_path, "gaia", "version: 0.2.0\n")
    (tmp_path / "npm-only" / "npm").mkdir(parents=True)
    assert discover_agents(tmp_path) == ["email", "gaia"]


def test_discover_agents_empty_when_dir_missing(tmp_path):
    assert discover_agents(tmp_path / "absent") == []


# --------------------------------------------------------------------------
# A real throwaway git repo
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _commit(repo: Path, rel_path: str, text: str, days_ago: int, subject: str) -> None:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    when = (NOW - timedelta(days=days_ago)).isoformat()
    _git(repo, "add", "-A")
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-m", subject],
        check=True,
        capture_output=True,
        text=True,
        # Inherit the real environment and override only what must be pinned.
        # A hand-built env would have to name a PATH that finds git on Linux,
        # macOS and Windows alike, which is three ways to be wrong; HOME plus an
        # explicit identity is all the isolation from the host gitconfig this
        # needs, and the fixed dates are what the age assertions rest on.
        env={
            **os.environ,
            "HOME": str(repo),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
        },
    )


@pytest.fixture
def repo(tmp_path):
    """A repo with one released agent, `email`, at 0.6.0 (tagged without a `v`)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _write_manifest(root / "hub" / "agents", "email", "id: email\nversion: 0.6.0\n")
    _commit(root, "hub/agents/email/python/app.py", "v1\n", 40, "feat: initial")
    _git(root, "tag", "agent-pkg-email-0.6.0")
    return root


def test_ok_when_nothing_merged_since_the_tag(repo):
    finding = check_agent(
        "email", ["agent-pkg-email-0.6.0"], 7, NOW, repo, repo / "hub" / "agents"
    )
    assert finding.status == "ok"
    assert not finding.is_error


def test_pending_below_the_sla(repo):
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 3, "fix: something")
    finding = check_agent(
        "email", ["agent-pkg-email-0.6.0"], 7, NOW, repo, repo / "hub" / "agents"
    )
    assert finding.status == "pending"
    assert not finding.is_error
    assert "1 commit(s)" in finding.message
    assert "3d old" in finding.message


def test_drift_above_the_sla(repo):
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 30, "fix: something")
    finding = check_agent(
        "email", ["agent-pkg-email-0.6.0"], 7, NOW, repo, repo / "hub" / "agents"
    )
    assert finding.status == "drift"
    assert finding.is_error
    assert "30d old" in finding.message
    assert "agent-pkg-email-v" in finding.message  # names the fix


def test_age_is_measured_from_the_oldest_commit_not_the_newest(repo):
    """A trickle of fresh commits must not reset the clock on stale work."""
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 30, "fix: old work")
    _commit(repo, "hub/agents/email/python/app.py", "v3\n", 1, "chore: fresh churn")
    finding = check_agent(
        "email", ["agent-pkg-email-0.6.0"], 7, NOW, repo, repo / "hub" / "agents"
    )
    assert finding.status == "drift"
    assert "2 commit(s)" in finding.message
    assert "30d old" in finding.message


def test_commits_outside_the_agent_package_are_ignored(repo):
    _commit(repo, "src/gaia/cli.py", "unrelated\n", 30, "feat: elsewhere")
    finding = check_agent(
        "email", ["agent-pkg-email-0.6.0"], 7, NOW, repo, repo / "hub" / "agents"
    )
    assert finding.status == "ok"


def test_another_agents_commits_are_ignored(repo):
    _write_manifest(repo / "hub" / "agents", "gaia", "id: gaia\nversion: 0.2.0\n")
    _commit(repo, "hub/agents/gaia/python/app.py", "x\n", 30, "feat: gaia")
    finding = check_agent(
        "email", ["agent-pkg-email-0.6.0"], 7, NOW, repo, repo / "hub" / "agents"
    )
    assert finding.status == "ok"


def test_never_released_agent_is_not_gated(repo):
    _write_manifest(repo / "hub" / "agents", "chat", "id: chat\nversion: 0.1.0\n")
    _commit(repo, "hub/agents/chat/python/app.py", "x\n", 90, "feat: chat")
    finding = check_agent(
        "chat", ["agent-pkg-email-0.6.0"], 7, NOW, repo, repo / "hub" / "agents"
    )
    assert finding.status == "never-released"
    assert not finding.is_error


def test_manifest_ahead_of_the_newest_tag_is_called_out(repo):
    """The git-visible shadow of the check's blind spot.

    A bump that merged without a release tag behind it is exactly how an
    untagged publish arises, so the finding says so rather than only counting
    commits.
    """
    _write_manifest(repo / "hub" / "agents", "email", "id: email\nversion: 0.7.0\n")
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 30, "release: 0.7.0")
    finding = check_agent(
        "email", ["agent-pkg-email-0.6.0"], 7, NOW, repo, repo / "hub" / "agents"
    )
    assert "manifest says 0.7.0" in finding.message
    assert "newest tag is 0.6.0" in finding.message


def test_no_mismatch_note_when_manifest_matches(repo):
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 30, "fix: something")
    finding = check_agent(
        "email", ["agent-pkg-email-0.6.0"], 7, NOW, repo, repo / "hub" / "agents"
    )
    assert "manifest says" not in finding.message


def test_tagging_a_release_turns_the_check_green(repo):
    """End to end: drift is reported, then the cure actually cures it."""
    _write_manifest(repo / "hub" / "agents", "email", "id: email\nversion: 0.7.0\n")
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 30, "fix: something")
    agents_dir = repo / "hub" / "agents"

    findings, report = run_check(7, NOW, repo, agents_dir)
    assert [f.status for f in findings] == ["drift"]
    assert "DRIFT" in report

    _git(repo, "tag", "agent-pkg-email-v0.7.0")

    findings, report = run_check(7, NOW, repo, agents_dir)
    assert [f.status for f in findings] == ["ok"]
    assert "agent-pkg-email-v0.7.0" in report


@pytest.mark.parametrize(
    "text",
    [
        "2026-08-22T12:00:00Z",  # git's spelling for a UTC commit
        "2026-08-22T12:00:00+00:00",
        "2026-08-22T05:00:00-07:00",
        "  2026-08-22T12:00:00Z  ",
    ],
)
def test_parse_git_date_accepts_every_spelling_git_emits(text):
    """The `Z` case is a real crash, not a style preference.

    Python 3.10's `fromisoformat` rejects a trailing `Z`, and git emits exactly
    that for a commit made in UTC — which is every CI runner. On 3.10 the check
    died before reporting anything. A dev box on a non-UTC clock never sees it.
    """
    assert parse_git_date(text) == datetime.fromisoformat(
        text.strip().replace("Z", "+00:00")
    )


def test_commits_since_reads_a_z_suffixed_timestamp(monkeypatch, tmp_path):
    """Pins the wiring, not just the parser.

    Stubbed rather than driven through a real commit on purpose: whether git
    spells a UTC commit `Z` or `+00:00` depends on the git version, so a real
    commit exercises this on a CI runner and silently does not on a dev box
    running an older git or a non-UTC clock. That gap is exactly how the crash
    reached CI in the first place.
    """
    monkeypatch.setattr(
        "check_agent_release_drift._git",
        lambda args, root: "abc123\x1f2026-08-22T12:00:00Z\x1ffix: something\n",
    )
    commits = commits_since("some-tag", "hub/agents/email/", tmp_path)
    assert len(commits) == 1
    assert commits[0].when == datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def test_commits_since_is_oldest_first(repo):
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 30, "first")
    _commit(repo, "hub/agents/email/python/app.py", "v3\n", 10, "second")
    commits = commits_since("agent-pkg-email-0.6.0", "hub/agents/email/", repo)
    assert [c.subject for c in commits] == ["first", "second"]


def test_commits_since_raises_on_an_unknown_ref(repo):
    with pytest.raises(RuntimeError, match="git log"):
        commits_since("agent-pkg-email-v9.9.9", "hub/agents/email/", repo)


# --------------------------------------------------------------------------
# Reporting and exit code
# --------------------------------------------------------------------------


def test_format_report_labels_each_status():
    findings = [
        Finding("email", "drift", "stale"),
        Finding("gaia", "pending", "recent"),
        Finding("chat", "never-released", "none"),
        Finding("word-count", "ok", "fine"),
    ]
    report = format_report(findings)
    assert "[   DRIFT] email" in report
    assert "[ pending] gaia" in report
    assert "[ skipped] chat" in report
    assert "[      ok] word-count" in report


def test_only_drift_is_an_error():
    assert Finding("a", "drift", "").is_error
    for status in ("ok", "pending", "never-released"):
        assert not Finding("a", status, "").is_error


def test_default_sla_is_a_week():
    assert DEFAULT_MAX_AGE_DAYS == 7


def test_main_exits_nonzero_on_drift_and_annotates(monkeypatch, capsys, repo):
    agents_dir = repo / "hub" / "agents"
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 30, "fix: something")
    monkeypatch.setattr("check_agent_release_drift.REPO_ROOT", repo)
    monkeypatch.setattr("check_agent_release_drift.AGENTS_DIR", agents_dir)

    assert main(["--github-annotations"]) == 1
    out = capsys.readouterr().out
    assert "1 agent(s) past the 7d publish SLA." in out

    # An annotation must be one line — Actions truncates at the first newline,
    # which would drop the "cut a release" instruction that makes it actionable.
    annotation = next(line for line in out.splitlines() if line.startswith("::error::"))
    assert annotation.startswith("::error::email release drift")
    assert "SLA is 7d" in annotation


def test_main_exits_zero_when_within_sla(monkeypatch, capsys, repo):
    agents_dir = repo / "hub" / "agents"
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 2, "fix: something")
    monkeypatch.setattr("check_agent_release_drift.REPO_ROOT", repo)
    monkeypatch.setattr("check_agent_release_drift.AGENTS_DIR", agents_dir)

    assert main([]) == 0
    out = capsys.readouterr().out
    assert "0 agent(s) past" in out
    assert "::error::" not in out


def test_main_honours_a_custom_sla(monkeypatch, repo):
    agents_dir = repo / "hub" / "agents"
    _commit(repo, "hub/agents/email/python/app.py", "v2\n", 10, "fix: something")
    monkeypatch.setattr("check_agent_release_drift.REPO_ROOT", repo)
    monkeypatch.setattr("check_agent_release_drift.AGENTS_DIR", agents_dir)

    assert main(["--max-age-days", "30"]) == 0
    assert main(["--max-age-days", "3"]) == 1
