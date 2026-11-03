# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship sidecar reaches the macOS keychain only through the daemon (#6481)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import keyring
import keyring.backend
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.connectors.daemon_keyring import (
    DaemonKeyring,
    install_daemon_keyring_from_env,
)
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.store import SERVICE_NAME
from gaia.daemon.custody.auth import CustodyAuth
from gaia.daemon.custody.constants import (
    CUSTODY_URL_ENV_VAR,
    KEYCHAIN_SECRET_FILE_ENV_VAR,
)
from gaia.daemon.custody.keychain_routes import SERVICE_NAME as ROUTE_SERVICE_NAME
from gaia.daemon.custody.keychain_routes import build_keychain_router
from gaia.daemon.custody.routes import install_custody_exception_handler


class _MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.items:
            raise keyring.errors.PasswordDeleteError("absent")
        del self.items[(service, username)]


@pytest.fixture
def os_store():
    previous = keyring.get_keyring()
    store = _MemoryKeyring()
    keyring.set_keyring(store)
    yield store
    keyring.set_keyring(previous)


@pytest.fixture
def daemon():
    auth = CustodyAuth()
    app = FastAPI()
    install_custody_exception_handler(app)
    app.include_router(build_keychain_router(auth))
    return auth, TestClient(app)


@pytest.fixture
def backend(daemon, monkeypatch):
    """A sidecar's backend whose HTTP calls land on the daemon's routes."""
    import httpx

    auth, client = daemon
    secret = auth.mint("gaia")

    def post(url, json, headers, timeout, trust_env):  # noqa: A002
        assert timeout.read >= 300, "must outlast a person answering the dialog"
        assert trust_env is False, "secrets must not follow proxy settings"
        return client.post(url.replace("http://daemon", ""), json=json, headers=headers)

    monkeypatch.setattr(httpx, "post", post)
    return DaemonKeyring("http://daemon", secret)


def test_route_service_name_matches_store():
    assert ROUTE_SERVICE_NAME == SERVICE_NAME


def test_read_write_delete_reach_the_os_store(os_store, backend):
    backend.set_password(SERVICE_NAME, "secret:x", "value")
    assert os_store.items[(SERVICE_NAME, "secret:x")] == "value"
    assert backend.get_password(SERVICE_NAME, "secret:x") == "value"
    backend.delete_password(SERVICE_NAME, "secret:x")
    assert backend.get_password(SERVICE_NAME, "secret:x") is None


def test_deleting_an_absent_item_raises_what_store_py_catches(os_store, backend):
    with pytest.raises(keyring.errors.PasswordDeleteError):
        backend.delete_password(SERVICE_NAME, "missing")


def test_store_py_works_on_top_of_the_backend(os_store, backend, monkeypatch):
    from gaia.connectors import store
    from gaia.daemon.custody import keychain_routes

    # Daemon and sidecar share one process here; keep the daemon on the OS store.
    daemon_side = SimpleNamespace(
        get_password=os_store.get_password,
        set_password=os_store.set_password,
        delete_password=os_store.delete_password,
        errors=keyring.errors,
    )
    monkeypatch.setattr(keychain_routes, "_keyring", lambda: daemon_side)
    keyring.set_keyring(backend)
    long_value = "x" * 3000
    store.save_secret("big", long_value)
    assert store.peek_secret("big") == long_value
    store.delete_secret("big")
    assert store.peek_secret("big") is None
    assert not os_store.items


@pytest.mark.parametrize("header", [None, "Bearer wrong"])
def test_routes_refuse_without_the_keychain_credential(os_store, daemon, header):
    auth, client = daemon
    auth.mint("gaia")
    os_store.items[(SERVICE_NAME, "s")] = "s3cret-value"
    headers = {"Authorization": header} if header else {}
    resp = client.post(
        "/host/v1/keychain/get",
        json={"service": SERVICE_NAME, "username": "s"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "s3cret-value" not in resp.text


def test_a_custody_secret_does_not_open_the_keychain(os_store, daemon):
    _auth, client = daemon
    custody_secret = CustodyAuth().mint("gaia")
    resp = client.post(
        "/host/v1/keychain/get",
        json={"service": SERVICE_NAME, "username": "s"},
        headers={"Authorization": f"Bearer {custody_secret}"},
    )
    assert resp.status_code == 403


def test_other_keychain_services_are_refused(os_store, daemon):
    auth, client = daemon
    secret = auth.mint("gaia")
    os_store.items[("com.apple.other", "s")] = "s3cret-value"
    resp = client.post(
        "/host/v1/keychain/get",
        json={"service": "com.apple.other", "username": "s"},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert resp.status_code == 403
    assert "s3cret-value" not in resp.text


def test_a_refusal_surfaces_as_a_keyring_error():
    import httpx

    def post(*_args, **_kwargs):
        raise httpx.ConnectError("refused")

    backend = DaemonKeyring("http://daemon", "s")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "post", post)
        with pytest.raises(keyring.errors.KeyringError, match="gaia daemon status"):
            backend.get_password(SERVICE_NAME, "x")


@pytest.fixture
def clean_keyring(monkeypatch):
    previous = keyring.get_keyring()
    monkeypatch.delenv("PYTHON_KEYRING_BACKEND", raising=False)
    yield
    keyring.set_keyring(previous)


def test_install_deletes_the_credential_and_clears_the_env(
    tmp_path, monkeypatch, clean_keyring
):
    secret_file = tmp_path / "secret"
    secret_file.write_text("s3cret", encoding="utf-8")
    monkeypatch.setenv(KEYCHAIN_SECRET_FILE_ENV_VAR, str(secret_file))
    monkeypatch.setenv(CUSTODY_URL_ENV_VAR, "http://daemon")

    assert install_daemon_keyring_from_env() is True

    assert isinstance(keyring.get_keyring(), DaemonKeyring)
    assert not secret_file.exists()
    assert KEYCHAIN_SECRET_FILE_ENV_VAR not in os.environ


def test_install_without_an_offer_keeps_the_os_keyring(monkeypatch, clean_keyring):
    monkeypatch.delenv(KEYCHAIN_SECRET_FILE_ENV_VAR, raising=False)
    before = keyring.get_keyring()
    assert install_daemon_keyring_from_env() is False
    assert keyring.get_keyring() is before


def test_explicit_backend_wins_but_the_credential_is_still_deleted(
    tmp_path, monkeypatch, clean_keyring
):
    secret_file = tmp_path / "secret"
    secret_file.write_text("s3cret", encoding="utf-8")
    monkeypatch.setenv(KEYCHAIN_SECRET_FILE_ENV_VAR, str(secret_file))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    before = keyring.get_keyring()

    assert install_daemon_keyring_from_env() is False

    assert keyring.get_keyring() is before
    assert not secret_file.exists()


def test_unreadable_credential_fails_loudly(tmp_path, monkeypatch, clean_keyring):
    monkeypatch.setenv(KEYCHAIN_SECRET_FILE_ENV_VAR, str(tmp_path / "gone"))
    with pytest.raises(ConnectorsError, match="gaia daemon restart"):
        install_daemon_keyring_from_env()


def test_only_the_flagship_spec_gets_the_keychain():
    from gaia.daemon.sidecars.spec import builtin_specs

    owners = [s.agent_id for s in builtin_specs().values() if s.daemon_owns_keychain]
    assert owners == ["gaia"]


def test_registry_mints_for_the_flagship_only(tmp_path):
    from dataclasses import replace

    from gaia.daemon.sidecars.registry import SidecarRegistry
    from gaia.daemon.sidecars.spec import builtin_specs

    class Manager:
        mint_keychain_secret = None

        def __init__(self, spec, mode, expected_api_version):
            self.spec = spec

    specs = builtin_specs()
    flagship = specs["gaia"]
    other = replace(flagship, agent_id="other", daemon_owns_keychain=False)
    keychain = CustodyAuth()
    registry = SidecarRegistry(
        {"gaia": flagship, "other": other},
        custody_auth=CustodyAuth(),
        custody_base_url="http://daemon",
        keychain_auth=keychain,
    )
    registry._manager_factory = Manager
    flagship_manager = registry._new_manager("gaia", flagship, None)
    other_manager = registry._new_manager("other", other, None)

    first = flagship_manager.mint_keychain_secret()
    assert keychain.resolve(first) == "gaia"
    assert other_manager.mint_keychain_secret is None

    registry._revoke_custody("gaia")
    with pytest.raises(Exception, match="not bound"):
        keychain.resolve(first)

    # A crashed sidecar restarts on the same manager, so it needs a live credential.
    assert keychain.resolve(flagship_manager.mint_keychain_secret()) == "gaia"


def _spawn_flagship(monkeypatch, tmp_path, version):
    from gaia.daemon.sidecars import manager as mgr_mod
    from gaia.daemon.sidecars.manager import AgentSidecarManager
    from gaia.daemon.sidecars.spec import builtin_specs

    captured = {}

    class Proc:
        pid = 4321

    def popen(_argv, **kwargs):
        captured.update(kwargs.get("env") or {})
        return Proc()

    monkeypatch.setattr(mgr_mod.subprocess, "Popen", popen)
    monkeypatch.setattr(mgr_mod.atexit, "register", lambda *a, **k: None)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    manager = AgentSidecarManager(builtin_specs()["gaia"], mode="user")
    monkeypatch.setattr(manager, "_open_log", lambda port: None)
    monkeypatch.setattr(manager, "_close_log", lambda: None)
    monkeypatch.setattr(manager, "_cleanup_secret_file", lambda: None)
    manager.installed_binary_version = version
    manager.custody_url = "http://daemon"
    manager.custody_secret = "custody"
    manager.mint_keychain_secret = lambda: "keychain-credential"

    manager._spawn_process(["sidecar"], {}, 9127)
    return captured


def test_spawn_delivers_the_credential_as_a_file_never_in_the_env(
    monkeypatch, tmp_path
):
    captured = _spawn_flagship(monkeypatch, tmp_path, "99.0.0")

    path = Path(captured[KEYCHAIN_SECRET_FILE_ENV_VAR])
    assert path.read_text(encoding="utf-8") == "keychain-credential"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "keychain-credential" not in captured.values()


def test_an_older_binary_that_would_not_delete_the_file_gets_none(
    monkeypatch, tmp_path
):
    captured = _spawn_flagship(monkeypatch, tmp_path, "0.3.3")
    assert KEYCHAIN_SECRET_FILE_ENV_VAR not in captured


def test_child_processes_do_not_inherit_the_credential_path(monkeypatch):
    from gaia.env import child_env

    monkeypatch.setenv(KEYCHAIN_SECRET_FILE_ENV_VAR, "/tmp/x")
    assert KEYCHAIN_SECRET_FILE_ENV_VAR not in child_env({})
