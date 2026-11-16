# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Several accounts per connector: two Gmails side by side.

Each test drives the real sign-in exchange, refresh and revoke code against a
mocked Google token endpoint, then checks what a user would see: which
accounts are listed, which token each one gets, and what is left after one is
removed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from gaia.connectors import _refresh_lock, accounts, api
from gaia.connectors import flow as flow_mod
from gaia.connectors.errors import (
    ConnectionRevokedError,
    ConnectorsError,
    UnknownAccountError,
)
from gaia.connectors.store import peek_connection, save_connection
from gaia.connectors.tokens import get_or_refresh

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", GMAIL]

ALICE = {"sub": "alice-sub", "email": "alice@example.com"}
BOB = {"sub": "bob-sub", "email": "bob@example.com"}


def _id_token(claims: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return f"header.{raw.rstrip('=')}.sig"


class _Google:
    """The token endpoint: sign-ins hand out the queued identity; refreshes
    return an access token naming the refresh token they were sent."""

    def __init__(self):
        self.next_identity = ALICE
        self.next_refresh = "rt-alice"
        self.refreshed_with = []
        self.revoked = []
        self.invalid = set()
        self.refresh_identity = None

    def token(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        if form["grant_type"] == ["refresh_token"]:
            sent = form["refresh_token"][0]
            self.refreshed_with.append(sent)
            if sent in self.invalid:
                return httpx.Response(400, json={"error": "invalid_grant"})
            reply = {"access_token": f"access-for-{sent}", "expires_in": 3600}
            if self.refresh_identity:
                reply["id_token"] = _id_token(self.refresh_identity)
            return httpx.Response(200, json=reply)
        return httpx.Response(
            200,
            json={
                "access_token": "signin-access",
                "refresh_token": self.next_refresh,
                "expires_in": 3600,
                "scope": " ".join(SCOPES),
                "id_token": _id_token(self.next_identity),
            },
        )

    def revoke(self, request: httpx.Request) -> httpx.Response:
        self.revoked.append(parse_qs(request.content.decode())["token"][0])
        return httpx.Response(200)


@pytest.fixture
def google(google_provider, no_browser):
    endpoint = _Google()
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).mock(side_effect=endpoint.token)
        router.post(REVOKE_URL).mock(side_effect=endpoint.revoke)
        yield endpoint


async def _sign_in(google, identity, refresh_token, reconnect=None):
    google.next_identity = identity
    google.next_refresh = refresh_token
    pending = flow_mod._PendingFlow(
        flow_id="f",
        provider_id="google",
        scopes=SCOPES,
        code_verifier="v",
        state="s",
        redirect_uri="http://127.0.0.1/callback",
        runner=None,
        future=asyncio.get_running_loop().create_future(),
        intent=flow_mod.SignInIntent(reconnect=reconnect),
    )
    return await flow_mod._exchange_code_for_tokens(pending, "code")


def _listed():
    return [(a.email, a.status, a.is_default) for a in accounts.list_accounts("google")]


async def _two_gmails(google):
    await _sign_in(google, ALICE, "rt-alice")
    await _sign_in(google, BOB, "rt-bob")


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------


async def test_a_second_gmail_is_added_beside_the_first(google):
    first = await _sign_in(google, ALICE, "rt-alice")
    second = await _sign_in(google, BOB, "rt-bob")

    assert first["added"] and second["added"]
    assert second["account_id"] == "bob-sub"
    assert _listed() == [
        ("alice@example.com", "ok", True),
        ("bob@example.com", "ok", False),
    ]


async def test_signing_in_again_as_the_same_account_updates_it_in_place(google):
    await _two_gmails(google)

    again = await _sign_in(google, ALICE, "rt-alice-2")

    assert again["added"] is False
    assert _listed() == [
        ("alice@example.com", "ok", True),
        ("bob@example.com", "ok", False),
    ]
    assert peek_connection("google")["refresh_token"] == "rt-alice-2"


async def test_reconnecting_as_another_account_adds_it_and_says_so(google):
    await _sign_in(google, ALICE, "rt-alice")

    result = await _sign_in(google, BOB, "rt-bob", reconnect="alice-sub")

    assert result["added"] is True
    assert result["requested_account_id"] == "alice-sub"
    alice = accounts.list_accounts("google")[0]
    assert peek_connection("google", slot=alice.slot)["refresh_token"] == "rt-alice"


async def test_a_sign_in_that_names_no_account_saves_nothing(google):
    with pytest.raises(ConnectorsError, match="did not say which account"):
        await _sign_in(google, {"email": "nobody@example.com"}, "rt-x")

    assert accounts.list_accounts("google") == []
    assert peek_connection("google") is None


async def test_a_new_account_never_overwrites_a_stored_one(google):
    await _sign_in(google, ALICE, "rt-alice")
    # Same address, different stable id: a recreated account is a new one.
    await _sign_in(google, {"sub": "alice-2", "email": ALICE["email"]}, "rt-new")

    alice = accounts.list_accounts("google")[0]
    assert peek_connection("google", slot=alice.slot)["refresh_token"] == "rt-alice"
    assert len(accounts.list_accounts("google")) == 2


# ---------------------------------------------------------------------------
# Tokens, one per account
# ---------------------------------------------------------------------------


async def test_each_account_gets_its_own_token(google):
    await _two_gmails(google)

    alice = await api.get_access_token(provider="google", scopes=[GMAIL])
    bob = await api.get_access_token(
        provider="google", scopes=[GMAIL], account="bob@example.com"
    )

    assert alice == "access-for-rt-alice"
    assert bob == "access-for-rt-bob"


async def test_an_unknown_account_is_refused_with_the_choices(google):
    await _two_gmails(google)

    with pytest.raises(UnknownAccountError) as exc:
        await api.get_access_token(
            provider="google", scopes=[GMAIL], account="carol@example.com"
        )
    assert exc.value.choices == ["alice@example.com", "bob@example.com"]


async def test_one_account_refreshing_does_not_block_another(google, monkeypatch):
    await _two_gmails(google)
    monkeypatch.setattr(_refresh_lock, "LOCK_TIMEOUT_SECONDS", 1.0)
    alice_slot, bob_slot = (a.slot for a in accounts.list_accounts("google"))

    holding, release = threading.Event(), threading.Event()

    def alice_refresh_in_progress():
        with _refresh_lock.held_account_sync("google", alice_slot):
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=alice_refresh_in_progress)
    holder.start()
    try:
        assert holding.wait(5)
        token = await asyncio.wait_for(get_or_refresh("google", slot=bob_slot), 5)
        assert token == "access-for-rt-bob"
        with pytest.raises(ConnectorsError, match="has been busy"):
            await get_or_refresh("google", slot=alice_slot)
    finally:
        release.set()
        holder.join()


async def test_an_expired_account_is_marked_and_cleared_by_signing_in(google):
    await _two_gmails(google)
    google.invalid.add("rt-alice")

    with pytest.raises(ConnectionRevokedError):
        await api.get_access_token(provider="google", scopes=[GMAIL])

    assert _listed() == [
        ("alice@example.com", "reauth_required", True),
        ("bob@example.com", "ok", False),
    ]
    # Bob keeps working while Alice waits to be reconnected.
    assert (
        await api.get_access_token(provider="google", scopes=[GMAIL], account="bob-sub")
        == "access-for-rt-bob"
    )

    google.invalid.clear()
    await _sign_in(google, ALICE, "rt-alice-2")
    assert _listed()[0] == ("alice@example.com", "ok", True)


# ---------------------------------------------------------------------------
# Removing accounts
# ---------------------------------------------------------------------------


async def test_disconnecting_one_account_leaves_the_other(google):
    await _two_gmails(google)

    result = await api.revoke_connection_async("google", "alice@example.com")

    assert google.revoked == ["rt-alice"]
    assert result["remaining_accounts"] == 1
    assert result["account_email"] == "alice@example.com"
    # Bob is the only account left, so he is the default now.
    assert _listed() == [("bob@example.com", "ok", True)]
    assert (
        await api.get_access_token(provider="google", scopes=[GMAIL])
        == "access-for-rt-bob"
    )


async def test_disconnecting_every_account(google):
    await _two_gmails(google)

    result = await api.revoke_connection_async("google", all_accounts=True)

    assert sorted(google.revoked) == ["rt-alice", "rt-bob"]
    assert result["remaining_accounts"] == 0
    assert [a["account_email"] for a in result["accounts"]] == [
        "alice@example.com",
        "bob@example.com",
    ]
    assert accounts.list_accounts("google") == []


async def test_a_new_sign_in_after_removing_the_first_account(google):
    await _two_gmails(google)
    await api.revoke_connection_async("google", "alice@example.com")

    await _sign_in(google, ALICE, "rt-alice-back")

    assert _listed() == [
        ("bob@example.com", "ok", True),
        ("alice@example.com", "ok", False),
    ]


async def test_choosing_the_default_account(google):
    await _two_gmails(google)

    api.set_default_account("google", "bob@example.com")

    assert [a.is_default for a in accounts.list_accounts("google")] == [False, True]
    assert (
        await api.get_access_token(provider="google", scopes=[GMAIL])
        == "access-for-rt-bob"
    )


# ---------------------------------------------------------------------------
# Old stores and interrupted writes
# ---------------------------------------------------------------------------


async def test_a_store_from_before_multi_account_works_unchanged(google):
    # One account in the ``default`` slot, no stable id, no account list.
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="rt-old",
        scopes=SCOPES,
        client_id_hash=google_provider_hash(),
    )
    assert not accounts.index_path().exists()

    assert _listed() == [("alice@example.com", "ok", True)]
    assert (
        await api.get_access_token(provider="google", scopes=[GMAIL])
        == "access-for-rt-old"
    )

    # Signing in as the same address claims that account rather than adding one.
    again = await _sign_in(google, ALICE, "rt-alice")
    assert again["added"] is False
    assert [a.account_id for a in accounts.list_accounts("google")] == ["alice-sub"]


async def test_an_old_account_learns_its_stable_id_on_refresh(google):
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="rt-old",
        scopes=SCOPES,
        client_id_hash=google_provider_hash(),
    )
    google.refresh_identity = ALICE

    await api.get_access_token(provider="google", scopes=[GMAIL])

    assert [a.account_id for a in accounts.list_accounts("google")] == ["alice-sub"]
    assert peek_connection("google")["account_id"] == "alice-sub"
    # A different Gmail signing in now lands beside it, not on top of it.
    await _sign_in(google, BOB, "rt-bob")
    assert peek_connection("google")["refresh_token"] == "rt-old"


def google_provider_hash():
    from gaia.connectors.providers import get as get_provider

    return get_provider("google").client_id_hash


async def test_a_crash_before_the_token_is_saved_can_be_finished(google, monkeypatch):
    await _sign_in(google, ALICE, "rt-alice")
    real_save = accounts.save_connection

    class PowerCut(Exception):
        pass

    def crash(**kwargs):
        raise PowerCut()

    monkeypatch.setattr(accounts, "save_connection", crash)
    with pytest.raises(PowerCut):
        await _sign_in(google, BOB, "rt-bob")
    monkeypatch.setattr(accounts, "save_connection", real_save)

    # Listed so the user can see and fix it, but never handed out as working.
    assert _listed()[1] == ("bob@example.com", "error", False)

    await _sign_in(google, BOB, "rt-bob")
    assert _listed()[1] == ("bob@example.com", "ok", False)


async def test_a_crashed_account_can_be_removed(google, monkeypatch):
    await _sign_in(google, ALICE, "rt-alice")
    monkeypatch.setattr(
        accounts, "save_connection", lambda **_kw: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(OSError):
        await _sign_in(google, BOB, "rt-bob")

    result = await api.revoke_connection_async("google", "bob@example.com")

    assert result["remaining_accounts"] == 1
    assert _listed() == [("alice@example.com", "ok", True)]


# ---------------------------------------------------------------------------
# Forwarded connections
# ---------------------------------------------------------------------------


def _forward(email, client_id="test.apps.example", refresh="fwd-refresh"):
    return api.import_forwarded_connection(
        provider="google",
        client_id=client_id,
        client_secret="",
        refresh_token=refresh,
        scopes=SCOPES,
        account_email=email,
    )


async def test_forwarding_one_account_keeps_the_others(google):
    await _two_gmails(google)

    _forward("alice@example.com")

    alice, bob = accounts.list_accounts("google")
    assert peek_connection("google", slot=alice.slot)["refresh_token"] == (
        "fwd-refresh"
    )
    assert peek_connection("google", slot=bob.slot)["refresh_token"] == "rt-bob"


async def test_forwarding_over_a_single_account_replaces_it_as_before(google):
    await _sign_in(google, ALICE, "rt-alice")

    _forward("carol@example.com", client_id="other.apps.example")

    assert _listed() == [("carol@example.com", "ok", True)]
    assert peek_connection("google")["refresh_token"] == "fwd-refresh"


async def test_forwarding_under_a_new_client_is_refused_while_others_remain(google):
    await _two_gmails(google)

    with pytest.raises(ConnectorsError):
        _forward("alice@example.com", client_id="other.apps.example")

    assert [
        peek_connection("google", slot=a.slot)["refresh_token"]
        for a in accounts.list_accounts("google")
    ] == ["rt-alice", "rt-bob"]


async def test_forwarding_an_unknown_address_is_refused_while_others_remain(google):
    await _two_gmails(google)

    with pytest.raises(ConnectorsError):
        _forward("carol@example.com")

    assert len(accounts.list_accounts("google")) == 2


# ---------------------------------------------------------------------------
# The account picker
# ---------------------------------------------------------------------------


async def _authorize_query(provider_id, **intent):
    info = await flow_mod.start_authorization(provider_id, scopes=SCOPES, **intent)
    await flow_mod.cancel_flow(info["flow_id"])
    return parse_qs(info["authorization_url"].split("?", 1)[1])


async def test_adding_a_google_account_shows_the_account_picker(google):
    await _sign_in(google, ALICE, "rt-alice")

    query = await _authorize_query("google", add_account=True)

    assert query["prompt"] == ["select_account consent"]
    assert "login_hint" not in query


async def test_reconnecting_a_google_account_names_it(google):
    await _two_gmails(google)

    query = await _authorize_query("google", account="bob-sub")

    assert query["login_hint"] == ["bob@example.com"]
    # Google still needs consent to hand back a refresh token.
    assert query["prompt"] == ["consent"]


async def test_adding_and_reconnecting_at_once_is_refused(google):
    with pytest.raises(ConnectorsError):
        await flow_mod.start_authorization(
            "google", scopes=SCOPES, add_account=True, account="alice@example.com"
        )


@pytest.mark.parametrize(
    "account_type, domain_hint",
    [("personal", "consumers"), ("work", "organizations"), (None, None)],
)
def test_microsoft_prompts(account_type, domain_hint):
    from gaia.connectors.providers.microsoft import MicrosoftOAuthProvider

    provider = MicrosoftOAuthProvider(client_id="test-client")
    adding = provider.account_prompt_params(
        select_account=True, login_hint=None, account_type=None
    )
    assert adding == {"prompt": "select_account"}
    reconnect = provider.account_prompt_params(
        select_account=False, login_hint="u@outlook.com", account_type=account_type
    )
    assert reconnect.get("login_hint") == "u@outlook.com"
    assert reconnect.get("domain_hint") == domain_hint
    assert "prompt" not in reconnect
