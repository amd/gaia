# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Locations of the user-visible folders supplied by the operating system."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_WINDOWS_DOCUMENTS_FOLDER = (
    0xFDD39AD0,
    0x238F,
    0x46AF,
    (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
)
_XDG_DOCUMENTS_DIR = re.compile(r"^XDG_DOCUMENTS_DIR=(?P<value>.*)$")


def _windows_documents_dir() -> Path | None:
    """Return Windows' Documents known folder, if the shell can resolve it."""
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder_id = _GUID(*_WINDOWS_DOCUMENTS_FOLDER)
        path = wintypes.LPWSTR()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(path)
        )
        if result or not path.value:
            return None
        try:
            return Path(path.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path)
    except (AttributeError, OSError):
        return None


def _linux_documents_dir(home: Path) -> Path | None:
    """Read ``XDG_DOCUMENTS_DIR`` without using this process' HOME variable."""
    try:
        lines = (
            (home / ".config" / "user-dirs.dirs")
            .read_text(encoding="utf-8")
            .splitlines()
        )
    except OSError:
        return None

    for line in lines:
        match = _XDG_DOCUMENTS_DIR.match(line.strip())
        if not match:
            continue
        value = match.group("value").strip().strip('"')
        if not value:
            return None
        value = value.replace("${HOME}", str(home)).replace("$HOME", str(home))
        path = Path(value).expanduser()
        return path if path.is_absolute() else home / path
    return None


def documents_dir(home: Path | None = None) -> Path:
    """Return the Documents folder the user sees, with a portable fallback.

    Windows Known Folder Move can redirect Documents to OneDrive. Linux users
    can configure a translated or relocated Documents folder in user-dirs.dirs.
    If the platform setting is unavailable, the conventional home/Documents is
    still the least surprising location.
    """
    actual_home = Path.home().expanduser()
    home = (home or actual_home).expanduser()
    if sys.platform == "win32":
        try:
            is_current_home = home.resolve() == actual_home.resolve()
        except OSError:
            is_current_home = home == actual_home
        return (
            _windows_documents_dir() if is_current_home else None
        ) or home / "Documents"
    if sys.platform.startswith("linux"):
        return _linux_documents_dir(home) or home / "Documents"
    return home / "Documents"
