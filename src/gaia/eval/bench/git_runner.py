# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one ``git`` runner the benchmarks fetch and check out repositories with."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional, Sequence, Type

GIT_TIMEOUT_S = 900


def run_git(
    args: Sequence[str],
    cwd: Optional[Path],
    error_cls: Type[Exception],
    missing_git: str,
    *,
    name_cwd: bool = False,
) -> str:
    """Run ``git *args`` in *cwd* and return its stdout.

    Every failure raises *error_cls*: *missing_git* when git is not on PATH,
    otherwise the command and its stderr (plus *cwd* when *name_cwd*).
    """
    git = shutil.which("git")
    if not git:
        raise error_cls(missing_git)
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
        where = f" in {cwd}" if name_cwd else ""
        raise error_cls(
            f"`git {' '.join(args)}` failed{where}: {exc.stderr.strip()[-500:]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise error_cls(
            f"`git {' '.join(args)}` did not finish in {GIT_TIMEOUT_S}s"
        ) from exc
    return proc.stdout
