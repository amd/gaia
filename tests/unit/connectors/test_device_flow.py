# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the device-code flow (#1275) — the zero-Azure-app-registration
sign-in used for Microsoft work/school + personal accounts.

All HTTP is mocked; there are NO live calls. Coverage:

- ``start_device_flow`` returns the user code / verification URI and raises
  loudly on a non-200 devicecode response or a provider with no device support.
- ``poll_device_flow`` honors ``authorization_pending`` / ``slow_down``, then
  persists the connection on success (via the real in-memory keyring) and
  commits per-agent grants atomically.
- The unhappy paths (declined, expired, missing refresh_token) each raise a
  typed error rather than returning a partial / empty result.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from gaia.connectors import flow as flow_mod
from gaia.connectors.errors import (
    ConnectorsError,
    ConsentDeniedError,
    FlowTimeoutError,
)

MAIL_READ = "https://graph.microsoft.com/Mail.Read"


@pytest.fixture(autouse=True)
def _ms_env(monkeypatch):
    monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("GAIA_MICROSOFT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GAIA_MICROSOFT_TENANT", raising=False)
    # Reset the provider registry so each test builds a fresh provider.
    from gaia.connectors import providers

    providers._registry.clear()  # type: ignore[attr-defined]
    yield
    providers._registry.clear()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _instant_sleep(monkeypatch):
    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(flow_mod.asyncio, "sleep", _no_wait)


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeAsyncClient:
    """Pops a queued response per ``post`` call; ``get`` pops from a separate
    queue (used for the userinfo /me fallback). Records the requests made."""

    _queue: list = []
    _get_queue: list = []
    calls: list = []
    get_calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None):
        _FakeAsyncClient.calls.append((url, data))
        return _FakeAsyncClient._queue.pop(0)

    async def get(self, url, headers=None):
        _FakeAsyncClient.get_calls.append((url, headers))
        return _FakeAsyncClient._get_queue.pop(0)


def _install_responses(monkeypatch, responses, get_responses=None):
    _FakeAsyncClient._queue = list(responses)
    _FakeAsyncClient._get_queue = list(get_responses or [])
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.get_calls = []
    monkeypatch.setattr(flow_mod.httpx, "AsyncClient", _FakeAsyncClient)


def _id_token(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.sig"


class TestStartDeviceFlow:
    def test_returns_user_code_and_uri(self, monkeypatch):
        _install_responses(
            monkeypatch,
            [
                _FakeResp(
                    200,
                    {
                        "device_code": "DEV",
                        "user_code": "ABCD-EFGH",
                        "verification_uri": "https://microsoft.com/devicelogin",
                        "expires_in": 900,
                        "interval": 5,
                        "message": "Go to ... and enter ABCD-EFGH",
                    },
                )
            ],
        )
        info = asyncio.run(flow_mod.start_device_flow("microsoft", [MAIL_READ]))
        assert info["user_code"] == "ABCD-EFGH"
        assert info["device_code"] == "DEV"
        assert info["verification_uri"].endswith("devicelogin")
        # Hit the tenant-scoped devicecode endpoint.
        assert _FakeAsyncClient.calls[0][0].endswith("/common/oauth2/v2.0/devicecode")

    def test_non_200_raises(self, monkeypatch):
        _install_responses(
            monkeypatch, [_FakeResp(400, {"error": "invalid_client"}, "bad")]
        )
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(flow_mod.start_device_flow("microsoft", [MAIL_READ]))
        assert "Device-code request" in str(exc.value)

    def test_provider_without_device_support_raises(self, monkeypatch):
        # Google has no device_code_url -> loud, actionable error.
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "g")
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_SECRET", "s")
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(flow_mod.start_device_flow("google", []))
        assert "does not support the device-code flow" in str(exc.value)


class TestPollDeviceFlow:
    def _success_payload(self):
        return {
            "access_token": "AT",
            "refresh_token": "RT-value",
            "expires_in": 3600,
            "id_token": _id_token({"preferred_username": "user@example.com"}),
            "scope": MAIL_READ,
        }

    def test_pending_then_success_persists_connection(self, monkeypatch):
        _install_responses(
            monkeypatch,
            [
                _FakeResp(400, {"error": "authorization_pending"}),
                _FakeResp(400, {"error": "slow_down"}),
                _FakeResp(200, self._success_payload()),
            ],
        )
        result = asyncio.run(
            flow_mod.poll_device_flow(
                "microsoft", "DEV", scopes=[MAIL_READ], interval=1, expires_in=900
            )
        )
        assert result["account_email"] == "user@example.com"

        # Connection persisted through the real (in-memory) keyring store.
        from gaia.connectors.store import peek_connection

        blob = peek_connection("microsoft")
        assert blob is not None
        assert blob["refresh_token"] == "RT-value"

    def test_grant_agents_committed_on_success(self, monkeypatch):
        _install_responses(monkeypatch, [_FakeResp(200, self._success_payload())])
        asyncio.run(
            flow_mod.poll_device_flow(
                "microsoft",
                "DEV",
                scopes=[MAIL_READ],
                grant_agents={"installed:email": [MAIL_READ]},
            )
        )
        from gaia.connectors.grants import list_agent_grants

        grants = list_agent_grants("microsoft")
        assert grants.get("installed:email") == [MAIL_READ]

    def test_account_email_falls_back_to_userinfo(self, monkeypatch):
        # Device-code id_token often carries no decodable email — the flow then
        # GETs the provider userinfo_url (Graph /me) with the access token.
        payload = self._success_payload()
        payload["id_token"] = ""  # no claim to decode
        _install_responses(
            monkeypatch,
            [_FakeResp(200, payload)],
            get_responses=[
                _FakeResp(
                    200,
                    {"mail": "user@example.com", "userPrincipalName": "u@example.com"},
                )
            ],
        )
        result = asyncio.run(
            flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
        )
        assert result["account_email"] == "user@example.com"
        # The /me endpoint was queried with the bearer token.
        assert _FakeAsyncClient.get_calls
        assert "/me" in _FakeAsyncClient.get_calls[0][0]

    def test_account_email_default_when_userinfo_unavailable(self, monkeypatch):
        payload = self._success_payload()
        payload["id_token"] = ""
        _install_responses(
            monkeypatch,
            [_FakeResp(200, payload)],
            get_responses=[_FakeResp(403, {}, "forbidden")],
        )
        result = asyncio.run(
            flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
        )
        # Label-only fallback — connection still persists, never fails.
        assert result["account_email"] == "default"

    def test_declined_raises_consent_denied(self, monkeypatch):
        _install_responses(
            monkeypatch, [_FakeResp(400, {"error": "authorization_declined"})]
        )
        with pytest.raises(ConsentDeniedError):
            asyncio.run(
                flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
            )

    def test_expired_token_raises_timeout(self, monkeypatch):
        _install_responses(monkeypatch, [_FakeResp(400, {"error": "expired_token"})])
        with pytest.raises(FlowTimeoutError):
            asyncio.run(
                flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
            )

    def test_missing_refresh_token_raises(self, monkeypatch):
        payload = self._success_payload()
        del payload["refresh_token"]
        _install_responses(monkeypatch, [_FakeResp(200, payload)])
        with pytest.raises(ConnectorsError) as exc:
            asyncio.run(
                flow_mod.poll_device_flow("microsoft", "DEV", scopes=[MAIL_READ])
            )
        assert "offline_access" in str(exc.value)
