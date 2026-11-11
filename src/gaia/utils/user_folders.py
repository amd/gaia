# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Locations of the user-visible folders supplied by the operating system."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_WINDOWS_DOCUMENTS_FOLDER = (
    0xFDD39AD0,
    0x238F,
    0x46AF,
    (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
)
_WINDOWS_PROFILE_FOLDER = (
    0x5E6C858F,
    0x0E22,
    0x4760,
    (0x9A, 0xFE, 0xEA, 0x33, 0x17, 0xB6, 0x71, 0x73),
)
_XDG_DOCUMENTS_DIR = re.compile(r"^XDG_DOCUMENTS_DIR=(?P<value>.*)$")
# Return the configured path without checking it exists, so a redirect to an
# unreachable share is never touched before the caller's roots check.
_KF_FLAG_DONT_VERIFY = 0x4000


def _windows_known_folder(folder: tuple) -> Path | None:
    """Return a Windows known folder, if the shell can resolve it."""
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

        folder_id = _GUID(*folder)
        path = wintypes.LPWSTR()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), _KF_FLAG_DONT_VERIFY, None, ctypes.byref(path)
        )
        try:
            if result or not path.value:
                return None
            return Path(path.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path)
    except (AttributeError, OSError):
        return None


def _windows_documents_dir() -> Path | None:
    """Return Windows' Documents known folder, if the shell can resolve it."""
    return _windows_known_folder(_WINDOWS_DOCUMENTS_FOLDER)


def _windows_profile_dir() -> Path | None:
    """Return the signed-in user's profile folder, independent of HOME."""
    return _windows_known_folder(_WINDOWS_PROFILE_FOLDER)


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except (OSError, ValueError):
        return a == b


def _same_path(a: Path, b: Path) -> bool:
    """Compare two paths without touching the filesystem."""
    return os.path.normpath(os.path.abspath(a)) == os.path.normpath(os.path.abspath(b))


def _account_home() -> Path | None:
    """Return the OS account's home folder, independent of HOME."""
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, AttributeError):
        return None


def _xdg_config_home(home: Path) -> Path:
    """Return the XDG config folder for *home*.

    ``$XDG_CONFIG_HOME`` belongs to this process' account, so it is honoured
    only when *home* is that account's home; a sandbox HOME keeps its own
    ``.config`` even when the variable is inherited.
    """
    configured = os.environ.get("XDG_CONFIG_HOME", "")
    account = _account_home()
    if (
        configured
        and os.path.isabs(configured)
        and account is not None
        and _same_path(home, account)
    ):
        return Path(configured)
    return home / ".config"


def _expand_home(value: str, home: Path) -> str:
    """Expand ``$HOME``, ``${HOME}`` and ``~`` against *home*, not the environment."""
    value = value.replace("${HOME}", str(home))
    for prefix in ("$HOME", "~"):
        if value == prefix or value.startswith(prefix + "/"):
            return str(home) + value[len(prefix) :]
    return value


def _linux_documents_dir(home: Path) -> Path | None:
    """Read ``XDG_DOCUMENTS_DIR`` without using this process' HOME variable."""
    try:
        raw = (_xdg_config_home(home) / "user-dirs.dirs").read_bytes()
        # The file holds raw filesystem bytes; decode them the way paths are.
        lines = os.fsdecode(raw).splitlines()
    except (OSError, ValueError):
        return None

    for line in lines:
        match = _XDG_DOCUMENTS_DIR.match(line.strip())
        if not match:
            continue
        value = match.group("value").strip().strip('"')
        if not value or "\x00" in value:
            return None
        path = Path(_expand_home(value, home))
        path = path if path.is_absolute() else home / path
        # xdg-user-dirs marks a disabled folder by pointing it at $HOME.
        return None if _same_path(path, home) else path
    return None


def documents_dir(home: Path | None = None) -> Path:
    """Return the Documents folder the user sees, with a portable fallback.

    Windows Known Folder Move can redirect Documents to OneDrive. Linux users
    can configure a translated or relocated Documents folder in user-dirs.dirs.
    If the platform setting is unavailable, the conventional home/Documents is
    still the least surprising location.
    """
    home = (home or Path.home()).expanduser()
    if sys.platform == "win32":
        # Only the signed-in user's own profile is redirected: a test or
        # sandbox HOME must not pick up that user's real Documents.
        profile = _windows_profile_dir()
        if profile is not None and _same_folder(home, profile):
            return _windows_documents_dir() or home / "Documents"
        return home / "Documents"
    if sys.platform.startswith("linux"):
        return _linux_documents_dir(home) or home / "Documents"
    return home / "Documents"
