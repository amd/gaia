# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The release notes quote these counts, so they must match the git history."""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "util" / "release_stats.py"
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


def test_main_without_github_prints_bullet_first_and_no_eval_results(
    repo, monkeypatch, capsys
):
    monkeypatch.chdir(repo)

    assert release_stats.main(["v1.0.0", "v1.1.0", "--no-github"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0] == "- 2 contributors merged 5 PRs since v1.0.0."
    assert "- PRs merged: 5" in out
    assert "1 beyond the maintainers" in out
    assert "## GitHub" not in out
    assert "eval" not in out.lower()


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


# ----------------------------------------------------------------------
# One person, many git identities
# ----------------------------------------------------------------------

MAINTAINER_WORK = ("kovtcharov-amd", "kalin.ovtcharov@amd.com")
ALIAS_LOGIN = ("ityashag", "97851640+ityashag@users.noreply.github.com")
ALIAS_NAME = ("Yash Agarwal", "97851640+ityashag@users.noreply.github.com")
SAME_NAME_OTHER_EMAIL = ("Ada Contributor", "ada@work.example.com")
STRANGER = ("Bob Stranger", "bob@example.com")
LATER = "2026-01-11T12:00:00+00:00"


@pytest.fixture
def alias_repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "README.md", "init")
    _git(tmp_path, "tag", "v1.0.0")
    for i, author in enumerate(
        [
            MAINTAINER,
            MAINTAINER,
            MAINTAINER_WORK,
            ALIAS_LOGIN,
            ALIAS_NAME,
            OUTSIDER,
            SAME_NAME_OTHER_EMAIL,
            STRANGER,
            BOT,
        ]
    ):
        _commit(tmp_path, f"f{i}.txt", f"fix: change {i} (#{100 + i})", author, LATER)
    _git(tmp_path, "tag", "v1.1.0", date=LATER)
    return tmp_path


def _collect(repo):
    return release_stats.collect(
        "v1.0.0", "v1.1.0", ["kovtcharov", "kovtcharov-amd", "Kalin Ovtcharov"], repo
    )


def test_aliases_of_one_person_count_once(alias_repo):
    stats = _collect(alias_repo)

    # Kalin (2 identities), Yash (2, shared email), Ada (2, shared name), Bob.
    assert stats["contributors"] == 4
    assert stats["outside_contributors"] == 3
    assert stats["top_authors"][0] == ("Kalin Ovtcharov", 3)
    assert dict(stats["top_authors"]) == {
        "Kalin Ovtcharov": 3,
        "Yash Agarwal": 2,
        "Ada Contributor": 2,
        "Bob Stranger": 1,
    }


def test_different_people_are_never_merged(alias_repo):
    stats = release_stats.collect("v1.0.0", "v1.1.0", [], alias_repo)

    # With no maintainer aliases, only shared names/emails merge: the two
    # maintainer identities share neither, so they stay two people.
    assert stats["contributors"] == 5
    assert stats["outside_contributors"] == 5
    names = dict(stats["top_authors"])
    assert names["Bob Stranger"] == 1
    assert names["Ada Contributor"] == 2


def test_mailmap_merges_identities_git_already_maps(alias_repo):
    (alias_repo / ".mailmap").write_text(
        "Bob Stranger <bob@example.com> Ada Contributor <ada@example.com>\n",
        encoding="utf-8",
    )
    _commit(alias_repo, "late.txt", "fix: late (#200)", STRANGER, LATER)
    _git(alias_repo, "tag", "-f", "v1.1.0", date=LATER)

    stats = _collect(alias_repo)

    # .mailmap folds Ada's first address into Bob, and her second address
    # shares her name with nobody left, so: Kalin, Yash, Bob(+Ada@1), Ada@2.
    assert stats["contributors"] == 4
    assert dict(stats["top_authors"])["Bob Stranger"] == 3


def test_bots_are_never_contributors_even_with_a_maintainer_login(alias_repo):
    stats = release_stats.collect(
        "v1.0.0", "v1.1.0", ["dependabot[bot]", "Kalin Ovtcharov"], alias_repo
    )

    assert all("[bot]" not in name for name, _ in stats["top_authors"])


def test_maintainer_match_is_exact_not_substring():
    maintainers = {"kalin ovtcharov", "kovtcharov"}

    assert release_stats._is_maintainer("Kalin Ovtcharov", "x@y.z", maintainers)
    assert release_stats._is_maintainer(
        "someone", "1+kovtcharov@users.noreply.github.com", maintainers
    )
    assert not release_stats._is_maintainer("Kalin", "kalin@y.z", maintainers)
    assert not release_stats._is_maintainer(
        "Not Kalin Ovtcharov", "kovtcharov2@y.z", maintainers
    )


def test_people_ignores_empty_names_and_emails():
    ids = [("", "a@x"), ("", "b@x"), ("Carl", ""), ("Dana", "")]

    roots = release_stats._people(ids, set())

    assert len(set(roots.values())) == 4


def test_bullet_flag_prints_one_line(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)

    assert release_stats.main(["v1.0.0", "v1.1.0", "--no-github", "--bullet"]) == 0

    assert capsys.readouterr().out == "- 2 contributors merged 5 PRs since v1.0.0.\n"


def test_bullet_mentions_closed_issues_when_github_is_read():
    stats = {"contributors": 1234, "prs": 5, "prev": "v1.0.0"}

    assert release_stats.stats_bullet(stats, {"closed": 7, "opened": 9}) == (
        "- 1,234 contributors merged 5 PRs and closed 7 issues since v1.0.0."
    )


def test_a_hung_git_fails_with_an_actionable_error(monkeypatch):
    def hang(*_args, **kwargs):
        assert kwargs.get("timeout"), "every git call must carry a timeout"
        raise release_stats.subprocess.TimeoutExpired("git", kwargs["timeout"])

    monkeypatch.setattr(release_stats.subprocess, "run", hang)

    with pytest.raises(release_stats.ReleaseStatsError, match="did not finish"):
        release_stats._git(["log"])


def test_a_hung_gh_fails_with_an_actionable_error(monkeypatch):
    def hang(*_args, **kwargs):
        assert kwargs.get("timeout"), "every gh call must carry a timeout"
        raise release_stats.subprocess.TimeoutExpired("gh", kwargs["timeout"])

    monkeypatch.setattr(release_stats.subprocess, "run", hang)

    with pytest.raises(release_stats.ReleaseStatsError, match="--no-github"):
        release_stats._gh_json(["repos/amd/gaia"])
