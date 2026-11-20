# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Owner-only files and folders on NTFS, where ``chmod`` changes nothing.

One copy of the DACL lockdown the daemon's launch secret, the engineering
store and the device-pairing folder share (#2250).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


class OwnerOnlyError(RuntimeError):
    """A path could not be limited to its owner, or other users can reach it."""


def _pywin32() -> Any:
    try:
        import ntsecuritycon
        import win32api
        import win32security
    except ImportError as e:
        raise OwnerOnlyError(
            "pywin32 is not installed, so GAIA cannot limit files to the current "
            "user. There is no fallback: without an owner-only ACL, other local "
            "accounts can read them on NTFS. Run `pip install pywin32` (it ships "
            "as a core amd-gaia dependency on Windows, so `pip install --upgrade "
            "amd-gaia` also repairs this). See issue #2250."
        ) from e
    return ntsecuritycon, win32api, win32security


def _current_user_sid(win32api: Any, win32security: Any) -> Any:
    # From the process token, not by name: LookupAccountName fails with 1332
    # for the service and machine accounts a daemon runs as.
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
    )
    user_sid, _attributes = win32security.GetTokenInformation(
        token, win32security.TokenUser
    )
    return user_sid


def _dacl_problem(path: Path, user_sid: Any, win32security: Any) -> Optional[str]:
    """``"null"`` or ``"foreign"`` when someone else can reach *path*, else None."""
    sd = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
    )
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        return "null"
    for i in range(dacl.GetAceCount()):
        _ace_type_flags, _mask, ace_sid = dacl.GetAce(i)
        if ace_sid != user_sid:
            return "foreign"
    return None


_PROBLEMS = {
    "null": "{path} has no DACL{when} (a null DACL grants everyone access on Windows)",
    "foreign": "{path} DACL grants access to a SID other than the current user{when}, "
    "so other local users could read it",
}


def lock_down_windows_acl(path: Path, *, inherit: bool = False) -> None:
    """Give *path* a protected DACL whose one ACE gives the current user full control.

    *inherit* makes that ACE apply to everything later created inside a
    folder: the NTFS form of a 0700 folder whose files are 0600. The DACL is
    read back, so a lockdown the OS ignored is an error, not a quiet success.

    Raises:
        OwnerOnlyError: pywin32 is missing, the API call failed, or another
            SID can still reach *path*.
    """
    ntsecuritycon, win32api, win32security = _pywin32()
    try:
        user_sid = _current_user_sid(win32api, win32security)
        dacl = win32security.ACL()
        if inherit:
            dacl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION,
                win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE,
                ntsecuritycon.FILE_ALL_ACCESS,
                user_sid,
            )
        else:
            dacl.AddAccessAllowedAce(
                win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, user_sid
            )
        # PROTECTED_DACL drops inherited ACEs; without it the parent's survive.
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        problem = _dacl_problem(path, user_sid, win32security)
    except Exception as e:  # pywin32 raises pywintypes.error, not OSError
        raise OwnerOnlyError(
            f"could not lock down {path} to the current user via a Windows ACL: {e}"
        ) from e
    if problem:
        raise OwnerOnlyError(
            _PROBLEMS[problem].format(path=path, when=" after lockdown")
        )


def windows_acl_problem(path: Path) -> Optional[str]:
    """Why someone other than the current user can reach *path*, or None.

    Raises:
        OwnerOnlyError: pywin32 is missing or the DACL can't be read.
    """
    _ntsecuritycon, win32api, win32security = _pywin32()
    try:
        problem = _dacl_problem(
            path, _current_user_sid(win32api, win32security), win32security
        )
    except Exception as e:  # pywin32 raises pywintypes.error, not OSError
        raise OwnerOnlyError(f"could not read the Windows ACL of {path}: {e}") from e
    return _PROBLEMS[problem].format(path=path, when="") if problem else None
