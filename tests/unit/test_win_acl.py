# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The shared owner-only DACL helper and the two stores that call it (#2250).

``test_daemon_secret_acl.py`` and ``engineering/test_store.py`` assert the
pywin32 call shape through each caller; this file pins what the shared helper
adds: each caller's own exception type and wording, on every failure path.
"""

import sys
from pathlib import Path

import pytest

from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.errors import SidecarSpawnError
from gaia.engineering import store
from gaia.utils.win_acl import AclMessages, lock_down_to_current_user

USER_SID = "S-1-5-21-fake-current-user"
FOREIGN_SID = "S-1-5-32-545"

MESSAGES = AclMessages(
    missing_pywin32="missing for {path}",
    null_dacl="null for {path}",
    foreign_sid="foreign for {path}",
    api_failure="api for {path}: {error}",
)


class _Error(Exception):
    pass


class _FakeACL:
    def __init__(self):
        self.sids = []

    def AddAccessAllowedAce(self, revision, mask, sid):
        self.sids.append(sid)

    def GetAceCount(self):
        return len(self.sids)

    def GetAce(self, index):
        return ((0, 0), 0x1F01FF, self.sids[index])


class _FakeSD:
    def __init__(self, dacl):
        self._dacl = dacl

    def GetSecurityDescriptorDacl(self):
        return self._dacl


def _install(monkeypatch, mode):
    """Fake pywin32 whose lockdown ends as *mode* says."""
    names = ("ntsecuritycon", "win32api", "win32security")
    if mode == "missing":
        for name in names:
            monkeypatch.setitem(sys.modules, name, None)
        return
    ntsecuritycon = type(sys)("ntsecuritycon")
    ntsecuritycon.FILE_ALL_ACCESS = 0x1F01FF
    win32api = type(sys)("win32api")
    win32api.GetCurrentProcess = lambda: "PROCESS"
    win32security = type(sys)("win32security")
    for const in (
        "ACL_REVISION",
        "SE_FILE_OBJECT",
        "TOKEN_QUERY",
        "TokenUser",
        "DACL_SECURITY_INFORMATION",
        "PROTECTED_DACL_SECURITY_INFORMATION",
    ):
        setattr(win32security, const, 1)
    win32security.ACL = _FakeACL
    win32security.OpenProcessToken = lambda process, access: "TOKEN"
    win32security.GetTokenInformation = lambda token, cls: (USER_SID, 0)

    def _set(*args):
        if mode == "api":
            raise OSError("(1307, 'SetNamedSecurityInfo', 'invalid owner')")

    def _get(*args):
        if mode == "null":
            return _FakeSD(None)
        dacl = _FakeACL()
        dacl.sids = [USER_SID] + ([FOREIGN_SID] if mode == "foreign" else [])
        return _FakeSD(dacl)

    win32security.SetNamedSecurityInfo = _set
    win32security.GetNamedSecurityInfo = _get
    for module in (ntsecuritycon, win32api, win32security):
        monkeypatch.setitem(sys.modules, module.__name__, module)


def test_a_clean_lockdown_raises_nothing(monkeypatch, tmp_path):
    _install(monkeypatch, "ok")
    lock_down_to_current_user(tmp_path, error_cls=_Error, messages=MESSAGES)


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("missing", "missing for {path}"),
        ("null", "null for {path}"),
        ("foreign", "foreign for {path}"),
        (
            "api",
            "api for {path}: (1307, 'SetNamedSecurityInfo', 'invalid owner')",
        ),
    ],
)
def test_each_failure_raises_the_callers_type_and_wording(
    monkeypatch, tmp_path, mode, expected
):
    _install(monkeypatch, mode)

    with pytest.raises(_Error) as raised:
        lock_down_to_current_user(tmp_path, error_cls=_Error, messages=MESSAGES)

    assert str(raised.value) == expected.format(path=tmp_path)
    assert type(raised.value) is _Error, "a refusal must not be re-wrapped"


@pytest.mark.parametrize("mode", ["missing", "null", "foreign", "api"])
@pytest.mark.parametrize(
    "lock_down, error_cls, wording",
    [
        (mgr._lock_down_windows_acl, SidecarSpawnError, "secret"),
        (store._lock_down_windows_acl, RuntimeError, "engineering directory"),
    ],
    ids=["sidecar-secret", "engineering-store"],
)
def test_both_stores_keep_their_own_error(
    monkeypatch, tmp_path, mode, lock_down, error_cls, wording
):
    _install(monkeypatch, mode)
    target = Path(tmp_path)

    with pytest.raises(error_cls) as raised:
        lock_down(target)

    assert str(target) in str(raised.value)
    if mode != "api":
        assert wording in str(raised.value)
