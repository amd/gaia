# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The leadership summary quotes these counts, so they must match the git history."""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "skills"
    / "gaia-release"
    / "release_stats.py"
)
spec = importlib.util.spec_from_file_location("release_stats", SCRIPT)
release_stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_stats)

MAINTAINER = ("Kalin Ovtcharov", "kalin@example.com")
OUTSIDER = ("Ada Contributor", "ada@example.com")
BOT = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")


def _git(repo, *args, author=MAINTAINER, date="2026-01-01T12:00:00+00:00"):
    name, email = author
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(repo / ".no-global-gitconfig"),
    }
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def _commit(repo, path, subject, author=MAINTAINER, date="2026-01-01T12:00:00+00:00"):
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f.read_text() + "x\n" if f.exists() else "x\n", encoding="utf-8")
    _git(repo, "add", path, author=author, date=date)
    _git(repo, "commit", "-q", "-m", subject, author=author, date=date)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(
        tmp_path, "tests/unit/test_old.py", "init", date="2026-01-01T12:00:00+00:00"
    )
    _commit(tmp_path, "hub/skills/old/SKILL.md", "feat: old skill (#1)")
    _git(tmp_path, "tag", "v1.0.0")

    later = "2026-01-11T12:00:00+00:00"
    _commit(tmp_path, "src/gaia/a.py", "fix(llm): reload model (#10)", date=later)
    _commit(tmp_path, "tests/unit/test_a.py", "test: cover reload (#11)", date=later)
    _commit(
        tmp_path,
        "hub/skills/new/SKILL.md",
        "feat(skills): new skill (#12)",
        author=OUTSIDER,
        date=later,
    )
    _commit(tmp_path, "hub/skills/new/notes.md", "skill notes", date=later)
    _commit(tmp_path, "docs/a.mdx", "chore(deps): bump x (#13)", author=BOT, date=later)
    _commit(tmp_path, "tests/helpers.py", "Update helpers (#14)", date=later)
    _git(tmp_path, "tag", "v1.1.0", date=later)
    return tmp_path


def test_counts_match_history(repo):
    stats = release_stats.collect(
        "v1.0.0", "v1.1.0", ["kovtcharov", "Kalin Ovtcharov"], cwd=repo
    )

    assert stats["days"] == 10
    assert stats["prs"] == 5
    assert stats["types"] == {
        "fix": 1,
        "feat": 1,
        "test": 1,
        "docs": 0,
        "ci": 0,
        "perf": 0,
        "refactor": 0,
        "other": 2,
    }
    assert stats["contributors"] == 2
    assert stats["outside_contributors"] == 1
    assert stats["top_authors"] == [("Kalin Ovtcharov", 4), ("Ada Contributor", 1)]
    assert stats["diff_total"]["files"] == 6
    assert stats["diff_areas"]["src/gaia"]["files"] == 1
    assert stats["diff_areas"]["hub"]["files"] == 2
    assert stats["diff_areas"]["tests"]["files"] == 2
    assert stats["trees"]["v1.0.0"] == {"test_files": 1, "skills": 1}
    assert stats["trees"]["v1.1.0"] == {"test_files": 2, "skills": 2}


def test_main_without_github_prints_markdown_and_no_eval_score(
    repo, monkeypatch, capsys
):
    monkeypatch.chdir(repo)

    assert release_stats.main(["v1.0.0", "v1.1.0", "--no-github"]) == 0

    out = capsys.readouterr().out
    assert "- PRs merged: 5" in out
    assert "1 beyond the maintainers" in out
    assert "## GitHub" not in out
    assert "Quote it from the release eval run" in out


def test_missing_ref_fails_with_actionable_message(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)

    assert release_stats.main(["v1.0.0", "v9.9.9", "--no-github"]) == 1

    err = capsys.readouterr().err
    assert "ref 'v9.9.9' does not exist" in err
    assert "git fetch origin --tags" in err


def test_missing_gh_fails_unless_no_github(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    monkeypatch.setattr(release_stats.shutil, "which", lambda _name: None)

    assert release_stats.main(["v1.0.0", "v1.1.0"]) == 1

    assert "--no-github" in capsys.readouterr().err
