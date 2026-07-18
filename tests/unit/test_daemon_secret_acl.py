# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Owner-only NTFS DACL for the sidecar launch-secret file (#2250).

Three layers, deliberately:

1. Mocked call-shape tests — run everywhere (ubuntu CI included). They assert
   the SHAPE of the pywin32 calls, not merely that they happened: a mock that
   returns success proves "we called it", never "the call is valid".
2. Real-Windows tests — the only layer that proves the pywin32 API calls are
   accepted by the OS and that inherited ACEs actually die. Skipped off-win32.
3. Fresh-install guard — pywin32 must be importable on Windows, i.e. the
   ``setup.py`` declaration is really in effect for a clean install.
"""

import os
import sys
from pathlib import Path

import pytest

from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.errors import SidecarSpawnError
from gaia.daemon.sidecars.spec import builtin_specs

USER_SID = "S-1-5-21-fake-current-user"
FOREIGN_SID = "S-1-5-32-545"  # BUILTIN\Users — the ACE #2250 is about
FILE_ALL_ACCESS = 0x1F01FF

# ---------------------------------------------------------------------------
# Fake pywin32 surface (injected via sys.modules)
# ---------------------------------------------------------------------------


class _FakeACL:
    def __init__(self):
        self.aces = []

    def AddAccessAllowedAce(self, revision, mask, sid):
        self.aces.append({"revision": revision, "mask": mask, "sid": sid})

    def GetAceCount(self):
        return len(self.aces)

    def GetAce(self, index):
        ace = self.aces[index]
        # pywin32 returns ((aceType, aceFlags), mask, sid).
        return ((0, 0), ace["mask"], ace["sid"])


class _FakeSD:
    def __init__(self, dacl):
        self._dacl = dacl

    def GetSecurityDescriptorDacl(self):
        return self._dacl


def _install_fake_pywin32(monkeypatch, *, readback_sids=(USER_SID,), null_dacl=False):
    """Inject fake win32security/win32api/ntsecuritycon and return the call log.

    ``readback_sids`` drives what GetNamedSecurityInfo reports AFTER the
    lockdown, which is what the function's verification pass inspects.
    """
    calls = {"set": [], "get": []}

    ntsecuritycon = type(sys)("ntsecuritycon")
    ntsecuritycon.FILE_ALL_ACCESS = FILE_ALL_ACCESS

    win32api = type(sys)("win32api")
    win32api.NameSamCompatible = 2
    win32api.GetCurrentProcess = lambda: "FAKE-PROCESS-HANDLE"
    win32api.GetUserNameEx = lambda fmt: "FAKEDOMAIN\\fakeuser"

    def _lookup_account_name(system, name):
        # The STX runner reproduced: a service/machine account has no
        # name->SID mapping, so deriving the SID by name breaks the daemon
        # outright. The SID must come from the process token instead.
        raise OSError(
            "(1332, 'LookupAccountName', 'No mapping between account names "
            "and security IDs was done.')"
        )

    win32security = type(sys)("win32security")
    win32security.ACL = _FakeACL
    win32security.ACL_REVISION = 2
    win32security.SE_FILE_OBJECT = 1
    win32security.TOKEN_QUERY = 0x0008
    win32security.TokenUser = 1
    win32security.DACL_SECURITY_INFORMATION = 0x00000004
    win32security.PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    win32security.LookupAccountName = _lookup_account_name
    win32security.OpenProcessToken = lambda process, access: "FAKE-TOKEN"
    win32security.GetTokenInformation = lambda token, info_class: (USER_SID, 0)

    def _set_named_security_info(path, obj_type, flags, owner, group, dacl, sacl):
        calls["set"].append(
            {
                "path": path,
                "obj_type": obj_type,
                "flags": flags,
                "owner": owner,
                "group": group,
                "dacl": dacl,
                "sacl": sacl,
            }
        )

    def _get_named_security_info(path, obj_type, flags):
        calls["get"].append({"path": path, "obj_type": obj_type, "flags": flags})
        if null_dacl:
            return _FakeSD(None)
        readback = _FakeACL()
        for sid in readback_sids:
            readback.AddAccessAllowedAce(2, FILE_ALL_ACCESS, sid)
        return _FakeSD(readback)

    win32security.SetNamedSecurityInfo = _set_named_security_info
    win32security.GetNamedSecurityInfo = _get_named_security_info

    monkeypatch.setitem(sys.modules, "ntsecuritycon", ntsecuritycon)
    monkeypatch.setitem(sys.modules, "win32api", win32api)
    monkeypatch.setitem(sys.modules, "win32security", win32security)
    return calls


# ---------------------------------------------------------------------------
# Layer 1 — mocked call shape (platform-agnostic)
# ---------------------------------------------------------------------------


def test_lockdown_requests_protected_dacl(monkeypatch, tmp_path):
    """The highest-value assertion in this file.

    Dropping PROTECTED_DACL_SECURITY_INFORMATION still "works" — the new ACE
    lands — but the parent dir's inherited ACEs survive alongside it, which IS
    the #2250 vulnerability. Assert both bits, not just DACL.
    """
    calls = _install_fake_pywin32(monkeypatch)
    target = tmp_path / "launch-secret"
    target.write_text("shh")

    mgr._lock_down_windows_acl(target)

    assert len(calls["set"]) == 1
    flags = calls["set"][0]["flags"]
    assert flags & 0x00000004, "DACL_SECURITY_INFORMATION must be set"
    assert flags & 0x80000000, (
        "PROTECTED_DACL_SECURITY_INFORMATION must be set — without it the "
        "parent temp dir's inherited ACEs survive (#2250)"
    )
    assert calls["set"][0]["path"] == str(target)
    assert calls["set"][0]["obj_type"] == 1  # SE_FILE_OBJECT
    # Owner/group/SACL are left untouched; only the DACL is rewritten.
    assert calls["set"][0]["owner"] is None
    assert calls["set"][0]["group"] is None
    assert calls["set"][0]["sacl"] is None


def test_lockdown_adds_exactly_one_full_control_ace_for_current_user(
    monkeypatch, tmp_path
):
    calls = _install_fake_pywin32(monkeypatch)
    target = tmp_path / "launch-secret"
    target.write_text("shh")

    mgr._lock_down_windows_acl(target)

    dacl = calls["set"][0]["dacl"]
    assert dacl.GetAceCount() == 1, "exactly one ACE — no extra grants"
    assert dacl.aces[0]["sid"] == USER_SID
    assert dacl.aces[0]["mask"] == FILE_ALL_ACCESS


def test_sid_comes_from_the_process_token_not_a_name_lookup(monkeypatch, tmp_path):
    """Regression: a daemon running as a service or machine account has no
    name->SID mapping, so LookupAccountName fails with 1332 and — being
    fail-loud — the daemon then refuses to spawn ANY sidecar. The fake's
    LookupAccountName raises that exact error, so reintroducing the name
    lookup fails here rather than only on a service-account machine."""
    calls = _install_fake_pywin32(monkeypatch)
    target = tmp_path / "launch-secret"
    target.write_text("shh")

    mgr._lock_down_windows_acl(target)

    assert calls["set"][0]["dacl"].aces[0]["sid"] == USER_SID


def test_lockdown_verifies_by_rereading_the_dacl(monkeypatch, tmp_path):
    """A write that is never read back cannot detect a silently-ignored ACL."""
    calls = _install_fake_pywin32(monkeypatch)
    target = tmp_path / "launch-secret"
    target.write_text("shh")

    mgr._lock_down_windows_acl(target)

    assert len(calls["get"]) == 1
    assert calls["get"][0]["path"] == str(target)


def test_foreign_sid_surviving_the_lockdown_raises(monkeypatch, tmp_path):
    _install_fake_pywin32(monkeypatch, readback_sids=(USER_SID, FOREIGN_SID))
    target = tmp_path / "launch-secret"
    target.write_text("shh")

    with pytest.raises(SidecarSpawnError, match="SID other than the current user"):
        mgr._lock_down_windows_acl(target)


def test_null_dacl_after_lockdown_raises(monkeypatch, tmp_path):
    # A NULL DACL is not "no access" on Windows — it grants Everyone.
    _install_fake_pywin32(monkeypatch, null_dacl=True)
    target = tmp_path / "launch-secret"
    target.write_text("shh")

    with pytest.raises(SidecarSpawnError, match="no DACL after lockdown"):
        mgr._lock_down_windows_acl(target)


def test_api_failure_is_wrapped_not_swallowed(monkeypatch, tmp_path):
    calls = _install_fake_pywin32(monkeypatch)
    del calls  # only the module injection matters here

    def _boom(*args, **kwargs):
        raise OSError("(1307, 'SetNamedSecurityInfo', 'invalid owner')")

    monkeypatch.setattr(sys.modules["win32security"], "SetNamedSecurityInfo", _boom)
    target = tmp_path / "launch-secret"
    target.write_text("shh")

    with pytest.raises(SidecarSpawnError, match="could not lock down"):
        mgr._lock_down_windows_acl(target)


def test_missing_pywin32_raises_and_names_the_real_remedy(monkeypatch, tmp_path):
    # `sys.modules[name] = None` makes `import name` raise ImportError.
    for name in ("win32security", "win32api", "ntsecuritycon"):
        monkeypatch.setitem(sys.modules, name, None)
    target = tmp_path / "launch-secret"
    target.write_text("shh")

    with pytest.raises(SidecarSpawnError) as excinfo:
        mgr._lock_down_windows_acl(target)

    message = str(excinfo.value)
    assert "pip install pywin32" in message
    # The old text pointed at the [ui] extra, which never carried pywin32.
    assert "amd-gaia[ui]" not in message


class _NTOs:
    """Real ``os`` with ``name == "nt"`` — lets the Windows leg of
    ``_write_secret_file`` run off-Windows. Patching the global ``os.name``
    instead would flip ``pathlib`` to WindowsPath and blow up on POSIX."""

    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


def test_missing_pywin32_aborts_the_spawn_instead_of_writing_a_loose_secret(
    monkeypatch, tmp_path
):
    """No silent fallback: an unlockable secret must abort, not degrade."""
    for name in ("win32security", "win32api", "ntsecuritycon"):
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(mgr, "os", _NTOs())
    monkeypatch.setattr(mgr.tempfile, "tempdir", str(tmp_path), raising=False)

    m = mgr.AgentSidecarManager(builtin_specs()["email"])
    with pytest.raises(SidecarSpawnError, match="pywin32 is not installed"):
        m._write_secret_file()

    assert m._secret_path is None, "no secret path is recorded on failure"
    leftovers = list(Path(tmp_path).glob("gaia-email-secret-*"))
    assert leftovers == [], f"secret dir left behind: {leftovers}"


def test_lockdown_is_wired_into_the_secret_write_path(monkeypatch, tmp_path):
    """Both the 0700 dir and the 0600 file get locked down on Windows."""
    locked = []
    monkeypatch.setattr(mgr, "_lock_down_windows_acl", lambda p: locked.append(Path(p)))
    monkeypatch.setattr(mgr, "os", _NTOs())
    monkeypatch.setattr(mgr.tempfile, "tempdir", str(tmp_path), raising=False)

    m = mgr.AgentSidecarManager(builtin_specs()["email"])
    path = m._write_secret_file()

    assert locked == [path.parent, path], (
        "the temp dir must be locked BEFORE the secret is written into it, "
        f"then the file itself; got {locked}"
    )
    m._remove_secret_dir(path.parent)


# ---------------------------------------------------------------------------
# Layer 2 — real Windows (the only layer that proves the API calls are valid)
# ---------------------------------------------------------------------------

win32_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="real DACL behavior is observable only on Windows/NTFS (#2250)",
)


def _current_user_sid():
    import win32api
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
    )
    sid, _attributes = win32security.GetTokenInformation(token, win32security.TokenUser)
    return sid


def _read_dacl_sids(path: Path):
    import win32security

    sd = win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
    )
    dacl = sd.GetSecurityDescriptorDacl()
    assert dacl is not None, "null DACL grants Everyone on Windows"
    return [dacl.GetAce(i)[2] for i in range(dacl.GetAceCount())]


@win32_only
def test_real_lockdown_leaves_only_the_current_user(tmp_path):
    target = tmp_path / "launch-secret"
    target.write_text("shh", encoding="utf-8")

    mgr._lock_down_windows_acl(target)

    sids = _read_dacl_sids(target)
    assert len(sids) == 1, f"expected exactly one ACE, got {sids}"
    assert sids[0] == _current_user_sid()


@win32_only
def test_real_lockdown_strips_inherited_parent_ace(tmp_path):
    """The actual #2250 bug: a broader ACE inherited from the parent directory
    must NOT survive on the secret file."""
    import ntsecuritycon
    import win32security

    parent = tmp_path / "loose-parent"
    parent.mkdir()

    # Grant BUILTIN\Users full control on the parent, INHERITED by children.
    # The current user is granted too — a Users-only DACL could deny the CI
    # account permission to create the file inside its own temp dir.
    users_sid = win32security.ConvertStringSidToSid("S-1-5-32-545")
    inherit = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
    dacl = win32security.ACL()
    for sid in (_current_user_sid(), users_sid):
        dacl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            inherit,
            ntsecuritycon.FILE_ALL_ACCESS,
            sid,
        )
    win32security.SetNamedSecurityInfo(
        str(parent),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )

    target = parent / "launch-secret"
    target.write_text("shh", encoding="utf-8")
    # Pre-condition: the child really did inherit the broad ACE.
    assert users_sid in _read_dacl_sids(target), "parent ACE was not inherited"

    mgr._lock_down_windows_acl(target)

    sids = _read_dacl_sids(target)
    assert users_sid not in sids, "inherited BUILTIN\\Users ACE survived (#2250)"
    assert len(sids) == 1
    assert sids[0] == _current_user_sid()


# ---------------------------------------------------------------------------
# Layer 3 — fresh-install guard
# ---------------------------------------------------------------------------


@win32_only
def test_pywin32_is_a_declared_windows_dependency():
    """A passing runtime does not prove a fresh install works.

    If ``pywin32`` is dropped from setup.py's install_requires, the daemon
    cannot spawn ANY sidecar on a clean Windows install (#2250). Fail loudly
    here rather than at a user's first sidecar launch.
    """
    import win32security  # noqa: F401

    assert hasattr(win32security, "SetNamedSecurityInfo")
    # pywin32-ctypes (pulled by keyring) has no win32security — assert we got
    # the real package, not its namesake.
    assert hasattr(win32security, "PROTECTED_DACL_SECURITY_INFORMATION")


def test_setup_py_declares_pywin32_for_win32():
    """Runs everywhere, so dropping the declaration fails ubuntu CI too."""
    setup_py = Path(__file__).resolve().parents[2] / "setup.py"
    source = setup_py.read_text(encoding="utf-8")
    assert "pywin32" in source, "pywin32 must stay declared in setup.py (#2250)"
    assert 'pywin32; sys_platform == "win32"' in source
