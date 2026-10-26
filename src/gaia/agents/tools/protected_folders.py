# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Home folders a default search must not walk into on macOS (#5578).

The first read of one of these folders makes macOS show "GAIA would like to
access files in your Documents folder" and blocks the reading thread inside the
kernel until someone answers. No time budget can interrupt that, so a search the
user did not point at them must not enter them at all. A tool whose job is to
read them asks :func:`macos_access` first, and reports the open dialog
instead of waiting on it.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from gaia.agents.tools.search_scope import is_broad_root, search_roots

#: Relative to home. iCloud Drive is guarded the same way, ~/Music and the TV
#: app's ~/Movies/TV behind the "Media & Apple Music" prompt, the Photos
#: library behind "Photos", and other apps' sandboxed data behind "access data
#: from other apps".
MACOS_PROTECTED_HOME_FOLDERS = (
    "Desktop",
    "Documents",
    "Downloads",
    "Library/Containers",
    "Library/Group Containers",
    "Library/Mobile Documents",
    "Movies/TV",
    "Music",
    "Pictures/Photos Library.photoslibrary",
)

#: How long the guarded folders' first reads may take, together, before a read
#: still running counts as held by the macOS permission dialog. A granted folder
#: answers in milliseconds.
MACOS_PROMPT_WAIT_S = 2.0

#: What a probe of a guarded folder found.
GRANTED, DENIED, ASKING = "granted", "denied", "asking"

_probes: Dict[Path, "_Probe"] = {}
_probes_lock = threading.Lock()


def is_macos_guarded(path: Any) -> bool:
    """True on macOS when *path* is one of :data:`MACOS_PROTECTED_HOME_FOLDERS`."""
    if sys.platform != "darwin":
        return False
    home = Path.home()
    target = Path(path).expanduser().resolve()
    return any(target == (home / rel).resolve() for rel in MACOS_PROTECTED_HOME_FOLDERS)


class _Probe(threading.Thread):
    """Reads a guarded folder's first entry; blocks while macOS's dialog is up."""

    def __init__(self, folder: Path):
        super().__init__(name=f"macos-permission-probe:{folder.name}", daemon=True)
        self.folder = folder
        self.outcome = GRANTED

    def run(self) -> None:
        self.outcome = _read_first_entry(self.folder)


def macos_access(
    folders: Iterable[Any], wait: Optional[float] = None
) -> Dict[Path, str]:
    """:data:`GRANTED`, :data:`DENIED` or :data:`ASKING` for each guarded folder.

    Keyed by resolved path; folders macOS does not guard are left out. A read
    blocks in the kernel until the user answers the dialog, so each runs on a
    daemon thread, all started at once and joined against one shared *wait*
    (default :data:`MACOS_PROMPT_WAIT_S`). One still running is
    :data:`ASKING`, and ends by itself once the user answers. A probe already
    waiting is reused, so asking again never stacks blocked threads.
    """
    if wait is None:
        wait = MACOS_PROMPT_WAIT_S
    probes: Dict[Path, _Probe] = {}
    with _probes_lock:
        for folder in folders:
            if not is_macos_guarded(folder):
                continue
            key = Path(folder).expanduser().resolve()
            probe = _probes.get(key)
            if probe is None or not probe.is_alive():
                probe = _Probe(key)
                _probes[key] = probe
                probe.start()
            probes[key] = probe
    deadline = time.monotonic() + wait
    for probe in probes.values():
        probe.join(max(0.0, deadline - time.monotonic()))
    return {
        key: ASKING if probe.is_alive() else probe.outcome
        for key, probe in probes.items()
    }


def _read_first_entry(folder: Path) -> str:
    try:
        with os.scandir(folder) as entries:
            next(entries, None)
    except PermissionError:
        return DENIED
    except OSError:
        # Gone, or unreadable for another reason: the caller's own read reports it.
        return GRANTED
    return GRANTED


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
