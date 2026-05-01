# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-7a (AC3, A8): OAuth flow + loopback callback server.

Coverage:
- ``start_authorization`` returns ``{flow_id, authorization_url}`` and binds
  a loopback ``aiohttp.web`` server on an ephemeral port.
- A successful redirect to ``/callback?code=...&state=...`` exchanges the
  code via the token endpoint and resolves the future.
- A8: explicit ``None`` guard before ``hmac.compare_digest`` — a request
  without ``state`` returns 400, not 500 from a TypeError.
- A8: success HTML page is a static string literal — XSS payloads in the
  query string never appear in the response body.
- A8: ``webbrowser.open`` is dispatched to ``run_in_executor`` so it does
  not block the event loop.
- ``?error=access_denied`` resolves the flow with ``ConsentDeniedError``.
- 120s timeout fires ``FlowTimeoutError`` and tears down the runner.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from gaia.connectors.errors import (
    ConsentDeniedError,
    FlowTimeoutError,
)
from gaia.connectors.flow import (
    _SUCCESS_HTML,
    cancel_flow,
    complete_authorization,
    start_authorization,
)
from gaia.connectors.providers import _registry


@pytest.fixture
def google_provider(monkeypatch):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    _registry.clear()
    from gaia.connectors.providers import get as get_provider

    return get_provider("google")


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    """Replace webbrowser.open so tests don't actually launch a browser."""
    monkeypatch.setattr("webbrowser.open", lambda *_, **__: True)


def _mock_token_endpoint():
    """Mock the Google token endpoint and pass-through 127.0.0.1.

    Without the pass_through() call respx would intercept the loopback
    callback round-trip and raise AllMockedAssertionError on first
    request. The token endpoint stays mocked because it's external HTTPS.
    """
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "fresh-access",
                "refresh_token": "fresh-refresh",
                "expires_in": 3600,
                "scope": "openid",
                "id_token": (
                    # JWT payload {"email": "alice@example.com"}; signature
                    # is a placeholder — flow.py decodes only the email
                    # claim, not the signature.
                    "header."
                    "eyJlbWFpbCI6ICJhbGljZUBleGFtcGxlLmNvbSJ9"
                    ".sig"
                ),
            },
        )
    )
    respx.route(host="127.0.0.1").pass_through()


class TestSuccessPath:
    @respx.mock
    async def test_callback_completes_flow(self, google_provider):
        _mock_token_endpoint()
        info = await start_authorization("google", scopes=["openid"])
        assert "authorization_url" in info
        assert "flow_id" in info
        assert info["authorization_url"].startswith(google_provider.auth_url)

        params = parse_qs(urlparse(info["authorization_url"]).query)
        redirect_uri = params["redirect_uri"][0]
        state = params["state"][0]

        async with httpx.AsyncClient() as c:
            resp = await c.get(f"{redirect_uri}?code=test-code&state={state}")
        assert resp.status_code == 200
        assert _SUCCESS_HTML in resp.text

        result = await asyncio.wait_for(
            complete_authorization(info["flow_id"]), timeout=2.0
        )
        assert result["account_email"] == "alice@example.com"
        assert result["scopes"] == ["openid"]


class TestStateValidation:
    @respx.mock
    async def test_missing_state_returns_400(self, google_provider):
        _mock_token_endpoint()
        info = await start_authorization("google", scopes=["openid"])
        params = parse_qs(urlparse(info["authorization_url"]).query)
        redirect_uri = params["redirect_uri"][0]

        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"{redirect_uri}?code=test-code")
            assert resp.status_code == 400
        finally:
            await cancel_flow(info["flow_id"])

    @respx.mock
    async def test_mismatched_state_returns_400(self, google_provider):
        _mock_token_endpoint()
        info = await start_authorization("google", scopes=["openid"])
        params = parse_qs(urlparse(info["authorization_url"]).query)
        redirect_uri = params["redirect_uri"][0]

        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"{redirect_uri}?code=test-code&state=WRONG-STATE")
            assert resp.status_code == 400
        finally:
            await cancel_flow(info["flow_id"])


class TestXssDefense:
    """A8: success HTML must be a static literal — no echoed input."""

    @respx.mock
    async def test_xss_payload_in_state_not_reflected(self, google_provider):
        _mock_token_endpoint()
        info = await start_authorization("google", scopes=["openid"])
        params = parse_qs(urlparse(info["authorization_url"]).query)
        redirect_uri = params["redirect_uri"][0]

        try:
            xss = "<script>alert(1)</script>"
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"{redirect_uri}?code=test-code&state={xss}")
            assert resp.status_code == 400
            assert "<script>" not in resp.text.lower()
            assert "alert(1)" not in resp.text
        finally:
            await cancel_flow(info["flow_id"])


class TestConsentDenied:
    @respx.mock
    async def test_access_denied_resolves_with_consent_denied(self, google_provider):
        _mock_token_endpoint()
        info = await start_authorization("google", scopes=["openid"])
        params = parse_qs(urlparse(info["authorization_url"]).query)
        redirect_uri = params["redirect_uri"][0]
        state = params["state"][0]

        async with httpx.AsyncClient() as c:
            resp = await c.get(f"{redirect_uri}?error=access_denied&state={state}")
        # Browser sees the rejection page — telling the user "Connected"
        # after they explicitly clicked "Deny" would be misleading.
        assert resp.status_code == 400

        with pytest.raises(ConsentDeniedError):
            await asyncio.wait_for(complete_authorization(info["flow_id"]), timeout=2.0)


class TestTimeout:
    async def test_flow_timeout(self, google_provider, monkeypatch):
        # Squash the timeout to 0.5s so the test runs fast.
        monkeypatch.setattr("gaia.connectors.flow._FLOW_TIMEOUT_SECONDS", 0.5)

        info = await start_authorization("google", scopes=["openid"])
        with pytest.raises(FlowTimeoutError):
            await complete_authorization(info["flow_id"])


class TestKeyringIsSourceOfTruth:
    """After a successful flow, the keyring blob — and *only* the
    keyring blob — must reflect the new connection. There is no
    separate state.json cache to keep in sync; the catalog UI reads
    ``configured`` / ``account_id`` / ``scopes`` live via
    ``store.peek_connection``."""

    @respx.mock
    async def test_successful_flow_makes_peek_return_blob(self, google_provider):
        from gaia.connectors.store import peek_connection

        _mock_token_endpoint()

        info = await start_authorization("google", scopes=["openid"])
        params = parse_qs(urlparse(info["authorization_url"]).query)
        redirect_uri = params["redirect_uri"][0]
        state = params["state"][0]

        async with httpx.AsyncClient() as c:
            await c.get(f"{redirect_uri}?code=ok&state={state}")
        await asyncio.wait_for(complete_authorization(info["flow_id"]), timeout=2.0)

        blob = peek_connection("google")
        assert blob is not None
        assert blob["account_email"] == "alice@example.com"
        assert blob["scopes"] == ["openid"]


class TestStaleFlowEviction:
    """`start_authorization` self-heals when a previous flow was
    abandoned (e.g. user picked the wrong Google account, never got
    redirected back to the loopback). User re-clicking Connect = the
    previous flow is dead; evict and proceed."""

    async def test_re_starting_evicts_stale_pending_flow(self, google_provider):
        first = await start_authorization("google", scopes=["openid"])
        # Don't complete the first flow — simulate the wrong-account case.
        second = await start_authorization("google", scopes=["openid"])

        from gaia.connectors.flow import _pending

        assert second["flow_id"] != first["flow_id"]
        assert first["flow_id"] not in _pending
        assert second["flow_id"] in _pending
        assert len(_pending) == 1

        await cancel_flow(second["flow_id"])


class TestBrowserOpenNonBlocking:
    """A8: webbrowser.open must NOT block the event loop. We assert that
    start_authorization returns even when the browser-open callable
    sleeps — this would freeze the loop without run_in_executor.
    """

    async def test_blocking_webbrowser_open_does_not_block_loop(
        self, google_provider, monkeypatch
    ):
        import time as time_mod

        def slow_open(url):
            time_mod.sleep(0.5)
            return True

        monkeypatch.setattr("webbrowser.open", slow_open)

        async def peer():
            return time_mod.monotonic()

        t0 = time_mod.monotonic()
        results = await asyncio.gather(
            start_authorization("google", scopes=["openid"]),
            peer(),
            asyncio.sleep(0),
        )
        # peer should run essentially immediately because the browser
        # open is dispatched to run_in_executor — the event loop keeps
        # spinning.
        assert results[1] - t0 < 0.4, (
            f"event loop was blocked during webbrowser.open "
            f"(peer ran at +{results[1] - t0:.3f}s)"
        )

        await cancel_flow(results[0]["flow_id"])
