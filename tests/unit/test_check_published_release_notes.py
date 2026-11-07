# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The post-release notes check reads only, and catches the v0.25.4 gap.

claude.yml's release-notes job used to regenerate a final's notes, bump
``version.py`` to the next patch, push to main and rewrite the release body.
The curated notes and the version bump now land in the release PR before the
tag, so that job would have reverted main's version and overwritten them. These
tests pin that it can no longer write anything, and that its replacement fails
when the published Full Changelog count disagrees with git.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_published_release_notes as cprn  # noqa: E402

CLAUDE_YML = REPO_ROOT / ".github" / "workflows" / "claude.yml"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> None:
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        message,
    )


def _body(claimed: int | None, listed: int, tail: str = "## Installation\n") -> str:
    lines = ["# GAIA v0.25.0 Release Notes", "", "## What's New", "", "- **x** - y."]
    lines += ["", "## Full Changelog", ""]
    if claimed is not None:
        lines += [f"**{claimed} commits** since v0.24.1:", ""]
    lines += [f"- `{i:09x}` — fix: thing {i} (#{i})" for i in range(listed)]
    lines += [
        "",
        "Full Changelog: [v0.24.1...v0.25.0](https://x/compare/a...b)",
        "",
        tail,
    ]
    return "\n".join(lines)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    for name in ("v0.24.1", "agent-pkg-gaia-v0.1.1", "v0.25.0-rc1"):
        _commit(tmp_path, name)
        _git(tmp_path, "tag", name)
    (tmp_path / "docs" / "releases").mkdir(parents=True)
    (tmp_path / "docs" / "releases" / "v0.25.0.mdx").write_text("notes\n")
    _git(tmp_path, "add", ".")
    _commit(tmp_path, "docs(release): v0.25.0 notes")
    _commit(tmp_path, "fix: merged after the notes")
    _git(tmp_path, "tag", "v0.25.0")
    return tmp_path


# ── The check ────────────────────────────────────────────────────────────────


def test_the_range_starts_at_the_previous_final_not_an_rc_or_agent_tag(repo):
    assert cprn.previous_final("v0.25.0", repo) == "v0.24.1"
    assert cprn.commit_count("v0.24.1", "v0.25.0", repo) == 4


def test_no_previous_final_is_an_error_not_an_empty_range(tmp_path):
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "first")
    _git(tmp_path, "tag", "v0.1.0")
    with pytest.raises(RuntimeError, match="no final release tag before v0.1.0"):
        cprn.previous_final("v0.1.0", tmp_path)


def test_notes_are_read_from_the_tagged_tree(repo):
    assert cprn.notes_at_tag("v0.25.0", repo)
    assert not cprn.notes_at_tag("v0.24.1", repo)


def test_counts_come_only_from_the_full_changelog_section():
    body = _body(4, 4) + "\n- `abcdef123` — not in the changelog\n**9 commits**\n"
    assert cprn.changelog_counts(body) == (4, 4)


def test_matching_notes_pass():
    assert cprn.check("v0.25.0", "v0.24.1", 4, _body(4, 4), True) == []
    # A changelog that names the count without listing every commit is fine.
    assert cprn.check("v0.25.0", "v0.24.1", 4, _body(4, 0), True) == []


def test_v0_25_4_shape_fails_on_both_the_claim_and_the_list():
    # Published claiming 481 and listing 481; git has 483.
    errors = cprn.check("v0.25.4", "v0.25.3", 483, _body(481, 481), True)
    assert errors == [
        "the published body claims 481 commits; `git log v0.25.3..v0.25.4` has 483.",
        "the published body lists 481 commits; `git log v0.25.3..v0.25.4` has 483.",
    ]


def test_a_right_claim_with_a_short_list_still_fails():
    errors = cprn.check("v0.25.0", "v0.24.1", 160, _body(160, 159), True)
    assert errors == [
        "the published body lists 159 commits; `git log v0.24.1..v0.25.0` has 160."
    ]


def test_missing_notes_and_missing_count_are_both_reported():
    errors = cprn.check("v0.25.0", "v0.24.1", 4, _body(None, 0), False)
    assert len(errors) == 2
    assert "docs/releases/v0.25.0.mdx is not in the tagged tree" in errors[0]
    assert "no `**N commits**` line" in errors[1]


def test_main_end_to_end(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    body = tmp_path / "body.md"
    body.write_text(_body(4, 4), encoding="utf-8")
    assert cprn.main(["--tag", "v0.25.0", "--body-file", str(body)]) == 0

    body.write_text(_body(3, 3), encoding="utf-8")
    capsys.readouterr()
    assert cprn.main(["--tag", "v0.25.0", "--body-file", str(body)]) == 1
    out = capsys.readouterr().out
    # The workflow turns each ::error line into an issue bullet with this sed.
    bullets = re.findall(r"^::error[^:]*::(.*)$", out, re.MULTILINE)
    assert (
        bullets[0]
        == "the published body claims 3 commits; `git log v0.24.1..v0.25.0` has 4."
    )


def test_an_rc_tag_is_rejected(tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("", encoding="utf-8")
    assert cprn.main(["--tag", "v0.25.0-rc1", "--body-file", str(body)]) == 1
    assert "not a final release tag" in capsys.readouterr().out


# ── The workflow cannot write ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(CLAUDE_YML.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def job(workflow) -> dict:
    return workflow["jobs"]["release-notes"]


def _runs(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def test_the_job_is_read_only(job):
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    (checkout,) = [s for s in job["steps"] if "actions/checkout@" in s.get("uses", "")]
    assert checkout["with"]["persist-credentials"] is False
    assert "token" not in checkout["with"]


def test_the_job_never_pushes_bumps_or_edits_the_release(job):
    text = yaml.safe_dump(job)
    for banned in (
        "git push",
        "git commit",
        "gh release edit",
        "gh release create",
        "gh release upload",
        "version.py",
        "docs.json",
        "RELEASE_PAT",
        "claude-code-action",
        "claude-credential",
    ):
        assert banned not in text, f"release-notes job still contains {banned!r}"


def test_the_job_runs_only_after_a_final_publishes(job, workflow):
    on = workflow.get("on", workflow.get(True))
    assert on["workflow_run"]["workflows"] == ["Publish Release"]
    cond = job["if"]
    assert "github.event.workflow_run.conclusion == 'success'" in cond
    assert "github.event.workflow_run.event == 'push'" in cond
    assert "!contains(github.event.workflow_run.head_branch, '-rc')" in cond


def test_the_job_runs_the_checker_against_the_published_body(job):
    run = _runs(job)
    assert 'gh release view "$TAG_NAME"' in run
    assert 'util/check_published_release_notes.py --tag "$TAG_NAME"' in run
    report = next(s for s in job["steps"] if s.get("with", {}).get("mode") == "report")
    assert report["if"] == "failure()"
    assert (
        report["with"]["key"]
        == "release-notes/${{ github.event.workflow_run.head_branch }}"
    )


def test_no_claude_yml_step_pushes_to_main_or_edits_a_release(workflow):
    for job_id, job_def in workflow["jobs"].items():
        for step in job_def.get("steps") or []:
            run = step.get("run", "")
            assert not re.search(
                r"git push\b[^\n]*\b(HEAD:)?(refs/heads/)?main\b", run
            ), f"{job_id} pushes to main"
            assert not re.search(
                r"gh release (edit|create|upload|delete)", run
            ), f"{job_id} edits a release"
    assert "RELEASE_PAT" not in CLAUDE_YML.read_text(encoding="utf-8")
