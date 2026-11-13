# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The allowed-folders check every file tool goes through.

No rule lives here. Each function asks the host's ``PathValidator`` — the same
``validate_read`` / ``validate_write`` that ``read_file`` and ``write_file``
use — so a tool that lists, searches, indexes or saves cannot drift from them.
The validator expands ``~`` and resolves ``..`` and symlinks before it decides.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from gaia.agents.base.verification import NOT_EXECUTED
from gaia.agents.tools.search_scope import path_validator_of

_ON_WINDOWS = os.name == "nt"
#: Seconds between tries at a file another program holds open.
IN_USE_RETRY_DELAYS = (0.5, 1.0, 2.0)
_ERROR_ACCESS_DENIED = 5
#: ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION: another program has it.
_IN_USE_WINERRORS = frozenset({32, 33})


def _open_for_read(path: str) -> None:
    with open(path, "rb"):
        pass


def _open_winerror(path: str) -> Optional[int]:
    """The Windows error opening *path* for reading gives now, or None if it opens.

    Python's ``open`` reports a sharing violation and an access denial alike as
    ``EACCES``; only the Win32 error tells them apart.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    generic_read, share_all, open_existing, normal = 0x80000000, 0x7, 3, 0x80
    handle = kernel32.CreateFileW(
        path, generic_read, share_all, None, open_existing, normal, None
    )
    if handle == wintypes.HANDLE(-1).value:
        return ctypes.get_last_error()
    kernel32.CloseHandle(handle)
    return None


def _in_use_error(path: str, waited: float) -> Dict[str, Any]:
    tried = f" (tried for {waited:g} seconds)" if waited else ""
    return {
        "status": "error",
        "error_type": "file_in_use",
        "error": (
            f"{path} is in use by another program, which has it locked, so it "
            f"could not be read{tried}. This is not a permission problem and "
            "is usually brief: read it again later in the task. If it is "
            "still locked when you finish, tell the user which file was in use."
        ),
    }


def _denied_error(path: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "error_type": "access_denied",
        "error": (
            f"Access denied: Windows does not let this account read {path}. "
            "Trying again will not help; tell the user, who can check the "
            "file's permissions."
        ),
    }


def locked_file_error(path: Any) -> Optional[Dict[str, Any]]:
    """None once *path* opens for reading; else why it can't, after a short wait.

    Windows only: there another program holding the file open without sharing
    makes ``open`` fail, usually for moments. A sharing or lock violation is
    retried at :data:`IN_USE_RETRY_DELAYS`; an access denial is reported at
    once. Anything else is left to the caller's own read.
    """
    if not _ON_WINDOWS:
        return None
    path = str(path)
    waited = 0.0
    for delay in (*IN_USE_RETRY_DELAYS, None):
        try:
            _open_for_read(path)
            return None
        except PermissionError:
            code = _open_winerror(path)
        if code == _ERROR_ACCESS_DENIED:
            return _denied_error(path)
        if code is not None and code not in _IN_USE_WINERRORS:
            return None
        if delay is None:
            break
        time.sleep(delay)
        waited += delay
    return _in_use_error(path, waited)


def read_permission_error(path: Any) -> Optional[Dict[str, Any]]:
    """The error for a read that raised ``PermissionError`` after *path* opened.

    On Windows a lock or a denial *path* still shows; None when it now opens
    cleanly (the error may not be about this file) and on other systems, so the
    caller reports the OS error as it is.
    """
    if not _ON_WINDOWS:
        return None
    code = _open_winerror(str(path))
    if code == _ERROR_ACCESS_DENIED:
        return _denied_error(str(path))
    if code in _IN_USE_WINERRORS:
        return _in_use_error(str(path), 0)
    return None


def read_access_error(
    host: Any, path: Any, *, prompt_user: bool = True
) -> Optional[Dict[str, Any]]:
    """Refusal for reading or listing *path*, or None when it is allowed.

    Out-of-scope paths go through the validator's approval prompt, exactly as
    ``read_file`` does. A host with no validator is library use with no
    sandbox declared, and is not checked.

    Args:
        host: The agent (or mixin host) whose validator applies.
        path: The path the tool was given.
        prompt_user: Ask the user when *path* is out of scope.

    Returns:
        A tool error dict, or None.
    """
    validator = path_validator_of(host)
    if validator is None:
        return None
    allowed, reason = validator.validate_read(str(path), prompt_user=prompt_user)
    if allowed:
        return None
    return {**NOT_EXECUTED, "status": "error", "error": reason}


def missing_file_error(path: Any) -> Dict[str, Any]:
    """The error for a file that isn't there, with when to stop looking.

    Without the stop, a model handed a wrong absolute path searched folder
    after folder across the whole drive for minutes before telling the user.
    """
    path = str(path)
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    if parent and not os.path.exists(parent):
        where = f" The folder '{parent}' does not exist either."
    elif parent:
        where = f" The folder '{parent}' exists, but the file is not in it."
    else:
        where = ""
    return {
        **NOT_EXECUTED,
        "status": "error",
        "error": (
            f"File not found: {path}.{where} If the user may have the location "
            f"wrong, run one file search for '{name}'. If that finds nothing, stop "
            "and tell the user the file is not on this PC and ask where it is; "
            "do not keep searching other folders or drives."
        ),
    }


def write_access_error(
    host: Any, path: Any, *, content_size: int = 0
) -> Optional[Dict[str, Any]]:
    """Refusal for writing *path*, or None when it is allowed.

    Args:
        host: The agent (or mixin host) whose validator applies.
        path: The file the tool will create or replace.
        content_size: Bytes about to be written, when known.

    Returns:
        A tool error dict, or None.
    """
    validator = path_validator_of(host)
    if validator is None:
        return None
    allowed, reason = validator.validate_write(str(path), content_size=content_size)
    if allowed:
        return None
    return {**NOT_EXECUTED, "status": "error", "error": reason}


def readable_entry(host: Any, path: Any) -> bool:
    """Whether a file met while walking an approved folder may be read.

    Never prompts: the folder was approved, and a symlink or secret inside it
    is skipped rather than turned into a dialog per file.

    Args:
        host: The agent (or mixin host) whose validator applies.
        path: A file found by the walk.

    Returns:
        True when the file is in scope and not a secret.
    """
    return read_access_error(host, path, prompt_user=False) is None
