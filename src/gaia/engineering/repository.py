# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Managed source cache and worktrees. No operation edits the running installation."""

from __future__ import annotations

import errno
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

OFFICIAL_REMOTE = "https://github.com/amd/gaia.git"


@contextmanager
def repository_lock(path: Path, timeout: float = 15):
    """A bounded OS lock, released by the kernel if a client crashes."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt  # pylint: disable=import-error

                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Engineering store is busy; retry after the active operation"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt  # pylint: disable=import-error

                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class Repository:
    """Only the host selects the cache/remote; model tools receive a job ID."""

    def __init__(
        self, cache_root: Path, *, remote: str = OFFICIAL_REMOTE, timeout: float = 120
    ):
        if Path(cache_root).is_symlink():
            raise ValueError("Repository cache must not be a symlink")
        self.root = Path(cache_root).expanduser().resolve()
        self.repo = self.root / "repos" / "gaia.git"
        self.remote = remote
        self.timeout = timeout

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=self.timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"GAIA repository operation failed: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _check_repo(self) -> None:
        if self.repo.is_symlink():
            raise ValueError("Source cache cannot be a symlink")
        if (
            self.git("--git-dir", str(self.repo), "rev-parse", "--is-bare-repository")
            != "true"
        ):
            raise ValueError("Expected a bare GAIA source cache")
        if (
            self.git("--git-dir", str(self.repo), "remote", "get-url", "origin")
            != self.remote
        ):
            raise ValueError(
                "Cached GAIA remote does not match the configured official source"
            )

    def ensure_clone(self) -> dict:
        with repository_lock(self.root / "repository.lock", timeout=self.timeout):
            self.repo.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.repo.exists():
                staging = Path(tempfile.mkdtemp(prefix="clone-", dir=self.repo.parent))
                try:
                    self.git(
                        "clone", "--bare", "--", self.remote, str(staging / "gaia.git")
                    )
                    os.replace(staging / "gaia.git", self.repo)
                finally:
                    shutil.rmtree(staging)
            self._check_repo()
            self.git(
                "--git-dir",
                str(self.repo),
                "fetch",
                "origin",
                "+HEAD:refs/gaia/upstream",
            )
            head = self.git(
                "--git-dir", str(self.repo), "rev-parse", "refs/gaia/upstream^{commit}"
            )
            return {
                "path": str(self.repo),
                "head": head,
                "fetched_at": time.time(),
                "remote": self.remote,
            }

    def create_worktree(self, job_id: str, base: str = "refs/gaia/upstream") -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("Invalid job ID")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", base) or base.startswith("-"):
            raise ValueError("Invalid base reference")
        with repository_lock(self.root / "repository.lock", timeout=self.timeout):
            self._check_repo()
            base_ref = f"refs/gaia/bases/{job_id}"
            commit = self.git(
                "--git-dir",
                str(self.repo),
                "for-each-ref",
                "--format=%(objectname)",
                base_ref,
            )
            if not commit:
                commit = self.git(
                    "--git-dir",
                    str(self.repo),
                    "rev-parse",
                    "--verify",
                    f"{base}^{{commit}}",
                )
                self.git("--git-dir", str(self.repo), "update-ref", base_ref, commit)
            path = self.root / "worktrees" / job_id
            branch = f"codex/engineering-{job_id}"
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink():
                raise ValueError("Worktree cannot be a symlink")
            if path.exists():
                common = Path(
                    self.git("-C", str(path), "rev-parse", "--git-common-dir")
                )
                if not common.is_absolute():
                    common = path / common
                if common.resolve() != self.repo:
                    raise ValueError("Existing path is not this job's GAIA worktree")
                actual_branch = self.git(
                    "-C", str(path), "symbolic-ref", "--short", "HEAD"
                )
                if actual_branch != branch:
                    raise ValueError(
                        "Existing worktree branch changed; investigate before reusing it"
                    )
            else:
                existing = self.git(
                    "--git-dir",
                    str(self.repo),
                    "for-each-ref",
                    "--format=%(objectname)",
                    f"refs/heads/{branch}",
                )
                if existing:
                    self.git(
                        "--git-dir",
                        str(self.repo),
                        "worktree",
                        "add",
                        str(path),
                        branch,
                    )
                else:
                    self.git(
                        "--git-dir",
                        str(self.repo),
                        "worktree",
                        "add",
                        "-b",
                        branch,
                        str(path),
                        commit,
                    )
            return {
                "path": str(path),
                "branch": branch,
                "base_commit": commit,
                "repository": str(self.repo),
            }
