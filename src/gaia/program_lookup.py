# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Whether a program can be run from here, including one installed just now.

A process keeps the ``PATH`` it started with, so a program installed while GAIA
is running stays invisible to ``shutil.which`` and to every child process until
GAIA restarts. :func:`refresh_path` gives this process the entries an installer
added since, and :func:`resolve_refreshing` looks again after doing so.

Only ``PATH`` is refreshed, and entries are only ever appended: nothing that
already resolves can be shadowed, and no other variable an installer wrote
(a proxy, a certificate file) changes how GAIA itself behaves mid-session.
"""

from __future__ import annotations

import ntpath
import os
import shutil
import sys
import threading
from typing import List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

_refresh_lock = threading.Lock()


def resolve(program: str) -> Optional[str]:
    """Where *program* runs from on this process's ``PATH``, or None. No side effects."""
    return shutil.which(program)


def resolve_refreshing(program: str) -> Optional[str]:
    """:func:`resolve`, looking again after a ``PATH`` refresh when it misses."""
    found = resolve(program)
    if found is None and refresh_path():
        found = resolve(program)
    return found


def refresh_path(after_install: bool = False) -> List[str]:
    """Append the ``PATH`` entries added since this process started.

    Windows reads the registry, which is what a new terminal would get. macOS
    asks the login shell, which runs the user's startup files, so it does that
    only when *after_install* says an install command just ran. Linux package
    managers install into folders already on ``PATH``.

    Returns:
        The entries added, empty when nothing changed.
    """
    with _refresh_lock:
        if sys.platform == "win32":
            candidates = _registry_path_entries()
        elif sys.platform == "darwin" and after_install:
            candidates = _login_path_entries()
        else:
            return []
        added = _append_new_entries(candidates)
    if added:
        logger.info("Added to PATH after an install: %s", os.pathsep.join(added))
    return added


def _registry_path_entries() -> List[str]:
    """The machine's then the user's ``Path`` from the Windows registry, expanded.

    Machine first, as Windows builds a new process's ``Path``.
    """
    import winreg  # pylint: disable=import-outside-toplevel,import-error

    entries: List[str] = []
    for root, subkey in (
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ):
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue  # this hive has no Path value
        entries += winreg.ExpandEnvironmentStrings(value).split(";")
    return entries


def _login_path_entries() -> List[str]:
    """The ``PATH`` a new Terminal window would have on macOS."""
    from gaia.login_path import (  # pylint: disable=import-outside-toplevel
        LoginPathError,
        read_login_path,
    )

    try:
        return read_login_path().split(os.pathsep)
    except LoginPathError as exc:
        logger.warning(
            "Could not re-read the login PATH after an install (%s). A program "
            "installed just now may not be found until GAIA restarts.",
            exc,
        )
        return []


def _entry_key(entry: str) -> str:
    """One spelling per folder, so ``C:\\Tools\\`` and ``c:\\tools`` are one entry."""
    if sys.platform == "win32":
        return ntpath.normcase(entry.rstrip("\\/"))
    return entry.rstrip("/") or entry


def _append_new_entries(candidates: List[str]) -> List[str]:
    """Append the *candidates* ``PATH`` lacks; returns the ones added.

    An entry is kept only when it is an absolute folder that exists. A relative
    entry would resolve against whatever folder a command runs in, and one with
    an unexpanded ``%VAR%`` names nothing.
    """
    existing = os.environ.get("PATH", "")
    seen = {_entry_key(p) for p in existing.split(os.pathsep) if p}
    added: List[str] = []
    for entry in (c.strip() for c in candidates):
        key = _entry_key(entry)
        if not entry or key in seen:
            continue
        seen.add(key)
        if "%" in entry or not os.path.isabs(entry) or not os.path.isdir(entry):
            continue
        added.append(entry)
    if added:
        # The existing value is kept as written; on POSIX an empty entry in it
        # means the current folder.
        os.environ["PATH"] = os.pathsep.join(([existing] if existing else []) + added)
    return added
