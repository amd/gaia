# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Router tests for /api/connectors/* — OAuth-specific functionality.

Coverage:
- GET /api/connectors       → returns catalog list (no refresh_token)
- GET /api/connectors/{id}  → returns one connector or 404
- DELETE /api/connectors/{id}          → CSRF required; calls disconnect handler
- GET/PUT/DELETE /api/connectors/{id}/grants/{agent_id}
- POST /api/connectors/{id}/authorize  → OAuth PKCE (CSRF required)
- GET /api/connectors/_debug           → gated by GAIA_DEBUG=1
- Exception → HTTP mapping (ConfigurationError → 503)
"""

from __future__ import annotations

import pytest

from gaia.connectors.providers import _registry

UI_HEADER = {"x-gaia-ui": "1"}


@pytest.fixture(autouse=True)
def google_provider_env(monkeypatch, tmp_path):
    """Provide a configured Google provider + isolated grants/state dirs."""
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.state.Path.home", lambda: tmp_path)
    _registry.clear()
    yield


# ---------------------------------------------------------------------------
# GET /api/connectors
# ---------------------------------------------------------------------------


class TestListConnections:
    def test_returns_connectors_key(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors")
        assert resp.status_code == 200
        body = resp.json()
        assert "connectors" in body
        assert isinstance(body["connectors"], list)

    def test_no_refresh_token_in_response(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors")
        assert resp.status_code == 200
        for entry in resp.json()["connectors"]:
            assert "refresh_token" not in entry


# ---------------------------------------------------------------------------
# GET /api/connectors/{id}
# ---------------------------------------------------------------------------


class TestGetConnection:
    def test_missing_returns_404(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors/nonexistent")
        assert resp.status_code == 404

    def test_known_connector_returns_id(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors/google")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "google"
        assert "refresh_token" not in body


# ---------------------------------------------------------------------------
# DELETE /api/connectors/{id}
# ---------------------------------------------------------------------------


class TestRevokeConnection:
    def test_revoke_requires_csrf_header(self, ui_api_client):
        resp = ui_api_client.delete("/api/connectors/google")
        assert resp.status_code == 403

    def test_revoke_with_header_returns_204(self, ui_api_client, monkeypatch):
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            "gaia.ui.routers.connectors.disconnect",
            AsyncMock(return_value=None),
        )
        resp = ui_api_client.delete("/api/connectors/google", headers=UI_HEADER)
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Grants endpoints
# ---------------------------------------------------------------------------


class TestGrants:
    def test_put_grant_then_get_grants(self, ui_api_client):
        resp = ui_api_client.put(
            "/api/connectors/google/grants/builtin:chat",
            json={"scopes": ["gmail.readonly"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200

        listing = ui_api_client.get("/api/connectors/google/grants").json()
        assert "grants" in listing

    def test_delete_grant(self, ui_api_client):
        ui_api_client.put(
            "/api/connectors/google/grants/builtin:chat",
            json={"scopes": ["gmail.readonly"]},
            headers=UI_HEADER,
        )
        resp = ui_api_client.delete(
            "/api/connectors/google/grants/builtin:chat",
            headers=UI_HEADER,
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# POST /api/connectors/{id}/authorize — OAuth PKCE flow
# ---------------------------------------------------------------------------


class TestAuthorizeFlow:
    def test_authorize_returns_flow_id_and_url(self, ui_api_client, monkeypatch):
        monkeypatch.setattr("webbrowser.open", lambda *_, **__: True)

        resp = ui_api_client.post(
            "/api/connectors/google/authorize",
            json={"scopes": ["gmail.readonly"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "flow_id" in body
        assert "authorization_url" in body
        assert body["authorization_url"].startswith("https://accounts.google.com/")

        # Cancel the started flow so subsequent tests can start a new one.
        from gaia.connectors.flow import _pending

        for fid in list(_pending.keys()):
            ui_api_client.delete(f"/api/connectors/_flows/{fid}", headers=UI_HEADER)


# ---------------------------------------------------------------------------
# GET /api/connectors/_debug
# ---------------------------------------------------------------------------


class TestDebugEndpoint:
    def test_debug_endpoint_blocked_when_env_unset(self, ui_api_client, monkeypatch):
        monkeypatch.delenv("GAIA_DEBUG", raising=False)
        resp = ui_api_client.get("/api/connectors/_debug")
        assert resp.status_code == 404

    def test_debug_endpoint_returns_state_when_env_set(
        self, ui_api_client, monkeypatch
    ):
        monkeypatch.setenv("GAIA_DEBUG", "1")
        resp = ui_api_client.get("/api/connectors/_debug")
        assert resp.status_code == 200
        body = resp.json()
        assert "provider_registered" in body
        assert "env_var_present" in body
        assert "keyring_backend_class" in body
        assert "grants_path_writable" in body
        assert "in_flight_flow_count" in body


# ---------------------------------------------------------------------------
# Exception → HTTP mapping
# ---------------------------------------------------------------------------


class TestExceptionMapping:
    """Contract: ConfigurationError → 503."""

    def test_get_connection_misconfigured_returns_503(self, monkeypatch, ui_api_client):
        # No GAIA_GOOGLE_CLIENT_ID → ConfigurationError → 503.
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        _registry.clear()
        resp = ui_api_client.post(
            "/api/connectors/google/authorize",
            json={"scopes": ["gmail.readonly"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 503
        body = resp.json()
        assert "GAIA_GOOGLE_CLIENT_ID" in body.get("detail", "")
