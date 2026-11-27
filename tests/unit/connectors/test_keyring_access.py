# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every feature that keeps a secret in the OS keyring runs the weak-backend check.

Each caller below is driven against three backends: a strong one (the secret
round-trips), a weak one and a chain that cannot be inspected (both refused,
and the store is never touched). Every fake sets ``viable = False`` so no real
``ChainerBackend`` can discover it, and the active backend is swapped by
monkeypatching ``keyring.core._keyring_backend``, so the real OS keyring is
never resolved, read or written.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

import keyring
import keyring.backend
import keyring.core
import keyring.errors
import pytest
from keyring.backends.chainer import ChainerBackend

from gaia.connectors import keyring_access, store
from gaia.connectors.errors import ConnectorsError

SERVICE = store.SERVICE_NAME


class _Recording(keyring.backend.KeyringBackend):
    viable = False
    priority = 1

    def __init__(self):
        super().__init__()
        self.items = {}
        self.calls = []

    def get_password(self, service, username):
        self.calls.append(("get", service, username))
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.calls.append(("set", service, username))
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.calls.append(("delete", service, username))
        if (service, username) not in self.items:
            raise keyring.errors.PasswordDeleteError("absent")
        del self.items[(service, username)]


class StrongKeyring(_Recording):
    pass


class PlaintextKeyring(_Recording):
    pass


class _UninspectableChain(ChainerBackend):
    viable = False

    def __init__(self, recorder):
        self.recorder = recorder

    @property
    def backends(self):
        raise RuntimeError("backend discovery failed")

    def get_password(self, service, username):
        return self.recorder.get_password(service, username)

    def set_password(self, service, username, password):
        self.recorder.set_password(service, username, password)

    def delete_password(self, service, username):
        self.recorder.delete_password(service, username)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for var in (
        "GAIA_SLACK_BOT_TOKEN",
        "GAIA_SLACK_APP_TOKEN",
        "GAIA_DISCORD_BOT_TOKEN",
        "GAIA_HUB_TOKEN",
        "PYPI_TOKEN",
        "PYTHON_KEYRING_BACKEND",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _fresh_warnings():
    keyring_access._warned.clear()
    yield
    keyring_access._warned.clear()


def _activate(monkeypatch, backend) -> None:
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)


@pytest.fixture
def strong(monkeypatch):
    backend = StrongKeyring()
    _activate(monkeypatch, backend)
    return backend


@pytest.fixture(params=["weak", "uninspectable"])
def refused(request, monkeypatch):
    """A backend the check must refuse; returns the recorder that must stay empty."""
    if request.param == "weak":
        backend = recorder = PlaintextKeyring()
    else:
        recorder = StrongKeyring()
        backend = _UninspectableChain(recorder)
    _activate(monkeypatch, backend)
    return recorder


# ---------------------------------------------------------------------------
# Every caller: (save, load, refusal exception)
# ---------------------------------------------------------------------------


def _slack():
    from gaia.messaging.slack import credentials

    def save():
        credentials.save("xoxb-bot", "xapp-app")

    def load():
        got = credentials.load()
        return (got.bot_token, got.app_token)

    return (
        save,
        load,
        ("xoxb-bot", "xapp-app"),
        (ConnectorsError, credentials.SlackCredentialsError),
    )


def _slack_clear():
    from gaia.messaging.slack import credentials

    def save():
        credentials.save("xoxb-bot", "xapp-app")

    def load():
        credentials.clear()
        return credentials.load_optional()

    return save, load, None, (ConnectorsError,)


def _discord():
    from gaia.messaging.discord import credentials

    return (
        lambda: credentials.save("discord-bot-token"),
        credentials.load,
        "discord-bot-token",
        (credentials.DiscordCredentialsError,),
    )


def _browser_session():
    from gaia.browser import session
    from gaia.browser.errors import SessionStoreError

    first = {}

    def save():
        first["key"] = session._load_key()

    def load():
        return session._load_key() == first["key"] and len(first["key"]) == 32

    return save, load, True, (SessionStoreError,)


def _mcp_keyring_ref():
    from gaia.mcp.client.mcp_client import _resolve_keyring_refs

    ref = {"TOKEN": {"$keyring": f"{SERVICE}:mcp-demo:TOKEN"}}

    def save():
        keyring_access.write(SERVICE, "mcp-demo:TOKEN", "mcp-secret")

    return (
        save,
        lambda: _resolve_keyring_refs(ref),
        {"TOKEN": "mcp-secret"},
        (ConnectorsError,),
    )


def _hub_publisher():
    from gaia.hub import publisher

    return (
        lambda: publisher.store_token("hub", "hub-token"),
        publisher.get_hub_token,
        "hub-token",
        (publisher.PublisherError,),
    )


def _connector_store():
    return (
        lambda: store.save_secret("provider:demo", "demo-secret-value"),
        lambda: store.peek_secret("provider:demo"),
        "demo-secret-value",
        (ConnectorsError,),
    )


def _mcp_server_slots():
    from gaia.connectors import mcp_server

    def save():
        keyring_access.write(SERVICE, "mcp-demo:TOKEN", "x")

    def load():
        mcp_server._delete_keyring_slots(["mcp-demo:TOKEN"])
        return keyring_access.read(SERVICE, "mcp-demo:TOKEN")

    return save, load, None, (ConnectorsError,)


def _daemon_keychain():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.daemon.custody.auth import CustodyAuth
    from gaia.daemon.custody.keychain_routes import build_keychain_router
    from gaia.daemon.custody.routes import install_custody_exception_handler

    auth = CustodyAuth()
    app = FastAPI()
    install_custody_exception_handler(app)
    app.include_router(build_keychain_router(auth))
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {auth.mint('gaia')}"}

    class _Refused(Exception):
        pass

    def post(action, body):
        resp = client.post(f"/host/v1/keychain/{action}", json=body, headers=headers)
        if resp.status_code == 503:
            raise _Refused(resp.json()["detail"])
        assert resp.status_code < 400, resp.text
        return resp.json()

    def save():
        post("set", {"service": SERVICE, "username": "d", "value": "daemon-secret"})

    def load():
        value = post("get", {"service": SERVICE, "username": "d"})["value"]
        post("delete", {"service": SERVICE, "username": "d"})
        return value

    return save, load, "daemon-secret", (_Refused,)


@dataclass
class Caller:
    name: str
    build: Callable


READ_WRITE_CALLERS = [
    Caller("slack", _slack),
    Caller("discord", _discord),
    Caller("browser-session", _browser_session),
    Caller("mcp-keyring-ref", _mcp_keyring_ref),
    Caller("hub-publisher", _hub_publisher),
    Caller("connector-store", _connector_store),
    Caller("daemon-keychain", _daemon_keychain),
]
CALLERS = READ_WRITE_CALLERS + [
    Caller("slack-clear", _slack_clear),
    Caller("mcp-server-slots", _mcp_server_slots),
]


@pytest.mark.parametrize("caller", CALLERS, ids=lambda c: c.name)
def test_strong_backend_round_trips(caller, strong):
    save, load, expected, _ = caller.build()
    save()
    assert load() == expected
    assert strong.calls, "the strong backend was never used"


@pytest.mark.parametrize("caller", READ_WRITE_CALLERS, ids=lambda c: c.name)
def test_refused_backend_is_never_read_or_written(caller, refused):
    save, load, _, errors = caller.build()
    with pytest.raises(errors):
        save()
    with pytest.raises(errors):
        load()
    assert refused.calls == []
    assert refused.items == {}


def test_refusal_message_is_actionable(monkeypatch):
    _activate(monkeypatch, PlaintextKeyring())
    with pytest.raises(ConnectorsError) as exc:
        keyring_access.read(SERVICE, "anything")
    msg = str(exc.value)
    assert "PlaintextKeyring" in msg and "See docs" in msg


def test_discord_connector_token_is_none_on_a_refused_backend(refused):
    from gaia.messaging.discord import credentials

    assert credentials.connector_token() is None
    assert refused.calls == []


@pytest.mark.parametrize("module", ["slack", "discord"])
def test_messaging_load_on_a_refused_backend_gives_only_the_refusal_remedy(
    module, monkeypatch
):
    import importlib

    credentials = importlib.import_module(f"gaia.messaging.{module}.credentials")
    _activate(monkeypatch, PlaintextKeyring())
    with pytest.raises(RuntimeError) as exc:
        credentials.load()
    msg = str(exc.value)
    assert "Insecure keyring backend" in msg
    assert "again" not in msg and "re-run" not in msg


# ---------------------------------------------------------------------------
# Deletes: allowed on a weak backend (removing a secret only reduces exposure)
# ---------------------------------------------------------------------------


def _daemon_delete(username):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.daemon.custody.auth import CustodyAuth
    from gaia.daemon.custody.keychain_routes import build_keychain_router
    from gaia.daemon.custody.routes import install_custody_exception_handler

    auth = CustodyAuth()
    app = FastAPI()
    install_custody_exception_handler(app)
    app.include_router(build_keychain_router(auth))
    resp = TestClient(app).post(
        "/host/v1/keychain/delete",
        json={"service": SERVICE, "username": username},
        headers={"Authorization": f"Bearer {auth.mint('gaia')}"},
    )
    assert resp.status_code == 200, resp.text


def _slack_clear_delete():
    from gaia.messaging.slack import credentials

    credentials.clear()


def _discord_clear_delete():
    from gaia.messaging.discord import credentials

    credentials.clear()


def _mcp_server_delete():
    from gaia.connectors import mcp_server

    mcp_server._delete_keyring_slots(["mcp-demo:TOKEN"])


def cloud_keys_slot():
    from gaia.llm import cloud_keys

    return cloud_keys._check(sorted(cloud_keys.PROVIDERS)[0])


def _forget_cloud_key():
    from gaia.llm import cloud_keys

    provider = sorted(cloud_keys.PROVIDERS)[0]
    assert cloud_keys.forget_key(provider) is True


def _gateway_slot():
    from gaia.llm import gateway

    return gateway.GATEWAY_SECRET_NAME


def _forget_gateway_token():
    from gaia.llm import gateway

    assert gateway.forget_token() is True


def _remove_all_accounts(provider):
    from gaia.connectors import accounts

    assert accounts.remove_all_accounts(provider) == 1


DELETERS = {
    "helper": (["x:y"], lambda: keyring_access.delete(SERVICE, "x:y")),
    "slack-clear": (["slack:bot_token", "slack:app_token"], _slack_clear_delete),
    "discord-clear": (["discord:bot_token"], _discord_clear_delete),
    "mcp-server-slots": (["mcp-demo:TOKEN"], _mcp_server_delete),
    "store-secret": (
        [store._secret_username("demo")],
        lambda: store.delete_secret("demo"),
    ),
    "store-chunked-secret": (
        [
            store._secret_username("big"),
            store._secret_username("big") + "#0",
            store._secret_username("big") + "#1",
        ],
        lambda: store.delete_secret("big"),
    ),
    "store-connection": (
        [store._connection_username("google", store.DEFAULT_ACCOUNT)],
        lambda: store.delete_connection("google"),
    ),
    "store-provider-credentials": (
        [store._provider_credentials_username("google")],
        lambda: store.clear_provider_credentials("google"),
    ),
    "daemon-keychain": (["d"], lambda: _daemon_delete("d")),
    "cloud-key": (
        [store._secret_username(cloud_keys_slot())],
        _forget_cloud_key,
    ),
    "gateway-token": (
        [store._secret_username(_gateway_slot())],
        _forget_gateway_token,
    ),
    "accounts-remove-all": (
        [store._connection_username("google", store.DEFAULT_ACCOUNT)],
        lambda: _remove_all_accounts("google"),
    ),
}


@pytest.mark.parametrize("name", sorted(DELETERS))
def test_refused_backend_still_deletes_and_warns(name, refused, caplog):
    usernames, delete = DELETERS[name]
    for username in usernames:
        value = "plaintext-secret"
        if name == "store-chunked-secret" and "#" not in username:
            value = "gaia-chunked-v2:2:00000000"
        refused.items[(SERVICE, username)] = value

    with caplog.at_level(logging.WARNING, logger="gaia.connectors.keyring_access"):
        delete()

    assert refused.items == {}
    assert {op for op, *_ in refused.calls} <= {"get", "delete"}
    assert "insecure keyring backend" in caplog.text
    assert keyring_access.DOCS_URL in caplog.text


@pytest.mark.parametrize("forget", ["gateway", "cloud-key"])
def test_forgetting_nothing_reports_nothing_removed(forget, refused):
    from gaia.llm import cloud_keys, gateway

    if forget == "gateway":
        assert gateway.forget_token() is False
    else:
        assert cloud_keys.forget_key(sorted(cloud_keys.PROVIDERS)[0]) is False
    assert refused.items == {}
    assert {op for op, *_ in refused.calls} <= {"get", "delete"}


def test_gateway_forget_on_a_strong_backend(strong):
    from gaia.llm import gateway

    assert gateway.forget_token() is False
    gateway.remember_token("tok-abc")
    assert gateway.forget_token() is True
    assert strong.items == {}


def test_weak_backend_delete_warns_once_per_process(monkeypatch, caplog):
    backend = PlaintextKeyring()
    backend.items[(SERVICE, "a")] = backend.items[(SERVICE, "b")] = "v"
    _activate(monkeypatch, backend)
    with caplog.at_level(logging.WARNING, logger="gaia.connectors.keyring_access"):
        keyring_access.delete(SERVICE, "a")
        keyring_access.delete(SERVICE, "b")
    assert caplog.text.count("insecure keyring backend") == 1


def test_strong_backend_delete_does_not_warn(strong, caplog):
    strong.items[(SERVICE, "a")] = "v"
    with caplog.at_level(logging.WARNING, logger="gaia.connectors.keyring_access"):
        keyring_access.delete(SERVICE, "a")
    assert strong.items == {}
    assert "insecure" not in caplog.text


def test_delete_with_no_usable_keyring_still_fails_loudly(monkeypatch):
    def broken():
        raise RuntimeError("backend failed to load")

    monkeypatch.setattr(keyring, "get_keyring", broken)
    with pytest.raises(ConnectorsError, match="Could not initialize"):
        keyring_access.delete(SERVICE, "a")


def test_read_for_delete_never_serves_a_normal_read(monkeypatch):
    """Only the delete path may read on a weak backend; ``read`` still refuses."""
    backend = PlaintextKeyring()
    backend.items[(SERVICE, "a")] = "v"
    _activate(monkeypatch, backend)
    with pytest.raises(ConnectorsError):
        keyring_access.read(SERVICE, "a")
    with pytest.raises(ConnectorsError):
        store.peek_secret("a")


def test_helper_delete_of_absent_entry_raises_keyring_error(strong):
    with pytest.raises(keyring.errors.PasswordDeleteError):
        keyring_access.delete(SERVICE, "never-stored")


def test_mcp_server_configure_refuses_before_writing(refused):
    import gaia.connectors.catalog  # noqa: F401  # registers the specs
    from gaia.connectors.mcp_server import McpServerHandler
    from gaia.connectors.registry import REGISTRY

    spec = next(s for s in REGISTRY.all() if s.type == "mcp_server" and s.mcp_env_keys)
    config = {k: "value-123456789" for k in spec.mcp_env_keys}
    with pytest.raises(ConnectorsError):
        asyncio.run(McpServerHandler().configure(spec, config))
    assert refused.calls == []


def _long_marker_items(monkeypatch) -> dict:
    """Store items holding a chunked recovery marker that belongs to ``acct-b``."""
    writer = StrongKeyring()
    _activate(monkeypatch, writer)
    snapshot = {
        store._provider_credentials_username("google"): "c" * 1500,
        store._connection_username("google", "acct-b"): None,
    }
    store.write_forward_recovery("google", snapshot, slot="acct-b")
    marker = writer.items[(SERVICE, store._forward_recovery_username("google"))]
    assert marker.startswith(store._CHUNK_SENTINEL), "marker must be chunked"
    return dict(writer.items)


@pytest.mark.parametrize("kind", ["strong", "weak"])
def test_deleting_one_account_keeps_another_accounts_chunked_marker(kind, monkeypatch):
    items = _long_marker_items(monkeypatch)
    backend = StrongKeyring() if kind == "strong" else PlaintextKeyring()
    backend.items.update(items)
    _activate(monkeypatch, backend)
    marker_key = (SERVICE, store._forward_recovery_username("google"))

    store.delete_connection("google", slot=store.DEFAULT_ACCOUNT)
    assert marker_key in backend.items

    store.delete_connection("google", slot="acct-b")
    assert not any(
        user.startswith(store._forward_recovery_username("google"))
        for _, user in backend.items
    )
