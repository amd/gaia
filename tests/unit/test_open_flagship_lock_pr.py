# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A flagship release ends by opening the PR that pins its hashes on main.

release_agent_gaia.yml regenerated binaries.lock.json with real hashes but only
shipped it in the npm tarball, so the committed lock stayed stale and the
installer build broke after every flagship release (#3949).
util/open_flagship_lock_pr.py closes that gap; these tests drive it against a
scripted git/gh so every branch -- including re-runs and a token that cannot
open PRs -- is exercised offline.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release_agent_gaia.yml"

_spec = importlib.util.spec_from_file_location(
    "open_flagship_lock_pr", REPO_ROOT / "util" / "open_flagship_lock_pr.py"
)
pin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pin)

VERSION = "0.3.1"
REPO = "amd/gaia"
BRANCH = f"release/flagship-lock-v{VERSION}"
PLACEHOLDER = "PENDING-replace-with-real-sha256"
SIDECAR = ("win32-x64", "darwin-arm64", "darwin-x64", "linux-x64")


def _lock(version=VERSION, sha="a", tui_sha="b", sidecar_version=None):
    sv = sidecar_version or version
    return {
        "schemaVersion": "3.0",
        "agentVersion": version,
        "components": {
            "sidecar": {
                "componentVersion": sv,
                "baseUrl": f"https://hub.amd-gaia.ai/agents/gaia/{sv}",
                "platforms": {
                    p: {
                        "filename": f"gaia-agent-{p}",
                        "executable": "gaia-agent",
                        "sha256": sha * 64 if len(sha) == 1 else sha,
                        "size": 100,
                    }
                    for p in SIDECAR
                },
            },
            "tui": {
                "componentVersion": "0.25.1",
                "baseUrl": "https://hub.amd-gaia.ai/agents/terminal-hub/0.25.1",
                "platforms": {
                    "linux-x64": {
                        "filename": "gaia-linux-x64",
                        "executable": "gaia-tui",
                        "sha256": tui_sha * 64 if len(tui_sha) == 1 else tui_sha,
                        "size": 50,
                    }
                },
            },
        },
    }


def _text(lock: dict) -> str:
    return json.dumps(lock, indent=2) + "\n"


class FakeRepo:
    """Scripted git + gh. Records every command it was asked to run."""

    def __init__(
        self,
        main_lock: dict,
        *,
        branch_lock: dict | None = None,
        open_prs: list | None = None,
        push_err: str | None = None,
        create_err: str | None = None,
    ):
        self.main_lock = main_lock
        self.branch_lock = branch_lock
        self.open_prs = open_prs or []
        self.push_err = push_err
        self.create_err = create_err
        self.calls: list[list[str]] = []
        self.pushed = False
        self.created = False

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)

        def ok(out=""):
            return subprocess.CompletedProcess(cmd, 0, out, "")

        def err(msg, rc=1):
            return subprocess.CompletedProcess(cmd, rc, "", msg)

        if cmd[:2] == ["git", "fetch"]:
            if BRANCH in cmd[-1] and self.branch_lock is None:
                return err(f"fatal: couldn't find remote ref {BRANCH}", 128)
            return ok()
        if cmd[:2] == ["git", "show"]:
            ref = cmd[2]
            if ref.startswith("origin/main:"):
                return ok(_text(self.main_lock))
            if self.branch_lock is None:
                return err("fatal: invalid object name", 128)
            return ok(_text(self.branch_lock))
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
            return ok(json.dumps(self.open_prs))
        if cmd[:2] == ["git", "push"]:
            if self.push_err:
                return err(self.push_err, 128)
            self.pushed = True
            return ok()
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "create":
            if self.create_err:
                return err(self.create_err)
            self.created = True
            return ok("https://github.com/amd/gaia/pull/9999\n")
        if cmd[0] == "git":  # checkout / add / commit
            return ok()
        raise AssertionError(f"unexpected command {cmd}")


@pytest.fixture
def workdir(tmp_path):
    (tmp_path / pin.LOCK).parent.mkdir(parents=True)
    return tmp_path


def _run(repo: FakeRepo, workdir: Path, lock: dict | None = None, version=VERSION):
    return pin.open_lock_pr(
        _text(lock if lock is not None else _lock()),
        version,
        REPO,
        run_url="https://github.com/amd/gaia/actions/runs/1",
        run=repo,
        workdir=workdir,
    )


# -- the normal path ---------------------------------------------------------


def test_first_run_commits_the_lock_and_opens_one_pr(workdir):
    repo = FakeRepo(_lock(sha="0"))  # main: version bumped, previous release's hashes
    out = _run(repo, workdir)

    assert repo.pushed and repo.created
    assert "pull/9999" in out
    assert json.loads((workdir / pin.LOCK).read_text()) == _lock()
    create = next(c for c in repo.calls if c[:3] == ["gh", "pr", "create"])
    assert create[create.index("--head") + 1] == BRANCH
    assert create[create.index("--base") + 1] == "main"
    # A bot PR notifies nobody on its own; it must land in a maintainer's queue.
    assert create[create.index("--reviewer") + 1] == "kovtcharov-amd,itomek-amd"
    assert create[create.index("--label") + 1] == "release"
    commit = next(c for c in repo.calls if "commit" in c)
    assert f"user.email={pin.BOT_EMAIL}" in commit
    assert not any("claude" in part.lower() for part in commit)


def test_a_placeholder_lock_on_main_is_replaced(workdir):
    repo = FakeRepo(_lock(sha=PLACEHOLDER, tui_sha=PLACEHOLDER))
    _run(repo, workdir)
    assert repo.pushed and repo.created


def test_the_pr_body_has_a_test_plan_and_no_branding(workdir, monkeypatch):
    bodies = []
    real_unlink = pin.os.unlink

    def capture(path):
        bodies.append(Path(path).read_text(encoding="utf-8"))
        real_unlink(path)

    monkeypatch.setattr(pin.os, "unlink", capture)
    _run(FakeRepo(_lock(sha="0")), workdir)
    (body,) = bodies
    assert "## Test plan" in body
    assert "check_flagship_release.py" in body
    assert "claude" not in body.lower() and "generated with" not in body.lower()


# -- idempotence -------------------------------------------------------------


def test_hashes_already_on_main_is_a_no_op(workdir):
    repo = FakeRepo(_lock())
    out = _run(repo, workdir)
    assert "no PR needed" in out
    assert not repo.pushed and not repo.created
    assert not any(c[:2] == ["gh", "pr"] for c in repo.calls)


def test_whitespace_only_differences_count_as_already_on_main(workdir):
    repo = FakeRepo(_lock())
    out = pin.open_lock_pr(
        json.dumps(_lock()), VERSION, REPO, run=repo, workdir=workdir
    )
    assert "no PR needed" in out and not repo.pushed


def test_rerun_with_the_pr_open_and_current_opens_nothing(workdir):
    pr = {"number": 42, "url": "https://github.com/amd/gaia/pull/42"}
    repo = FakeRepo(_lock(sha="0"), branch_lock=_lock(), open_prs=[pr])
    out = _run(repo, workdir)
    assert "#42" in out and "already carries" in out
    assert not repo.pushed and not repo.created


def test_rerun_with_the_pr_open_but_stale_refreshes_its_branch_only(workdir):
    pr = {"number": 42, "url": "https://github.com/amd/gaia/pull/42"}
    repo = FakeRepo(_lock(sha="0"), branch_lock=_lock(sha="c"), open_prs=[pr])
    out = _run(repo, workdir)
    assert "#42" in out and "refreshed" in out
    assert repo.pushed and not repo.created


def test_rerun_after_pr_creation_failed_opens_it_without_repushing(workdir):
    # The previous attempt pushed the branch and died before `gh pr create`.
    repo = FakeRepo(_lock(sha="0"), branch_lock=_lock())
    _run(repo, workdir)
    assert repo.created and not repo.pushed


# -- refusals ----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        _lock(sha=PLACEHOLDER),
        _lock(tui_sha=PLACEHOLDER),
        _lock(sha="A"),  # uppercase hex is not what the generator writes
        _lock(sidecar_version="0.3.0"),
        _lock(version="0.3.0"),  # agentVersion != the release
    ],
    ids=[
        "sidecar-placeholder",
        "tui-placeholder",
        "not-lowercase",
        "sidecar-0.3.0",
        "agent-0.3.0",
    ],
)
def test_a_lock_that_is_not_this_release_is_never_committed(workdir, bad):
    repo = FakeRepo(_lock(sha="0"))
    with pytest.raises(pin.LockPRError) as e:
        _run(repo, workdir, lock=bad)
    assert "refusing to commit" in str(e.value)
    assert repo.calls == []  # stopped before touching git or GitHub


def test_a_zero_size_entry_is_refused(workdir):
    bad = _lock()
    bad["components"]["sidecar"]["platforms"]["linux-x64"]["size"] = 0
    with pytest.raises(pin.LockPRError, match="sidecar/linux-x64"):
        _run(FakeRepo(_lock(sha="0")), workdir, lock=bad)


def test_main_on_a_different_flagship_version_is_refused(workdir):
    # Re-running 0.3.1's release after main moved to 0.3.2 must not roll it back.
    repo = FakeRepo(_lock(version="0.3.2", sha="d"))
    with pytest.raises(pin.LockPRError) as e:
        _run(repo, workdir)
    assert "0.3.2" in str(e.value) and VERSION in str(e.value)
    assert not repo.pushed and not repo.created


def test_invalid_json_is_refused(workdir):
    with pytest.raises(pin.LockPRError, match="not valid JSON"):
        pin.open_lock_pr("{nope", VERSION, REPO, run=FakeRepo(_lock()), workdir=workdir)


# -- a token without permission fails loudly ---------------------------------


def test_a_token_that_cannot_open_prs_names_the_permission(workdir):
    repo = FakeRepo(
        _lock(sha="0"),
        create_err="pull request create failed: GraphQL: GitHub Actions is not "
        "permitted to create or approve pull requests (createPullRequest)",
    )
    with pytest.raises(pin.LockPRError) as e:
        _run(repo, workdir)
    msg = str(e.value)
    assert "pull-requests: write" in msg
    assert "Allow GitHub Actions to create and approve pull requests" in msg
    assert f"--head {BRANCH}" in msg  # the manual unblock


def test_a_token_that_cannot_push_names_contents_write(workdir):
    repo = FakeRepo(
        _lock(sha="0"),
        push_err="remote: Permission to amd/gaia.git denied to github-actions[bot].",
    )
    with pytest.raises(pin.LockPRError) as e:
        _run(repo, workdir)
    assert "contents: write" in str(e.value)
    assert not repo.created


def test_main_reports_failure_as_an_annotation_and_exit_1(workdir, capsys, monkeypatch):
    lock_file = workdir / "regenerated.json"
    lock_file.write_text(_text(_lock(sha=PLACEHOLDER)), encoding="utf-8")
    assert (
        pin.main(["--lock", str(lock_file), "--version", VERSION, "--repo", REPO]) == 1
    )
    out = capsys.readouterr().out
    assert "::error::" in out and "installer builds" in out


# -- the release workflow runs it, safely ------------------------------------


def _jobs():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_the_release_opens_the_lock_pr_after_publishing():
    job = _jobs()["pin-lock"]
    assert "publish" in job["needs"]
    assert "!cancelled()" in job["if"]
    assert "needs.publish.result == 'success'" in job["if"]
    assert job["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert job["concurrency"]["cancel-in-progress"] is False
    assert "environment" not in job  # the write token never sits behind npm OIDC
    run = next(
        s["run"] for s in job["steps"] if "open_flagship_lock_pr.py" in s.get("run", "")
    )
    assert "--version" in run and "--lock" in run
    checkout = next(s for s in job["steps"] if "actions/checkout" in s.get("uses", ""))
    assert checkout["with"]["ref"] == "main"


def test_publish_hands_the_verified_lock_to_the_pin_job():
    steps = _jobs()["publish"]["steps"]
    names = [s.get("name", "") for s in steps]
    upload = next(
        i for i, s in enumerate(steps) if "upload-artifact" in s.get("uses", "")
    )
    # Only a lock that passed fetch-verify AND reached npm is handed over.
    assert upload > names.index(
        "Verify every published object is fetchable + hash-matches"
    )
    assert upload > names.index("Publish to npm (OIDC trusted publishing, provenance)")
    assert steps[upload]["with"]["path"].endswith("binaries.lock.json")
    assert "contents" not in _jobs()["publish"]["permissions"] or (
        _jobs()["publish"]["permissions"]["contents"] == "read"
    )
