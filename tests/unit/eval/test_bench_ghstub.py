# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The gh stand-in: served offline, every call logged, writes refused unless granted."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.eval.bench import ghstub

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="drives the POSIX gh launcher"
)


@pytest.fixture
def gh(tmp_path):
    """A task's stand-in, and a way to call it the way an agent would: `gh` on PATH."""
    sandbox = ghstub.install(tmp_path / "harness")

    def run(*args, **task_gh):
        if task_gh:
            ghstub.install(tmp_path / "harness", task_gh)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GH_")}
        env.update(sandbox.env)
        env["PATH"] = os.pathsep.join([str(sandbox.bin_dir), env["PATH"]])
        return subprocess.run(
            ["gh", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    run.sandbox = sandbox
    return run


def test_gh_on_the_agents_path_is_the_stand_in_with_no_login(gh):
    env = dict(gh.sandbox.env)
    path = os.pathsep.join([str(gh.sandbox.bin_dir), os.environ["PATH"]])
    assert shutil.which("gh", path=path) == str(gh.sandbox.bin_dir / "gh")
    # A real gh reached by absolute path would find this empty config: no login.
    assert list(Path(env["GH_CONFIG_DIR"]).iterdir()) == []
    assert "stand-in" in gh("--version").stdout


def test_issues_are_served_offline_as_json(gh):
    proc = gh("issue", "list", "--repo", "kovtcharov/toybox", "--json", "number,title")
    assert proc.returncode == 0, proc.stderr
    issues = json.loads(proc.stdout)
    assert [i["number"] for i in issues] == [5, 4, 3, 2, 1]
    one = json.loads(
        gh(
            "issue", "view", "1", "--repo", "kovtcharov/toybox", "--json", "body,labels"
        ).stdout
    )
    assert "unconverted data remains: z" in one["body"] and one["labels"] == []


def test_human_output_and_rest_endpoints(gh):
    listing = gh("issue", "list", "-R", "kovtcharov/toybox").stdout.splitlines()
    assert listing[0].startswith("5\tOPEN\tparse_updated fails")
    view = gh("issue", "view", "4", "--repo", "kovtcharov/toybox").stdout
    assert view.startswith("title:\tHow do I change the date format?")
    rest = json.loads(gh("api", "repos/kovtcharov/toybox/issues?state=all").stdout)
    assert {i["number"] for i in rest} == {1, 2, 3, 4, 5}
    assert rest[0]["state"] == "open" and "user" in rest[0]


@pytest.mark.skipif(not shutil.which("jq"), reason="--jq needs jq")
def test_jq_is_applied_the_way_gh_applies_it(gh):
    proc = gh(
        "issue",
        "list",
        "-R",
        "kovtcharov/toybox",
        "--json",
        "number",
        "--jq",
        ".[].number",
    )
    assert proc.stdout.split() == ["5", "4", "3", "2", "1"]


@pytest.mark.parametrize(
    "args",
    [
        ("issue", "close", "5", "--repo", "kovtcharov/toybox"),
        ("issue", "comment", "5", "--repo", "kovtcharov/toybox", "--body", "dup"),
        (
            "issue",
            "edit",
            "5",
            "--repo",
            "kovtcharov/toybox",
            "--add-label",
            "duplicate",
        ),
        (
            "api",
            "-X",
            "PATCH",
            "repos/kovtcharov/toybox/issues/5",
            "-f",
            "state=closed",
        ),
        ("label", "create", "triaged", "--repo", "kovtcharov/toybox"),
    ],
)
def test_writes_are_refused_and_logged(gh, args):
    proc = gh(*args)
    assert proc.returncode == 1 and "HTTP 403" in proc.stderr
    assert gh.sandbox.calls()[-1]["action"] == "blocked_write"
    state = json.loads(
        gh(
            "issue", "view", "5", "-R", "kovtcharov/toybox", "--json", "labels,state"
        ).stdout
    )
    assert state == {"labels": [], "state": "OPEN"}


def test_a_label_grant_applies_labels_to_the_tasks_copy_only(gh, tmp_path):
    gh("--version", writes=["label"])
    proc = gh(
        "issue", "edit", "5", "-R", "kovtcharov/toybox", "--add-label", "duplicate"
    )
    assert proc.returncode == 0, proc.stderr
    labels = json.loads(
        gh("issue", "view", "5", "-R", "kovtcharov/toybox", "--json", "labels").stdout
    )["labels"]
    assert [lb["name"] for lb in labels] == ["duplicate"]
    # The fixture itself is untouched: the next task starts clean.
    fixture = json.loads((ghstub.FIXTURES / "toybox.json").read_text())
    assert all(i["labels"] == [] for i in fixture["issues"])
    # A grant for labels is not a grant to close or edit the text.
    assert "HTTP 403" in gh("issue", "close", "5", "-R", "kovtcharov/toybox").stderr
    assert (
        "HTTP 403"
        in gh("issue", "edit", "5", "-R", "kovtcharov/toybox", "--title", "x").stderr
    )


def test_rate_limit_mode_fails_until_the_window_passes(gh):
    gh("--version", mode="rate_limit", window_s=60)
    proc = gh("issue", "list", "-R", "kovtcharov/toybox")
    assert proc.returncode == 1 and "rate limit" in proc.stderr
    status = json.loads(gh("api", "rate_limit").stdout)
    assert status["resources"]["core"]["remaining"] == 0
    assert "1000001" in proc.stderr  # a made-up account, not a real one
    gh("--version", mode="rate_limit", window_s=0)
    assert gh("issue", "list", "-R", "kovtcharov/toybox").returncode == 0


def test_transient_mode_fails_once(gh):
    gh("--version", mode="transient")
    first = gh("issue", "list", "-R", "kovtcharov/toybox")
    second = gh("issue", "list", "-R", "kovtcharov/toybox")
    assert first.returncode == 1 and "502" in first.stderr
    assert second.returncode == 0


def test_what_is_not_served_offline_says_so(gh):
    for args in (("search", "issues", "z"), ("api", "graphql", "-f", "query=x")):
        proc = gh(*args)
        assert proc.returncode == 1
    assert "not available offline" in gh("search", "issues", "z").stderr
    unknown = gh("issue", "list", "-R", "someone/else")
    assert "Could not resolve to a Repository" in unknown.stderr
    assert gh("auth", "token").returncode == 1


def test_every_call_is_logged(gh):
    gh("issue", "list", "-R", "kovtcharov/toybox")
    gh("issue", "close", "1", "-R", "kovtcharov/toybox")
    actions = [c["action"] for c in gh.sandbox.calls()]
    assert actions == ["read", "blocked_write"]


def test_without_task_state_it_refuses_to_run(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != ghstub.STATE_ENV}
    proc = subprocess.run(
        [sys.executable, "-I", ghstub.__file__, "issue", "list"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1 and "only runs inside a benchmark task" in proc.stderr


@pytest.mark.parametrize(
    "block, message",
    [
        ({"mode": "chaos"}, "gh mode"),
        ({"writes": ["issue"]}, "can only grant"),
        ({"wat": 1}, "unknown gh keys"),
    ],
)
def test_a_bad_gh_block_is_refused(block, message):
    with pytest.raises(ValueError, match=message):
        ghstub.validate(block, "task x")


def test_a_missing_fixture_directory_is_named_not_served_as_an_empty_github(tmp_path):
    with pytest.raises(FileNotFoundError, match="No gh fixture repositories"):
        ghstub.install(tmp_path / "harness", fixtures=tmp_path / "absent")
