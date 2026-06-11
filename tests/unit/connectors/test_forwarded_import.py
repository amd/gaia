# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for the forwarded-connection ingestion path (#1292).

A host app that already authenticated a user FORWARDS the OAuth client
(``client_id`` + ``client_secret``) plus the ``refresh_token`` to GAIA.
``import_forwarded_connection`` persists this — no browser step, no PKCE
flow — so the connectors refresh engine can act on the mailbox AS THE
HOST APP'S CLIENT.

These tests assert, against the in-memory keyring + a stubbed token
endpoint (never real Google):
- the correct keyring slots are written (provider client + connection),
- ``client_id_hash`` is computed from the FORWARDED client (not env),
- the provider cache AND token cache are evicted,
- a scope shortfall fails loudly,
- an insecure keyring backend fails loudly,
- the return value omits the refresh token / client secret,
- ``grant_agents`` writes per-agent grants,
- a subsequent refresh uses the forwarded client with no interactive step.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from gaia.connectors.api import import_forwarded_connection
from gaia.connectors.errors import ConnectorsError, ScopeMismatchError
from gaia.connectors.store import (
    peek_connection,
    peek_provider_credentials,
)

# Forwarded grant fixture values. The agent's required union (#962) is
# gmail.modify + gmail.send + calendar.events; this grant covers all three.
FWD_CLIENT_ID = "forwarded-host-app.apps.googleusercontent.com"
FWD_CLIENT_SECRET = "FWD-SECRET-do-not-leak"
FWD_REFRESH = "FWD-REFRESH-TOKEN-do-not-leak"
FULL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]


@pytest.fixture(autouse=True)
def _isolate_grants(monkeypatch, tmp_path):
    # conftest already redirects grants Path.home, but be explicit so this
    # file can run standalone.
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)


@pytest.fixture(autouse=True)
def _clear_provider_registry(monkeypatch):
    """Start each test with an empty provider registry + a known env client
    so we can prove the forwarded client (not env) wins."""
    from gaia.connectors.providers import _registry

    _registry.clear()
    # An env client whose id differs from the forwarded one; if the import
    # path accidentally used the env client, client_id_hash would mismatch.
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "ENV-CLIENT.apps.googleusercontent.com")
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_SECRET", "ENV-SECRET")
    yield
    _registry.clear()


def _do_import(**overrides):
    kwargs = dict(
        provider="google",
        client_id=FWD_CLIENT_ID,
        client_secret=FWD_CLIENT_SECRET,
        refresh_token=FWD_REFRESH,
        scopes=FULL_SCOPES,
        account_email="alice@example.com",
    )
    kwargs.update(overrides)
    return import_forwarded_connection(**kwargs)


class TestPersistence:
    def test_writes_provider_client_slot(self):
        _do_import()
        creds = peek_provider_credentials("google")
        assert creds is not None
        assert creds["client_id"] == FWD_CLIENT_ID
        assert creds["client_secret"] == FWD_CLIENT_SECRET

    def test_writes_connection_slot(self):
        _do_import()
        blob = peek_connection("google")
        assert blob is not None
        assert blob["refresh_token"] == FWD_REFRESH
        assert blob["account_email"] == "alice@example.com"
        assert set(blob["scopes"]) == set(FULL_SCOPES)

    def test_client_id_hash_from_forwarded_client(self):
        import zlib

        _do_import()
        blob = peek_connection("google")
        expected = format(zlib.crc32(FWD_CLIENT_ID.encode()), "08x")
        assert blob["client_id_hash"] == expected
        # And NOT the env client's hash.
        env_hash = format(zlib.crc32(b"ENV-CLIENT.apps.googleusercontent.com"), "08x")
        assert blob["client_id_hash"] != env_hash

    def test_no_account_email_defaults_to_default(self):
        _do_import(account_email="")
        blob = peek_connection("google")
        assert blob["account_email"] == "default"


class TestCacheEviction:
    def test_evicts_provider_cache(self):
        # Pre-seed the registry with a stale provider (env client).
        from gaia.connectors.providers import _registry
        from gaia.connectors.providers import get as get_provider

        stale = get_provider("google")
        assert stale.client_id == "ENV-CLIENT.apps.googleusercontent.com"
        assert "google" in _registry

        _do_import()

        fresh = get_provider("google")
        assert fresh.client_id == FWD_CLIENT_ID

    def test_evicts_token_cache(self):
        from gaia.connectors import tokens

        tokens._cache[("google", "alice@example.com")] = tokens._AccessTokenCache(
            access_token="STALE", expires_at=10**12
        )
        _do_import()
        assert ("google", "alice@example.com") not in tokens._cache


class TestLoudFailures:
    def test_scope_shortfall_raises_loudly(self):
        # Missing gmail.send.
        with pytest.raises(ScopeMismatchError) as ei:
            _do_import(
                scopes=[
                    "https://www.googleapis.com/auth/gmail.modify",
                    "https://www.googleapis.com/auth/calendar.events",
                ]
            )
        assert "gmail.send" in str(ei.value)
        # Nothing persisted on the failure path.
        assert peek_connection("google") is None
        assert peek_provider_credentials("google") is None

    def test_insecure_keyring_backend_raises_loudly(self, monkeypatch):
        class _PlaintextKeyring:
            pass

        _PlaintextKeyring.__name__ = "PlaintextKeyring"

        import keyring

        monkeypatch.setattr(keyring, "get_keyring", lambda: _PlaintextKeyring())
        with pytest.raises(ConnectorsError) as ei:
            _do_import()
        assert "plaintext" in str(ei.value).lower()

    def test_missing_client_id_raises_loudly(self):
        with pytest.raises(ConnectorsError):
            _do_import(client_id="")

    def test_missing_refresh_token_raises_loudly(self):
        with pytest.raises(ConnectorsError):
            _do_import(refresh_token="")


class TestSecretHygiene:
    def test_return_value_omits_secrets(self):
        result = _do_import()
        as_str = str(result)
        assert FWD_REFRESH not in as_str
        assert FWD_CLIENT_SECRET not in as_str
        assert "refresh_token" not in result
        assert "client_secret" not in result
        # Metadata IS present.
        assert result["provider"] == "google"
        assert result["account_email"] == "alice@example.com"


class TestGrants:
    def test_grant_agents_written(self):
        from gaia.connectors.grants import check_agent_grant

        _do_import(grant_agents=["installed:email"])
        assert check_agent_grant("google", "installed:email", FULL_SCOPES)

    def test_no_grant_agents_writes_nothing(self):
        from gaia.connectors.grants import list_agent_grants

        _do_import()
        assert list_agent_grants("google") == {}


class TestProviderAwareScopeDefaults:
    """Verify the fix for the provider-agnostic _DEFAULT_REQUIRED_SCOPES bug.

    Before the fix, forwarding any provider with required_scopes=None fell
    through to the Google-only default, so Microsoft (and every other non-Google
    provider) was demanded Gmail scopes.  After the fix:
      - Unknown providers default to an empty requirement (no import-time check).
      - An explicit required_scopes=[] is honoured (not overridden by the default).
      - Google's existing default is preserved for backward compat.
    """

    MS_SCOPES = [
        "openid",
        "offline_access",
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/Mail.Send",
        "https://graph.microsoft.com/Calendars.ReadWrite",
    ]

    def test_non_google_provider_none_path_does_not_demand_google_scopes(
        self, monkeypatch
    ):
        """Microsoft forward with required_scopes=None must NOT raise — there
        is no Microsoft entry in _DEFAULT_REQUIRED_SCOPES_BY_PROVIDER so the
        default is an empty list.  The forwarded Graph scopes need not include
        any gmail.* URL.

        We patch ``gaia.connectors.api.get_provider`` rather than injecting
        into ``_registry`` because ``import_forwarded_connection`` pops the
        registry entry (step 5) before re-calling ``get_provider`` to compute
        the fresh ``client_id_hash``.  Patching the function avoids the pop.
        """
        import zlib
        from unittest.mock import MagicMock

        fake_ms = MagicMock()
        fake_ms.client_id_hash = format(zlib.crc32(b"ms-client-id"), "08x")
        monkeypatch.setattr("gaia.connectors.api.get_provider", lambda _: fake_ms)

        result = import_forwarded_connection(
            provider="microsoft",
            client_id="ms-client-id",
            client_secret="ms-secret",
            refresh_token="ms-refresh",
            scopes=self.MS_SCOPES,
            account_email="user@outlook.com",
            # required_scopes intentionally omitted → defaults to [] for microsoft
        )
        assert result["provider"] == "microsoft"
        assert result["account_email"] == "user@outlook.com"

    def test_explicit_empty_required_scopes_is_honoured(self):
        """required_scopes=[] must mean "require nothing", not "use the
        Google default".  Before the fix, [] was falsy so the Google default
        fired and a Google forward with only openid would fail."""
        # Even a minimal Google scope list is accepted when required_scopes=[].
        _do_import(scopes=["openid"], required_scopes=[])

    def test_explicit_nonempty_required_scopes_are_still_enforced(self):
        """Passing an explicit required list tighter than the forwarded
        scopes must still fail loudly."""
        with pytest.raises(ScopeMismatchError):
            _do_import(
                scopes=["openid"],
                required_scopes=["https://www.googleapis.com/auth/gmail.modify"],
            )

    def test_google_default_still_enforced_on_none_path(self):
        """Regression: the Google None-path default must still gate a
        shortfall.  This is the existing test_scope_shortfall_raises_loudly
        re-asserted to prove the map entry is wired up."""
        with pytest.raises(ScopeMismatchError) as ei:
            _do_import(scopes=["https://www.googleapis.com/auth/gmail.modify"])
        assert "gmail.send" in str(ei.value)


class TestRefreshUsesForwardedClient:
    @respx.mock
    async def test_refresh_posts_forwarded_client(self):
        """After a forwarded import, get_access_token refreshes using the
        forwarded client_id+secret — proving GAIA acts as the host app with
        no interactive OAuth step."""
        captured = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode()
            return httpx.Response(
                200, json={"access_token": "STUB-ACCESS", "expires_in": 3600}
            )

        respx.post("https://oauth2.googleapis.com/token").mock(side_effect=_capture)

        # v1 store is single-slot per provider: the keyring key is always
        # DEFAULT_ACCOUNT regardless of the display account_email. Import
        # without a display email so the read-side default key matches.
        _do_import(grant_agents=["installed:email"], account_email="")

        from gaia.connectors.api import get_access_token

        token = await get_access_token(
            provider="google",
            scopes=FULL_SCOPES,
            agent_id="installed:email",
        )
        assert token == "STUB-ACCESS"
        assert FWD_CLIENT_ID in captured["body"]
        assert FWD_CLIENT_SECRET in captured["body"]
        assert FWD_REFRESH in captured["body"]
        # ENV client must NOT appear — forwarded client beats env.
        assert "ENV-CLIENT" not in captured["body"]
