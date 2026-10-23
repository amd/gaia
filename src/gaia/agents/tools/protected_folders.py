# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Home folders a default search must not walk into on macOS (#5578).

The first read of one of these folders makes macOS show "GAIA would like to
access files in your Documents folder" and blocks the reading thread inside the
kernel until someone answers. No time budget can interrupt that, so a search the
user did not point at them must not enter them at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from gaia.agents.tools.search_scope import is_broad_root, search_roots

#: Relative to home. iCloud Drive is guarded the same way, ~/Music and the TV
#: app's ~/Movies/TV behind the "Media & Apple Music" prompt, and the Photos
#: library behind "Photos".
MACOS_PROTECTED_HOME_FOLDERS = (
    "Desktop",
    "Documents",
    "Downloads",
    "Library/Mobile Documents",
    "Movies/TV",
    "Music",
    "Pictures/Photos Library.photoslibrary",
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
        # A walk of / meets ~/Music again as /System/Volumes/Data/Users/<u>/Music.
        self._by_id: Dict[Tuple[int, int], Path] = {}
        self._names: set = set()
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
            try:
                st = os.stat(resolved)
            except FileNotFoundError:
                continue
            self._by_id[(st.st_dev, st.st_ino)] = folder
        self._names = {f.name for f in self._guarded.values()}

    def blocks(self, path: Any) -> bool:
        """True (and recorded) when *path* is a guarded folder to skip.

        Matches by path, then by inode for a folder of the same name, so an alias
        of a guarded folder (a firmlink, a symlinked parent) is skipped too.
        Stat-ing the folder itself never triggers the permission prompt.
        """
        if not self._guarded:
            return False
        path = Path(path)
        folder = self._guarded.get(path)
        if folder is None and path.name in self._names:
            try:
                st = os.stat(path)
            except FileNotFoundError:
                return False
            folder = self._by_id.get((st.st_dev, st.st_ino))
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
