# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Owner-only Windows DACL — the NTFS analog of ``chmod 0600``/``0700`` (#2250).

POSIX mode bits are a no-op on NTFS, so a file or directory that must stay
private to the current user gets an explicit DACL instead. pywin32 is imported
only when the lockdown runs, so importing this module is safe on any platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Type


@dataclass(frozen=True)
class AclMessages:
    """A caller's error text, as ``str.format`` templates.

    Every template may use ``{path}``; ``api_failure`` also gets ``{error}``.
    """

    missing_pywin32: str
    null_dacl: str
    foreign_sid: str
    api_failure: str


def lock_down_to_current_user(
    path: Path, *, error_cls: Type[Exception], messages: AclMessages
) -> None:
    """Restrict *path*'s DACL to the current user only.

    Builds a fresh, non-inherited DACL with a single ACE granting the current
    user full control, then re-reads it to confirm no other SID has access.
    Any failure (missing pywin32, a SetNamedSecurityInfo error, a foreign SID
    surviving the lockdown) raises *error_cls*; there is no fallback that
    leaves *path* readable by other local users.
    """
    try:
        import ntsecuritycon
        import win32api
        import win32security
    except ImportError as e:
        raise error_cls(messages.missing_pywin32.format(path=path)) from e

    try:
        # Read the SID from the process token, not by name: LookupAccountName
        # fails with 1332 for the service/machine accounts a daemon runs as.
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        user_sid, _attributes = win32security.GetTokenInformation(
            token, win32security.TokenUser
        )

        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, user_sid
        )
        # PROTECTED_DACL strips inherited ACEs — without it the parent dir's
        # (looser) grants would survive alongside the new ACE.
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

        sd = win32security.GetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
        )
        result_dacl = sd.GetSecurityDescriptorDacl()
        if result_dacl is None:
            # A null DACL grants everyone access on Windows.
            raise error_cls(messages.null_dacl.format(path=path))
        for i in range(result_dacl.GetAceCount()):
            _ace_type_flags, _mask, ace_sid = result_dacl.GetAce(i)
            if ace_sid != user_sid:
                raise error_cls(messages.foreign_sid.format(path=path))
    except error_cls:
        raise
    except Exception as e:
        # pywin32 raises pywintypes.error (not OSError) on API failures.
        raise error_cls(messages.api_failure.format(path=path, error=e)) from e
