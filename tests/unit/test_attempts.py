# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for ``gaia -p --attempts``: private workspaces, drafts, and cleanup."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gaia import attempts

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def folder(tmp_path):
    """A plain project folder (no git) with a rebuilt dir that must not be copied."""
    root = tmp_path / "proj"
    _write(root / "pkg" / "a.py", "x = 1\n")
    _write(root / "README.md", "hello\n")
    _write(root / "node_modules" / "dep" / "big.js", "//")
    return root


@pytest.fixture
def repo(tmp_path):
    """A git repo on branch main with a remote, uncommitted work and an ignored file."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "core.autocrlf", "false")
    _write(root / "pkg" / "a.py", "x = 1\n")
    _write(root / "gone.txt", "bye\n")
    _write(root / ".gitignore", "build/\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    _git(root, "checkout", "-qb", "feature")
    _git(root, "remote", "add", "origin", "https://example.invalid/r.git")
    _write(root / "pkg" / "a.py", "x = 2  # uncommitted\n")
    _write(root / "new.txt", "untracked\n")
    (root / "gone.txt").unlink()
    _write(root / "build" / "out.bin", "ignored\n")
    return root


@pytest.fixture
def scratch(tmp_path):
    d = tmp_path / "scratch"
    d.mkdir()
    return d


# --- git workspaces ----------------------------------------------------------


@needs_git
def test_git_workspace_holds_the_folders_current_files_and_the_real_refs(repo, scratch):
    head, index = (
        _git(repo, "rev-parse", "HEAD"),
        (repo / ".git" / "index").read_bytes(),
    )
    ws = attempts.prepare(repo, scratch / "w")
    try:
        assert (ws.cwd / "pkg" / "a.py").read_text(encoding="utf-8") == (
            "x = 2  # uncommitted\n"
        )
        assert (ws.cwd / "new.txt").is_file()
        assert not (ws.cwd / "gone.txt").exists()
        assert not (ws.cwd / "build").exists()  # ignored files stay out
        # Same repository: branches and remotes a review needs are all there.
        assert _git(ws.cwd, "rev-parse", "HEAD") == head
        assert _git(ws.cwd, "rev-parse", "main") == _git(repo, "rev-parse", "main")
        assert _git(ws.cwd, "remote", "get-url", "origin").endswith("r.git")
        _git(ws.cwd, "diff", "--name-only", "main...HEAD")  # "review against main"
        # Uncommitted work shows as uncommitted, as it does in the real folder.
        assert "pkg/a.py" in _git(ws.cwd, "status", "--porcelain")
        assert attempts.collect(ws, "a", scratch / "d0") == []
    finally:
        attempts.dispose(ws)
    # The real repository is exactly as it was.
    assert _git(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git" / "index").read_bytes() == index
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feature"
    assert str(scratch / "w") not in _git(repo, "worktree", "list")
    assert not (scratch / "w").exists()


@needs_git
def test_git_draft_has_edits_additions_deletions_and_survives_commits(repo, scratch):
    ws = attempts.prepare(repo, scratch / "w")
    try:
        _write(ws.cwd / "pkg" / "a.py", "x = 3\n")
        _write(ws.cwd / "pkg" / "b.py", "y = 1\n")
        (ws.cwd / "new.txt").unlink()
        _write(ws.cwd / "build" / "artifact.o", "ignored output")
        _git(ws.cwd, "add", "-A")
        _git(ws.cwd, "commit", "-qm", "attempt commits its work")
        draft = scratch / "d"
        touched = attempts.collect(ws, "my answer", draft)
    finally:
        attempts.dispose(ws)
    assert touched == ["pkg/a.py", "pkg/b.py", "new.txt"]
    patch = (draft / "CHANGES.patch").read_text(encoding="utf-8")
    assert "-x = 2  # uncommitted" in patch and "+x = 3" in patch
    assert "artifact.o" not in patch
    assert (draft / "files" / "pkg" / "b.py").read_text(encoding="utf-8") == "y = 1\n"
    assert (draft / "ANSWER.md").read_text(encoding="utf-8") == "my answer"
    # The attempt's commit landed on a detached HEAD: no real branch moved.
    assert _git(repo, "log", "--oneline", "feature").count("\n") == 0
    assert (repo / "pkg" / "a.py").read_text(
        encoding="utf-8"
    ) == "x = 2  # uncommitted\n"


@needs_git
def test_running_from_a_subfolder_keeps_git_and_the_same_place(repo, scratch):
    ws = attempts.prepare(repo / "pkg", scratch / "w")
    try:
        assert ws.cwd == ws.top / "pkg"
        assert _git(ws.cwd, "rev-parse", "--show-toplevel")
        _write(ws.cwd / "c.py", "z = 1\n")
        assert attempts.collect(ws, "", scratch / "d") == ["pkg/c.py"]
    finally:
        attempts.dispose(ws)


@needs_git
def test_a_repo_with_no_commits_is_copied_instead(tmp_path, scratch):
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init", "-q")
    _write(root / "a.py", "x = 1\n")
    ws = attempts.prepare(root, scratch / "w")
    assert ws.git_top is None and (ws.cwd / "a.py").is_file()
    attempts.dispose(ws)
    assert not (scratch / "w").exists()


@needs_git
def test_workspace_keeps_the_branch_name_upstream_and_remotes(repo, scratch):
    _git(repo, "config", "branch.feature.remote", "origin")
    _git(repo, "config", "branch.feature.merge", "refs/heads/feature")
    ws = attempts.prepare(repo, scratch / "w")
    try:
        assert _git(ws.cwd, "rev-parse", "--abbrev-ref", "HEAD") == "feature"
        assert _git(ws.cwd, "config", "branch.feature.remote") == "origin"
        assert _git(ws.cwd, "remote") == "origin"
    finally:
        attempts.dispose(ws)


@needs_git
def test_an_attempt_cannot_touch_the_real_stash_branches_or_tags(repo, scratch):
    _write(repo / "stashed.txt", "keep me\n")
    _git(repo, "add", "stashed.txt")
    _git(repo, "stash", "push", "-q", "--", "stashed.txt")
    stash = _git(repo, "stash", "list")
    ws = attempts.prepare(repo, scratch / "w")
    try:
        # What an agent might do: pop a stash, branch, tag, delete main.
        subprocess.run(["git", "stash", "pop"], cwd=ws.cwd, capture_output=True)
        _git(ws.cwd, "branch", "attempt-branch")
        _git(ws.cwd, "tag", "attempt-tag")
        _git(ws.cwd, "branch", "-D", "main")
        _git(ws.cwd, "config", "user.name", "someone else")
    finally:
        attempts.dispose(ws)
    assert _git(repo, "stash", "list") == stash and stash
    assert "attempt-branch" not in _git(repo, "branch")
    assert _git(repo, "tag") == ""
    assert _git(repo, "rev-parse", "--verify", "main")
    assert "someone else" not in _git(repo, "config", "--local", "--list")


@needs_git
def test_repository_hooks_do_not_run_when_a_workspace_is_made(repo, scratch, tmp_path):
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    ws = attempts.prepare(repo, scratch / "w")
    attempts.dispose(ws)
    assert not marker.exists()


@needs_git
def test_git_variables_from_a_calling_hook_never_reach_the_real_index(
    repo, scratch, monkeypatch
):
    _git(repo, "add", "pkg/a.py")  # staged work the user cares about
    status = _git(repo, "status", "--porcelain")
    index = (repo / ".git" / "index").read_bytes()
    monkeypatch.setenv("GIT_INDEX_FILE", str(repo / ".git" / "index"))
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo))
    ws = attempts.prepare(repo, scratch / "w")
    try:
        _write(ws.cwd / "pkg" / "a.py", "x = 99\n")
        attempts.collect(ws, "", scratch / "d")
    finally:
        attempts.dispose(ws)
    monkeypatch.delenv("GIT_INDEX_FILE")
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    assert (repo / ".git" / "index").read_bytes() == index
    assert _git(repo, "status", "--porcelain") == status


@needs_git
def test_untracked_files_too_big_to_snapshot_are_refused(repo, scratch, monkeypatch):
    monkeypatch.setattr(attempts, "MAX_COPY_BYTES", 4)
    with pytest.raises(attempts.AttemptsError, match="untracked files"):
        attempts.prepare(repo, scratch / "w")
    assert not (scratch / "w").exists()


def test_a_project_module_cannot_shadow_the_standard_library_in_attempts(tmp_path):
    _write(tmp_path / "json.py", "raise SystemExit('project json.py was imported')\n")
    probe = attempts._CHILD_MAIN.replace(
        "from gaia.cli import main; sys.exit(main())",
        "import json; print(json.__file__)",
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()).parent != tmp_path


def test_cleanup_that_cannot_delete_warns_instead_of_failing(
    tmp_path, monkeypatch, capsys
):
    def locked(path):
        raise PermissionError("in use")

    monkeypatch.setattr(attempts, "remove_tree", locked)
    attempts.try_remove(tmp_path)
    assert "could not remove" in capsys.readouterr().err


def test_original_changed_by_the_user_mid_run_is_not_diffed_backwards(folder, scratch):
    ws = attempts.prepare(folder, scratch / "w")
    _write(ws.cwd / "pkg" / "a.py", "x = 2\n")
    _write(folder / "pkg" / "a.py", "x = 1  # user edit during the run\n")
    draft = scratch / "d"
    assert attempts.collect(ws, "", draft) == ["pkg/a.py"]
    patch = (draft / "CHANGES.patch").read_text(encoding="utf-8")
    assert "the original changed during the run" in patch
    assert "user edit" not in patch


# --- plain-folder copies -----------------------------------------------------


def test_copy_skips_rebuilt_dirs_and_reports_nothing_untouched(folder, scratch):
    ws = attempts.prepare(folder, scratch / "w")
    assert (ws.cwd / "pkg" / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (ws.cwd / "node_modules").exists()
    assert attempts.collect(ws, "", scratch / "d") == []


def test_copy_draft_finds_edits_additions_deletions_and_binaries(folder, scratch):
    ws = attempts.prepare(folder, scratch / "w")
    _write(ws.cwd / "pkg" / "a.py", "x = 2\n")
    (ws.cwd / "img.bin").write_bytes(b"\xff\xfe\x00\x01")
    (ws.cwd / "README.md").unlink()
    draft = scratch / "d"
    assert attempts.collect(ws, "", draft) == ["img.bin", "pkg/a.py", "README.md"]
    patch = (draft / "CHANGES.patch").read_text(encoding="utf-8")
    assert "-x = 1" in patch and "+x = 2" in patch
    assert "binary file changed" in patch and "+++ /dev/null" in patch
    assert (draft / "ANSWER.md").read_text(encoding="utf-8") == "(no answer)"
    assert (folder / "README.md").exists()


def test_same_size_edit_with_the_old_mtime_is_still_a_change(folder, scratch):
    ws = attempts.prepare(folder, scratch / "w")
    target = ws.cwd / "pkg" / "a.py"
    st = target.stat()
    _write(target, "x = 9\n")
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert attempts.collect(ws, "", scratch / "d") == ["pkg/a.py"]


def test_user_editing_the_real_folder_mid_run_is_not_an_attempts_change(
    folder, scratch
):
    ws = attempts.prepare(folder, scratch / "w")
    _write(folder / "README.md", "the user kept working\n")
    _write(folder / "later.txt", "new\n")
    assert attempts.collect(ws, "", scratch / "d") == []


def test_home_folder_is_refused(folder, scratch, monkeypatch):
    monkeypatch.setattr(attempts.Path, "home", classmethod(lambda cls: folder))
    with pytest.raises(attempts.AttemptsError, match="home or temp folder"):
        attempts.prepare(folder, scratch / "w")
    assert not (scratch / "w").exists()


def test_folder_holding_the_temp_dir_is_refused(folder, scratch, monkeypatch):
    monkeypatch.setattr(attempts.tempfile, "gettempdir", lambda: str(folder / "tmp"))
    with pytest.raises(attempts.AttemptsError, match="home or temp folder"):
        attempts.prepare(folder, scratch / "w")


def test_folder_too_big_to_copy_is_refused_before_copying(folder, scratch, monkeypatch):
    monkeypatch.setattr(attempts, "MAX_COPY_FILES", 1)
    with pytest.raises(
        attempts.AttemptsError, match="make the folder a git repository"
    ):
        attempts.prepare(folder, scratch / "w")
    assert not (scratch / "w").exists()


def test_linked_folder_is_not_entered(folder, scratch, tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "secret.txt", "s")
    try:
        os.symlink(outside, folder / "link", target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    ws = attempts.prepare(folder, scratch / "w")
    assert not (ws.cwd / "link").exists()
    assert ws.skipped == ["link"]


def test_remove_tree_deletes_read_only_files(tmp_path):
    victim = tmp_path / "scratch"
    _write(victim / "objects" / "pack", "x")
    os.chmod(victim / "objects" / "pack", 0o400)
    attempts.remove_tree(victim)
    assert not victim.exists()
    attempts.remove_tree(victim)  # already gone: no error


# --- running attempts --------------------------------------------------------


class _SerialPool:
    def __init__(self, max_workers):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, fn, items):
        return [fn(i) for i in items]


def _fake_runs(monkeypatch, behaviours):
    """Each attempt edits its workspace, then answers or fails, per *behaviours*."""
    calls = []

    def fake(prompt, workdir, flags):
        k = len(calls)
        calls.append((prompt, workdir, list(flags)))
        _write(workdir / "pkg" / "a.py", f"x = {k + 10}\n")
        if behaviours[k] == "fail":
            return {"is_error": True, "error": f"boom {k}"}
        return {"is_error": False, "result": f"answer {k}"}

    monkeypatch.setattr(attempts, "_run_attempt", fake)
    monkeypatch.setattr(attempts, "ThreadPoolExecutor", _SerialPool)
    return calls


@pytest.fixture
def private_temp(tmp_path, monkeypatch):
    """Scratch folders land here, so a leak is visible."""
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(attempts.tempfile, "tempdir", str(temp))
    return temp


@pytest.mark.parametrize("count", [0, 1, attempts.MAX_ATTEMPTS + 1])
def test_attempt_count_is_bounded(folder, count):
    with pytest.raises(ValueError, match="--attempts takes"):
        attempts.run_attempts("task", count, [], root=folder)


@pytest.mark.parametrize("kind", ["folder", "repo"])
def test_attempts_run_apart_and_leave_the_real_folder_alone(
    request, kind, monkeypatch, private_temp
):
    if kind == "repo" and shutil.which("git") is None:
        pytest.skip("git not installed")
    root = request.getfixturevalue(kind)
    before = (root / "pkg" / "a.py").read_text(encoding="utf-8")
    calls = _fake_runs(monkeypatch, ["ok", "ok"])
    prompt, records, scratch = attempts.run_attempts(
        "Review it.", 2, ["--model", "m"], root=root
    )
    try:
        workdirs = {c[1] for c in calls}
        assert len(workdirs) == 2 and root not in workdirs
        assert all(c[2] == ["--model", "m"] for c in calls)
        assert (root / "pkg" / "a.py").read_text(encoding="utf-8") == before
        assert not any(w.exists() for w in workdirs)  # workspaces removed
        drafts = scratch / "drafts"
        assert sorted(p.name for p in drafts.iterdir()) == ["attempt-1", "attempt-2"]
        assert "+x = 11" in (drafts / "attempt-2" / "CHANGES.patch").read_text(
            encoding="utf-8"
        )
        assert prompt.startswith("Review it.") and drafts.as_posix() in prompt
        assert "A merge is not a union" in prompt
        assert [r["result"] for r in records] == ["answer 0", "answer 1"]
    finally:
        attempts.remove_tree(scratch)
    if kind == "repo":
        assert len(_git(root, "worktree", "list").splitlines()) == 1


def test_failed_attempt_leaves_no_draft(folder, monkeypatch, private_temp):
    _fake_runs(monkeypatch, ["fail", "ok"])
    _, records, scratch = attempts.run_attempts("t", 2, [], root=folder)
    try:
        assert records[0]["is_error"]
        drafts = scratch / "drafts"
        assert [p.name for p in drafts.iterdir()] == ["attempt-1"]
        assert (drafts / "attempt-1" / "ANSWER.md").read_text(encoding="utf-8") == (
            "answer 1"
        )
    finally:
        attempts.remove_tree(scratch)


def test_every_attempt_failing_raises_and_leaves_nothing(
    folder, monkeypatch, private_temp
):
    _fake_runs(monkeypatch, ["fail", "fail"])
    with pytest.raises(attempts.AttemptsError, match="the last said: boom 1"):
        attempts.run_attempts("t", 2, [], root=folder)
    assert list(private_temp.iterdir()) == []
    assert (folder / "pkg" / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_a_workspace_that_cannot_be_made_leaves_nothing(
    folder, monkeypatch, private_temp
):
    real_prepare = attempts.prepare
    made = []

    def flaky(root, dest):
        if made:
            raise attempts.AttemptsError("disk full")
        made.append(real_prepare(root, dest))
        return made[-1]

    monkeypatch.setattr(attempts, "prepare", flaky)
    with pytest.raises(attempts.AttemptsError, match="disk full"):
        attempts.run_attempts("t", 2, [], root=folder)
    assert list(private_temp.iterdir()) == []


def test_temp_folder_inside_the_project_is_refused(folder, monkeypatch):
    inside = folder / "tmp"
    inside.mkdir()
    monkeypatch.setattr(attempts.tempfile, "tempdir", str(inside))
    with pytest.raises(attempts.AttemptsError, match="TMP/TMPDIR"):
        attempts.run_attempts("t", 2, [], root=folder)
    assert list(inside.iterdir()) == []


def test_attempt_that_prints_no_result_is_an_error_record(tmp_path, monkeypatch):
    class Done:
        returncode = 1
        stdout = "not json\n"
        stderr = "line1\nTraceback: KeyError\n"

    seen = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, **kw)
        return Done()

    monkeypatch.setattr(attempts.subprocess, "run", fake_run)
    record = attempts._run_attempt("the task", tmp_path, ["--full-access"])
    assert record["is_error"] and "exit 1" in record["error"]
    assert "KeyError" in record["error"]
    # The prompt goes on stdin, never the command line, and says it is a draft.
    assert seen["input"].startswith("the task")
    assert "take no action outside this folder" in seen["input"]
    assert not any("the task" in a for a in seen["argv"])
    assert seen["argv"][:5] == [
        sys.executable,
        "-c",
        attempts._CHILD_MAIN,
        "-p",
        "--output-format",
    ]
    assert seen["argv"][-1] == "--full-access" and seen["cwd"] == tmp_path
    assert "GIT_DIR" not in seen["env"]


def test_attempt_that_cannot_start_is_an_error_record(tmp_path, monkeypatch):
    def boom(argv, **kw):
        raise FileNotFoundError("python vanished")

    monkeypatch.setattr(attempts.subprocess, "run", boom)
    record = attempts._run_attempt("t", tmp_path, [])
    assert record["is_error"] and "python vanished" in record["error"]


def test_attempt_record_is_the_last_json_line(tmp_path, monkeypatch):
    class Done:
        returncode = 0
        stdout = '{"is_error": false, "result": "ok"}\n{not json, a stray log line\n'
        stderr = ""

    monkeypatch.setattr(attempts.subprocess, "run", lambda argv, **kw: Done())
    assert attempts._run_attempt("t", tmp_path, [])["result"] == "ok"


def test_paths_are_posix_relative(folder, scratch):
    ws = attempts.prepare(folder, scratch / "w")
    _write(ws.cwd / "pkg" / "deep" / "n.py", "1\n")
    assert attempts.collect(ws, "", scratch / "d") == ["pkg/deep/n.py"]
    assert Path(scratch / "d" / "files" / "pkg" / "deep" / "n.py").is_file()
