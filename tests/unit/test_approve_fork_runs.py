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

SCRIPTS = Path(__file__).resolve().parents[2] / "util"
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
    with pytest.raises(afr.NotApprovable) as exc:
        plan(monkeypatch, head="f" * 40)
    assert "head is now" in str(exc.value)


def test_runs_for_a_different_sha_are_not_approved(monkeypatch):
    assert plan(monkeypatch, stalled={"f" * 40: [99]})["runs"] == []


def test_closed_pr_is_refused(monkeypatch):
    with pytest.raises(afr.NotApprovable):
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
        "hub/agents/gaia/python/pyproject.toml",
        "docs/sub/Dockerfile",
        ".devcontainer/Dockerfile",
        "src/gaia/installer/lemonade_installer.py",
        "src/gaia/apps/webui/.npmrc",
        "tools/deep/er/site.pth",
        "hub/agents/example/npm/package.json",
        "src/gaia/utils/util/helper.py",
        "pkg/uv.lock",
        "pkg/sitecustomize.py",
        "tests/usercustomize.py",
        ".githooks/pre-push",
        ".pre-commit-config.yaml",
        "tools/action.yml",
        "GNUmakefile",
        "build/rules.mk",
        "noxfile.py",
        "pytest.ini",
        "go.mod",
        ".gitmodules",
        "web/postinstall.js",
        ".GitHub/workflows/ci.yml",
        "docs/makefile",
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
        "src/gaia/utilities/helper.py",
        "docs/installers-overview.mdx",
        "src/gaia/scripting/x.py",
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
    with pytest.raises(afr.NotApprovable):
        plan(monkeypatch, sha="6d02e4e")


class Posts:
    """Records every approve POST the tool would make."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


def run_main(monkeypatch, capsys, argv, **api_kw):
    posts = Posts()
    monkeypatch.setattr(afr.ts, "gh_binary", lambda: "gh")
    monkeypatch.setattr(afr.ts, "maintainers", lambda *a: TEAM)
    monkeypatch.setattr(afr.ts, "awaiting_approval", lambda *a: {SHA: [11, 12]})
    monkeypatch.setattr(afr, "api", fake_api(**api_kw))
    monkeypatch.setattr(afr.subprocess, "run", posts)
    monkeypatch.setattr(sys, "argv", ["approve_fork_runs.py", *argv])
    code = 0
    try:
        afr.main()
    except SystemExit as exc:
        code = exc.code or 0
    return code, capsys.readouterr().out, posts


def test_dry_run_posts_nothing(monkeypatch, capsys):
    code, out, posts = run_main(monkeypatch, capsys, ["--reviewed", f"5@{SHA[:7]}"])
    assert code == 0 and "WOULD APPROVE" in out and posts.calls == []


def test_apply_posts_one_request_per_stalled_run(monkeypatch, capsys):
    code, out, posts = run_main(
        monkeypatch, capsys, ["--reviewed", f"5@{SHA[:7]}", "--apply"]
    )
    assert code == 0 and len(posts.calls) == 2
    assert all("/approve" in c[-1] for c in posts.calls)


def test_sensitive_path_is_refused_even_with_apply(monkeypatch, capsys):
    code, out, posts = run_main(
        monkeypatch,
        capsys,
        ["--reviewed", f"5@{SHA[:7]}", "--apply"],
        files=(".github/workflows/ci.yml",),
    )
    assert code == 1 and "REFUSE" in out and posts.calls == []


def test_sensitive_path_needs_the_explicit_override(monkeypatch, capsys):
    code, out, posts = run_main(
        monkeypatch,
        capsys,
        ["--reviewed", f"5@{SHA[:7]}", "--apply", "--include-risky", "5"],
        files=(".github/workflows/ci.yml",),
    )
    assert code == 0 and len(posts.calls) == 2


def test_override_for_another_pr_does_not_unlock_this_one(monkeypatch, capsys):
    code, out, posts = run_main(
        monkeypatch,
        capsys,
        ["--reviewed", f"5@{SHA[:7]}", "--apply", "--include-risky", "6"],
        files=("setup.py",),
    )
    assert code == 1 and posts.calls == []


def test_maintainer_pr_is_skipped_and_posts_nothing(monkeypatch, capsys):
    code, out, posts = run_main(
        monkeypatch,
        capsys,
        ["--reviewed", f"5@{SHA[:7]}", "--apply"],
        login="kovtcharov",
    )
    assert "SKIP" in out and posts.calls == []


def test_moved_head_aborts_before_any_post(monkeypatch, capsys):
    code, out, posts = run_main(
        monkeypatch,
        capsys,
        ["--reviewed", f"5@{SHA[:7]}", "--apply"],
        head="f" * 40,
    )
    assert code != 0 and posts.calls == []


def test_a_failed_post_is_reported_and_fails_the_run(monkeypatch, capsys):
    def failing(cmd, **kw):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "HTTP 500"})()

    monkeypatch.setattr(afr.ts, "gh_binary", lambda: "gh")
    monkeypatch.setattr(afr.ts, "maintainers", lambda *a: TEAM)
    monkeypatch.setattr(afr.ts, "awaiting_approval", lambda *a: {SHA: [11]})
    monkeypatch.setattr(afr, "api", fake_api())
    monkeypatch.setattr(afr.subprocess, "run", failing)
    monkeypatch.setattr(sys, "argv", ["x", "--reviewed", f"5@{SHA[:7]}", "--apply"])
    with pytest.raises(SystemExit) as exc:
        afr.main()
    assert exc.value.code == 1
    assert "HTTP 500" in capsys.readouterr().out


def test_a_moved_head_does_not_abort_the_rest_of_the_batch(monkeypatch, capsys):
    other = "a" * 40
    posts = Posts()

    def api(gh, path, *extra):
        if path.endswith("/files?per_page=100"):
            return json.dumps([[{"filename": "src/gaia/x.py"}]])
        n = path.rsplit("/", 1)[-1]
        head = other if n == "6" else SHA
        return json.dumps(
            {"state": "open", "head": {"sha": head}, "user": {"login": "o"}}
        )

    monkeypatch.setattr(afr.ts, "gh_binary", lambda: "gh")
    monkeypatch.setattr(afr.ts, "maintainers", lambda *a: TEAM)
    monkeypatch.setattr(
        afr.ts, "awaiting_approval", lambda *a: {SHA: [11], other: [22]}
    )
    monkeypatch.setattr(afr, "api", api)
    monkeypatch.setattr(afr.subprocess, "run", posts)
    monkeypatch.setattr(
        sys,
        "argv",
        ["x", "--reviewed", f"5@{SHA[:7]}", "--reviewed", f"6@{SHA[:7]}", "--apply"],
    )
    with pytest.raises(SystemExit) as exc:
        afr.main()
    out = capsys.readouterr().out
    assert exc.value.code == 1
    assert "REFUSE #6" in out and "APPROVED #5" in out
    assert len(posts.calls) == 1 and "11" in posts.calls[0][-1]


def test_a_file_renamed_out_of_a_sensitive_path_is_still_reported(monkeypatch):
    def api(gh, path, *extra):
        if path.endswith("/files?per_page=100"):
            return json.dumps(
                [
                    [
                        {
                            "filename": "docs/x.md",
                            "previous_filename": ".github/workflows/ci.yml",
                        }
                    ]
                ]
            )
        return json.dumps(
            {"state": "open", "head": {"sha": SHA}, "user": {"login": "o"}}
        )

    monkeypatch.setattr(afr, "api", api)
    plan = afr.plan_for("gh", "amd/gaia", 5, SHA[:7], {SHA: [1]}, TEAM)
    assert ".github/workflows/ci.yml" in plan["risky"]
