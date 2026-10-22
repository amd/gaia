# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Home folders a default search must not walk into on macOS (#5578).

The first read of one of these folders makes macOS show "GAIA would like to
access files in your Documents folder" and blocks the reading thread inside the
kernel until someone answers. No time budget can interrupt that, so a search the
user did not point at them must not enter them at all.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

from gaia.agents.tools.search_scope import is_broad_root, search_roots

#: Relative to home. iCloud Drive is guarded the same way.
MACOS_PROTECTED_HOME_FOLDERS = (
    "Desktop",
    "Documents",
    "Downloads",
    "Library/Mobile Documents",
)


def _covers(granted: Path, folder: Path) -> bool:
    return granted == folder or granted in folder.parents


class ProtectedFolders:
    """Which guarded folders this search skips, and which it skipped.

    A folder is walked when the user named it (or a folder holding it) as the
    search scope, or when it is inside one of the chat's allowed paths. Home
    and ``/`` grant nothing: the flagship's default sandbox is ``~``.
    """

    def __init__(self, host: Any, named: Iterable[Any] = ()):
        self.skipped: List[Path] = []
        self._guarded: Dict[Path, Path] = {}
        if sys.platform != "darwin":
            return
        granted = [
            Path(p).expanduser().resolve()
            for p in [*named, *search_roots(host)]
            if not is_broad_root(Path(p))
        ]
        home = Path.home()
        for rel in MACOS_PROTECTED_HOME_FOLDERS:
            folder = home / rel
            resolved = folder.resolve()
            if any(_covers(g, resolved) for g in granted):
                continue
            self._guarded[folder] = folder
            self._guarded[resolved] = folder

    def blocks(self, path: Any) -> bool:
        """True (and recorded) when *path* is a guarded folder to skip."""
        if not self._guarded:
            return False
        folder = self._guarded.get(Path(path))
        if folder is None:
            return False
        if folder not in self.skipped:
            self.skipped.append(folder)
        return True

    def note(self) -> str:
        """One line for the tool result, or ``""`` when nothing was skipped."""
        if not self.skipped:
            return ""
        home = Path.home()
        order = [home / rel for rel in MACOS_PROTECTED_HOME_FOLDERS]
        names = ", ".join(
            f"~/{f.relative_to(home).as_posix()}"
            for f in sorted(self.skipped, key=order.index)
        )
        return (
            f"Skipped {names}: macOS asks for permission before GAIA can read "
            "them; ask the user to name the folder to search it."
        )
