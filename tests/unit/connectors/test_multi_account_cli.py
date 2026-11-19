# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia connectors`` with two Google accounts connected."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from gaia.connectors import accounts
from tests.unit.connectors.test_cli import _run

REVOKE_URL = "https://oauth2.googleapis.com/revoke"


@pytest.fixture
def two_gmails(google_provider):
    for sub, email in (
        ("alice-sub", "alice@example.com"),
        ("bob-sub", "bob@example.com"),
    ):
        accounts.save_signed_in_account(
            "google",
            account_id=sub,
            email=email,
            refresh_token=f"rt-{sub}",
            scopes=["openid"],
            client_id_hash=google_provider.client_id_hash,
        )


@pytest.fixture
def revoked():
    tokens = []

    def _revoke(request: httpx.Request) -> httpx.Response:
        tokens.append(parse_qs(request.content.decode())["token"][0])
        return httpx.Response(200)

    with respx.mock(assert_all_called=False) as router:
        router.post(REVOKE_URL).mock(side_effect=_revoke)
        yield tokens


def _emails():
    return [a.email for a in accounts.list_accounts("google")]


def test_list_json_carries_every_account(two_gmails):
    rc, out, err = _run("connectors", "list", "google", "--json")

    assert rc == 0, err
    (row,) = json.loads(out)
    assert row["account_id"] == "alice@example.com"
    assert row["accounts"] == [
        {
            "account_id": "alice-sub",
            "email": "alice@example.com",
            "status": "ok",
            "is_default": True,
        },
        {
            "account_id": "bob-sub",
            "email": "bob@example.com",
            "status": "ok",
            "is_default": False,
        },
    ]


def test_list_prints_one_line_per_account(two_gmails):
    rc, out, err = _run("connectors", "list", "google")

    assert rc == 0, err
    assert "    alice@example.com  (default)\n" in out
    assert "    bob@example.com\n" in out


def test_a_single_account_prints_as_before(google_provider):
    accounts.save_signed_in_account(
        "google",
        account_id="alice-sub",
        email="alice@example.com",
        refresh_token="rt",
        scopes=["openid"],
        client_id_hash=google_provider.client_id_hash,
    )
    rc, out, _err = _run("connectors", "list", "google")

    assert rc == 0
    assert out.splitlines() == [
        "google                          [oauth_pkce]  configured (alice@example.com)"
    ]


def test_an_expired_account_says_how_to_fix_it(two_gmails):
    accounts.mark_state("google", "id:bob-sub", "reauth_required")

    _rc, out, _err = _run("connectors", "list", "google")

    assert "bob@example.com  (sign-in expired: reconnect with" in out


def test_disconnect_asks_which_account_when_there_are_several(two_gmails, revoked):
    rc, _out, err = _run("connectors", "disconnect", "google")

    assert rc == 5
    assert "alice@example.com, bob@example.com" in err
    assert "--account" in err and "--all" in err
    assert revoked == []
    assert _emails() == ["alice@example.com", "bob@example.com"]


def test_disconnect_one_account(two_gmails, revoked):
    rc, out, err = _run(
        "connectors", "disconnect", "google", "--account", "bob@example.com"
    )

    assert rc == 0, err
    assert (
        "Removed bob@example.com from google; 1 other account still connected." in out
    )
    assert revoked == ["rt-bob-sub"]
    assert _emails() == ["alice@example.com"]


def test_disconnect_all_accounts(two_gmails, revoked):
    rc, _out, err = _run("connectors", "disconnect", "google", "--all")

    assert rc == 0, err
    assert sorted(revoked) == ["rt-alice-sub", "rt-bob-sub"]
    assert _emails() == []


def test_set_default(two_gmails):
    rc, out, err = _run("connectors", "set-default", "google", "bob@example.com")

    assert rc == 0, err
    assert out == "google now uses bob@example.com by default.\n"
    assert accounts.default_slot("google") == "id:bob-sub"


def test_set_default_to_an_unknown_account_names_the_choices(two_gmails):
    rc, _out, err = _run("connectors", "set-default", "google", "carol@example.com")

    assert rc == 5
    assert "alice@example.com" in err and "bob@example.com" in err


@pytest.mark.parametrize(
    "flags, intent",
    [
        (["--add"], {"add_account": True, "account": None}),
        (
            ["--account", "bob@example.com"],
            {"add_account": False, "account": "bob@example.com"},
        ),
    ],
)
def test_connect_passes_the_account_choice(monkeypatch, flags, intent):
    seen = {}

    async def _start(connector_id, *, scopes, grant_agents=None, **account):
        seen.update(account)
        return {
            "flow_id": "F1",
            "authorization_url": "https://auth.example",
            "callback_port": 8085,
        }

    async def _complete(flow_id):
        return {"account_email": "carol@example.com", "added": True}

    monkeypatch.setattr("gaia.connectors.api.start_authorization", _start)
    monkeypatch.setattr("gaia.connectors.api.complete_authorization", _complete)

    rc, out, err = _run("connectors", "connect", "google", *flags)

    assert rc == 0, err
    assert seen == intent
    if "--add" in flags:
        assert "Added carol@example.com" in out
    else:
        assert "a different account from bob@example.com" in out


def test_add_and_account_cannot_be_combined():
    rc, _out, _err = _run(
        "connectors", "connect", "google", "--add", "--account", "a@example.com"
    )
    assert rc == 2


def _fake_browser_sign_in(monkeypatch, result):
    async def _start(connector_id, *, scopes, grant_agents=None, **account):
        return {
            "flow_id": "F1",
            "authorization_url": "https://auth.example",
            "callback_port": 8085,
        }

    async def _complete(flow_id):
        return result

    monkeypatch.setattr("gaia.connectors.api.start_authorization", _start)
    monkeypatch.setattr("gaia.connectors.api.complete_authorization", _complete)


def test_a_plain_connect_as_another_account_says_it_was_added(monkeypatch):
    _fake_browser_sign_in(
        monkeypatch,
        {"account_email": "carol@example.com", "added": True, "is_default": False},
    )

    rc, out, err = _run("connectors", "connect", "google")

    assert rc == 0, err
    assert "Connected as" not in out
    assert "Added carol@example.com as another account; the default is unchanged" in (
        out
    )
    assert "gaia connectors set-default google carol@example.com" in out


def test_the_first_account_says_connected(monkeypatch):
    _fake_browser_sign_in(
        monkeypatch,
        {"account_email": "carol@example.com", "added": True, "is_default": True},
    )

    rc, out, err = _run("connectors", "connect", "google")

    assert rc == 0, err
    assert out.endswith("Connected as carol@example.com\n")


@pytest.mark.parametrize(
    "flags, intent",
    [
        ([], {"add_account": False, "account": None}),
        (["--add"], {"add_account": True, "account": None}),
        (
            ["--account", "bob@example.com"],
            {"add_account": False, "account": "bob@example.com"},
        ),
    ],
)
def test_device_connect_passes_the_account_choice(monkeypatch, flags, intent):
    seen = {}

    async def _start(connector_id, scopes, **account):
        seen.update(account)
        return {
            "device_code": "DEV",
            "user_code": "CODE",
            "verification_uri": "https://example.com/device",
            "scopes": scopes,
            "interval": 1,
            "expires_in": 60,
            "message": "",
            "intent": "the-intent",
        }

    async def _poll(connector_id, device_code, **kwargs):
        seen["polled_intent"] = kwargs["intent"]
        return {
            "account_email": "carol@example.com",
            "added": True,
            "is_default": False,
        }

    monkeypatch.setattr("gaia.connectors.api.start_device_flow", _start)
    monkeypatch.setattr("gaia.connectors.api.poll_device_flow", _poll)

    rc, out, err = _run("connectors", "connect", "google", "--device", *flags)

    assert rc == 0, err
    assert seen == {**intent, "polled_intent": "the-intent"}
    assert "carol@example.com" in out and "Connected as" not in out
