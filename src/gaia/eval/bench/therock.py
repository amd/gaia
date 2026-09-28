# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TheRock tasks: a real codebase at the commit before a real fix, from public git.

Nothing of TheRock is committed here, only task definitions naming two commits:
``base`` (the fix's true parent) and ``merge`` (the fix). At run time:

- :func:`checkout` gives the agent a single-branch, shallow checkout of
  ``base``, then removes the remote. Its history stops at ``base``, so the fix
  is not on disk, and git offers no way back to GitHub.
- :func:`reference_diff` fetches ``merge`` and diffs it against its first
  parent (``merge^..merge``), only in the grading step, after the agent has
  exited. GitHub's ``base.sha`` for a pull request is not necessarily that
  parent (for #7998 it is not), so the parent is checked against ``base``.

Fetched base commits are cached per repository URL under the work root, so
repeats do not refetch. The cache never holds a fix.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Mapping, Sequence

#: Commits of history behind the base, so ``git log`` shows real context.
HISTORY_DEPTH = 200
GIT_TIMEOUT_S = 900


class TheRockError(RuntimeError):
    """The checkout or the reference diff could not be built as pinned."""


def validate(block: Mapping[str, object], where: str) -> None:
    for key in ("base", "merge"):
        sha = block.get(key)
        if not (
            isinstance(sha, str)
            and len(sha) == 40
            and all(c in "0123456789abcdef" for c in sha)
        ):
            raise ValueError(f"{where}: therock.{key} must be a full 40-hex commit SHA")
    files = block.get("reference_files")
    if not (
        isinstance(files, list) and files and all(isinstance(f, str) for f in files)
    ):
        raise ValueError(
            f"{where}: therock.reference_files must list the files the fix touches"
        )
    unknown = set(block) - {"base", "merge", "reference_files"}
    if unknown:
        raise ValueError(f"{where}: unknown therock keys {sorted(unknown)}")


def _git(args: Sequence[str], cwd: Path | None = None) -> str:
    git = shutil.which("git")
    if not git:
        raise TheRockError("TheRock tasks need git on PATH.")
    try:
        proc = subprocess.run(
            [git, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_S,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise TheRockError(
            f"`git {' '.join(args)}` failed: {exc.stderr.strip()[-500:]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TheRockError(
            f"`git {' '.join(args)}` did not finish in {GIT_TIMEOUT_S}s"
        ) from exc
    return proc.stdout


def _cache(work_root: Path, url: str) -> Path:
    key = hashlib.sha256(url.encode()).hexdigest()[:12]
    cache = work_root / "cache" / f"therock-{key}.git"
    if not cache.is_dir():
        cache.parent.mkdir(parents=True, exist_ok=True)
        _git(["init", "--quiet", "--bare", str(cache)])
    return cache


def _branch(sha: str) -> str:
    return f"base-{sha[:12]}"


def checkout(workdir: Path, url: str, base: str, merge: str, work_root: Path) -> None:
    """*workdir* becomes a checkout of *base* whose history cannot contain *merge*."""
    cache = _cache(work_root, url)
    branch = _branch(base)
    if not _git(["for-each-ref", f"refs/heads/{branch}"], cwd=cache).strip():
        _git(
            [
                "fetch",
                "--quiet",
                "--no-tags",
                f"--depth={HISTORY_DEPTH}",
                url,
                f"{base}:refs/heads/{branch}",
            ],
            cwd=cache,
        )
    _git(
        [
            "clone",
            "--quiet",
            "--no-tags",
            "--single-branch",
            f"--branch={branch}",
            f"--depth={HISTORY_DEPTH}",
            (cache.resolve()).as_uri(),
            str(workdir),
        ]
    )
    _git(["remote", "remove", "origin"], cwd=workdir)
    _git(["checkout", "--quiet", "--detach"], cwd=workdir)
    _git(["branch", "--quiet", "-D", branch], cwd=workdir)
    head = _git(["rev-parse", "HEAD"], cwd=workdir).strip()
    if head != base:
        raise TheRockError(f"checkout is at {head}, not the pinned base {base}")
    if _has_commit(workdir, merge):
        raise TheRockError(
            f"the checkout's history contains the fix {merge}; the task would be "
            "answerable from git. Refusing to run it."
        )
    remotes = _git(["remote"], cwd=workdir).strip()
    if remotes:
        raise TheRockError(f"the checkout still has remotes: {remotes}")


def _has_commit(repo: Path, sha: str) -> bool:
    git = shutil.which("git") or "git"
    return (
        subprocess.run(
            [git, "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=repo,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def reference_diff(url: str, base: str, merge: str) -> str:
    """``merge^..merge`` from the public repository; checks ``merge^`` is *base*."""
    with tempfile.TemporaryDirectory(prefix="gaia-therock-ref-") as tmp:
        repo = Path(tmp) / "ref.git"
        _git(["init", "--quiet", "--bare", str(repo)])
        _git(["fetch", "--quiet", "--no-tags", "--depth=2", url, merge], cwd=repo)
        parent = _git(["rev-parse", f"{merge}^"], cwd=repo).strip()
        if parent != base:
            raise TheRockError(
                f"{merge}'s parent is {parent}, not the pinned base {base}. The task "
                "would grade against a different change than the agent was given."
            )
        return _git(["diff", "--no-color", f"{merge}^", merge], cwd=repo)


def agent_diff(workdir: Path) -> str:
    """Everything the agent changed, new files included."""
    _git(["add", "--all", "--intent-to-add"], cwd=workdir)
    return _git(["-c", "core.quotepath=off", "diff", "--no-color", "HEAD"], cwd=workdir)


def touched_files(diff: str) -> List[str]:
    """Files a diff adds, changes or deletes."""
    return sorted(
        {
            line[6:].strip()
            for line in diff.splitlines()
            if line.startswith(("+++ b/", "--- a/"))
        }
    )
