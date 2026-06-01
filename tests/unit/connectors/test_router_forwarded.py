# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration tests for the forwarded-connection REST endpoints (#1292):
``POST/GET/DELETE /v1/connections[/{provider}]``.

Driven against the in-process AgentUI FastAPI app (``ui_api_client``) with
the autouse in-memory keyring from ``tests/unit/connectors/conftest.py``.
No real Google call is made; refresh is stubbed via respx where needed.

Asserts:
- POST forwards a grant, persists it, returns a masked summary (201);
- POST requires the ``X-Gaia-UI`` CSRF header;
- a scope shortfall fails loudly with HTTP 403 + structured error;
- GET (list + single) returns metadata only, NEVER a secret;
- DELETE revokes the connection;
- after a forward, the agent can resolve a token with NO interactive step.
"""

from __future__ import annotations

import httpx
import pytest
import respx

UI_HEADER = {"x-gaia-ui": "1"}

FWD_CLIENT_ID = "forwarded-host-app.apps.googleusercontent.com"
FWD_CLIENT_SECRET = "FWD-SECRET-do-not-leak"
FWD_REFRESH = "FWD-REFRESH-TOKEN-do-not-leak"
FULL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from gaia.connectors.providers import _registry

    _registry.clear()
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "ENV-CLIENT.apps.googleusercontent.com")
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_SECRET", "ENV-SECRET")
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    yield
    _registry.clear()


def _forward_body(**overrides):
    body = {
        "client_id": FWD_CLIENT_ID,
        "client_secret": FWD_CLIENT_SECRET,
        "refresh_token": FWD_REFRESH,
        "scopes": FULL_SCOPES,
        "account_email": "alice@example.com",
        "grant_agents": ["builtin:email"],
    }
    body.update(overrides)
    return body


class TestForwardPost:
    def test_persists_and_returns_masked_summary(self, ui_api_client):
        resp = ui_api_client.post(
            "/v1/connections/google", json=_forward_body(), headers=UI_HEADER
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["provider"] == "google"
        assert data["account_email"] == "alice@example.com"
        # No secrets echoed.
        body_str = resp.text
        assert FWD_REFRESH not in body_str
        assert FWD_CLIENT_SECRET not in body_str
        assert "refresh_token" not in data
        assert "client_secret" not in data

    def test_requires_csrf_header(self, ui_api_client):
        resp = ui_api_client.post("/v1/connections/google", json=_forward_body())
        assert resp.status_code == 403

    def test_scope_shortfall_fails_loudly(self, ui_api_client):
        resp = ui_api_client.post(
            "/v1/connections/google",
            json=_forward_body(scopes=["https://www.googleapis.com/auth/gmail.modify"]),
            headers=UI_HEADER,
        )
        assert resp.status_code == 403, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "scope_mismatch"
        assert any("gmail.send" in s for s in detail["missing_scopes"])

    def test_empty_refresh_token_fails_loudly(self, ui_api_client):
        resp = ui_api_client.post(
            "/v1/connections/google",
            json=_forward_body(refresh_token=""),
            headers=UI_HEADER,
        )
        # Empty string fails pydantic min_length OR the loud ConnectorsError.
        assert resp.status_code in (400, 422, 500)


class TestForwardGet:
    def test_list_after_forward_masks_secret(self, ui_api_client):
        ui_api_client.post(
            "/v1/connections/google", json=_forward_body(), headers=UI_HEADER
        )
        resp = ui_api_client.get("/v1/connections")
        assert resp.status_code == 200
        assert FWD_REFRESH not in resp.text
        assert FWD_CLIENT_SECRET not in resp.text
        providers = [c["provider"] for c in resp.json()["connections"]]
        assert "google" in providers

    def test_get_single_masks_secret(self, ui_api_client):
        ui_api_client.post(
            "/v1/connections/google", json=_forward_body(), headers=UI_HEADER
        )
        resp = ui_api_client.get("/v1/connections/google")
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "google"
        assert "refresh_token" not in resp.text
        assert FWD_REFRESH not in resp.text

    def test_get_missing_provider_404(self, ui_api_client):
        resp = ui_api_client.get("/v1/connections/google")
        assert resp.status_code == 404


class TestForwardDelete:
    def test_revoke_clears_connection(self, ui_api_client):
        ui_api_client.post(
            "/v1/connections/google", json=_forward_body(), headers=UI_HEADER
        )
        resp = ui_api_client.delete("/v1/connections/google", headers=UI_HEADER)
        assert resp.status_code == 204
        # Now gone.
        assert ui_api_client.get("/v1/connections/google").status_code == 404

    def test_delete_requires_csrf(self, ui_api_client):
        ui_api_client.post(
            "/v1/connections/google", json=_forward_body(), headers=UI_HEADER
        )
        resp = ui_api_client.delete("/v1/connections/google")
        assert resp.status_code == 403


class TestAgentActsAfterForward:
    @respx.mock
    async def test_token_resolved_with_no_interactive_step(self, ui_api_client):
        """After forwarding a grant, the agent resolves a token ambiently —
        the refresh hits the stubbed token endpoint with the forwarded client,
        no browser/PKCE flow involved."""
        captured = {}

        def _cap(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode()
            return httpx.Response(
                200, json={"access_token": "STUB-ACCESS", "expires_in": 3600}
            )

        respx.post("https://oauth2.googleapis.com/token").mock(side_effect=_cap)

        resp = ui_api_client.post(
            "/v1/connections/google",
            json=_forward_body(account_email=""),
            headers=UI_HEADER,
        )
        assert resp.status_code == 201, resp.text

        from gaia.connectors.api import get_access_token

        token = await get_access_token(
            provider="google", scopes=FULL_SCOPES, agent_id="builtin:email"
        )
        assert token == "STUB-ACCESS"
        assert FWD_CLIENT_ID in captured["body"]
        assert FWD_CLIENT_SECRET in captured["body"]
