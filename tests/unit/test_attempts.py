# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for ``gaia -p --attempts``: private workspaces, drafts, and cleanup."""

import _thread
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
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
    monkeypatch.setattr(attempts, "_REMOVE_WAIT_S", 0)
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


@needs_git
def test_ignored_task_inputs_come_along_but_build_output_does_not(repo, scratch):
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("_task/\n", encoding="utf-8")
    _write(repo / "_task" / "PROMPT.md", "review the diff\n")
    _write(repo / "_task" / "pr-diff.txt", "+x\n")
    _write(repo / "_task" / "a[1].txt", "brackets are literal\n")
    _write(repo / "build" / "out.bin", "ignored and rebuilt\n")
    _write(repo / ".gitignore", "build/\nnode_modules/\n")
    _write(repo / "node_modules" / "dep" / "i.js", "//\n")
    ws = attempts.prepare(repo, scratch / "w")
    try:
        assert (ws.cwd / "_task" / "PROMPT.md").read_text(encoding="utf-8") == (
            "review the diff\n"
        )
        assert (ws.cwd / "_task" / "pr-diff.txt").is_file()
        assert (ws.cwd / "_task" / "a[1].txt").is_file()
        assert not (ws.cwd / "build").exists()
        assert not (ws.cwd / "node_modules").exists()
        # Nothing changed yet: the inputs are part of the starting point.
        assert attempts.collect(ws, "", scratch / "d0") == []
    finally:
        attempts.dispose(ws)


@needs_git
def test_a_deliverable_written_to_an_ignored_folder_reaches_the_draft(repo, scratch):
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("_task/\n", encoding="utf-8")
    _write(repo / "_task" / "PROMPT.md", "write OUTPUT.md\n")
    ws = attempts.prepare(repo, scratch / "w")
    try:
        _write(ws.cwd / "_task" / "OUTPUT.md", "the review\n")
        _write(ws.cwd / "build" / "tmp.o", "rebuilt\n")
        draft = scratch / "d"
        assert attempts.collect(ws, "done", draft) == ["_task/OUTPUT.md"]
    finally:
        attempts.dispose(ws)
    assert (draft / "files" / "_task" / "OUTPUT.md").read_text(encoding="utf-8") == (
        "the review\n"
    )
    assert not (repo / "_task" / "OUTPUT.md").exists()


@needs_git
def test_ignored_files_count_toward_the_size_limit(repo, scratch, monkeypatch):
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("data/\n", encoding="utf-8")
    _write(repo / "data" / "big.csv", "x" * 5000)
    monkeypatch.setattr(attempts, "MAX_COPY_BYTES", 1000)
    with pytest.raises(attempts.AttemptsError, match="untracked files"):
        attempts.prepare(repo, scratch / "w")


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
    (folder / "tmp").mkdir()
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

    def submit(self, fn, *args):
        future = Future()
        future.set_result(fn(*args))
        return future


def _fake_runs(monkeypatch, behaviours):
    """Each attempt edits its workspace, then answers or fails, per *behaviours*."""
    calls = []

    def fake(prompt, workdir, flags, stop=None):
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


def test_a_partly_failed_run_logs_why_the_attempt_failed(
    folder, monkeypatch, private_temp, caplog
):
    _fake_runs(monkeypatch, ["fail", "ok"])
    with caplog.at_level("WARNING", logger=attempts.log.name):
        _, _, scratch = attempts.run_attempts("t", 2, [], root=folder)
    attempts.remove_tree(scratch)
    assert "attempt 1 failed: boom 0" in caplog.text


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

    monkeypatch.setattr(attempts, "_run", fake_run)
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


def test_a_wedged_git_command_is_an_attempts_error(tmp_path, monkeypatch):
    def wedged(argv, **kw):
        assert kw["timeout"] == attempts._GIT_TIMEOUT_S
        raise attempts.subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(attempts, "_run", wedged)
    with pytest.raises(attempts.AttemptsError, match="did not finish"):
        attempts._git("status", cwd=tmp_path)
    with pytest.raises(attempts.AttemptsError, match="did not finish"):
        attempts._local_config(tmp_path, r"^remote.")


def test_attempt_that_cannot_start_is_an_error_record(tmp_path, monkeypatch):
    def boom(argv, **kw):
        raise FileNotFoundError("python vanished")

    monkeypatch.setattr(attempts, "_run", boom)
    record = attempts._run_attempt("t", tmp_path, [])
    assert record["is_error"] and "python vanished" in record["error"]


def test_attempt_record_is_the_last_json_line(tmp_path, monkeypatch):
    class Done:
        returncode = 0
        stdout = '{"is_error": false, "result": "ok"}\n{not json, a stray log line\n'
        stderr = ""

    monkeypatch.setattr(attempts, "_run", lambda argv, **kw: Done())
    assert attempts._run_attempt("t", tmp_path, [])["result"] == "ok"


def test_paths_are_posix_relative(folder, scratch):
    ws = attempts.prepare(folder, scratch / "w")
    _write(ws.cwd / "pkg" / "deep" / "n.py", "1\n")
    assert attempts.collect(ws, "", scratch / "d") == ["pkg/deep/n.py"]
    assert Path(scratch / "d" / "files" / "pkg" / "deep" / "n.py").is_file()


# --- real process trees -------------------------------------------------------

#: A stand-in for one attempt's ``gaia -p``: it starts a grandchild the way the
#: shell tool does (its own session on POSIX), records both pids, then hangs.
_STUB = r"""
import os, subprocess, sys, time
pids, mode = sys.argv[1], sys.argv[2]
kw = {} if os.name == "nt" else {"start_new_session": True}
if mode == "hold":  # holds a file open in the workspace
    code = "import time; f = open('held.txt', 'w'); time.sleep(60)"
    out = subprocess.DEVNULL
else:  # "pipe" / "exit": holds this process's stdout
    code = "import time; time.sleep(60)"
    out = None
grandchild = subprocess.Popen([sys.executable, "-c", code], stdout=out, **kw)
with open(os.path.join(pids, f"{os.getpid()}.txt"), "w") as f:
    f.write(f"{os.getpid()} {grandchild.pid}")
if mode != "exit":
    time.sleep(60)
"""


def _alive(pid):
    """Still running 5 s on; a killed process can take a moment to be gone."""
    psutil = pytest.importorskip("psutil")
    try:
        psutil.Process(pid).wait(timeout=5)
    except psutil.NoSuchProcess:
        return False
    except psutil.TimeoutExpired:
        return True
    return False


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """Attempts run the stub in *mode*; yields a reader of every pid it started."""
    psutil = pytest.importorskip("psutil")
    script = tmp_path / "stub.py"
    script.write_text(_STUB, encoding="utf-8")
    pids = tmp_path / "pids"
    pids.mkdir()
    mode = {"value": "hold"}
    monkeypatch.setattr(
        attempts,
        "_child_argv",
        lambda flags: [sys.executable, str(script), str(pids), mode["value"]],
    )

    def started():
        return [int(p) for f in pids.glob("*.txt") for p in f.read_text().split()]

    started.mode = mode
    started.dir = pids
    started.script = script
    yield started
    for pid in started():  # only the pids this test started
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass


def _interrupt_once_started(pids_dir, count):
    """Ctrl+C the main thread once *count* stubs have written their pids."""

    def watch():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if len(list(pids_dir.glob("*.txt"))) >= count:
                time.sleep(0.5)
                _thread.interrupt_main()
                return
            time.sleep(0.1)

    threading.Thread(target=watch, daemon=True).start()


def test_a_timed_out_attempt_dies_with_its_grandchild_and_leaves_nothing(
    folder, stub, monkeypatch, private_temp
):
    monkeypatch.setattr(attempts, "ATTEMPT_TIMEOUT_S", 4)
    started = time.monotonic()
    with pytest.raises(attempts.AttemptsError, match="stopped after"):
        attempts.run_attempts("t", 2, [], root=folder)
    assert time.monotonic() - started < 40  # the grandchildren sleep 60 s
    pids = stub()
    assert len(pids) == 4  # two attempts, each with a grandchild
    assert not any(_alive(p) for p in pids)
    assert list(private_temp.iterdir()) == []  # removed though held.txt was open


def test_a_grandchild_holding_the_output_cannot_stall_the_timeout(
    tmp_path, stub, monkeypatch, private_temp
):
    stub.mode["value"] = "pipe"
    monkeypatch.setattr(attempts, "ATTEMPT_TIMEOUT_S", 3)
    started = time.monotonic()
    record = attempts._run_attempt("t", tmp_path, [])
    assert time.monotonic() - started < 20  # the grandchild sleeps 60 s
    assert record["is_error"] and "stopped after" in record["error"]
    assert not any(_alive(p) for p in stub())


def test_an_attempt_that_exits_returns_though_its_grandchild_holds_the_output(
    tmp_path, stub, monkeypatch, private_temp
):
    stub.mode["value"] = "exit"
    monkeypatch.setattr(attempts, "_REMOVE_WAIT_S", 0)
    started = time.monotonic()
    record = attempts._run_attempt("t", tmp_path, [])
    assert time.monotonic() - started < 20  # the grandchild sleeps 60 s
    assert record["is_error"] and "printed no result" in record["error"]


def test_ctrl_c_during_attempts_kills_every_tree_and_leaves_nothing(
    folder, stub, monkeypatch, private_temp
):
    # Past the watcher's deadline, so a missed interrupt fails instead of hanging.
    monkeypatch.setattr(attempts, "ATTEMPT_TIMEOUT_S", 40)
    _interrupt_once_started(stub.dir, 2)
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        attempts.run_attempts("t", 2, [], root=folder)
    assert time.monotonic() - started < 30
    pids = stub()
    assert len(pids) == 4
    assert not any(_alive(p) for p in pids)
    assert list(private_temp.iterdir()) == []


def test_ctrl_c_during_a_git_command_kills_its_tree(tmp_path, stub):
    # _git runs on the main thread; the stub stands in for git and its filters.
    _interrupt_once_started(stub.dir, 1)
    with pytest.raises(KeyboardInterrupt):
        attempts._run(
            [sys.executable, str(stub.script), str(stub.dir), "hold"],
            cwd=tmp_path,
            env=attempts.clean_env(),
            timeout=60,
        )
    pids = stub()
    assert len(pids) == 2
    assert not any(_alive(p) for p in pids)


#: Put before ``_CHILD_MAIN``: the real child up to its prompt, which it prints
#: as its result instead of running a turn.
_ECHO_PROMPT = r"""
import json, sys
from gaia import print_mode
_read = print_mode.read_prompt
def _echo(prompt):
    print(json.dumps({"result": _read(prompt)}))
    sys.exit(0)
print_mode.read_prompt = _echo
"""

_needs_windows = pytest.mark.skipif(
    sys.platform != "win32", reason="the console code page is Windows-only"
)


@_needs_windows
@pytest.mark.parametrize("prompt", ["fix the bug in a.py", "café – 日本"])
def test_the_real_child_reads_the_exact_prompt(tmp_path, monkeypatch, prompt):
    # Stdin is a file there: a console call that shared it once read it to EOF.
    monkeypatch.setattr(attempts, "_CHILD_MAIN", _ECHO_PROMPT + attempts._CHILD_MAIN)
    record = attempts._run_attempt(prompt, tmp_path, [])
    assert record == {"result": (prompt + attempts.ATTEMPT_INSTRUCTIONS).strip()}


_LINGER = r"""
import json, os, subprocess, sys, time
pids = sys.argv[1]
held = "import time; f = open('held.txt', 'w'); time.sleep(60)"
kw = {} if os.name == "nt" else {"start_new_session": True}
grandchild = subprocess.Popen([sys.executable, "-c", held], **kw)
with open(os.path.join(pids, f"{os.getpid()}.txt"), "w") as f:
    f.write(f"{os.getpid()} {grandchild.pid}")
time.sleep(2)  # long enough to be seen
print(json.dumps({"result": "done"}))
"""


@pytest.fixture
def linger(stub, monkeypatch):
    """An attempt that leaves a grandchild holding a file in its workspace."""
    stub.script.write_text(_LINGER, encoding="utf-8")
    monkeypatch.setattr(
        attempts,
        "_child_argv",
        lambda flags: [sys.executable, str(stub.script), str(stub.dir)],
    )
    return stub


def test_what_a_finished_attempt_left_running_dies_with_it(tmp_path, linger):
    work = tmp_path / "work"
    work.mkdir()
    record = attempts._run_attempt("t", work, [])
    assert record == {"result": "done"}
    child, grandchild = linger()
    assert not _alive(grandchild)
    attempts.remove_tree(work)  # nothing holds held.txt any more


def test_a_finished_attempt_spares_the_gaia_daemon_it_started(
    tmp_path, linger, monkeypatch
):
    monkeypatch.setattr(attempts, "_gaia_daemon", lambda: linger()[1:])
    record = attempts._run_attempt("t", tmp_path, [])
    assert record == {"result": "done"}
    assert _alive(linger()[1])  # the fixture stops it


def test_a_timed_out_attempt_spares_the_gaia_daemon(tmp_path, stub, monkeypatch):
    monkeypatch.setattr(attempts, "ATTEMPT_TIMEOUT_S", 3)
    monkeypatch.setattr(attempts, "_gaia_daemon", lambda: stub()[1:])
    record = attempts._run_attempt("t", tmp_path, [])
    assert "stopped after" in record["error"]
    child, daemon = stub()
    assert not _alive(child)
    assert _alive(daemon)  # the fixture stops it


def test_descendants_are_listed_every_few_seconds_not_every_wake(tmp_path, monkeypatch):
    from gaia.utils import process_tree

    calls = []
    real = process_tree._sample
    monkeypatch.setattr(process_tree, "_sample", lambda *a: (calls.append(1), real(*a)))
    code, timed_out = process_tree.launch(
        [sys.executable, "-c", "import time; time.sleep(3)"],
        env=os.environ,
        cwd=tmp_path,
        timeout_s=30,
        stdout_path=tmp_path / "out",
        stderr_path=tmp_path / "err",
    )
    assert (code, timed_out) == (0, False)
    assert 1 <= len(calls) <= 3  # waking every 0.5 s would list ~6 times


# --- which daemon is spared ---------------------------------------------------


def _instance_file(tmp_path, monkeypatch, write):
    """Point instance.json into *tmp_path*; *write* fills it on every lookup."""
    from gaia.daemon import paths

    path = tmp_path / "instance.json"

    def instance_path():
        text = write()
        if text is not None:
            path.write_text(text, encoding="utf-8")
        return path

    monkeypatch.setattr(paths, "instance_path", instance_path)
    return path


def _instance(pid, started_at):
    return f'{{"pid": {pid}, "port": 1, "token": "t", "started_at": {started_at}}}'


def _workdir(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return work


def test_the_daemon_is_spared_when_its_start_time_matches(tmp_path, monkeypatch):
    psutil = pytest.importorskip("psutil")
    me = psutil.Process(os.getpid())
    _instance_file(
        tmp_path, monkeypatch, lambda: _instance(me.pid, me.create_time() + 1)
    )
    assert attempts._gaia_daemon() == [me.pid]


def test_a_pid_that_started_after_the_daemon_is_not_spared(tmp_path, monkeypatch):
    psutil = pytest.importorskip("psutil")
    me = psutil.Process(os.getpid())
    _instance_file(
        tmp_path, monkeypatch, lambda: _instance(me.pid, me.create_time() - 60)
    )
    assert attempts._gaia_daemon() == []


@pytest.mark.parametrize("pid", ["-5", "0", "1e999", '"x"', "99999999999999999999"])
def test_a_malformed_instance_spares_nothing_and_never_raises(
    tmp_path, monkeypatch, pid
):
    _instance_file(tmp_path, monkeypatch, lambda: _instance(pid, 0))
    assert attempts._gaia_daemon() == []


def test_a_missing_instance_is_logged_and_spares_nothing(tmp_path, monkeypatch, caplog):
    import logging

    _instance_file(tmp_path, monkeypatch, lambda: None)
    with caplog.at_level(logging.DEBUG, logger=attempts.__name__):
        assert attempts._gaia_daemon() == []
    assert any("no GAIA daemon to spare" in r.message for r in caplog.records)


def test_the_kill_path_never_raises_on_a_bad_spare_pid(tmp_path):
    from gaia.utils import process_tree

    code, timed_out = process_tree.launch(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env=os.environ,
        cwd=tmp_path,
        timeout_s=1,
        stdout_path=tmp_path / "out",
        stderr_path=tmp_path / "err",
        spare=lambda: [-5, 0],
    )
    assert (code, timed_out) == (None, True)


@pytest.mark.parametrize("which", [0, 1], ids=["child", "grandchild"])
def test_a_stale_daemon_pid_reused_by_the_attempt_is_still_killed(
    tmp_path, stub, monkeypatch, which
):
    def stale():
        pids = stub()
        return _instance(pids[which], time.time() - 3600) if pids else None

    _instance_file(tmp_path, monkeypatch, stale)
    monkeypatch.setattr(attempts, "ATTEMPT_TIMEOUT_S", 3)
    record = attempts._run_attempt("t", _workdir(tmp_path), [])
    assert "stopped after" in record["error"]
    assert len(stub()) == 2
    assert not any(_alive(p) for p in stub())


@pytest.mark.parametrize("pid", ["-5", "1e999"])
def test_a_malformed_instance_still_kills_a_timed_out_tree(
    tmp_path, stub, monkeypatch, pid
):
    _instance_file(tmp_path, monkeypatch, lambda: _instance(pid, 0))
    monkeypatch.setattr(attempts, "ATTEMPT_TIMEOUT_S", 3)
    record = attempts._run_attempt("t", _workdir(tmp_path), [])
    assert "stopped after" in record["error"]
    assert len(stub()) == 2
    assert not any(_alive(p) for p in stub())


@pytest.mark.parametrize("pid", ["-5", "1e999"])
def test_a_malformed_instance_does_not_crash_a_finished_attempt(
    tmp_path, linger, monkeypatch, pid
):
    _instance_file(tmp_path, monkeypatch, lambda: _instance(pid, 0))
    assert attempts._run_attempt("t", _workdir(tmp_path), []) == {"result": "done"}
    assert not _alive(linger()[1])
