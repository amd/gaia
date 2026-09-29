# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The everyday suite: the ported GitHub and use-case tasks are sound both ways."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.eval import flagship_tasks as ft
from gaia.eval import task_setups as setups
from gaia.eval.bench import ghstub

EVERYDAY = ft.load_suite("everyday")
TASKS = {t.id: t for t in EVERYDAY}
NEW = (
    "b3-triage",
    "b3-duplicate",
    "b3-issue-to-fix",
    "b3-question",
    "b6-shell-injection",
    "b6-github-labels",
)
REPO = "kovtcharov/toybox"

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX gh launcher, gzip"
)


@pytest.fixture
def prepare(tmp_path):
    made = []

    def _prepare(task):
        workdir, baseline = ft.prepare_workdir(task, tmp_path / task.id)
        made.append(workdir)
        return workdir, baseline

    yield _prepare
    for workdir in made:
        setups.remove_leftovers(workdir)


def _gh(workdir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        env=ft.probe_env(workdir),
        capture_output=True,
        text=True,
        check=False,
    )


def test_everyday_is_the_labs_fourteen_in_the_labs_order():
    assert [t.id for t in EVERYDAY] == [
        "21-qa",
        "11-codereview",
        "01-refactor",
        "02-bugfix",
        "b3-triage",
        "b3-duplicate",
        "b3-issue-to-fix",
        "b3-question",
        "b3-pr-review",
        "b6-write-tests",
        "b6-weekly-report",
        "b6-shell-injection",
        "b6-github-labels",
        "b6-config-env",
    ]


@pytest.mark.parametrize("task_id", [t for t in NEW if TASKS[t].check == "stated"])
def test_a_new_question_is_checked_both_ways(task_id):
    task = TASKS[task_id]
    assert task.must_establish and task.genuine_answer and len(task.wrong_answers) >= 3


def test_the_questions_rest_on_what_the_fixture_repo_really_says():
    repo = json.loads((ghstub.FIXTURES / "toybox.json").read_text())
    issues = {i["number"]: i for i in repo["issues"]}
    assert set(issues) == {1, 2, 3, 4, 5} and all(
        i["state"] == "OPEN" for i in issues.values()
    )
    # b3-duplicate: #1 and #5 are the same lowercase-z defect, #1 first and with a traceback.
    assert "lowercase z" in issues[1]["title"] and "lowercase z" in issues[5]["title"]
    assert (
        issues[1]["createdAt"] < issues[5]["createdAt"]
        and "Traceback" in issues[1]["body"]
    )
    # b3-question: the format really is hard-coded in the fixture project.
    assert "%Y-%m-%d %H:%M:%S" in (ft.FIXTURE / "toybox" / "dates.py").read_text()
    assert all(i["labels"] == [] for i in issues.values())


@posix_only
def test_issue_to_fix_fails_untouched_and_passes_a_fix_with_a_test(prepare):
    task = TASKS["b3-issue-to-fix"]
    workdir, baseline = prepare(task)
    assert not ft.evaluate(task, workdir, baseline)[0]
    dates = workdir / "toybox" / "dates.py"
    dates.write_text(
        dates.read_text().replace('if v.endswith("Z"):', 'if v[-1:] in ("Z", "z"):')
    )
    assert not ft.evaluate(task, workdir, baseline)[
        0
    ], "a fix with no regression test passed"
    tests = workdir / "tests" / "test_dates.py"
    tests.write_text(
        tests.read_text()
        + "\n\ndef test_z():\n    assert parse_updated('2026-01-02 03:04:05z')\n"
    )
    passed, why = ft.evaluate(task, workdir, baseline)
    assert passed, why


@posix_only
@pytest.mark.skipif(not shutil.which("gzip"), reason="the helper shells out to gzip")
def test_shell_injection_is_caught_and_a_safe_fix_passes(prepare, tmp_path):
    task = TASKS["b6-shell-injection"]
    workdir, baseline = prepare(task)
    passed, why = ft.evaluate(task, workdir, baseline)
    assert not passed and "shell commands" in why
    # The probe writes into the project it grades, so the fix gets a fresh copy.
    workdir, baseline = ft.prepare_workdir(task, tmp_path / "fixed")
    (workdir / "toybox" / "archive.py").write_text(
        '"""Archive helpers."""\nimport subprocess\n\n\n'
        "def compress(path):\n"
        '    subprocess.run(["gzip", "-kf", "--", path], check=True)\n'
        '    return path + ".gz"\n'
    )
    passed, why = ft.evaluate(task, workdir, baseline)
    assert passed, why


@posix_only
def test_labels_are_checked_through_the_stand_in(prepare):
    task = TASKS["b6-github-labels"]
    workdir, baseline = prepare(task)
    assert not ft.evaluate(task, workdir, baseline)[0]
    for number, label in (("5", "duplicate"), ("4", "question")):
        proc = _gh(
            workdir, "issue", "edit", number, "--repo", REPO, "--add-label", label
        )
        assert proc.returncode == 0, proc.stderr
    passed, why = ft.evaluate(task, workdir, baseline)
    assert passed, why


@posix_only
@pytest.mark.parametrize(
    "marked, why",
    [
        (["1"], "#5 labels"),
        (["5", "1"], "the original (#1) was marked duplicate"),
    ],
    ids=["the-older-one", "both"],
)
def test_labelling_the_wrong_issue_fails(prepare, marked, why):
    task = TASKS["b6-github-labels"]
    workdir, baseline = prepare(task)
    _gh(workdir, "issue", "edit", "4", "--repo", REPO, "--add-label", "question")
    for number in marked:
        _gh(
            workdir, "issue", "edit", number, "--repo", REPO, "--add-label", "duplicate"
        )
    passed, reason = ft.evaluate(task, workdir, baseline)
    assert not passed and why in reason, reason


@posix_only
def test_closing_an_issue_is_refused_so_it_cannot_pass_by_closing(prepare):
    task = TASKS["b6-github-labels"]
    workdir, _ = prepare(task)
    proc = _gh(workdir, "issue", "close", "5", "--repo", REPO)
    assert proc.returncode == 1 and "HTTP 403" in proc.stderr
    assert ft.gh_sandbox(workdir).calls()[-1]["action"] == "blocked_write"


def test_only_the_labels_task_may_write_and_only_labels():
    writers = {t.id: t.gh for t in EVERYDAY if t.gh.get("writes")}
    assert writers == {"b6-github-labels": {"writes": ["label"]}}


def test_every_task_gets_its_own_gh_stand_in(prepare):
    for task_id in ("02-bugfix", "b3-triage"):
        workdir, _ = prepare(TASKS[task_id])
        gh = ft.gh_sandbox(workdir)
        assert gh.state.is_file() and (gh.bin_dir / "gh").is_file()
        assert workdir.parent / "harness" in gh.state.parents
        assert not str(gh.state).startswith(str(workdir))
        env = ft.probe_env(workdir)
        assert env["PATH"].split(os.pathsep)[0] == str(gh.bin_dir)
