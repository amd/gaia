# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-10a (AC12-AC16): FastAPI router contract tests.

Uses the ``ui_api_client`` fixture (plan amendment A12) — NOT ``api_client``,
which targets the OpenAI-compat server and would 404 on these routes.

Coverage:
- POST /api/connections/{provider}/authorize → ``{flow_id, authorization_url}``
- GET  /api/connections                       → list
- GET  /api/connections/{provider}            → one
- DELETE /api/connections/{provider}          → revoke
- GET  /api/connections/{provider}/grants
- PUT  /api/connections/{provider}/grants/{agent_id}
- DELETE /api/connections/{provider}/grants/{agent_id}
- GET  /api/connections/_debug                → gated by GAIA_DEBUG=1
- Exception → HTTP mapping table
- No refresh_token in any response body
"""

from __future__ import annotations

import os

import pytest

from gaia.connections.providers import _registry
from gaia.connections.store import save_connection


@pytest.fixture(autouse=True)
def google_provider_env(monkeypatch, tmp_path):
    """Provide a configured Google provider + isolated grants dir."""
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setattr("gaia.connections.grants.Path.home", lambda: tmp_path)
    _registry.clear()
    yield


@pytest.fixture
def seeded_connection(google_provider_env):
    from gaia.connections.providers import get as get_provider

    provider = get_provider("google")
    save_connection(
        provider="google",
        account_email="alice@example.com",
        refresh_token="seed-rt",
        scopes=["gmail.readonly"],
        client_id_hash=provider.client_id_hash,
    )


class TestListConnections:
    def test_empty_list(self, ui_api_client):
        resp = ui_api_client.get("/api/connections")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"connections": []}

    def test_seeded_list_no_refresh_token(self, ui_api_client, seeded_connection):
        resp = ui_api_client.get("/api/connections")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["connections"]) == 1
        row = body["connections"][0]
        assert row["provider"] == "google"
        assert row["account_email"] == "alice@example.com"
        assert "refresh_token" not in row


class TestGetConnection:
    def test_missing(self, ui_api_client):
        resp = ui_api_client.get("/api/connections/google")
        assert resp.status_code == 404

    def test_returns_metadata(self, ui_api_client, seeded_connection):
        resp = ui_api_client.get("/api/connections/google")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "google"
        assert "refresh_token" not in body


class TestRevokeConnection:
    def test_revoke_clears_keyring(self, ui_api_client, seeded_connection):
        resp = ui_api_client.delete("/api/connections/google")
        assert resp.status_code == 204
        # Subsequent list returns empty.
        listing = ui_api_client.get("/api/connections").json()
        assert listing["connections"] == []


class TestGrants:
    def test_put_grant_then_get_grants(self, ui_api_client, seeded_connection):
        resp = ui_api_client.put(
            "/api/connections/google/grants/builtin:chat",
            json={"scopes": ["gmail.readonly"]},
        )
        assert resp.status_code == 200

        listing = ui_api_client.get("/api/connections/google/grants").json()
        assert listing == {"grants": {"builtin:chat": ["gmail.readonly"]}}

    def test_delete_grant(self, ui_api_client, seeded_connection):
        ui_api_client.put(
            "/api/connections/google/grants/builtin:chat",
            json={"scopes": ["gmail.readonly"]},
        )
        resp = ui_api_client.delete("/api/connections/google/grants/builtin:chat")
        assert resp.status_code == 204
        listing = ui_api_client.get("/api/connections/google/grants").json()
        assert listing == {"grants": {}}


class TestAuthorizeFlow:
    def test_authorize_returns_flow_id_and_url(self, ui_api_client, monkeypatch):
        # The handler shouldn't actually open a browser during a test.
        monkeypatch.setattr("webbrowser.open", lambda *_, **__: True)

        resp = ui_api_client.post(
            "/api/connections/google/authorize",
            json={"scopes": ["gmail.readonly"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "flow_id" in body
        assert "authorization_url" in body
        assert body["authorization_url"].startswith("https://accounts.google.com/")

        # Cancel the started flow so subsequent tests can start a new one.
        from gaia.connections.flow import _pending

        for fid in list(_pending.keys()):
            ui_api_client.delete(f"/api/connections/_flows/{fid}")


class TestDebugEndpoint:
    def test_debug_endpoint_blocked_when_env_unset(self, ui_api_client, monkeypatch):
        monkeypatch.delenv("GAIA_DEBUG", raising=False)
        resp = ui_api_client.get("/api/connections/_debug")
        assert resp.status_code == 404

    def test_debug_endpoint_returns_state_when_env_set(
        self, ui_api_client, monkeypatch
    ):
        monkeypatch.setenv("GAIA_DEBUG", "1")
        resp = ui_api_client.get("/api/connections/_debug")
        assert resp.status_code == 200
        body = resp.json()
        # The debug payload must name the things you'd check when "Connect
        # button does nothing": provider state, env var, keyring backend,
        # grants path, in-flight flow count.
        assert "provider_registered" in body
        assert "env_var_present" in body
        assert "keyring_backend_class" in body
        assert "grants_path_writable" in body
        assert "in_flight_flow_count" in body


class TestExceptionMapping:
    """Contract: AuthRequiredError.Reason → distinct HTTP status."""

    def test_get_connection_misconfigured_returns_503(self, monkeypatch, ui_api_client):
        # No GAIA_GOOGLE_CLIENT_ID → ConfigurationError → 503.
        # (The autouse fixture above sets it; override here.)
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        _registry.clear()
        resp = ui_api_client.post(
            "/api/connections/google/authorize",
            json={"scopes": ["gmail.readonly"]},
        )
        assert resp.status_code == 503
        body = resp.json()
        assert "GAIA_GOOGLE_CLIENT_ID" in body.get("detail", "")
