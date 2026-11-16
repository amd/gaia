# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Device pairing: the switch, the key on disk, tokens and invitations."""

from __future__ import annotations

import asyncio
import base64
import hmac
import os
import sqlite3
import stat
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gaia import env as gaia_env
from gaia.config import GaiaConfig
from gaia.security import secret_read_refusal
from gaia.ui.devices import (
    DEVICE_IDLE_DAYS,
    DEVICE_TOKEN_PREFIX,
    PENDING_DEVICE_SECONDS,
    RECENT_FAILURES_KEPT,
)
from gaia.ui.devices import pairing as pairing_mod
from gaia.ui.devices import store as store_mod
from gaia.ui.devices.access import DeviceAccess
from gaia.ui.devices.listener import ListenerError
from gaia.ui.devices.pairing import (
    PairingRefused,
    PairingWindow,
    sanitize_device_name,
)
from gaia.ui.devices.storage import (
    DeviceCertificateError,
    UnsafeDeviceFolderError,
    ensure_private_folder,
    load_identity,
    pin_of,
)
from gaia.ui.devices.store import DeviceStore, token_hash
from gaia.ui.server import create_app

pytestmark = pytest.mark.allow_network  # TestClient only; nothing leaves the machine


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_device_pairing_not_enabled_by_env_or_dotenv(tmp_path, monkeypatch):
    """Only the user's own config file opens a listener on the network."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text(
        "GAIA_DEVICE_PAIRING=1\nDEVICE_PAIRING=true\ndevice_pairing=true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr(gaia_env, "_install_tree_dotenv", lambda: None)
    for name in ("GAIA_DEVICE_PAIRING", "DEVICE_PAIRING", "device_pairing"):
        monkeypatch.setenv(name, "1")
    gaia_env.load_env()

    assert GaiaConfig.load().device_pairing is False
    app = create_app(db_path=":memory:")
    app.state.devices = DeviceAccess(app, folder=tmp_path / "devices", port=0)
    pc = TestClient(app, client=("127.0.0.1", 50000))
    for method, path in [
        ("GET", "/api/devices"),
        ("GET", "/api/devices/status"),
        ("POST", "/api/devices/listener"),
        ("POST", "/api/devices/pairing"),
        ("POST", "/api/devices/pairing/reset"),
        ("POST", "/api/devices/pair"),
        ("DELETE", "/api/devices/me"),
    ]:
        resp = pc.request(method, path, json={})
        assert resp.status_code == 403 and "device_pairing" in resp.text, path
    asyncio.run(app.state.devices.resume())
    with pytest.raises(ListenerError, match="device_pairing"):
        asyncio.run(app.state.devices.start())
    assert not app.state.devices.listening
    assert not (tmp_path / "devices").exists()

    GaiaConfig.save_key("device_pairing", "true")
    assert GaiaConfig.load().device_pairing is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_key_file_created_0600_atomically(tmp_path):
    folder = tmp_path / "devices"
    identity = load_identity(folder)

    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for path in (identity.key_path, identity.certificate_path):
        info = path.stat()
        assert stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid()
    assert [p.name for p in folder.iterdir() if p.name.startswith(".")] == []
    assert identity.pin == pin_of(identity.certificate_path.read_bytes())
    assert load_identity(folder).pin == identity.pin

    identity.key_path.chmod(0o644)
    with pytest.raises(UnsafeDeviceFolderError, match="other users can open it"):
        load_identity(folder)
    identity.key_path.chmod(0o600)
    folder.chmod(0o755)
    with pytest.raises(UnsafeDeviceFolderError):
        load_identity(folder)
    folder.chmod(0o700)
    identity.certificate_path.unlink()
    with pytest.raises(DeviceCertificateError, match="half"):
        load_identity(folder)

    # Agent tools can't read anything in the real device folder, whatever its name.
    real_table = Path.home() / ".gaia" / "devices" / "paired.db"
    assert "holds credentials" in secret_read_refusal(real_table)


@pytest.mark.skipif(sys.platform != "win32", reason="real NTFS ACLs")
def test_device_folder_acl_is_owner_only_on_windows(tmp_path):
    from gaia.utils.owner_only import windows_acl_problem

    identity = load_identity(tmp_path / "devices")
    for path in (
        identity.key_path.parent,
        identity.key_path,
        identity.certificate_path,
    ):
        assert windows_acl_problem(path) is None


def test_device_token_256_bit_and_stored_hashed(tmp_path, monkeypatch):
    table = ensure_private_folder(tmp_path / "devices") / "paired.db"
    store = DeviceStore(table)
    device, token = store.add_pending("Pixel", "android")
    _, other = store.add_pending("iPad", "ipados")

    assert token.startswith(DEVICE_TOKEN_PREFIX) and token != other
    raw = token[len(DEVICE_TOKEN_PREFIX) :]
    assert len(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))) == 32
    stored = sqlite3.connect(table).execute(
        "SELECT token_hash FROM paired_devices WHERE id = ?", (device.id,)
    )
    assert stored.fetchone() == (token_hash(token),)
    assert token.encode() not in table.read_bytes()

    compared, real_compare = [], hmac.compare_digest

    def spy(a, b):
        compared.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(store_mod.hmac, "compare_digest", spy)
    assert store.authenticate(token).id == device.id
    assert compared == [(token_hash(token), token_hash(token))]
    assert store.authenticate(token[:-1] + ("B" if token[-1] == "A" else "A")) is None
    assert store.authenticate("00000000-0000-4000-8000-000000000000") is None


def test_idle_and_pending_devices_expire(tmp_path):
    clock = Clock()
    store = DeviceStore(
        ensure_private_folder(tmp_path / "devices") / "paired.db", clock=clock
    )
    waiting, waiting_token = store.add_pending("Phone", "ios")
    used, used_token = store.add_pending("Tablet", "ipados")
    assert store.allow(used.id).allowed

    clock.now += PENDING_DEVICE_SECONDS
    assert store.authenticate(waiting_token) is None
    assert store.allow(waiting.id) is None
    assert store.authenticate(used_token).id == used.id

    clock.now += DEVICE_IDLE_DAYS * 86400 - 1
    assert store.authenticate(used_token) is not None
    clock.now += DEVICE_IDLE_DAYS * 86400
    assert store.authenticate(used_token) is None
    assert sorted(store.expire()) == sorted([waiting.id, used.id])
    assert store.devices() == []


def test_invitation_single_use_concurrent():
    window = PairingWindow()
    invitation, _ = window.open()
    start = threading.Barrier(8)
    paired, refused = [], []

    def redeem(i):
        start.wait()
        try:
            paired.append(window.redeem(invitation, f"192.168.1.{i}", lambda: i))
        except PairingRefused as e:
            refused.append(e.reason)

    threads = [threading.Thread(target=redeem, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(paired) == 1 and refused == ["used"] * 7
    assert window.expires_at() is None


def test_pair_refusal_reasons_reach_the_pc(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(pairing_mod.time, "time", clock)
    window = PairingWindow(clock=clock)

    def reason(invitation):
        with pytest.raises(PairingRefused) as e:
            window.redeem(invitation, "192.168.1.20", lambda: "paired")
        return e.value.reason

    assert reason("anything") == "closed"
    first, _ = window.open()
    assert reason("not-the-code") == "wrong"
    assert window.redeem(first, "192.168.1.20", lambda: "paired") == "paired"
    assert reason(first) == "used"  # the window closed after the first pairing

    second, _ = window.open()
    clock.now += pairing_mod.PAIRING_WINDOW_SECONDS
    assert reason(second) == "expired"
    assert window.expires_at() is None
    assert [f["reason"] for f in window.failures()] == [
        "closed",
        "wrong",
        "used",
        "expired",
    ]
    assert all(f["address"] == "192.168.1.20" for f in window.failures())


def test_pair_rate_limit_is_per_peer():
    clock = Clock()
    window = PairingWindow(clock=clock)
    invitation, _ = window.open()
    attacker, phone = "192.168.1.66", "192.168.1.20"

    outcomes = []
    for _ in range(5):
        with pytest.raises(PairingRefused) as e:
            window.redeem("guess", attacker, lambda: "paired")
        outcomes.append(e.value.locked_out)
    assert outcomes == [False] * 4 + [True]
    with pytest.raises(PairingRefused) as e:
        window.redeem(invitation, attacker, lambda: "paired")
    assert e.value.reason == "rate_limited"

    assert window.redeem(invitation, phone, lambda: "paired") == "paired"
    clock.now += pairing_mod.PAIR_LOCKOUT_SECONDS
    with pytest.raises(PairingRefused) as e:
        window.redeem("guess", attacker, lambda: "paired")
    assert e.value.reason == "closed"  # unlocked again, and the window is shut

    for i in range(RECENT_FAILURES_KEPT * 2):
        with pytest.raises(PairingRefused):
            window.redeem("guess", f"10.0.0.{i}", lambda: "paired")
    assert len(window.failures()) == RECENT_FAILURES_KEPT


def test_device_name_sanitized():
    spoof = "‮enohPi s'hciR‬"
    assert sanitize_device_name(spoof) == "enohPi s'hciR"
    assert sanitize_device_name("Test\tphone\n\x07 15") == "Test phone 15"
    assert len(sanitize_device_name("x" * 500)) == 64
    with pytest.raises(ValueError):
        sanitize_device_name("⁦⁩\x00 ")


def test_diagnostics_bundle_excludes_phone_secrets(tmp_path, monkeypatch):
    import argparse
    import subprocess
    import tarfile

    from gaia import cli

    gaia_dir = tmp_path / ".gaia"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("GAIA_HOME", str(gaia_dir))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon-home"))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    identity = load_identity(gaia_dir / "devices")
    store = DeviceStore(gaia_dir / "devices" / "paired.db")
    _, token = store.add_pending("Phone", "ios")
    store.close()
    (gaia_dir / "gaia.log").write_text("gaia\n", encoding="utf-8")

    bundle = tmp_path / "bundle.tgz"
    cli.handle_diagnostics_command(
        argparse.Namespace(output=str(bundle), no_logs=False)
    )
    with tarfile.open(bundle) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}

    assert "gaia.log" in members
    assert not [name for name in members if "devices" in name.split("/")]
    key = identity.key_path.read_bytes()
    for blob in members.values():
        assert key not in blob and token.encode() not in blob
