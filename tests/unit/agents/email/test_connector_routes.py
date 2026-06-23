# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the flag-gated mailbox-connector routes (email playground).

Two guarantees:
  * the routes are mounted ONLY in playground mode (so a production sidecar
    stays connector-free — milestone 40's "consuming app owns the connection");
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

    def test_unknown_provider_is_404(self, client):
        r = client.post("/v1/email/connectors/yahoo/configure", json={"client_id": "x"})
        assert r.status_code == 404


class TestSidecarGating:
    def _build_app(self, with_connectors: bool):
        # packaging/server.py is a frozen-binary script, not an importable
        # package module — load it by path.
        root = pathlib.Path(__file__).resolve()
        while root.name and not (root / "hub").exists():
            root = root.parent
        srv = root / "hub/agents/python/email/packaging/server.py"
        spec = importlib.util.spec_from_file_location("email_sidecar_server", srv)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.build_app(with_connectors=with_connectors)

    def test_connectors_mounted_only_in_playground_mode(self):
        on = self._build_app(with_connectors=True).openapi()["paths"]
        off = self._build_app(with_connectors=False).openapi()["paths"]
        assert "/v1/email/connectors" in on
        assert "/v1/email/connectors" not in off
        # The core email surface is present either way.
        assert "/v1/email/triage" in off
