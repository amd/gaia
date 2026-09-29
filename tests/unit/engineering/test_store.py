# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Consent boundaries and durable context records."""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from gaia.engineering.service import EngineeringService
from gaia.engineering.store import JobStore, clean_context, private_directory


def test_developer_mode_is_required_before_any_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("GAIA_DEVELOPER_MODE", raising=False)
    path = tmp_path / "private"
    with pytest.raises(PermissionError, match="developer mode"):
        EngineeringService(path)
    assert not path.exists()
    monkeypatch.setenv("GAIA_DEVELOPER_MODE", "1")
    with pytest.raises(PermissionError):
        EngineeringService(path, developer_mode=False)
    assert not path.exists()


def test_grants_recipient_expiry_revocation_and_cursor(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("claude", "Bad citations", "private-otter-729", {})
    assert (
        store.context(job["id"], "claude")["evidence"][0]["text"] == "private-otter-729"
    )
    with pytest.raises(PermissionError):
        store.context(job["id"], "codex")
    store.append(job["id"], "second revision feedback")
    assert [e["seq"] for e in store.context(job["id"], "claude", 1)["evidence"]] == [2]
    store.revoke(job["id"])
    with pytest.raises(PermissionError):
        store.context(job["id"], "claude", 1)
    fresh = store.create("codex", "Case", "x", {})
    store.update(fresh["id"], lambda item: item["grant"].update(expires_at=0))
    with pytest.raises(PermissionError):
        store.context(fresh["id"], "codex")


def test_atomic_concurrent_feedback_preserves_all_updates(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("codex", "Task", "initial", {})
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: store.append(job["id"], f"feedback {i}"), range(12)))
    record = store.read(job["id"])
    assert len(record["evidence"]) == 13
    assert record["revision"] == 13
    assert [e["seq"] for e in record["evidence"]] == list(range(1, 14))
    assert os.stat(store.path(job["id"])).st_mode & 0o777 == 0o600


def test_revision_conflict_corruption_and_path_refusal(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("claude", "Task", "x", {})
    with pytest.raises(ValueError, match="changed"):
        store.update(job["id"], lambda item: None, expected_revision=0)
    with pytest.raises(ValueError, match="ID"):
        store.read("../../secret")
    store.path(job["id"]).write_text("{broken")
    with pytest.raises(json.JSONDecodeError):
        store.read(job["id"])
    assert store.path(job["id"]).read_text() == "{broken"


def test_context_limits_and_redaction():
    assert (
        clean_context("\x1b[31mhello\x1b[0m\nAPI_KEY=topsecret")
        == "hello\nAPI_KEY=[REDACTED]"
    )
    # JSON- and dict-shaped secrets, the shape most logs and config dumps take.
    assert clean_context('{"api_key": "abc123"}') == '{"api_key": [REDACTED]'
    assert clean_context("{'password': 'hunter2'}") == "{'password': [REDACTED]"
    with pytest.raises(ValueError):
        clean_context("x" * (128 * 1024 + 1))
    with pytest.raises(ValueError):
        clean_context("")


def test_prefixed_credential_names_are_redacted():
    # Environment variables carry a prefix, which is the shape a terminal snapshot has.
    for line in (
        "DB_PASSWORD=hunter2",
        "MY_API_KEY=zzz",
        "AWS_SECRET_ACCESS_KEY=wJalrXUt",
        "ANTHROPIC_AUTH_TOKEN=xyzzy",
        "client_secret=shhh",
        "MYSQL_PASSWD=abc",
        'export GH_ACCESS_TOKEN="abc"',
    ):
        key = line.split("=", 1)[0]
        assert clean_context(line) == f"{key}=[REDACTED]"


def test_ordinary_developer_text_is_not_redacted():
    for line in (
        "secretary: alice",
        "the password reset flow",
        'parser.add_argument("--api-key")',
        "keyboard: mechanical",
        "passwordless login: enabled",
    ):
        assert clean_context(line) == line


@pytest.mark.parametrize("after_seq", [True, -1, "1", 1.0])
def test_context_cursor_must_be_a_nonnegative_int(tmp_path, after_seq):
    with pytest.raises(ValueError, match="nonnegative integer"):
        JobStore(tmp_path).context("job", "codex", after_seq=after_seq)


def test_pairing_has_no_access_and_no_server_consent_tools(tmp_path):
    service = EngineeringService(tmp_path, developer_mode=True)
    result = service.connection("codex")
    assert service.store.jobs.is_dir()
    assert list(service.store.jobs.iterdir()) == []
    assert "token" not in json.dumps(result)
    with pytest.raises(PermissionError):
        service.authenticate("codex", "not-a-token")
    job = service.share("codex", "Task", "Selected private context")
    with pytest.raises(ValueError, match="diagnosis"):
        service.approve_code(
            job["id"], expected_revision=service.status(job["id"])["revision"]
        )
    service.report_diagnosis(
        job["id"], "codex", "Model configuration is correct; narrow patch proposed"
    )
    service.approve_code(
        job["id"], expected_revision=service.status(job["id"])["revision"]
    )
    assert service.status(job["id"])["code_approved"]
    service.revoke(job["id"])
    with pytest.raises(PermissionError):
        service.report_result(job["id"], "codex", "Cannot report after revocation")


def test_stale_approval_and_separate_profile_roots(tmp_path):
    first = EngineeringService(tmp_path / "first", developer_mode=True)
    second = EngineeringService(tmp_path / "second", developer_mode=True)
    assert first.repository.root != second.repository.root
    job = first.share("claude", "Patch scope", "selected evidence")
    first.report_diagnosis(job["id"], "claude", "original diagnosis")
    displayed = first.status(job["id"])
    first.report_diagnosis(job["id"], "claude", "changed diagnosis")
    with pytest.raises(ValueError, match="changed"):
        first.approve_code(job["id"], expected_revision=displayed["revision"])
    assert not first.status(job["id"])["code_approved"]


def test_frozen_sidecar_never_becomes_python_launcher(tmp_path, monkeypatch):
    import sys

    service = EngineeringService(tmp_path, developer_mode=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(RuntimeError, match="Python installation"):
        service.connection("codex")
    assert not (tmp_path / "clients" / "codex.json").exists()


# ---------------------------------------------------------------------------
# Windows DACL lockdown (#2250 gap: chmod 0700 is a no-op on NTFS) — the
# fake pywin32 surface lets this run on every CI platform, not just Windows.
# ---------------------------------------------------------------------------

USER_SID = "S-1-5-21-fake-current-user"
FOREIGN_SID = "S-1-5-32-545"  # BUILTIN\Users
FILE_ALL_ACCESS = 0x1F01FF


class _FakeACL:
    def __init__(self):
        self.aces = []

    def AddAccessAllowedAce(self, revision, mask, sid):
        self.aces.append({"revision": revision, "mask": mask, "sid": sid})

    def GetAceCount(self):
        return len(self.aces)

    def GetAce(self, index):
        ace = self.aces[index]
        return ((0, 0), ace["mask"], ace["sid"])


class _FakeSD:
    def __init__(self, dacl):
        self._dacl = dacl

    def GetSecurityDescriptorDacl(self):
        return self._dacl


def _install_fake_pywin32(monkeypatch, *, readback_sids=(USER_SID,), null_dacl=False):
    calls = {"set": []}

    ntsecuritycon = type(sys)("ntsecuritycon")
    ntsecuritycon.FILE_ALL_ACCESS = FILE_ALL_ACCESS

    win32api = type(sys)("win32api")
    win32api.GetCurrentProcess = lambda: "FAKE-PROCESS-HANDLE"

    win32security = type(sys)("win32security")
    win32security.ACL = _FakeACL
    win32security.ACL_REVISION = 2
    win32security.SE_FILE_OBJECT = 1
    win32security.TOKEN_QUERY = 0x0008
    win32security.TokenUser = 1
    win32security.DACL_SECURITY_INFORMATION = 0x00000004
    win32security.PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    win32security.OpenProcessToken = lambda process, access: "FAKE-TOKEN"
    win32security.GetTokenInformation = lambda token, info_class: (USER_SID, 0)

    def _set_named_security_info(path, obj_type, flags, owner, group, dacl, sacl):
        calls["set"].append({"path": path, "flags": flags, "dacl": dacl})

    def _get_named_security_info(path, obj_type, flags):
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


def test_private_directory_locks_down_windows_acl(tmp_path, monkeypatch):
    """The regression this fixes: chmod(0o700) alone is a no-op on NTFS, so a
    shared engineering snapshot was readable by any other local account.
    """
    monkeypatch.setattr(os, "name", "nt")
    calls = _install_fake_pywin32(monkeypatch)

    target = tmp_path / "engineering-root"
    private_directory(target)

    assert len(calls["set"]) == 1
    flags = calls["set"][0]["flags"]
    assert flags & 0x00000004, "DACL_SECURITY_INFORMATION must be set"
    assert flags & 0x80000000, (
        "PROTECTED_DACL_SECURITY_INFORMATION must be set — without it the "
        "parent temp dir's inherited ACEs survive alongside the new one"
    )
    assert calls["set"][0]["path"] == str(target)


def test_private_directory_skips_acl_lockdown_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    calls = _install_fake_pywin32(monkeypatch)

    private_directory(tmp_path / "engineering-root")

    assert calls["set"] == []


def test_private_directory_refuses_a_directory_a_foreign_sid_can_still_read(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(os, "name", "nt")
    _install_fake_pywin32(monkeypatch, readback_sids=(USER_SID, FOREIGN_SID))

    with pytest.raises(RuntimeError, match="other local users"):
        private_directory(tmp_path / "engineering-root")


def test_private_directory_refuses_a_null_dacl(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    _install_fake_pywin32(monkeypatch, null_dacl=True)

    with pytest.raises(RuntimeError, match="no DACL"):
        private_directory(tmp_path / "engineering-root")


def test_private_directory_requires_pywin32_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delitem(sys.modules, "win32security", raising=False)
    monkeypatch.delitem(sys.modules, "win32api", raising=False)
    monkeypatch.delitem(sys.modules, "ntsecuritycon", raising=False)
    monkeypatch.setattr(
        "builtins.__import__",
        _raise_on_pywin32_import(__import__),
    )

    with pytest.raises(RuntimeError, match="pywin32"):
        private_directory(tmp_path / "engineering-root")


def _raise_on_pywin32_import(real_import):
    def _guarded(name, *args, **kwargs):
        if name in {"win32api", "win32security", "ntsecuritycon"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    return _guarded
