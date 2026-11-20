# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Disconnecting an OAuth account works on a keyring GAIA won't read from.

Revoking at the provider needs the stored refresh token, and a weak or
uninspectable keyring refuses reads. The disconnect must still remove the
account here, skip the provider call, and tell the user where to revoke by
hand. Every fake sets ``viable = False`` and is swapped in through
``keyring.core._keyring_backend``, so the real OS keyring is never touched.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import keyring
import keyring.backend
import keyring.core
import keyring.errors
import pytest
from keyring.backends.chainer import ChainerBackend

import gaia.connectors.catalog  # noqa: F401  # registers the specs
from gaia.connectors import accounts, api
from gaia.connectors import flow as flow_mod
from gaia.connectors import keyring_access, store
from gaia.connectors.errors import (
    AccountChoiceRequiredError,
    ConnectorsError,
    UnknownAccountError,
)

SERVICE = store.SERVICE_NAME
ALICE = ("alice-sub", "alice@example.com", "rt-alice-secret")
BOB = ("bob-sub", "bob@example.com", "rt-bob-secret")


class _Recording(keyring.backend.KeyringBackend):
    viable = False
    priority = 1

    def __init__(self, items=None):
        super().__init__()
        self.items = dict(items or {})
        self.calls = []

    def get_password(self, service, username):
        self.calls.append(("get", username))
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.calls.append(("set", username))
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.calls.append(("delete", username))
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
    monkeypatch.delenv("PYTHON_KEYRING_BACKEND", raising=False)
    keyring_access._warned.clear()
    yield
    keyring_access._warned.clear()


@pytest.fixture
def no_provider_call(monkeypatch):
    """Fail the test if anything tries the provider revoke."""
    calls = []

    async def _forbidden(provider_id, *, slot=store.DEFAULT_ACCOUNT):
        calls.append((provider_id, slot))
        raise AssertionError("the provider revoke must not run on a weak keyring")

    monkeypatch.setattr(flow_mod, "revoke_provider_token", _forbidden)
    return calls


def _activate(monkeypatch, backend):
    monkeypatch.setattr(keyring.core, "_keyring_backend", backend)


def _seed(monkeypatch, provider, *people, items=None):
    """Sign ``people`` in on a strong keyring holding ``items``; return its items."""
    writer = StrongKeyring(items)
    _activate(monkeypatch, writer)
    for account_id, email, refresh in people:
        accounts.save_signed_in_account(
            provider,
            account_id=account_id,
            email=email,
            refresh_token=refresh,
            scopes=["openid"],
            client_id_hash="hash",
            add_account=True,
        )
    return dict(writer.items)


@pytest.fixture(params=["weak", "uninspectable"])
def refused(request, monkeypatch):
    """Returns ``install(items) -> recorder``: a refused backend holding ``items``."""

    def install(items):
        recorder = (
            PlaintextKeyring(items) if request.param == "weak" else StrongKeyring(items)
        )
        backend = recorder if request.param == "weak" else _UninspectableChain(recorder)
        _activate(monkeypatch, backend)
        return recorder

    return install


def _connection_keys(provider):
    return lambda items: [user for _, user in items if user.startswith(f"{provider}:")]


def _writes(recorder):
    """What a disconnect wrote to a refused backend; it may only read and delete."""
    return [c for c in recorder.calls if c[0] == "set"]


def _index_section(provider):
    path = accounts.index_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["connectors"].get(provider)


# ---------------------------------------------------------------------------
# Full disconnect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,page",
    [
        ("google", "https://myaccount.google.com/permissions"),
        ("microsoft", "https://account.live.com/consent/Manage"),
        ("microsoft_work", "https://myapps.microsoft.com"),
    ],
)
def test_full_disconnect_removes_every_account_without_the_provider(
    provider, page, refused, monkeypatch, no_provider_call
):
    from gaia.connectors.handler import disconnect

    recorder = refused(_seed(monkeypatch, provider, ALICE, BOB))
    assert len(_connection_keys(provider)(recorder.items)) == 2

    result = asyncio.run(disconnect(provider, all_accounts=True))

    assert recorder.items == {}
    assert _index_section(provider) is None
    assert no_provider_call == []
    assert _writes(recorder) == []
    assert result["revoke_supported"] is False
    assert result["revoked_remotely"] is False
    assert result["remaining_accounts"] == 0
    assert page in result["revoke_error"]
    assert "https://amd-gaia.ai/docs/security/connections" in result["revoke_error"]
    assert "did not revoke" in result["revoke_error"]
    assert {a["account_email"] for a in result["accounts"]} == {
        ALICE[1],
        BOB[1],
    }
    for acct in result["accounts"]:
        assert acct["revoke_error"] == result["revoke_error"]


def test_the_message_never_carries_a_token(refused, monkeypatch, no_provider_call):
    recorder = refused(_seed(monkeypatch, "google", ALICE))
    result = asyncio.run(api.revoke_connection_async("google", all_accounts=True))
    text = json.dumps(result)
    assert ALICE[2] not in text and BOB[2] not in text
    assert recorder.items == {}


def test_single_account_connector_disconnects_without_naming_it(
    refused, monkeypatch, no_provider_call
):
    from gaia.connectors.handler import disconnect

    recorder = refused(_seed(monkeypatch, "google", ALICE))
    result = asyncio.run(disconnect("google"))
    assert recorder.items == {}
    assert result["remaining_accounts"] == 0
    assert [a["account_email"] for a in result["accounts"]] == [ALICE[1]]


def test_disconnect_clears_agent_grants_on_a_weak_keyring(
    refused, monkeypatch, no_provider_call
):
    from gaia.connectors import grants
    from gaia.connectors.handler import disconnect

    items = _seed(monkeypatch, "google", ALICE)
    grants.grant_agent("google", "builtin:chat", ["openid"])
    refused(items)

    asyncio.run(disconnect("google", all_accounts=True))

    assert grants.list_agent_grants("google") == {}


# ---------------------------------------------------------------------------
# One account of several
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("named_by", ["email", "id"])
def test_one_account_goes_and_the_other_stays(
    named_by, refused, monkeypatch, no_provider_call
):
    from gaia.connectors.handler import disconnect

    recorder = refused(_seed(monkeypatch, "google", ALICE, BOB))
    target = BOB[1] if named_by == "email" else BOB[0]

    result = asyncio.run(disconnect("google", account_id=target))

    left = _connection_keys("google")(recorder.items)
    assert left == ["google:default"]
    assert ALICE[2] in recorder.items[(SERVICE, "google:default")]
    assert result["remaining_accounts"] == 1
    assert result["account_email"] == BOB[1]
    assert BOB[1] not in json.dumps(_index_section("google"))
    assert ALICE[1] in json.dumps(_index_section("google"))
    assert no_provider_call == []


def test_several_accounts_still_ask_which_one(refused, monkeypatch, no_provider_call):
    from gaia.connectors.handler import disconnect

    recorder = refused(_seed(monkeypatch, "google", ALICE, BOB))
    before = dict(recorder.items)

    with pytest.raises(AccountChoiceRequiredError) as exc:
        asyncio.run(disconnect("google"))

    assert set(exc.value.choices) == {ALICE[1], BOB[1]}
    assert recorder.items == before


def test_an_unknown_account_is_still_named_as_unknown(
    refused, monkeypatch, no_provider_call
):
    from gaia.connectors.handler import disconnect

    recorder = refused(_seed(monkeypatch, "google", ALICE, BOB))
    before = dict(recorder.items)

    with pytest.raises(UnknownAccountError) as exc:
        asyncio.run(disconnect("google", account_id="mallory@example.com"))

    assert set(exc.value.choices) == {ALICE[1], BOB[1]}
    assert recorder.items == before


def test_nothing_stored_is_idempotent(refused, no_provider_call):
    from gaia.connectors.handler import disconnect

    recorder = refused({})
    result = asyncio.run(disconnect("google", all_accounts=True))
    assert recorder.items == {}
    assert result["remaining_accounts"] == 0
    assert _writes(recorder) == []


# ---------------------------------------------------------------------------
# An account saved before the account index existed
# ---------------------------------------------------------------------------


def _legacy_only_items(monkeypatch):
    writer = StrongKeyring()
    _activate(monkeypatch, writer)
    store.save_connection(
        provider="google",
        account_email=ALICE[1],
        refresh_token=ALICE[2],
        scopes=["openid"],
        client_id_hash="hash",
    )
    assert _index_section("google") is None
    return dict(writer.items)


def test_a_pre_index_account_is_removed(refused, monkeypatch, no_provider_call):
    recorder = refused(_legacy_only_items(monkeypatch))
    result = asyncio.run(api.revoke_connection_async("google", all_accounts=True))
    assert recorder.items == {}
    assert result["revoke_supported"] is False


def test_a_pre_index_account_survives_removing_another(
    refused, monkeypatch, no_provider_call
):
    items = _seed(monkeypatch, "google", BOB, items=_legacy_only_items(monkeypatch))
    bob_key = f"google:{accounts.slot_for(BOB[0])}"
    assert sorted(_connection_keys("google")(items)) == ["google:default", bob_key]
    # Drop the legacy entry the sign-in wrote, so only the keyring knows it.
    path = accounts.index_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    section = data["connectors"]["google"]
    section["accounts"] = [e for e in section["accounts"] if e["slot"] != "default"]
    section.pop("default_slot", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    recorder = refused(items)

    result = asyncio.run(api.revoke_connection_async("google", account=BOB[1]))

    assert _connection_keys("google")(recorder.items) == ["google:default"]
    assert result["remaining_accounts"] == 1


def test_a_pre_index_account_is_named_by_its_slot(
    refused, monkeypatch, no_provider_call
):
    recorder = refused(_legacy_only_items(monkeypatch))
    asyncio.run(api.revoke_connection_async("google", account="default"))
    assert recorder.items == {}


# ---------------------------------------------------------------------------
# What does not change
# ---------------------------------------------------------------------------


def test_a_strong_keyring_still_revokes_at_the_provider(monkeypatch):
    calls = []

    async def _revoke(provider_id, *, slot=store.DEFAULT_ACCOUNT):
        calls.append((provider_id, slot))
        return {
            "revoke_supported": True,
            "revoked_remotely": True,
            "revoke_error": None,
        }

    monkeypatch.setattr(flow_mod, "revoke_provider_token", _revoke)
    items = _seed(monkeypatch, "google", ALICE, BOB)
    strong = StrongKeyring(items)
    _activate(monkeypatch, strong)

    result = asyncio.run(api.revoke_connection_async("google", all_accounts=True))

    assert sorted(calls) == sorted(
        [("google", "default"), ("google", accounts.slot_for(BOB[0]))]
    )
    assert result["revoked_remotely"] is True
    assert result["revoke_error"] is None
    assert strong.items == {}


def test_a_keyring_that_cannot_load_still_fails_loudly(monkeypatch, no_provider_call):
    items = _seed(monkeypatch, "google", ALICE)
    recorder = StrongKeyring(items)

    def broken():
        raise RuntimeError("backend failed to load")

    monkeypatch.setattr(keyring, "get_keyring", broken)
    with pytest.raises(ConnectorsError, match="Could not initialize"):
        asyncio.run(api.revoke_connection_async("google", all_accounts=True))
    assert recorder.items == items


def test_reads_stay_refused_after_a_disconnect(refused, monkeypatch, no_provider_call):
    recorder = refused(_seed(monkeypatch, "google", ALICE, BOB))
    asyncio.run(api.revoke_connection_async("google", account=BOB[1]))
    with pytest.raises(store.WeakKeyringError):
        store.peek_connection("google")
    with pytest.raises(store.WeakKeyringError):
        accounts.list_accounts("google")
    assert ALICE[2] in recorder.items[(SERVICE, "google:default")]


def test_the_cli_tells_the_user_where_to_revoke(
    refused, monkeypatch, no_provider_call, capsys
):
    from gaia.connectors.cli import _handle_disconnect

    refused(_seed(monkeypatch, "google", ALICE))
    args = argparse.Namespace(connector_id="google", account=None, all_accounts=True)

    assert _handle_disconnect(args) == 0

    out = capsys.readouterr().out
    assert "Disconnected google locally." in out
    assert "https://myaccount.google.com/permissions" in out
    assert "provider access revoked" not in out


def test_the_ui_route_reports_the_local_only_outcome(
    refused, monkeypatch, no_provider_call
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.ui.routers import connectors as router_mod

    events = []

    async def _emit(name, payload):
        events.append((name, payload))

    monkeypatch.setattr(router_mod._emitter, "emit", _emit)
    recorder = refused(_seed(monkeypatch, "google", ALICE, BOB))
    app = FastAPI()
    app.include_router(router_mod.router)

    resp = TestClient(app).delete(f"{router_mod.router.prefix}/google?all=true")

    assert resp.status_code == 204, resp.text
    assert recorder.items == {}
    (name, payload), *_ = events
    assert name == "connector.disconnected"
    assert payload["revoke_supported"] is False
    assert "https://myaccount.google.com/permissions" in payload["revoke_error"]
