# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Approving fork CI runs executes contributor code on our runners, so the tool
must only approve the exact commit that was reviewed, and never a sensitive one
by accident."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = (
    Path(__file__).resolve().parents[2] / ".claude/skills/contributor-triage/scripts"
)
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "approve_fork_runs", SCRIPTS / "approve_fork_runs.py"
)
afr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(afr)

TEAM = {"kovtcharov"}
SHA = "6d02e4f217d1570a3227878c7ef2e8317d97544d"


def fake_api(pr_state="open", head=SHA, login="outsider", files=("src/gaia/x.py",)):
    def api(gh, path, *extra):
        if path.endswith("/files?per_page=100"):
            return json.dumps([[{"filename": f} for f in files]])
        return json.dumps(
            {
                "state": pr_state,
                "head": {"sha": head},
                "user": {"login": login} if login else None,
            }
        )

    return api


def plan(monkeypatch, sha=SHA[:7], stalled=None, **kw):
    monkeypatch.setattr(afr, "api", fake_api(**kw))
    stalled = {SHA: [11, 12]} if stalled is None else stalled
    return afr.plan_for("gh", "amd/gaia", 5, sha, stalled, TEAM)


def test_plan_collects_runs_for_the_reviewed_commit(monkeypatch):
    p = plan(monkeypatch)
    assert p["runs"] == [11, 12] and p["third_party"] and not p["risky"]


def test_head_moved_since_review_is_refused(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        plan(monkeypatch, head="f" * 40)
    assert "head is now" in str(exc.value)


def test_runs_for_a_different_sha_are_not_approved(monkeypatch):
    assert plan(monkeypatch, stalled={"f" * 40: [99]})["runs"] == []


def test_closed_pr_is_refused(monkeypatch):
    with pytest.raises(SystemExit):
        plan(monkeypatch, pr_state="closed")


def test_maintainer_pr_is_not_third_party(monkeypatch):
    assert not plan(monkeypatch, login="kovtcharov")["third_party"]


def test_deleted_author_is_treated_as_third_party(monkeypatch):
    assert plan(monkeypatch, login=None)["third_party"]


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        ".claude/skills/x/SKILL.md",
        "util/lint.py",
        "scripts/install.sh",
        "setup.py",
        "pyproject.toml",
        "requirements-dev.txt",
        "tests/unit/conftest.py",
        "src/gaia/apps/webui/package.json",
        "package-lock.json",
        "tools/run.ps1",
        "Dockerfile.test",
        ".npmrc",
    ],
)
def test_sensitive_paths_are_flagged(path):
    assert afr.risky_files([path]) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "src/gaia/cli.py",
        "tests/unit/test_cli.py",
        "docs/guide.mdx",
        "src/gaia/apps/webui/src/App.tsx",
    ],
)
def test_ordinary_paths_are_not_flagged(path):
    assert afr.risky_files([path]) == []


def test_reviewed_argument_parsing():
    assert afr.parse_reviewed(["5732@6D02E4F", "7@" + "a" * 40]) == {
        5732: "6d02e4f",
        7: "a" * 40,
    }


@pytest.mark.parametrize(
    "bad", ["5732", "5732@", "5732@zzzzzzz", "5732@abc", "x@abcdef0", "@abcdef0"]
)
def test_malformed_reviewed_argument_is_rejected(bad):
    with pytest.raises(SystemExit):
        afr.parse_reviewed([bad])


def test_short_sha_prefix_cannot_match_a_different_commit(monkeypatch):
    with pytest.raises(SystemExit):
        plan(monkeypatch, sha="6d02e4e")
