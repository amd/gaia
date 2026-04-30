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
import time

import httpx
import pytest
import respx

from gaia.connections.errors import (
    AuthRequiredError,
    ConnectionRevokedError,
)
from gaia.connections.providers import _registry
from gaia.connections.store import (
    load_connection,
    save_connection,
)
from gaia.connections.tokens import _cache, get_or_refresh


@pytest.fixture
def google_provider(monkeypatch):
    """Build a known Google provider in the registry for refresh tests."""
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    _registry.clear()
    from gaia.connections.providers import get as get_provider

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
        from gaia.connections.tokens import _AccessTokenCache, _cache_key

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
        from gaia.connections.tokens import _AccessTokenCache, _cache_key

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
        from gaia.connections.tokens import _cache_key

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
