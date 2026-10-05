# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Working trees at pinned commits, filled from the bench's bare-repo cache.

One working tree per repository is reused across commits: moving it to the
next commit leaves unchanged files byte-identical, which is exactly what an
incremental re-index sees on a developer's machine. The bare caches are the
ones ``gaia eval tasks`` already fills (:func:`gaia.eval.bench.swebench.fetch_commit`),
so a repository fetched for one benchmark is not fetched again for another.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from gaia.eval.bench.swebench import fetch_commit

GIT_TIMEOUT_S = 900


class RepoError(RuntimeError):
    """A repository could not be fetched or moved to a commit."""


def git(args: Sequence[str], cwd: Optional[Path] = None) -> str:
    exe = shutil.which("git")
    if not exe:
        raise RepoError("The retrieval benchmark needs git on PATH.")
    try:
        proc = subprocess.run(
            [exe, *args],
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
        raise RepoError(
            f"`git {' '.join(args)}` failed in {cwd}: {exc.stderr.strip()[-500:]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RepoError(
            f"`git {' '.join(args)}` did not finish in {GIT_TIMEOUT_S}s"
        ) from exc
    return proc.stdout


@dataclass(frozen=True)
class RepoPin:
    """A repository at one commit."""

    name: str
    url: str
    commit: str
    #: Bare-cache name prefix; ``therock`` shares :mod:`gaia.eval.bench.therock`'s cache.
    cache_prefix: str = "swebench"


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")


def checkout_at(pin: RepoPin, workdir: Path, cache_root: Path) -> Path:
    """*workdir* holds *pin.commit*, clean, with line endings as committed."""
    cache, branch = fetch_commit(pin.url, pin.commit, cache_root, pin.cache_prefix)
    if not (workdir / ".git").is_dir():
        workdir.mkdir(parents=True, exist_ok=True)
        git(["init", "--quiet", str(workdir)])
        # Files must match the committed bytes: CRLF conversion would change
        # every chunk, and Windows needs long paths for deep trees.
        git(["config", "core.autocrlf", "false"], cwd=workdir)
        git(["config", "core.longpaths", "true"], cwd=workdir)
    git(
        [
            "fetch",
            "--quiet",
            "--no-tags",
            # The cache is shallow; each new base brings its own shallow roots.
            "--update-shallow",
            cache.resolve().as_uri(),
            f"refs/heads/{branch}",
        ],
        cwd=workdir,
    )
    git(["checkout", "--quiet", "--force", "--detach", pin.commit], cwd=workdir)
    git(["clean", "-ffdxq"], cwd=workdir)
    head = git(["rev-parse", "HEAD"], cwd=workdir).strip()
    if head != pin.commit:
        raise RepoError(f"{pin.name}: checkout is at {head}, not {pin.commit}")
    return workdir


def first_parent_chain(workdir: Path, tip: str, count: int) -> List[str]:
    """The *count* commits ending at *tip* along first parents, oldest first."""
    shas = git(
        ["rev-list", "--first-parent", f"--max-count={count}", tip], cwd=workdir
    ).split()
    if len(shas) < count:
        raise RepoError(
            f"only {len(shas)} first-parent commits reach {tip[:12]}; "
            f"{count} were asked for (the cache holds a limited history depth)"
        )
    return list(reversed(shas))


def changed_files(workdir: Path, old: str, new: str) -> List[str]:
    return git(["diff", "--name-only", old, new], cwd=workdir).split("\n")[:-1]


def commit_subject(workdir: Path, sha: str) -> str:
    return git(["show", "-s", "--format=%s", sha], cwd=workdir).strip()


def commit_time(workdir: Path, sha: str) -> int:
    return int(git(["show", "-s", "--format=%ct", sha], cwd=workdir).strip())


def read_text(workdir: Path, rel: str) -> Optional[str]:
    path = workdir / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")
