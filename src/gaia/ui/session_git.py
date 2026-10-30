# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Commit and open a PR for what a chat changed, from the session panel (#5278).

Nothing here runs on its own: every call that changes a repository needs
``confirmed`` from a dialog where the user saw and could edit the message.
Only the files the chat changed are staged, no command takes a force or
history-rewriting flag, hooks run as usual, and opening a PR is refused while
the chat's internet is off.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gaia.agents.base import file_snapshots
from gaia.logger import get_logger

from .database import ChatDatabase

logger = get_logger(__name__)

_TIMEOUT_S = 120
_TITLE_MAX = 72
_NUL = "\x00"


class GitActionError(RuntimeError):
    """A commit or PR the user asked for that could not be made."""


def _git_exe() -> str:
    exe = shutil.which("git")
    if exe is None:
        raise GitActionError(
            "Git is not installed or not on PATH. Install it from "
            "https://git-scm.com/downloads and restart GAIA."
        )
    return exe


def _run(
    argv: List[str],
    cwd: str,
    stdin: Optional[str] = None,
    ok_codes: Tuple[int, ...] = (0,),
    literal: bool = True,
) -> str:
    env = dict(os.environ)
    if literal:
        # File names are names, never patterns: `*` must not stage the repo.
        env["GIT_LITERAL_PATHSPECS"] = "1"
    else:
        env.pop("GIT_LITERAL_PATHSPECS", None)
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise GitActionError(
            f"`{' '.join(argv[:3])}` took longer than {_TIMEOUT_S} s and was stopped."
        ) from e
    except OSError as e:
        raise GitActionError(f"Could not run {argv[0]}: {e}") from e
    if proc.returncode not in ok_codes:
        detail = (proc.stderr or proc.stdout).strip()
        raise GitActionError(f"`{' '.join(argv[:3])}` failed: {detail[:800]}")
    # Leading space is meaningful: `git status --porcelain` starts lines with it.
    return proc.stdout.rstrip()


def _toplevel(folder: str) -> Optional[str]:
    try:
        return str(Path(_run([_git_exe(), "rev-parse", "--show-toplevel"], folder)))
    except GitActionError:
        return None


def _existing_parent(path: str) -> Optional[str]:
    p = Path(path).parent
    while not p.is_dir():
        if p.parent == p:
            return None
        p = p.parent
    return str(p)


def _ignored(root: str, rel: List[str]) -> List[str]:
    """The paths in *rel* git ignores; a tracked file never counts as ignored."""
    if not rel:
        return []
    posix = [r.replace(os.sep, "/") for r in rel]
    # check-ignore reads plain names, not pathspecs, and rejects the literal
    # flag. Exit 1 means none of them are ignored.
    out = _run(
        [_git_exe(), "check-ignore", "-z", "--stdin"],
        root,
        stdin=_NUL.join(posix) + _NUL,
        ok_codes=(0, 1),
        literal=False,
    )
    hits = {p for p in out.split(_NUL) if p}
    return [r for r, p in zip(rel, posix) if p in hits]


def _changed_by_chat(db: ChatDatabase, session_id: str) -> List[str]:
    """The chat's changed files, minus any in a folder the user removed."""
    from . import session_environment

    withheld = session_environment.load(db, session_id)["withheld"]
    return [
        f["path"]
        for f in file_snapshots.session_changes(session_id)["files"]
        if not any(session_environment.within(f["path"], w) for w in withheld)
    ]


def _user_requests(db: ChatDatabase, session_id: str) -> Dict[int, str]:
    """Answer id -> the user message it answered."""
    messages = db.get_messages(session_id, limit=100_000)
    asked: Dict[int, str] = {}
    last_user = ""
    for m in messages:
        if m["role"] == "user":
            last_user = m["content"]
        elif m["role"] == "assistant":
            asked[m["id"]] = last_user
    return asked


def _first_line(text: str, limit: int) -> str:
    line = " ".join(text.strip().split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def draft_message(
    db: ChatDatabase, session_id: str, files: List[str], root: str
) -> Dict[str, str]:
    """A commit message drafted from the turns that changed *files*."""
    asked = _user_requests(db, session_id)
    keys = {os.path.normcase(f) for f in files}
    requests: List[str] = []
    for turn in file_snapshots.list_turns(session_id):
        if turn["restored_at"] or turn["message_id"] is None:
            continue
        if not any(os.path.normcase(f["path"]) in keys for f in turn["files"]):
            continue
        request = asked.get(turn["message_id"], "").strip()
        if request and request not in requests:
            requests.append(request)
    title = _first_line(requests[0], _TITLE_MAX) if requests else "Update files"
    rel = sorted(os.path.relpath(f, root).replace(os.sep, "/") for f in files)
    body_lines = []
    if len(requests) > 1:
        body_lines += [f"- {_first_line(r, 100)}" for r in requests]
        body_lines.append("")
    body_lines.append("Files:")
    body_lines += [f"- {r}" for r in rel]
    return {"title": title, "body": "\n".join(body_lines)}


def _repo_state(root: str) -> Dict[str, Any]:
    git = _git_exe()
    # Names the branch before its first commit too; "HEAD" means detached.
    branch = (
        _run([git, "symbolic-ref", "--short", "-q", "HEAD"], root, ok_codes=(0, 1))
        or "HEAD"
    )
    remotes = _run([git, "remote"], root).split()
    default = None
    if "origin" in remotes:
        try:
            ref = _run(
                [git, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root
            )
            default = ref.split("/", 1)[1] if "/" in ref else ref
        except GitActionError:
            default = None
    return {
        "branch": branch,
        "remote": (
            "origin" if "origin" in remotes else (remotes[0] if remotes else None)
        ),
        "default_branch": default,
        "gh": shutil.which("gh") is not None,
    }


def describe(db: ChatDatabase, session_id: str) -> Dict[str, Any]:
    """The git repositories holding the chat's changed files, with a draft each."""
    if shutil.which("git") is None:
        return {"available": False, "repos": [], "reason": "Git is not installed."}
    by_root: Dict[str, List[str]] = {}
    for path in _changed_by_chat(db, session_id):
        parent = _existing_parent(path)
        root = _toplevel(parent) if parent else None
        if root:
            by_root.setdefault(root, []).append(path)
    repos = []
    for root, files in sorted(by_root.items()):
        state = _repo_state(root)
        rel = [os.path.relpath(f, root) for f in files]
        ignored = _ignored(root, rel)
        files = [f for f, r in zip(files, rel) if r not in ignored]
        rel = [r for r in rel if r not in ignored]
        dirty = (
            _run(
                [
                    _git_exe(),
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                    "--",
                    *rel,
                ],
                root,
            )
            if rel
            else ""
        )
        pending = [line[3:].strip().strip('"') for line in dirty.splitlines() if line]
        pr_blocked = None
        if not state["gh"]:
            pr_blocked = "Install the GitHub CLI (gh) to open a pull request from here."
        elif state["remote"] is None:
            pr_blocked = "This repository has no remote to push to."
        elif state["branch"] in ("HEAD", state["default_branch"], "main", "master"):
            pr_blocked = (
                f"You're on {state['branch']}. Switch to a new branch to open a "
                "pull request."
            )
        repos.append(
            {
                "root": root,
                **state,
                "files": [f.replace(os.sep, "/") for f in rel],
                "uncommitted": pending,
                "ignored": [f.replace(os.sep, "/") for f in ignored],
                "draft": draft_message(db, session_id, files, root),
                "pr_blocked": pr_blocked,
            }
        )
    return {"available": True, "repos": repos}


def _check_repo(db: ChatDatabase, session_id: str, root: str) -> List[str]:
    """The chat's changed files in *root*; refuses a repo the chat can't touch."""
    from . import session_environment

    env = session_environment.load(db, session_id)
    for withheld in env["withheld"]:
        if session_environment.within(root, withheld):
            raise GitActionError(
                f"{root} is in {withheld}, which you removed from this chat."
            )
    files = [
        f
        for f in _changed_by_chat(db, session_id)
        if session_environment.within(f, root)
    ]
    if not files:
        raise GitActionError(f"This chat changed no files in {root}.")
    return files


def commit(
    db: ChatDatabase, session_id: str, root: str, message: str, confirmed: bool
) -> Dict[str, Any]:
    """Stage only the chat's files in *root* and commit them with *message*."""
    if not confirmed:
        raise GitActionError("Nothing was committed: the commit was not confirmed.")
    if not message.strip():
        raise GitActionError("Nothing was committed: the message is empty.")
    files = _check_repo(db, session_id, root)
    git = _git_exe()
    rel = [os.path.relpath(f, root) for f in files]
    # Git refuses to stage an ignored file, so those stay out of the commit.
    ignored = _ignored(root, rel)
    rel = [r for r in rel if r not in ignored]
    if not rel:
        raise GitActionError(
            "Nothing was committed: every file this chat changed in "
            f"{root} is ignored by git (.gitignore)."
        )
    # --all stages the deletions of files the chat removed too.
    _run([git, "add", "--all", "--", *rel], root)
    staged = _run([git, "diff", "--cached", "--name-only", "--", *rel], root)
    if not staged:
        raise GitActionError(
            "Nothing was committed: the chat's changes are already committed."
        )
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".txt", delete=False
    ) as fh:
        fh.write(message.strip() + "\n")
        msg_file = fh.name
    try:
        # Only the chat's paths: anything else the user staged stays staged.
        _run([git, "commit", "-F", msg_file, "--", *rel], root)
    finally:
        os.unlink(msg_file)
    sha = _run([git, "rev-parse", "--short", "HEAD"], root)
    logger.info("Committed %d file(s) in %s as %s", len(rel), root, sha)
    return {
        "root": root,
        "commit": sha,
        "files": staged.splitlines(),
        "ignored": [f.replace(os.sep, "/") for f in ignored],
    }


def create_pr(
    db: ChatDatabase,
    session_id: str,
    root: str,
    title: str,
    body: str,
    confirmed: bool,
) -> Dict[str, Any]:
    """Push the current branch (never forced) and open a pull request."""
    from . import session_environment

    if not confirmed:
        raise GitActionError("No pull request was opened: it was not confirmed.")
    if not title.strip():
        raise GitActionError("No pull request was opened: the title is empty.")
    if session_environment.load(db, session_id)["internet_off"]:
        raise GitActionError(
            "Internet is off for this chat. Turn it on under Environment to push "
            "and open a pull request."
        )
    _check_repo(db, session_id, root)
    state = _repo_state(root)
    if not state["gh"]:
        raise GitActionError(
            "The GitHub CLI (gh) is not installed. Install it from "
            "https://cli.github.com and run `gh auth login`."
        )
    if state["remote"] is None:
        raise GitActionError(f"{root} has no remote to push to.")
    if state["branch"] in ("HEAD", state["default_branch"], "main", "master"):
        raise GitActionError(
            f"You're on {state['branch']}. Switch to a new branch to open a pull "
            "request."
        )
    _run([_git_exe(), "push", "--set-upstream", state["remote"], "HEAD"], root)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".md", delete=False
    ) as fh:
        fh.write(body)
        body_file = fh.name
    try:
        out = _run(
            [
                shutil.which("gh") or "gh",
                "pr",
                "create",
                "--title",
                title.strip(),
                "--body-file",
                body_file,
            ],
            root,
        )
    finally:
        os.unlink(body_file)
    url = next((w for w in out.split() if w.startswith("https://")), out)
    logger.info("Opened a pull request from %s: %s", root, url)
    return {"root": root, "url": url}
