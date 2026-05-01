# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-6 router tests — /api/connectors/* endpoints.

Coverage:
- GET /api/connectors → returns catalog list with state
- GET /api/connectors/{connector_id} → returns one entry
- POST /api/connectors/{connector_id}/configure → CSRF + dispatches configure
- DELETE /api/connectors/{connector_id} → CSRF + dispatches disconnect
- POST /api/connectors/{connector_id}/test → CSRF + dispatches health_check
- PUT/DELETE /api/connectors/{connector_id}/grants/{agent_id} → CSRF guarded
- CSRF guard: mutating routes reject missing header with 403
- Unknown connector → 404
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UI_HEADER = {"x-gaia-ui": "1"}


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch, tmp_path):
    """Each test gets a fresh REGISTRY and isolated grants/state dirs."""
    from gaia.connectors.registry import ConnectorRegistry
    from gaia.connectors.spec import ConnectorSpec

    fresh = ConnectorRegistry()
    spec = ConnectorSpec(
        id="google",
        display_name="Google",
        icon="G",
        category="productivity",
        tier=1,
        type="oauth_pkce",
        description="Google OAuth",
        default_scopes=("openid",),
        oauth_provider_ref="google",
    )
    fresh.register(spec)

    monkeypatch.setattr("gaia.ui.routers.connectors.REGISTRY", fresh)
    monkeypatch.setattr("gaia.connectors.handler.REGISTRY", fresh)
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.state.Path.home", lambda: tmp_path)
    yield fresh


# ---------------------------------------------------------------------------
# GET /api/connectors — catalog list
# ---------------------------------------------------------------------------


class TestListConnectors:
    def test_returns_connectors_key(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors")
        assert resp.status_code == 200
        body = resp.json()
        assert "connectors" in body
        assert isinstance(body["connectors"], list)

    def test_catalog_entry_shape(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors")
        assert resp.status_code == 200
        entry = next(
            (e for e in resp.json()["connectors"] if e["id"] == "google"), None
        )
        assert entry is not None
        assert entry["type"] == "oauth_pkce"
        assert "configured" in entry
        assert "description" in entry

    def test_unconfigured_connector_shows_configured_false(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors")
        entry = next(e for e in resp.json()["connectors"] if e["id"] == "google")
        assert entry["configured"] is False


class TestConfigurableField:
    """The summary endpoint surfaces ``configurable`` + ``config_error``
    so the AgentUI can render a friendly "needs setup" tile when the
    OAuth provider can't be instantiated (typically because
    ``GAIA_GOOGLE_CLIENT_ID`` isn't set), instead of letting the user
    click Connect and then surfacing a raw 503.
    """

    def test_configurable_false_when_provider_missing_env(
        self, ui_api_client, monkeypatch
    ):
        # Ensure the provider can't init: clear the env var AND the
        # cached provider instance from any earlier test that primed it.
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        from gaia.connectors.providers import _registry

        _registry.pop("google", None)

        resp = ui_api_client.get("/api/connectors/google")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configurable"] is False
        assert "GAIA_GOOGLE_CLIENT_ID" in (body["config_error"] or "")

    def test_configurable_true_when_provider_ok(self, ui_api_client, monkeypatch):
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
        from gaia.connectors.providers import _registry

        _registry.pop("google", None)

        resp = ui_api_client.get("/api/connectors/google")
        body = resp.json()
        assert body["configurable"] is True
        assert body["config_error"] is None


# ---------------------------------------------------------------------------
# GET /api/connectors/{connector_id}
# ---------------------------------------------------------------------------


class TestGetConnector:
    def test_known_connector_returns_200(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors/google")
        assert resp.status_code == 200
        assert resp.json()["id"] == "google"

    def test_unknown_connector_returns_404(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CSRF guard — mutating routes require X-Gaia-UI: 1
# ---------------------------------------------------------------------------


class TestCsrfGuard:
    def test_configure_without_header_is_403(self, ui_api_client):
        resp = ui_api_client.post(
            "/api/connectors/google/configure", json={"config": {}}
        )
        assert resp.status_code == 403

    def test_disconnect_without_header_is_403(self, ui_api_client):
        resp = ui_api_client.delete("/api/connectors/google")
        assert resp.status_code == 403

    def test_test_without_header_is_403(self, ui_api_client):
        resp = ui_api_client.post("/api/connectors/google/test")
        assert resp.status_code == 403

    def test_grant_put_without_header_is_403(self, ui_api_client):
        resp = ui_api_client.put(
            "/api/connectors/google/grants/builtin:chat",
            json={"scopes": []},
        )
        assert resp.status_code == 403

    def test_grant_delete_without_header_is_403(self, ui_api_client):
        resp = ui_api_client.delete(
            "/api/connectors/google/grants/builtin:chat",
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/connectors/{connector_id}/configure
# ---------------------------------------------------------------------------


class TestConfigureEndpoint:
    def test_configure_dispatches_to_handler(self, ui_api_client, monkeypatch):
        mock_configure = AsyncMock(return_value={"configured": True, "flow_id": "f1"})
        monkeypatch.setattr("gaia.ui.routers.connectors.configure", mock_configure)

        resp = ui_api_client.post(
            "/api/connectors/google/configure",
            json={"config": {"scopes": ["openid"]}},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json().get("configured") is True

    def test_unknown_connector_is_404(self, ui_api_client, monkeypatch):
        monkeypatch.setattr(
            "gaia.ui.routers.connectors.configure",
            AsyncMock(side_effect=KeyError("nope")),
        )
        resp = ui_api_client.post(
            "/api/connectors/missing/configure",
            json={"config": {}},
            headers=UI_HEADER,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/connectors/{connector_id}/test
# ---------------------------------------------------------------------------


class TestTestEndpoint:
    def test_test_dispatches_to_handler(self, ui_api_client, monkeypatch):
        monkeypatch.setattr(
            "gaia.ui.routers.connectors.health_check",
            AsyncMock(return_value={"ok": True, "detail": "token_valid"}),
        )
        resp = ui_api_client.post(
            "/api/connectors/google/test",
            headers=UI_HEADER,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# DELETE /api/connectors/{connector_id}
# ---------------------------------------------------------------------------


class TestDisconnectEndpoint:
    def test_disconnect_returns_204(self, ui_api_client, monkeypatch):
        monkeypatch.setattr(
            "gaia.ui.routers.connectors.disconnect",
            AsyncMock(return_value=None),
        )
        resp = ui_api_client.delete(
            "/api/connectors/google",
            headers=UI_HEADER,
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Grants endpoints
# ---------------------------------------------------------------------------


class TestGrantsEndpoints:
    def test_get_grants_returns_grants_key(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors/google/grants")
        assert resp.status_code == 200
        assert "grants" in resp.json()

    def test_put_grant_with_header_succeeds(self, ui_api_client):
        resp = ui_api_client.put(
            "/api/connectors/google/grants/builtin:chat",
            json={"scopes": ["openid"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["connector_id"] == "google"
        assert "openid" in body["scopes"]

    def test_delete_grant_with_header_succeeds(self, ui_api_client):
        resp = ui_api_client.delete(
            "/api/connectors/google/grants/builtin:chat",
            headers=UI_HEADER,
        )
        assert resp.status_code == 204
