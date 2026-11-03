# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Where a file write must always be asked about.

One set of rules for every path that lets a write skip its prompt: "accept
edits" in a workspace (:func:`gaia.agents.base.console.edit_is_inside`) and an
"always allow" folder grant (:mod:`gaia.agents.base.tool_grants`).
"""

from __future__ import annotations

import os
import shutil
from typing import Iterable


def is_too_broad_folder(folder: str) -> bool:
    """A home folder or a drive root holds everything the user owns."""
    real = os.path.normcase(os.path.realpath(folder))
    home = os.path.normcase(os.path.realpath(os.path.expanduser("~")))
    return real == home or os.path.dirname(real) == real


def has_hidden_part(parts: Iterable[str]) -> bool:
    """True when any path part is a dotfile or dot-folder (``.`` and ``..`` aside)."""
    return any(part.startswith(".") and part not in (".", "..") for part in parts)


def lands_where_code_runs(path: str) -> bool:
    """True when writing *path* can change what runs without an approval.

    Anything under ``.git`` (a hook or config runs on the next git command), a
    file in a folder on ``PATH``, and on Windows a file cmd.exe would run in
    place of a real program. Checks *path* as given; resolve it first to
    check where a write really lands.
    """
    parts = {part.lower() for part in path.replace("\\", "/").split("/")}
    if ".git" in parts:
        return True
    parent = os.path.normcase(os.path.dirname(os.path.abspath(path)))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and os.path.normcase(os.path.realpath(entry)) == parent:
            return True
    return shadows_a_program(path)


def shadows_a_program(path: str) -> bool:
    """On Windows, cmd.exe looks in the current folder before ``PATH``, so a
    ``git.bat`` written into the workspace runs instead of git."""
    if os.name != "nt":
        return False
    stem, ext = os.path.splitext(os.path.basename(path))
    pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").lower().split(";")
    return ext.lower() in pathext and shutil.which(stem) is not None
