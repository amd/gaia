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


CALLERS = [
    Caller("slack", _slack),
    Caller("slack-clear", _slack_clear),
    Caller("discord", _discord),
    Caller("browser-session", _browser_session),
    Caller("mcp-keyring-ref", _mcp_keyring_ref),
    Caller("hub-publisher", _hub_publisher),
    Caller("connector-store", _connector_store),
    Caller("mcp-server-slots", _mcp_server_slots),
    Caller("daemon-keychain", _daemon_keychain),
]


@pytest.mark.parametrize("caller", CALLERS, ids=lambda c: c.name)
def test_strong_backend_round_trips(caller, strong):
    save, load, expected, _ = caller.build()
    save()
    assert load() == expected
    assert strong.calls, "the strong backend was never used"


@pytest.mark.parametrize("caller", CALLERS, ids=lambda c: c.name)
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
    assert "PlaintextKeyring" in msg and "docs/security/connections.mdx" in msg


def test_discord_connector_token_is_none_on_a_refused_backend(refused):
    from gaia.messaging.discord import credentials

    assert credentials.connector_token() is None
    assert refused.calls == []


def test_discord_clear_on_a_refused_backend_raises_what_disconnect_catches(refused):
    from gaia.messaging.discord import credentials

    with pytest.raises(RuntimeError) as exc:
        credentials.clear()
    assert isinstance(exc.value, credentials.DiscordCredentialsError)
    assert refused.calls == []


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
