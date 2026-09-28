# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TheRock tasks: the agent's checkout cannot contain the fix; the grader's diff is the fix.

A small local repository stands in for the public one, with the same shape:
history, a base commit, and a later fix on top of it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import therock

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path):
    """``older`` -> ``base`` -> ``fix``, on one branch, like a merged pull request."""
    repo = tmp_path / "public"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "matrix.py").write_text("ARCHS = ['gfx90a', 'gfx942']\n")
    older = _commit(repo, "older")
    (repo / "README").write_text("TheRock-like\n")
    base = _commit(repo, "base")
    (repo / "matrix.py").write_text("ARCHS = ['gfx942']\nPOSTSUBMIT = ['gfx90a']\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "matrix_test.py").write_text("def test_it(): pass\n")
    fix = _commit(repo, "fix: skip gfx90a on PRs")
    return {"url": repo.as_uri(), "older": older, "base": base, "fix": fix}


def test_the_checkout_stops_at_the_base_with_no_way_back(upstream, tmp_path):
    work = tmp_path / "work" / "therock"
    therock.checkout(
        work, upstream["url"], upstream["base"], upstream["fix"], tmp_path / "root"
    )
    assert _git(work, "rev-parse", "HEAD") == upstream["base"]
    assert _git(work, "remote") == ""
    # The fix is on no ref and in no object: `git log --all` cannot find it.
    assert upstream["fix"] not in _git(work, "log", "--all", "--format=%H")
    assert subprocess.run(
        ["git", "cat-file", "-e", upstream["fix"]], cwd=work, capture_output=True
    ).returncode
    # History behind the base is there, as a developer would have it.
    assert upstream["older"] in _git(work, "log", "--format=%H")
    assert "POSTSUBMIT" not in (work / "matrix.py").read_text()


def test_a_second_checkout_reuses_the_cache_and_the_cache_holds_no_fix(
    upstream, tmp_path
):
    root = tmp_path / "root"
    for name in ("a", "b"):
        therock.checkout(
            tmp_path / name, upstream["url"], upstream["base"], upstream["fix"], root
        )
    (cache,) = (root / "cache").glob("therock-*.git")
    assert upstream["fix"] not in _git(cache, "log", "--all", "--format=%H")


def test_the_reference_is_merge_parent_to_merge(upstream):
    diff = therock.reference_diff(upstream["url"], upstream["base"], upstream["fix"])
    assert "+POSTSUBMIT = ['gfx90a']" in diff
    assert therock.touched_files(diff) == ["matrix.py", "tests/matrix_test.py"]


def test_a_pinned_base_that_is_not_the_fixs_parent_is_refused(upstream):
    with pytest.raises(therock.TheRockError, match="not the pinned base"):
        therock.reference_diff(upstream["url"], upstream["older"], upstream["fix"])


def test_the_agents_diff_includes_new_files(upstream, tmp_path):
    work = tmp_path / "w"
    therock.checkout(work, upstream["url"], upstream["base"], upstream["fix"], tmp_path)
    (work / "matrix.py").write_text("ARCHS = []\n")
    (work / "new_test.py").write_text("def test_new(): pass\n")
    diff = therock.agent_diff(work)
    assert therock.touched_files(diff) == ["matrix.py", "new_test.py"]


def _task(reference_files):
    return ft.Task(
        id="tr",
        check="diff",
        prompt="fix it",
        max_steps=5,
        therock={
            "base": "a" * 40,
            "merge": "b" * 40,
            "reference_files": reference_files,
        },
    )


@pytest.mark.parametrize(
    "diff, passed, why",
    [
        ("", False, "nothing was changed"),
        ("+++ b/elsewhere.py\n", False, "none of the files the fix needs"),
        (
            "+++ b/matrix.py\n",
            None,
            "changed 1 of 2 reference files; the judge decides",
        ),
    ],
)
def test_the_gate_leaves_a_fix_in_the_right_place_to_the_judge(diff, passed, why):
    task = _task(["matrix.py", "tests/matrix_test.py"])
    result = ft.score(task, Path("."), Path("."), diff)
    assert result[0] is passed and why in result[1]


def test_the_judges_verdict_decides_a_gated_fix():
    task = _task(["matrix.py"])
    entry = {
        "passed": None,
        "why": "",
        "judge": {"solves_problem": True, "approach": "different but valid"},
    }
    ft._apply_verdict(entry, task)
    assert entry["passed"] is True and "different but valid" in entry["why"]
    entry = {
        "passed": False,
        "why": "nothing was changed",
        "judge": {"solves_problem": True},
    }
    ft._apply_verdict(entry, task)
    assert entry["passed"] is False, "the judge overrode the mechanical gate"


def test_the_therock_suite_pins_full_shas_and_true_parents():
    tasks = ft.load_suite("therock")
    assert [t.id for t in tasks] == ["tr-8319", "tr-7998", "tr-8319-cb", "tr-7998-cb"]
    by_id = {t.id: t for t in tasks}
    # #7998's parent is 800180b7b, not GitHub's base.sha for the pull request.
    assert by_id["tr-7998"].therock["base"].startswith("800180b7b")
    for task in tasks:
        assert len(task.therock["base"]) == len(task.therock["merge"]) == 40
    for plain, closed in (("tr-8319", "tr-8319-cb"), ("tr-7998", "tr-7998-cb")):
        assert by_id[closed].therock == by_id[plain].therock
        assert by_id[closed].closed_book == "instructed"
        assert by_id[closed].prompt.startswith(by_id[plain].prompt)
        assert "Do not use the internet" in by_id[closed].prompt


@pytest.mark.parametrize(
    "block, message",
    [
        ({"base": "abc", "merge": "b" * 40, "reference_files": ["x"]}, "full 40-hex"),
        (
            {"base": "a" * 40, "merge": "b" * 40, "reference_files": []},
            "reference_files",
        ),
        (
            {"base": "a" * 40, "merge": "b" * 40, "reference_files": ["x"], "z": 1},
            "unknown",
        ),
    ],
)
def test_a_bad_therock_block_is_refused(block, message):
    with pytest.raises(ValueError, match=message):
        therock.validate(block, "task x")
