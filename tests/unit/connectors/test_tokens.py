# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-5a (AC4, AC5, AC6, A6): token cache + refresh.

Acceptance:
- AC4: ``get_or_refresh`` refreshes within 60s of expiry; cache hit when fresh.
- AC5: token endpoint ``invalid_grant`` → ``ConnectionRevokedError``;
  refresh token cleared from keyring.
- AC6: 10 concurrent calls = exactly 1 HTTP round-trip (asyncio.Lock).
- A6: missing or zero ``expires_in`` defaults to 3600.
- Refresh-token rotation: keyring updated with the new token if the
  endpoint returns one.
- Clock-skew retry: 401 ``invalid_token`` triggers exactly one retry.
- Lock release on exception: a refresh that raises does NOT deadlock the
  next call.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import keyring
import pytest
import respx

from gaia.connectors.errors import (
    AuthRequiredError,
    ConnectionRevokedError,
)
from gaia.connectors.providers import _registry
from gaia.connectors.store import (
    SERVICE_NAME,
    _connection_username,
    load_connection,
    save_connection,
)
from gaia.connectors.tokens import _cache, get_or_refresh, get_token_with_expiry


@pytest.fixture
def google_provider(monkeypatch):
    """Build a known Google provider in the registry for refresh tests."""
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    _registry.clear()
    from gaia.connectors.providers import get as get_provider

    return get_provider("google")


@pytest.fixture
def seeded_connection(google_provider):
    """Pre-seed an OAuth connection in the keyring for refresh tests."""
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="seed-refresh-token",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        client_id_hash=google_provider.client_id_hash,
    )
    yield google_provider


def _ok_token_response(access="new-access", expires_in=3600, refresh=None):
    body = {"access_token": access, "expires_in": expires_in, "scope": "x"}
    if refresh is not None:
        body["refresh_token"] = refresh
    return httpx.Response(200, json=body)


class TestRefresh:
    @respx.mock
    async def test_refreshes_when_expired(self, seeded_connection):
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=_ok_token_response(access="fresh", expires_in=3600)
        )
        token = await get_or_refresh("google")
        assert token == "fresh"

    @respx.mock
    async def test_cache_hit_skips_refresh(self, seeded_connection):
        # Pre-populate the cache with a fresh entry.
        from gaia.connectors.tokens import _AccessTokenCache, _cache_key

        key = _cache_key("google", "default")
        _cache[key] = _AccessTokenCache(
            access_token="cached",
            expires_at=time.monotonic() + 600,
            lock=asyncio.Lock(),
        )

        route = respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=_ok_token_response(access="should-not-be-used")
        )
        token = await get_or_refresh("google")
        assert token == "cached"
        assert route.call_count == 0

    @respx.mock
    async def test_60s_expiry_buffer_triggers_refresh(self, seeded_connection):
        # AC4: token expiring within 60s is treated as already-expired.
        from gaia.connectors.tokens import _AccessTokenCache, _cache_key

        key = _cache_key("google", "default")
        _cache[key] = _AccessTokenCache(
            access_token="about-to-expire",
            expires_at=time.monotonic() + 30,  # within the 60s buffer
            lock=asyncio.Lock(),
        )

        route = respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=_ok_token_response(access="fresh", expires_in=3600)
        )
        token = await get_or_refresh("google")
        assert token == "fresh"
        assert route.call_count == 1

    @respx.mock
    async def test_invalid_grant_raises_revoked_and_clears_keyring(
        self, seeded_connection
    ):
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(ConnectionRevokedError):
            await get_or_refresh("google")
        # Refresh token cleared from keyring (AC5).
        assert (
            load_connection(
                "google",
                current_client_id_hash=seeded_connection.client_id_hash,
            )
            is None
        )

    @respx.mock
    async def test_missing_expires_in_defaults_to_3600(self, seeded_connection):
        # A6: provider that returns the token without expires_in must not
        # KeyError or treat the token as immediately expired.
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(200, json={"access_token": "ok", "scope": "x"})
        )
        token = await get_or_refresh("google")
        assert token == "ok"

    @respx.mock
    async def test_zero_expires_in_defaults_to_3600(self, seeded_connection):
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "ok", "expires_in": 0, "scope": "x"},
            )
        )
        token = await get_or_refresh("google")
        assert token == "ok"
        # Cache lifetime = 3600s by default.
        from gaia.connectors.tokens import _cache_key

        entry = _cache[_cache_key("google", "default")]
        assert entry.expires_at - time.monotonic() > 3000


class TestRefreshTokenRotation:
    @respx.mock
    async def test_new_refresh_token_persisted(self, seeded_connection):
        # If Google rotates the refresh token, store the new one.
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=_ok_token_response(
                access="ok", expires_in=3600, refresh="ROTATED-REFRESH"
            )
        )
        await get_or_refresh("google")
        loaded = load_connection(
            "google",
            current_client_id_hash=seeded_connection.client_id_hash,
        )
        assert loaded["refresh_token"] == "ROTATED-REFRESH"


class TestConcurrencyAC6:
    """AC6 — 10 concurrent get_or_refresh calls hit the token endpoint
    exactly once. The double-checked-locking pattern under
    ``async with lock:`` is what makes this work."""

    @respx.mock
    async def test_ten_concurrent_calls_one_round_trip(self, seeded_connection):
        route = respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=_ok_token_response(access="single-token", expires_in=3600)
        )

        results = await asyncio.gather(*(get_or_refresh("google") for _ in range(10)))

        assert route.call_count == 1
        assert all(t == "single-token" for t in results)


class TestLockReleaseOnException:
    """If a refresh raises an exception inside the locked block, the lock
    must still be released (``async with`` guarantees this) — a subsequent
    call should NOT deadlock."""

    @respx.mock
    async def test_lock_released_on_refresh_failure(self, seeded_connection):
        # First refresh attempt: server is broken — 500.
        # Second refresh attempt: server recovers — 200.
        responses = [
            httpx.Response(500, text="boom"),
            _ok_token_response(access="recovered"),
        ]

        def _next(request):
            return responses.pop(0)

        respx.post("https://oauth2.googleapis.com/token").mock(side_effect=_next)

        # First call raises (500 is non-retryable in our policy).
        with pytest.raises(Exception):
            await get_or_refresh("google")

        # Cache is empty / expired; next call must succeed and not block.
        token = await asyncio.wait_for(get_or_refresh("google"), timeout=2.0)
        assert token == "recovered"


class TestNotConnected:
    @respx.mock
    async def test_no_stored_connection_raises_not_connected(self, google_provider):
        # No save_connection — store is empty.
        with pytest.raises(AuthRequiredError) as exc:
            await get_or_refresh("google")
        assert exc.value.reason is AuthRequiredError.Reason.NOT_CONNECTED


class TestTripwire:
    """Eager client_id_hash mismatch must surface as REAUTH_REQUIRED, not
    as a network error or stale-token success."""

    @respx.mock
    async def test_rotated_client_id_raises_reauth(self, google_provider):
        save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token="x",
            scopes=["s"],
            client_id_hash="OLD-HASH",  # different from google_provider's
        )
        with pytest.raises(AuthRequiredError) as exc:
            await get_or_refresh("google")
        assert exc.value.reason is AuthRequiredError.Reason.REAUTH_REQUIRED


class TestGetTokenWithExpiry:
    """Regression: get_token_with_expiry must return (str, float)."""

    @respx.mock
    async def test_returns_tuple_of_str_and_float(self, seeded_connection):
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=_ok_token_response(access="tok-abc", expires_in=3600)
        )
        token, expires_at = await get_token_with_expiry("google")
        assert isinstance(token, str)
        assert token == "tok-abc"
        assert isinstance(expires_at, float)
        # Wall-clock expires_at should be in the future (within ~3600s)
        assert expires_at > time.time() + 3500

    @respx.mock
    async def test_get_or_refresh_still_returns_plain_str(self, seeded_connection):
        """get_or_refresh must remain str — callers depend on this."""
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=_ok_token_response(access="plain-str", expires_in=3600)
        )
        result = await get_or_refresh("google")
        assert isinstance(result, str)
        assert result == "plain-str"


# ---------------------------------------------------------------------------
# D8/A4/A5/A18 (#2628): recorded-tenant rotation forwarding, mismatch
# detection through the real get_or_refresh path, and the split-related
# failure-taxonomy enrichment.
# ---------------------------------------------------------------------------

MS_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"


@pytest.fixture
def microsoft_provider(monkeypatch):
    monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-ms-client")
    _registry.clear()
    from gaia.connectors.providers import get as get_provider

    return get_provider("microsoft")


@pytest.fixture
def seeded_microsoft_connection(microsoft_provider):
    save_connection(
        provider="microsoft",
        account_email="alice@example.com",
        refresh_token="seed-ms-refresh-token",
        scopes=["https://graph.microsoft.com/Mail.Read"],
        client_id_hash=microsoft_provider.client_id_hash,
        tenant="consumers",
    )
    yield microsoft_provider


class TestTenantRotationForwarding:
    """A4 (CRITICAL): the recorded tenant must survive refresh-token
    rotation — Microsoft rotates the refresh token on EVERY refresh, so a
    rotation path that drops the tenant makes D8's diagnostic useless
    within one daemon tick (~300s)."""

    @respx.mock
    async def test_tenant_survives_one_rotation(self, seeded_microsoft_connection):
        respx.post(MS_TOKEN_URL).mock(
            return_value=_ok_token_response(
                access="a1", expires_in=3600, refresh="ROTATED-1"
            )
        )
        await get_or_refresh("microsoft")
        loaded = load_connection(
            "microsoft",
            current_client_id_hash=seeded_microsoft_connection.client_id_hash,
        )
        assert loaded["refresh_token"] == "ROTATED-1"
        assert loaded["tenant"] == "consumers"

    @respx.mock
    async def test_tenant_survives_two_rotation_cycles(
        self, seeded_microsoft_connection
    ):
        # Cross TWO rotations — the plan explicitly calls out that a naive
        # fix could survive exactly one cycle (forwarding the ORIGINAL
        # stored value once) and still lose it on the second.
        responses = [
            _ok_token_response(access="a1", expires_in=3600, refresh="ROTATED-1"),
            _ok_token_response(access="a2", expires_in=3600, refresh="ROTATED-2"),
        ]

        def _next(request):
            return responses.pop(0)

        respx.post(MS_TOKEN_URL).mock(side_effect=_next)

        await get_or_refresh("microsoft")
        # Force a second refresh by expiring the cache entry.
        from gaia.connectors.tokens import _cache_key

        _cache[_cache_key("microsoft", "default")].expires_at = 0.0

        await get_or_refresh("microsoft")
        loaded = load_connection(
            "microsoft",
            current_client_id_hash=seeded_microsoft_connection.client_id_hash,
        )
        assert loaded["refresh_token"] == "ROTATED-2"
        assert loaded["tenant"] == "consumers"


class TestTenantMismatchThroughGetOrRefresh:
    """A18: get_or_refresh must pass the LIVE provider's tenant as
    current_tenant to load_connection, so the store-level TENANT_MISMATCH
    tripwire actually fires on the real production path, not only when a
    test calls store.load_connection directly."""

    @respx.mock
    async def test_mismatched_recorded_tenant_raises_tenant_mismatch(
        self, microsoft_provider
    ):
        # Recorded tenant disagrees with the CURRENT "microsoft" provider's
        # tenant ("consumers") — simulates a stored Directory-tenant-ID
        # override changing after the connection was made.
        save_connection(
            provider="microsoft",
            account_email="alice@example.com",
            refresh_token="seed",
            scopes=["s"],
            client_id_hash=microsoft_provider.client_id_hash,
            tenant="organizations",
        )
        with pytest.raises(AuthRequiredError) as exc:
            await get_or_refresh("microsoft")
        assert exc.value.reason is AuthRequiredError.Reason.TENANT_MISMATCH

    @respx.mock
    async def test_legacy_blob_with_no_tenant_does_not_raise_mismatch(
        self, seeded_connection
    ):
        # seeded_connection is a GOOGLE fixture with no tenant key at all —
        # negative control: a provider with no .tenant attribute (Google)
        # must not crash get_or_refresh's tenant-threading code.
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=_ok_token_response(access="fresh", expires_in=3600)
        )
        token = await get_or_refresh("google")
        assert token == "fresh"


class TestSplitLanguageFailureTaxonomy:
    """A5 (CRITICAL): a legacy blob (no recorded tenant) whose refresh FAILS
    gets split-related guidance ONLY for a genuine OAuth-protocol rejection
    (invalid_grant / another 400 with an error body) — never for a
    ConfigurationError or a raw network failure, and delete_connection must
    NEVER be called from this enrichment path (only a genuine tenant
    mismatch, tested above, may clear stored state)."""

    @pytest.fixture
    def legacy_microsoft_connection(self, microsoft_provider):
        # Raw JSON with NO "tenant" key at all, written directly via
        # keyring.set_password (not save_connection(tenant=None)) — this is
        # the exact shape 100% of existing users' blobs have on their first
        # post-upgrade refresh, and it must stay independent of whatever
        # save_connection's own omission logic does, so a future refactor
        # of one can't silently break this test's premise.
        blob = {
            "account_email": "alice@example.com",
            "refresh_token": "seed-legacy",
            "scopes": ["s"],
            "connected_at": time.time(),
            "client_id_hash": microsoft_provider.client_id_hash,
        }
        keyring.set_password(
            SERVICE_NAME,
            _connection_username("microsoft", "default"),
            json.dumps(blob, sort_keys=True),
        )
        return microsoft_provider

    @respx.mock
    async def test_invalid_grant_on_legacy_blob_names_the_split(
        self, legacy_microsoft_connection
    ):
        respx.post(MS_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(ConnectionRevokedError) as exc:
            await get_or_refresh("microsoft")
        msg = str(exc.value).lower()
        assert "microsoft_work" in msg or "split" in msg

    @respx.mock
    async def test_invalid_grant_on_recorded_tenant_blob_does_not_name_the_split(
        self, seeded_microsoft_connection
    ):
        # Positive control: a blob that DOES record a tenant is not the
        # uncertain "maybe this is the split" case — no enrichment needed.
        respx.post(MS_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(ConnectionRevokedError) as exc:
            await get_or_refresh("microsoft")
        msg = str(exc.value).lower()
        assert "microsoft_work" not in msg

    @respx.mock
    async def test_secretless_401_never_gets_split_language(
        self, legacy_microsoft_connection
    ):
        # Negative case (I5-required): a 401 on a secretless public client is
        # a rejected-credential problem, not an OAuth-protocol rejection of a
        # legacy blob, so it must propagate as itself with no split language.
        #
        # This asserted ConfigurationError until #1638 landed: that fix gated
        # the "go configure a client_secret" branch on the providers that
        # actually require one, so a secretless Microsoft 401 now correctly
        # raises AuthRequiredError(REAUTH_REQUIRED) instead. A5 excludes
        # AuthRequiredError from enrichment, so the exclusion still has to
        # hold — on the path that now really occurs rather than one that
        # cannot.
        respx.post(MS_TOKEN_URL).mock(
            return_value=httpx.Response(401, json={"error": "invalid_client"})
        )
        with pytest.raises(AuthRequiredError) as exc:
            await get_or_refresh("microsoft")
        assert exc.value.reason is AuthRequiredError.Reason.REAUTH_REQUIRED
        assert "split" not in str(exc.value).lower()
        assert "microsoft_work" not in str(exc.value).lower()

    @respx.mock
    async def test_network_failure_never_gets_split_language(
        self, legacy_microsoft_connection
    ):
        # Negative case (I5-required): a raw connection failure must
        # propagate as itself — never reinterpreted as split-related.
        respx.post(MS_TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(httpx.ConnectError):
            await get_or_refresh("microsoft")

    @respx.mock
    async def test_legacy_blob_invalid_grant_still_clears_the_keyring(
        self, legacy_microsoft_connection
    ):
        # The enrichment must not change the EXISTING invalid_grant
        # behaviour (clearing a genuinely revoked token) — only forbid NEW
        # delete_connection calls from the enrichment path itself.
        respx.post(MS_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(ConnectionRevokedError):
            await get_or_refresh("microsoft")
        assert (
            load_connection(
                "microsoft",
                current_client_id_hash=legacy_microsoft_connection.client_id_hash,
            )
            is None
        )

    @respx.mock
    async def test_legacy_blob_upgraded_on_successful_refresh(
        self, legacy_microsoft_connection
    ):
        # A successful refresh is the strongest evidence available that the
        # NEW refresh token was minted by the live provider's tenant — a
        # rotation always accompanies a successful refresh (Microsoft
        # rotates on every refresh), so record it then, upgrading the blob
        # out of "legacy" exactly once, on proof rather than a guess.
        respx.post(MS_TOKEN_URL).mock(
            return_value=_ok_token_response(
                access="a1", expires_in=3600, refresh="ROTATED-1"
            )
        )
        await get_or_refresh("microsoft")
        loaded = load_connection(
            "microsoft",
            current_client_id_hash=legacy_microsoft_connection.client_id_hash,
        )
        assert loaded["tenant"] == "consumers"

        # Second refresh: the blob is no longer legacy, so a subsequent
        # invalid_grant must NOT take the split-language enrichment path.
        _cache[("microsoft", "default")].expires_at = 0.0
        respx.post(MS_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(ConnectionRevokedError) as exc:
            await get_or_refresh("microsoft")
        assert "microsoft_work" not in str(exc.value)
        assert "split" not in str(exc.value).lower()
