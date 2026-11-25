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
CALENDAR = "https://www.googleapis.com/auth/calendar"
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
        self.granted = SCOPES
        self.rotate_to = None

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
            if self.rotate_to:
                reply["refresh_token"] = self.rotate_to
            return httpx.Response(200, json=reply)
        return httpx.Response(
            200,
            json={
                "access_token": "signin-access",
                "refresh_token": self.next_refresh,
                "expires_in": 3600,
                "scope": " ".join(self.granted),
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


async def _sign_in(google, identity, refresh_token, reconnect=None, add=False):
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
        intent=flow_mod.SignInIntent(add_account=add, reconnect=reconnect),
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


# ---------------------------------------------------------------------------
# Ambiguous sign-ins: never merge two accounts, never leave one behind
# ---------------------------------------------------------------------------


def test_a_personal_microsoft_account_never_replaces_a_work_one_at_one_address():
    # An old build stored the work account without its stable id.
    save_connection(
        provider="microsoft",
        account_email="pat@contoso.com",
        refresh_token="rt-work",
        scopes=["Mail.Read"],
        client_id_hash="h",
        account_type="work",
    )

    _, added = accounts.save_signed_in_account(
        "microsoft",
        account_id="personal-cid",
        email="pat@contoso.com",
        refresh_token="rt-personal",
        scopes=["Mail.Read"],
        client_id_hash="h",
        account_type="personal",
    )

    assert added is True
    assert peek_connection("microsoft")["refresh_token"] == "rt-work"
    assert peek_connection("microsoft")["account_type"] == "work"
    assert sorted(a.account_type for a in accounts.list_accounts("microsoft")) == [
        "personal",
        "work",
    ]


def _old_store_without_an_address(refresh_token="rt-old"):
    # Old builds wrote "default" when they could not look the address up.
    save_connection(
        provider="google",
        account_email="default",
        refresh_token=refresh_token,
        scopes=SCOPES,
        client_id_hash=google_provider_hash(),
        connected_at=1000.0,
    )


async def test_reconnecting_an_account_stored_without_an_address_updates_it(google):
    _old_store_without_an_address()

    result = await _sign_in(google, ALICE, "rt-new")

    assert result["added"] is False
    assert _listed() == [("alice@example.com", "ok", True)]
    assert peek_connection("google")["refresh_token"] == "rt-new"
    assert peek_connection("google")["account_id"] == "alice-sub"


async def test_adding_beside_an_account_stored_without_an_address_adds_one(google):
    _old_store_without_an_address()

    result = await _sign_in(google, BOB, "rt-bob", add=True)

    assert result["added"] is True
    assert peek_connection("google")["refresh_token"] == "rt-old"
    assert len(accounts.list_accounts("google")) == 2


async def _merge_old_slot_with_a_later_sign_in(google, granted, before_refresh=None):
    _old_store_without_an_address()
    # The same Gmail was later added beside it, under its stable id.
    google.granted = granted
    await _sign_in(google, ALICE, "rt-new", add=True)
    assert len(accounts.list_accounts("google")) == 2
    if before_refresh:
        before_refresh()

    google.refresh_identity = ALICE
    token = await api.get_access_token(provider="google", scopes=[GMAIL])

    assert token == "access-for-rt-old"
    listed = accounts.list_accounts("google")
    assert [(a.account_id, a.email, a.is_default) for a in listed] == [
        ("alice-sub", "alice@example.com", True)
    ]
    assert accounts.resolve_slot("google", "alice-sub") == listed[0].slot
    return peek_connection("google", slot=listed[0].slot)


async def test_an_old_slot_learning_an_id_already_stored_merges_into_one(google):
    kept = await _merge_old_slot_with_a_later_sign_in(google, SCOPES)

    # Same grants: the token the refresh just proved is the one kept.
    assert kept["refresh_token"] == "rt-old"


async def test_a_merge_keeps_the_later_sign_in_when_it_granted_more(google):
    kept = await _merge_old_slot_with_a_later_sign_in(google, SCOPES + [CALENDAR])

    # The user added calendar access by signing in again; it is not lost.
    assert kept["refresh_token"] == "rt-new"
    assert CALENDAR in kept["scopes"]
    token = await api.get_access_token(provider="google", scopes=[CALENDAR])
    assert token == "access-for-rt-new"


async def test_a_merge_keeps_the_proven_sign_in_when_grants_only_overlap(google):
    kept = await _merge_old_slot_with_a_later_sign_in(
        google, ["openid", "https://www.googleapis.com/auth/userinfo.email", CALENDAR]
    )

    # Neither copy covers the other, so the token just proven to work stays.
    assert kept["refresh_token"] == "rt-old"


async def test_a_merge_never_keeps_a_sign_in_from_another_oauth_client(google):
    def move_later_sign_in_to_another_client():
        twin = accounts.resolve_slot("google", "alice-sub")
        blob = peek_connection("google", slot=twin)
        save_connection(
            provider="google",
            slot=twin,
            account_id="alice-sub",
            account_email=blob["account_email"],
            refresh_token=blob["refresh_token"],
            scopes=blob["scopes"],
            client_id_hash="another-client",
        )

    kept = await _merge_old_slot_with_a_later_sign_in(
        google, SCOPES + [CALENDAR], before_refresh=move_later_sign_in_to_another_client
    )

    assert kept["refresh_token"] == "rt-old"


async def test_an_old_slot_learns_its_address_with_its_id(google):
    _old_store_without_an_address()
    google.refresh_identity = ALICE

    await api.get_access_token(provider="google", scopes=[GMAIL])

    assert _listed() == [("alice@example.com", "ok", True)]
    assert peek_connection("google")["account_email"] == "alice@example.com"


async def test_uninstall_clears_the_first_account_even_with_a_torn_account_list(
    google,
):
    await _two_gmails(google)
    accounts.index_path().write_text("{not json", encoding="utf-8")

    with pytest.raises(ConnectorsError, match="could not be read"):
        accounts.remove_all_accounts("google")

    assert peek_connection("google") is None


async def test_a_token_that_cannot_read_its_profile_asks_only_once(monkeypatch, caplog):
    from gaia.connectors import tokens
    from gaia.connectors.providers import _registry
    from gaia.connectors.providers import get as get_provider

    monkeypatch.setenv(
        "GAIA_MICROSOFT_CLIENT_ID", "11112222-bbbb-3333-cccc-4444dddd5555"
    )
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    _registry.clear()
    provider = get_provider("microsoft")
    # A forwarded token without User.Read: refreshes work, /me does not.
    save_connection(
        provider="microsoft",
        account_email="pat@outlook.com",
        refresh_token="rt-fwd",
        scopes=["Mail.Read", "offline_access"],
        client_id_hash=provider.client_id_hash,
        tenant=getattr(provider, "tenant", None),
    )
    with respx.mock(assert_all_called=False) as router:
        router.post(provider.token_url).mock(
            return_value=httpx.Response(
                200, json={"access_token": "at", "expires_in": 3600}
            )
        )
        me = router.get(url__startswith="https://graph.microsoft.com/v1.0/me").mock(
            return_value=httpx.Response(403, json={"error": {"code": "Forbidden"}})
        )
        with caplog.at_level("WARNING", logger="gaia.connectors.tokens"):
            for _ in range(3):
                tokens._cache.clear()
                assert await get_or_refresh("microsoft") == "at"

    assert me.call_count == 1
    assert len([r for r in caplog.records if "account id" in r.getMessage()]) == 1


async def _refresh_default():
    from gaia.connectors import tokens

    tokens._cache.clear()
    return await api.get_access_token(provider="google", scopes=[GMAIL])


async def test_a_merge_waits_while_a_sign_in_holds_the_provider(google):
    _old_store_without_an_address()
    await _sign_in(google, ALICE, "rt-new", add=True)
    google.refresh_identity = ALICE

    with _refresh_lock.held_sync("google"):
        assert await _refresh_default() == "access-for-rt-old"
    # Nothing merged under a sign-in's feet, and nothing was lost.
    assert len(accounts.list_accounts("google")) == 2
    assert peek_connection("google").get("account_id") is None

    await _refresh_default()
    assert [a.account_id for a in accounts.list_accounts("google")] == ["alice-sub"]


async def test_one_id_is_never_recorded_in_two_slots(google, monkeypatch):
    _old_store_without_an_address()
    await _sign_in(google, ALICE, "rt-new", add=True)
    google.refresh_identity = ALICE
    # Another process recorded the id in the second slot after we looked.
    monkeypatch.setattr(accounts, "slot_holding", lambda *a, **k: None)

    await _refresh_default()

    ids = [a.account_id for a in accounts.list_accounts("google")]
    assert ids.count("alice-sub") == 1
    assert peek_connection("google").get("account_id") is None

    monkeypatch.undo()
    await _refresh_default()
    assert [a.account_id for a in accounts.list_accounts("google")] == ["alice-sub"]


async def test_a_crash_while_recording_an_id_is_finished_by_the_next_refresh(
    google, monkeypatch
):
    _old_store_without_an_address()
    await _sign_in(google, BOB, "rt-bob", add=True)
    google.refresh_identity = ALICE
    real_record = accounts.record_identity

    class PowerCut(Exception):
        pass

    def crash(*a, **k):
        raise PowerCut()

    monkeypatch.setattr(accounts, "record_identity", crash)
    with pytest.raises(PowerCut):
        await _refresh_default()
    monkeypatch.setattr(accounts, "record_identity", real_record)

    # The same Gmail is added beside it, then the old slot refreshes again.
    await _sign_in(google, ALICE, "rt-new", add=True)
    await _refresh_default()

    ids = sorted(a.account_id for a in accounts.list_accounts("google"))
    assert ids == ["alice-sub", "bob-sub"]


@pytest.mark.parametrize("failure", [OSError("disk full"), ConnectorsError("busy")])
async def test_a_refresh_saves_its_new_token_when_the_account_list_fails(
    google, monkeypatch, caplog, failure
):
    _old_store_without_an_address()
    google.refresh_identity = ALICE
    google.rotate_to = "rt-rotated"

    def fail(*a, **k):
        raise failure

    monkeypatch.setattr(accounts, "record_identity", fail)
    with caplog.at_level("WARNING", logger="gaia.connectors.tokens"):
        assert await _refresh_default() == "access-for-rt-old"

    assert peek_connection("google")["refresh_token"] == "rt-rotated"
    assert peek_connection("google").get("account_id") is None
    assert any("account id" in r.getMessage() for r in caplog.records)

    monkeypatch.undo()
    await _refresh_default()
    assert peek_connection("google")["account_id"] == "alice-sub"


def test_a_sign_in_whose_account_vanished_says_so(monkeypatch):
    monkeypatch.setattr(accounts, "list_accounts", lambda _cid: [])

    with pytest.raises(ConnectorsError, match="account list"):
        accounts.save_signed_in_account(
            "google",
            account_id="alice-sub",
            email="alice@example.com",
            refresh_token="rt",
            scopes=SCOPES,
            client_id_hash="h",
        )


async def test_a_network_error_reading_the_profile_is_retried(monkeypatch):
    from gaia.connectors import tokens
    from gaia.connectors.providers import _registry
    from gaia.connectors.providers import get as get_provider

    monkeypatch.setenv(
        "GAIA_MICROSOFT_CLIENT_ID", "11112222-bbbb-3333-cccc-4444dddd5555"
    )
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    _registry.clear()
    provider = get_provider("microsoft")
    save_connection(
        provider="microsoft",
        account_email="pat@outlook.com",
        refresh_token="rt",
        scopes=["Mail.Read", "User.Read", "offline_access"],
        client_id_hash=provider.client_id_hash,
        tenant=getattr(provider, "tenant", None),
    )
    with respx.mock(assert_all_called=False) as router:
        router.post(provider.token_url).mock(
            return_value=httpx.Response(
                200, json={"access_token": "at", "expires_in": 3600}
            )
        )
        me = router.get(url__startswith="https://graph.microsoft.com/v1.0/me").mock(
            side_effect=[
                httpx.ConnectTimeout("slow"),
                httpx.Response(200, json={"id": "pat-id", "mail": "pat@outlook.com"}),
            ]
        )
        for _ in range(2):
            tokens._cache.clear()
            assert await get_or_refresh("microsoft") == "at"

    assert me.call_count == 2
    assert [a.account_id for a in accounts.list_accounts("microsoft")] == ["pat-id"]


def _old_outlook_account():
    from gaia.connectors.providers import _registry
    from gaia.connectors.providers import get as get_provider

    _registry.clear()
    provider = get_provider("microsoft")
    save_connection(
        provider="microsoft",
        account_email="pat@outlook.com",
        refresh_token="rt-old",
        scopes=["Mail.Read", "User.Read", "offline_access"],
        client_id_hash=provider.client_id_hash,
        tenant=getattr(provider, "tenant", None),
    )
    return provider


@pytest.fixture
def outlook_env(monkeypatch):
    monkeypatch.setenv(
        "GAIA_MICROSOFT_CLIENT_ID", "11112222-bbbb-3333-cccc-4444dddd5555"
    )
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)


_profile_lookups = []


async def _hang(request):
    _profile_lookups.append(request.url)
    await asyncio.sleep(60)


@pytest.mark.parametrize("budget", [5.5, 3.5])
async def test_a_slow_profile_lookup_never_fails_a_refresh(
    outlook_env, monkeypatch, budget
):
    from gaia.connectors import tokens

    provider = _old_outlook_account()
    # 5.5 s leaves the lookup about 2.5 s, room for a slow runner; 3.5 s leaves too little to try.
    monkeypatch.setattr(tokens, "_REFRESH_BUDGET_SECONDS", budget, raising=False)
    _profile_lookups.clear()
    with respx.mock(assert_all_called=False) as router:
        router.post(provider.token_url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "at",
                    "expires_in": 3600,
                    "refresh_token": "rt-2",
                },
            )
        )
        router.get(url__startswith="https://graph.microsoft.com/v1.0/me").mock(
            side_effect=_hang
        )
        token = await asyncio.wait_for(get_or_refresh("microsoft"), 5.0)

    assert token == "at"
    assert peek_connection("microsoft")["refresh_token"] == "rt-2"
    assert len(_profile_lookups) == (1 if budget == 5.5 else 0)


async def test_the_sign_in_profile_lookup_has_an_overall_deadline(monkeypatch):
    monkeypatch.setattr(flow_mod, "_USERINFO_DEADLINE_SECONDS", 0.2, raising=False)
    with respx.mock(assert_all_called=False) as router:
        router.get("https://graph.microsoft.com/v1.0/me").mock(side_effect=_hang)
        with pytest.raises(ConnectorsError, match="did not answer"):
            await asyncio.wait_for(
                flow_mod._fetch_userinfo(
                    "microsoft", "https://graph.microsoft.com/v1.0/me", "at"
                ),
                2.0,
            )
