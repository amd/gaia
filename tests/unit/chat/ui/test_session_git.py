# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Commit and Create PR from the session panel's Changes (#5278).

Runs against a real git repository. Nothing is committed or pushed without
``confirmed``, only the chat's own files are staged, and a PR is refused
while the chat's internet is off.
"""

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from gaia.agents.base import file_snapshots as fs
from gaia.ui import session_environment, session_git
from gaia.ui.server import create_app

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def home(tmp_path, monkeypatch):
    gaia_home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", gaia_home / "config.json")
    return gaia_home


@pytest.fixture(autouse=True)
def in_process_agent(monkeypatch):
    """No agent registry in unit tests; chats run in-process, as with a source install."""
    monkeypatch.setattr(session_environment, "relayed", lambda _session: False)


@pytest.fixture
def client(home):
    return TestClient(create_app(db_path=":memory:"))


@pytest.fixture
def session_id(client):
    return client.post("/api/sessions", json={}).json()["id"]


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "feature")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "mine.txt").write_text("user work\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def chat_edit(client, session_id, repo):
    """One turn in which the chat edited app.py and created util.py."""
    db = client.app.state.db
    db.add_message(session_id, "user", "Add a helper for the totals")
    with fs.recording_turn(session_id) as rec:
        fs.before_change(repo / "app.py", "edit_file")
        (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        fs.before_change(repo / "util.py", "write_file")
        (repo / "util.py").write_text("def total(): ...\n", encoding="utf-8")
    msg_id = db.add_message(session_id, "assistant", "Done.")
    rec.link_message(msg_id)
    # The user's own unrelated edit must never be swept into the commit.
    (repo / "mine.txt").write_text("user work, changed\n", encoding="utf-8")
    return rec


def test_describe_lists_the_repo_with_a_drafted_message(
    client, session_id, repo, chat_edit
):
    body = client.get(f"/api/sessions/{session_id}/changes/git").json()
    (r,) = body["repos"]
    assert r["root"] == str(repo)
    assert r["branch"] == "feature"
    assert sorted(r["files"]) == ["app.py", "util.py"]
    # The first status line starts with a space; it must not eat the name.
    assert sorted(r["uncommitted"]) == ["app.py", "util.py"]
    assert r["draft"]["title"] == "Add a helper for the totals"
    assert "- app.py" in r["draft"]["body"] and "- util.py" in r["draft"]["body"]


def test_commit_without_confirmation_runs_nothing(client, session_id, repo, chat_edit):
    before = _git(repo, "rev-parse", "HEAD")
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "msg"},
    )
    assert resp.status_code == 400
    assert "not confirmed" in resp.json()["detail"]
    assert _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_confirmed_commit_stages_only_the_chats_files(
    client, session_id, repo, chat_edit
):
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={
            "root": str(repo),
            "message": "Add totals helper\n\nBody",
            "confirmed": True,
        },
    )
    assert resp.status_code == 200, resp.text
    committed = _git(repo, "show", "--name-only", "--format=%s", "HEAD").splitlines()
    assert committed[0] == "Add totals helper"
    assert sorted(committed[2:]) == ["app.py", "util.py"]
    # The user's own edit is untouched and still uncommitted.
    assert "mine.txt" in _git(repo, "status", "--porcelain")


def test_pr_without_confirmation_runs_nothing(
    client, session_id, repo, chat_edit, monkeypatch
):
    ran = []
    monkeypatch.setattr(
        session_git, "_run", lambda argv, *a, **k: ran.append(argv) or ""
    )
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/pr",
        json={"root": str(repo), "title": "t", "body": "b"},
    )
    assert resp.status_code == 400
    assert ran == []


def test_pr_is_refused_while_internet_is_off(
    client, session_id, repo, chat_edit, monkeypatch
):
    client.put(f"/api/sessions/{session_id}/environment/internet", json={"on": False})
    ran = []
    monkeypatch.setattr(
        session_git, "_run", lambda argv, *a, **k: ran.append(argv) or ""
    )
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/pr",
        json={"root": str(repo), "title": "t", "body": "b", "confirmed": True},
    )
    assert resp.status_code == 400
    assert "Internet is off" in resp.json()["detail"]
    assert ran == []


def test_confirmed_pr_pushes_without_force_then_opens_it(
    client, session_id, repo, chat_edit, monkeypatch
):
    calls = []

    def fake_run(argv, cwd, stdin=None, **_):
        calls.append(argv)
        joined = " ".join(argv)
        if joined.endswith("symbolic-ref --short -q HEAD"):
            return "feature"
        if argv[1:] == ["remote"]:
            return "origin"
        if "refs/remotes/origin/HEAD" in joined:
            return "origin/main"
        if "pr" in argv and "create" in argv:
            return "https://github.com/o/r/pull/7"
        return ""

    monkeypatch.setattr(session_git, "_run", fake_run)
    monkeypatch.setattr(session_git.shutil, "which", lambda name: f"/usr/bin/{name}")
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/pr",
        json={"root": str(repo), "title": "Totals", "body": "b", "confirmed": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["url"] == "https://github.com/o/r/pull/7"
    push = next(c for c in calls if "push" in c)
    assert not any(a.startswith(("-f", "--force", "+")) for a in push[2:])


def test_pr_from_the_default_branch_is_refused(
    client, session_id, repo, chat_edit, monkeypatch
):
    _git(repo, "branch", "-m", "main")
    real_which = shutil.which
    monkeypatch.setattr(
        session_git.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "gh" else real_which(name),
    )
    _git(repo, "remote", "add", "origin", "https://example.invalid/r.git")
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/pr",
        json={"root": str(repo), "title": "t", "confirmed": True},
    )
    assert resp.status_code == 400
    assert "Switch to a new branch" in resp.json()["detail"]


def test_a_repo_in_a_removed_folder_is_refused(client, session_id, repo, chat_edit):
    client.post(
        f"/api/sessions/{session_id}/environment/folders/remove",
        json={"path": str(repo)},
    )
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "m", "confirmed": True},
    )
    assert resp.status_code == 400
    assert "removed from this chat" in resp.json()["detail"]


def _remove_folder(client, session_id, path):
    resp = client.post(
        f"/api/sessions/{session_id}/environment/folders/remove",
        json={"path": str(path)},
    )
    assert resp.status_code == 200, resp.text


def test_a_removed_subfolder_inside_the_repo_stays_out_of_the_commit(
    client, session_id, repo
):
    _chat_writes(client, session_id, repo / "report.md", repo / "private" / "keys.md")
    _remove_folder(client, session_id, repo / "private")

    (r,) = client.get(f"/api/sessions/{session_id}/changes/git").json()["repos"]
    assert r["files"] == ["report.md"]
    assert "keys.md" not in r["draft"]["body"]

    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "m", "confirmed": True},
    )
    assert resp.status_code == 200, resp.text
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert committed == ["report.md"]
    assert "private/" in _git(repo, "status", "--porcelain", "--untracked-files=all")


def test_a_removed_folder_sharing_a_name_prefix_filters_nothing_else(
    client, session_id, repo
):
    _chat_writes(client, session_id, repo / "private" / "notes.md")
    (repo / "priv").mkdir()
    _remove_folder(client, session_id, repo / "priv")
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "m", "confirmed": True},
    )
    assert resp.status_code == 200, resp.text
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert committed == ["private/notes.md"]


def test_only_removed_folder_changes_commit_nothing(client, session_id, repo):
    before = _git(repo, "rev-parse", "HEAD")
    _chat_writes(client, session_id, repo / "private" / "keys.md")
    _remove_folder(client, session_id, repo / "private")
    assert client.get(f"/api/sessions/{session_id}/changes/git").json()["repos"] == []
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "m", "confirmed": True},
    )
    assert resp.status_code == 400
    assert "changed no files" in resp.json()["detail"]
    assert _git(repo, "rev-parse", "HEAD") == before


@pytest.fixture
def new_repo(tmp_path):
    """A folder just `git init`-ed: no commits, so HEAD names no commit yet."""
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "start")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def test_a_repo_with_no_commits_is_listed_and_commits(client, session_id, new_repo):
    _chat_writes(client, session_id, new_repo / "app.py", new_repo / "README.md")
    resp = client.get(f"/api/sessions/{session_id}/changes/git")
    assert resp.status_code == 200, resp.text
    (r,) = resp.json()["repos"]
    assert r["branch"] == "start"
    assert sorted(r["files"]) == ["README.md", "app.py"]

    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(new_repo), "message": "First", "confirmed": True},
    )
    assert resp.status_code == 200, resp.text
    committed = _git(new_repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(committed) == ["README.md", "app.py"]


def test_a_detached_head_is_still_reported_as_detached(
    client, session_id, repo, chat_edit
):
    _git(repo, "checkout", "-q", "--detach")
    (r,) = client.get(f"/api/sessions/{session_id}/changes/git").json()["repos"]
    assert r["branch"] == "HEAD"


def test_commit_refused_while_a_turn_runs(client, session_id, repo, chat_edit):
    import asyncio

    lock = client.app.state.session_locks.setdefault(session_id, asyncio.Lock())
    lock._locked = True
    try:
        resp = client.post(
            f"/api/sessions/{session_id}/changes/git/commit",
            json={"root": str(repo), "message": "m", "confirmed": True},
        )
    finally:
        lock._locked = False
    assert resp.status_code == 409


def test_chat_with_no_changes_has_no_repos(client, session_id):
    body = client.get(f"/api/sessions/{session_id}/changes/git").json()
    assert body["repos"] == []


def test_odd_file_names_are_names_not_patterns(client, session_id, repo):
    db = client.app.state.db
    (repo / "notes.md").write_text("theirs", encoding="utf-8")
    (repo / "secret.env").write_text("TOKEN=x", encoding="utf-8")
    odd = repo / "note[s].md"
    with fs.recording_turn(session_id) as rec:
        fs.before_change(odd, "write_file")
        odd.write_text("chat", encoding="utf-8")
    rec.link_message(db.add_message(session_id, "assistant", "ok"))
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "m", "confirmed": True},
    )
    assert resp.status_code == 200, resp.text
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert committed == ["note[s].md"]
    status = _git(repo, "status", "--porcelain")
    assert "notes.md" in status and "secret.env" in status


def _chat_writes(client, session_id, *paths):
    db = client.app.state.db
    db.add_message(session_id, "user", "Write the report")
    with fs.recording_turn(session_id) as rec:
        for path in paths:
            fs.before_change(path, "write_file")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("chat\n", encoding="utf-8")
    rec.link_message(db.add_message(session_id, "assistant", "ok"))


def _ignore(repo, *patterns):
    (repo / ".gitignore").write_text("\n".join(patterns) + "\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")


def test_ignored_files_are_left_out_of_the_commit(client, session_id, repo):
    _ignore(repo, "*.csv", "build/")
    _chat_writes(
        client,
        session_id,
        repo / "report.md",
        repo / "summary.csv",
        repo / "build" / "out.bin",
        repo / "deep" / "data.csv",
    )
    (r,) = client.get(f"/api/sessions/{session_id}/changes/git").json()["repos"]
    assert r["files"] == ["report.md"]
    assert sorted(r["ignored"]) == ["build/out.bin", "deep/data.csv", "summary.csv"]
    assert r["uncommitted"] == ["report.md"]
    assert "csv" not in r["draft"]["body"] and "out.bin" not in r["draft"]["body"]

    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "m", "confirmed": True},
    )
    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["ignored"]) == [
        "build/out.bin",
        "deep/data.csv",
        "summary.csv",
    ]
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert committed == ["report.md"]
    # Never force-added, and still on disk.
    assert _git(repo, "ls-files", "summary.csv", "build", "deep") == ""
    assert (repo / "summary.csv").exists()


def test_a_tracked_file_matching_an_ignore_pattern_is_still_committed(
    client, session_id, repo
):
    (repo / "config.csv").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "config.csv")
    _git(repo, "commit", "-q", "-m", "tracked csv")
    _ignore(repo, "*.csv")
    _chat_writes(client, session_id, repo / "config.csv")
    (r,) = client.get(f"/api/sessions/{session_id}/changes/git").json()["repos"]
    assert r["files"] == ["config.csv"] and r["ignored"] == []
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "m", "confirmed": True},
    )
    assert resp.status_code == 200, resp.text
    assert _git(repo, "show", "--name-only", "--format=", "HEAD") == "config.csv"


def test_only_ignored_changes_commit_nothing_and_say_why(client, session_id, repo):
    _ignore(repo, "*.log")
    _chat_writes(client, session_id, repo / "run.log")
    before = _git(repo, "rev-parse", "HEAD")
    (r,) = client.get(f"/api/sessions/{session_id}/changes/git").json()["repos"]
    assert r["files"] == [] and r["uncommitted"] == [] and r["ignored"] == ["run.log"]
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "m", "confirmed": True},
    )
    assert resp.status_code == 400
    assert "ignored by git" in resp.json()["detail"]
    assert _git(repo, "rev-parse", "HEAD") == before


def test_ignore_check_takes_names_literally(client, session_id, repo):
    """Names with spaces and brackets are checked and committed as written."""
    _ignore(repo, "secret*")
    _chat_writes(client, session_id, repo / "a b.txt", repo / "[x].md")
    resp = client.post(
        f"/api/sessions/{session_id}/changes/git/commit",
        json={"root": str(repo), "message": "m", "confirmed": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ignored"] == []
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(committed) == ["[x].md", "a b.txt"]
