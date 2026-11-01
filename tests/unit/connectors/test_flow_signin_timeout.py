# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The OAuth browser sign-in window is long enough for a first-time consent,
settable without editing source, and says the old link is dead when it
lapses (#4008)."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

pytestmark = pytest.mark.allow_network

from gaia.connectors import flow
from gaia.connectors.errors import ConnectorsError, FlowTimeoutError
from gaia.connectors.providers import _registry


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    import gaia.config as config_mod

    path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", path)
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
    return path


@pytest.fixture
def google_provider(monkeypatch):
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    _registry.clear()
    monkeypatch.setattr("webbrowser.open", lambda *_, **__: True)
    from gaia.connectors.providers import get as get_provider

    return get_provider("google")


# --- resolving the window -----------------------------------------------------


def test_default_window_is_well_past_the_old_two_minutes(config_file):
    assert flow.signin_timeout_seconds() == flow.DEFAULT_SIGNIN_TIMEOUT_SECONDS
    assert flow.DEFAULT_SIGNIN_TIMEOUT_SECONDS >= 12 * 60 + 10


def test_config_key_sets_the_window(config_file):
    config_file.write_text(json.dumps({"oauth_signin_timeout_seconds": 1800}))
    assert flow.signin_timeout_seconds() == 1800


def test_explicit_override_beats_the_config_key(config_file):
    config_file.write_text(json.dumps({"oauth_signin_timeout_seconds": 1800}))
    assert flow.signin_timeout_seconds(45) == 45


def test_a_one_second_window_is_the_smallest_allowed(config_file):
    assert flow.signin_timeout_seconds(1) == 1


@pytest.mark.parametrize("bad", [0, -5, True, 2.5, "60"])
def test_bad_override_fails_loudly(config_file, bad):
    with pytest.raises(ConnectorsError, match="--timeout"):
        flow.signin_timeout_seconds(bad)


def test_zero_in_config_fails_loudly_naming_the_key(config_file):
    config_file.write_text(json.dumps({"oauth_signin_timeout_seconds": 0}))
    with pytest.raises(ConnectorsError, match="oauth_signin_timeout_seconds"):
        flow.signin_timeout_seconds()


def test_config_set_accepts_the_key(config_file):
    from gaia.config import GaiaConfig

    assert "oauth_signin_timeout_seconds" in GaiaConfig.field_names()
    assert GaiaConfig._coerce("oauth_signin_timeout_seconds", "1200") == 1200


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
    google_provider, config_file, monkeypatch
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
    assert seen == [flow.DEFAULT_SIGNIN_TIMEOUT_SECONDS]
    assert seen[0] > 120


@respx.mock
async def test_the_caller_window_reaches_wait_for(
    google_provider, config_file, monkeypatch
):
    _mock_token_endpoint()
    seen = _record_wait_for(monkeypatch)
    info = await flow.start_authorization("google", scopes=["openid"])
    params = parse_qs(urlparse(info["authorization_url"]).query)
    async with httpx.AsyncClient() as c:
        await c.get(f"{params['redirect_uri'][0]}?code=c&state={params['state'][0]}")
    await flow.complete_authorization(info["flow_id"], timeout_seconds=1800)
    assert seen == [1800]


@respx.mock
async def test_a_wrong_state_is_still_rejected_inside_the_longer_window(
    google_provider, config_file
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
    google_provider, config_file
):
    info = await flow.start_authorization("google", scopes=["openid"])
    redirect = parse_qs(urlparse(info["authorization_url"]).query)["redirect_uri"][0]
    with pytest.raises(FlowTimeoutError) as exc:
        await flow.complete_authorization(info["flow_id"], timeout_seconds=1)
    msg = str(exc.value)
    assert "within 1s" in msg
    assert "no longer works" in msg
    assert "gaia connectors connect google" in msg
    assert "--timeout" in msg and "oauth_signin_timeout_seconds" in msg
    assert "Restart the flow" not in msg
    assert info["flow_id"] not in flow._pending
    async with httpx.AsyncClient() as c:
        # Windows reports a closed loopback port as a connect timeout.
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            await c.get(f"{redirect}?code=c&state=x", timeout=2.0)


async def test_a_bad_window_tears_the_flow_down(google_provider, config_file):
    info = await flow.start_authorization("google", scopes=["openid"])
    with pytest.raises(ConnectorsError):
        await flow.complete_authorization(info["flow_id"], timeout_seconds=0)
    assert info["flow_id"] not in flow._pending


# --- CLI ----------------------------------------------------------------------------


def _run_connect(monkeypatch, *extra):
    from io import StringIO

    import gaia.connectors.cli as connections_cli

    calls = []

    async def _start(connector_id, *, scopes, grant_agents=None):
        return {"flow_id": "F1", "authorization_url": "https://auth.example"}

    async def _complete(flow_id, **kwargs):
        calls.append((flow_id, kwargs))
        return {"account_email": "alice@example.com"}

    monkeypatch.setattr("gaia.connectors.api.start_authorization", _start)
    monkeypatch.setattr("gaia.connectors.api.complete_authorization", _complete)
    out, err = StringIO(), StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    rc = connections_cli.main(
        ["connectors", "connect", "google", "--scopes", "openid", *extra]
    )
    return rc, calls, out.getvalue(), err.getvalue()


def test_cli_timeout_flag_reaches_complete_authorization(
    google_provider, config_file, monkeypatch
):
    rc, calls, out, _ = _run_connect(monkeypatch, "--timeout", "1800")
    assert rc == 0
    assert calls == [("F1", {"timeout_seconds": 1800})]
    assert "Connected as alice@example.com" in out


def test_cli_without_the_flag_leaves_the_window_to_config(
    google_provider, config_file, monkeypatch
):
    rc, calls, _, _ = _run_connect(monkeypatch)
    assert rc == 0
    assert calls == [("F1", {})]


def test_cli_rejects_a_zero_timeout_before_any_flow_starts(
    google_provider, config_file, monkeypatch
):
    rc, calls, _, err = _run_connect(monkeypatch, "--timeout", "0")
    assert rc == 5
    assert calls == []
    assert "--timeout" in err
