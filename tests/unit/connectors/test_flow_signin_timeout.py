# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The OAuth browser sign-in window is long enough for a first-time consent,
and says the old link is dead when it lapses (#4008)."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

pytestmark = pytest.mark.allow_network

from gaia.connectors import flow
from gaia.connectors.errors import FlowTimeoutError
from gaia.connectors.providers import _registry


@pytest.fixture
def google_provider(monkeypatch):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    _registry.clear()
    monkeypatch.setattr("webbrowser.open", lambda *_, **__: True)
    from gaia.connectors.providers import get as get_provider

    return get_provider("google")


# --- the flow -------------------------------------------------------------------


def _mock_token_endpoint():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 3600,
                "scope": "openid",
                "id_token": "h.eyJlbWFpbCI6ICJhbGljZUBleGFtcGxlLmNvbSJ9.s",
            },
        )
    )
    respx.route(host="127.0.0.1").pass_through()


def _record_wait_for(monkeypatch):
    seen = []
    real_wait_for = asyncio.wait_for

    async def _wait_for(aw, timeout):
        seen.append(timeout)
        return await real_wait_for(aw, timeout=5.0)

    monkeypatch.setattr(flow.asyncio, "wait_for", _wait_for)
    return seen


@respx.mock
async def test_a_callback_after_the_old_two_minutes_still_completes(
    google_provider, monkeypatch
):
    _mock_token_endpoint()
    seen = _record_wait_for(monkeypatch)
    info = await flow.start_authorization("google", scopes=["openid"])
    params = parse_qs(urlparse(info["authorization_url"]).query)
    callback = f"{params['redirect_uri'][0]}?code=c&state={params['state'][0]}"

    async def _late_click():
        async with httpx.AsyncClient() as c:
            return await c.get(callback)

    result, resp = await asyncio.gather(
        flow.complete_authorization(info["flow_id"]), _late_click()
    )
    assert resp.status_code == 200
    assert result["account_email"] == "alice@example.com"
    assert seen == [flow._FLOW_TIMEOUT_SECONDS]
    assert seen[0] >= 12 * 60 + 10, "shorter than the measured first-time consent"


@respx.mock
async def test_a_wrong_state_is_still_rejected_inside_the_longer_window(
    google_provider,
):
    _mock_token_endpoint()
    info = await flow.start_authorization("google", scopes=["openid"])
    params = parse_qs(urlparse(info["authorization_url"]).query)
    redirect = params["redirect_uri"][0]
    async with httpx.AsyncClient() as c:
        bad = await c.get(f"{redirect}?code=c&state=forged")
        assert bad.status_code == 400
        assert info["flow_id"] in flow._pending
        good = await c.get(f"{redirect}?code=c&state={params['state'][0]}")
    assert good.status_code == 200
    result = await flow.complete_authorization(info["flow_id"])
    assert result["account_email"] == "alice@example.com"


async def test_expiry_says_the_link_is_dead_and_how_to_get_a_new_one(
    google_provider, monkeypatch
):
    monkeypatch.setattr(flow, "_FLOW_TIMEOUT_SECONDS", 0.5)
    info = await flow.start_authorization("google", scopes=["openid"])
    redirect = parse_qs(urlparse(info["authorization_url"]).query)["redirect_uri"][0]
    with pytest.raises(FlowTimeoutError) as exc:
        await flow.complete_authorization(info["flow_id"])
    msg = str(exc.value)
    assert "no longer works" in msg
    assert "gaia connectors connect google" in msg
    assert "--timeout" not in msg and "config set" not in msg
    assert "Restart the flow" not in msg
    assert info["flow_id"] not in flow._pending
    async with httpx.AsyncClient() as c:
        # Windows reports a closed loopback port as a connect timeout.
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            await c.get(f"{redirect}?code=c&state=x", timeout=2.0)
