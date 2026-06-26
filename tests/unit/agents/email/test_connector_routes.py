# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the mailbox-connector routes (email playground).

Two guarantees:
  * the routes are always mounted on the sidecar but excluded from the OpenAPI
    contract (a playground convenience, not part of the frozen email REST API);
  * each route delegates correctly to GAIA's connector framework, including the
    grant to ``installed:email`` that makes a fresh connection usable by send.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("gaia_agent_email")

from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from gaia_agent_email.connector_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestConnectorRoutes:
    def test_list_reports_per_provider_status(self, client, monkeypatch):
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: ["google"])
        monkeypatch.setattr(
            capi,
            "get_connection",
            lambda p: (
                {"account_email": "me@gmail.com", "scopes": ["s1"]}
                if p == "google"
                else None
            ),
        )
        body = client.get("/v1/email/connectors").json()
        assert body["agent_id"] == "installed:email"
        by = {p["provider"]: p for p in body["providers"]}
        assert by["google"]["connected"] is True
        assert by["google"]["account_email"] == "me@gmail.com"
        assert by["microsoft"]["connected"] is False

    def test_default_account_sentinel_is_hidden(self, client, monkeypatch):
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: ["microsoft"])
        monkeypatch.setattr(
            capi,
            "get_connection",
            lambda p: (
                {"account_email": "default", "scopes": []} if p == "microsoft" else None
            ),
        )
        by = {
            p["provider"]: p
            for p in client.get("/v1/email/connectors").json()["providers"]
        }
        # "default" is the store's no-email sentinel — never surfaced as an email.
        assert by["microsoft"]["connected"] is True
        assert by["microsoft"]["account_email"] is None

    def test_configure_starts_the_oauth_flow(self, client, monkeypatch):
        import gaia.connectors.handler as handler

        async def fake_configure(connector_id, config):
            assert connector_id == "google"
            assert config["client_id"] == "cid"
            return {
                "flow_id": "f1",
                "authorization_url": "https://accounts.google.com/x",
            }

        monkeypatch.setattr(handler, "configure", fake_configure)
        r = client.post(
            "/v1/email/connectors/google/configure",
            json={"client_id": "cid", "client_secret": "sec"},
        )
        assert r.status_code == 200
        assert r.json()["flow_id"] == "f1"

    def test_complete_grants_connection_to_email_agent(self, client, monkeypatch):
        import gaia.connectors.api as capi

        granted = {}

        async def fake_complete(flow_id):
            assert flow_id == "f1"
            return {
                "provider": "google",
                "account_email": "me@gmail.com",
                "scopes": ["s1", "s2"],
            }

        def fake_grant(connector_id, agent_id, scopes):
            granted.update(connector_id=connector_id, agent_id=agent_id, scopes=scopes)

        monkeypatch.setattr(capi, "complete_authorization", fake_complete)
        monkeypatch.setattr(capi, "grant_agent", fake_grant)
        r = client.post("/v1/email/connectors/google/complete", json={"flow_id": "f1"})
        assert r.status_code == 200
        assert r.json()["connected"] is True
        # The grant is what makes the connection usable by /v1/email/send.
        assert granted == {
            "connector_id": "google",
            "agent_id": "installed:email",
            "scopes": ["s1", "s2"],
        }

    def test_complete_hides_default_account_sentinel(self, client, monkeypatch):
        import gaia.connectors.api as capi

        async def fake_complete(flow_id):
            return {
                "provider": "microsoft",
                "account_email": "default",
                "scopes": ["s1"],
            }

        monkeypatch.setattr(capi, "complete_authorization", fake_complete)
        monkeypatch.setattr(capi, "grant_agent", lambda *a, **k: None)
        body = client.post(
            "/v1/email/connectors/microsoft/complete", json={"flow_id": "f1"}
        ).json()
        # The store's "default" no-email sentinel is normalized server-side.
        assert body["connected"] is True
        assert body["account_email"] is None

    def test_unknown_provider_is_404(self, client):
        r = client.post("/v1/email/connectors/yahoo/configure", json={"client_id": "x"})
        assert r.status_code == 404

    def test_disconnect_removes_tokens_and_grants(self, client, monkeypatch):
        import gaia.connectors.handler as handler

        called = {}

        async def fake_disconnect(connector_id, **kw):
            called["id"] = connector_id

        # Uses the framework's full disconnect (tokens + per-agent grants), not a
        # bare connection delete — so a reconnect can't inherit stale consent.
        monkeypatch.setattr(handler, "disconnect", fake_disconnect)
        r = client.delete("/v1/email/connectors/google")
        assert r.status_code == 200
        assert r.json() == {"provider": "google", "connected": False}
        assert called["id"] == "google"


class TestSidecarMount:
    def _build_app(self):
        # packaging/server.py is a frozen-binary script, not an importable
        # package module — load it by path.
        root = pathlib.Path(__file__).resolve()
        while root.name and not (root / "hub").exists():
            root = root.parent
        srv = root / "hub/agents/python/email/packaging/server.py"
        spec = importlib.util.spec_from_file_location("email_sidecar_server", srv)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.build_app()

    def test_connectors_always_mounted(self, monkeypatch):
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: [])
        monkeypatch.setattr(capi, "get_connection", lambda p: None)
        client = TestClient(self._build_app())
        # Always mounted (never 404) and the core surface is intact.
        assert client.get("/v1/email/connectors").status_code == 200
        assert client.get("/v1/email/playground").status_code == 200
        assert client.post("/v1/email/triage", json={}).status_code == 422

    def test_connectors_excluded_from_openapi_contract(self):
        # Playground convenience, not part of the frozen REST contract.
        paths = (
            TestClient(self._build_app()).get("/openapi.json").json().get("paths", {})
        )
        assert "/v1/email/connectors" not in paths
        assert "/v1/email/triage" in paths
