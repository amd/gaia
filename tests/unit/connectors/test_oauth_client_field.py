# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Settings OAuth-client field — #2104 interim deliverable.

The Settings tile previously only offered a client-credentials form in the
never-configured state; every other state (client from env, connected
account with a lost client, rotation) dead-ended with no field. These
tests cover the two backend pieces the always-available editor rides on:

- ``POST /{id}/configure`` with ``save_only`` persists the OAuth client to
  the same keyring slot the ``gaia connectors configure`` CLI writes —
  WITHOUT starting an OAuth flow — and never echoes the secret.
- The connector summary's ``oauth_client`` block reports which client the
  provider resolves (keyring / env / none) and never includes the secret
  value, only whether one is stored.
"""

from __future__ import annotations

import pytest

UI_HEADER = {"x-gaia-ui": "1"}


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Fresh REGISTRY with one OAuth and one MCP spec (mirrors
    test_router_connectors.py) so tests don't depend on the shipped catalog."""
    from gaia.connectors.registry import ConnectorRegistry
    from gaia.connectors.spec import ConnectorSpec

    fresh = ConnectorRegistry()
    fresh.register(
        ConnectorSpec(
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
    )
    fresh.register(
        ConnectorSpec(
            id="mcp-test",
            display_name="MCP Test",
            icon="M",
            category="dev-tools",
            tier=1,
            type="mcp_server",
            description="Stub MCP server",
            mcp_command="true",
            mcp_args=(),
        )
    )
    monkeypatch.setattr("gaia.ui.routers.connectors.REGISTRY", fresh)
    monkeypatch.setattr("gaia.connectors.handler.REGISTRY", fresh)
    monkeypatch.setattr("gaia.connectors.registry.REGISTRY", fresh)
    yield fresh


class TestConfigureSaveOnly:
    def test_save_only_persists_client_without_flow(self, ui_api_client, monkeypatch):
        async def _explode(*_a, **_k):  # pragma: no cover — must not run
            raise AssertionError("save_only must not start an OAuth flow")

        monkeypatch.setattr("gaia.connectors.oauth_pkce.start_authorization", _explode)
        resp = ui_api_client.post(
            "/api/connectors/google/configure",
            json={
                "config": {
                    "client_id": "abc.apps.example",
                    "client_secret": "SECRET-VALUE",
                    "save_only": True,
                }
            },
            headers=UI_HEADER,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "saved"
        assert "SECRET-VALUE" not in resp.text

        from gaia.connectors.store import peek_provider_credentials

        creds = peek_provider_credentials("google")
        assert creds == {
            "client_id": "abc.apps.example",
            "client_secret": "SECRET-VALUE",
        }

    def test_save_only_without_client_id_fails_loudly(self, ui_api_client):
        resp = ui_api_client.post(
            "/api/connectors/google/configure",
            json={"config": {"save_only": True}},
            headers=UI_HEADER,
        )
        assert resp.status_code == 503
        assert "client_id" in resp.json()["detail"]


class TestSummaryOAuthClient:
    def test_keyring_source(self, ui_api_client, monkeypatch):
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        from gaia.connectors.store import save_provider_credentials

        save_provider_credentials(
            "google", client_id="stored.apps.example", client_secret="SECRET-VALUE"
        )
        resp = ui_api_client.get("/api/connectors/google")
        assert resp.status_code == 200
        client = resp.json()["oauth_client"]
        assert client == {
            "source": "keyring",
            "client_id": "stored.apps.example",
            "has_secret": True,
        }
        assert "SECRET-VALUE" not in resp.text

    def test_env_source(self, ui_api_client, monkeypatch):
        monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "env.apps.example")
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)
        resp = ui_api_client.get("/api/connectors/google")
        client = resp.json()["oauth_client"]
        assert client == {
            "source": "env",
            "client_id": "env.apps.example",
            "has_secret": False,
        }

    def test_no_client_configured(self, ui_api_client, monkeypatch):
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GAIA_GOOGLE_CLIENT_SECRET", raising=False)
        resp = ui_api_client.get("/api/connectors/google")
        client = resp.json()["oauth_client"]
        assert client == {"source": None, "client_id": None, "has_secret": False}

    def test_mcp_connector_has_no_oauth_client(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors/mcp-test")
        assert resp.json()["oauth_client"] is None
