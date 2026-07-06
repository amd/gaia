# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for Fix 2 (scope union on configure) and Fix 3 (can_send badge) (#1877).

Fix 2 — configure_email_connector with no scopes must pass
  default_scopes ∪ mail_scopes into the connector framework (so the OAuth
  flow requests mail-send permission, not just identity).

Fix 3 — list_email_connectors must include a per-provider ``can_send`` bool
  that is True only when the installed:email grant contains the provider's
  send scope.
"""

from __future__ import annotations

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


class TestConfigureEmailConnectorScopeUnion:
    """Fix 2: when body.scopes is None, configure passes default_scopes ∪ mail_scopes."""

    def _capture_configure(self, monkeypatch):
        """Monkeypatch gaia.connectors.handler.configure to capture the config."""
        captured = {}

        import gaia.connectors.handler as handler

        async def fake_configure(connector_id, config):
            captured.update(connector_id=connector_id, config=config)
            return {"flow_id": "f1", "authorization_url": "https://example.com/auth"}

        monkeypatch.setattr(handler, "configure", fake_configure)
        return captured

    def test_google_no_scopes_includes_gmail_send(self, client, monkeypatch):
        """configure with no scopes → gmail.send present in passed scopes."""
        captured = self._capture_configure(monkeypatch)

        r = client.post(
            "/v1/email/connectors/google/configure",
            json={"client_id": "cid", "client_secret": "sec"},
        )
        assert r.status_code == 200, r.text
        scopes = captured["config"].get("scopes", [])
        assert (
            "https://www.googleapis.com/auth/gmail.send" in scopes
        ), f"gmail.send missing from scopes passed to framework: {scopes}"

    def test_google_no_scopes_retains_identity_scopes(self, client, monkeypatch):
        """configure with no scopes → openid / offline_access still present."""
        import gaia.connectors.catalog  # noqa: F401 — populates registry
        from gaia.connectors.registry import REGISTRY

        captured = self._capture_configure(monkeypatch)
        r = client.post(
            "/v1/email/connectors/google/configure",
            json={"client_id": "cid", "client_secret": "sec"},
        )
        assert r.status_code == 200, r.text
        scopes = captured["config"].get("scopes", [])
        spec = REGISTRY.get("google")
        if spec and spec.default_scopes:
            for s in spec.default_scopes:
                assert s in scopes, f"default scope {s!r} missing; got: {scopes}"

    def test_microsoft_no_scopes_includes_mail_send(self, client, monkeypatch):
        """configure microsoft with no scopes → Mail.Send present."""
        captured = self._capture_configure(monkeypatch)

        r = client.post(
            "/v1/email/connectors/microsoft/configure",
            json={"client_id": "cid", "client_secret": ""},
        )
        assert r.status_code == 200, r.text
        scopes = captured["config"].get("scopes", [])
        assert (
            "https://graph.microsoft.com/Mail.Send" in scopes
        ), f"Mail.Send missing from scopes passed to framework: {scopes}"

    def test_explicit_scopes_honored_unchanged(self, client, monkeypatch):
        """When body.scopes is provided, the framework receives them unchanged."""
        captured = self._capture_configure(monkeypatch)
        custom = ["https://www.googleapis.com/auth/gmail.readonly"]

        r = client.post(
            "/v1/email/connectors/google/configure",
            json={"client_id": "cid", "client_secret": "sec", "scopes": custom},
        )
        assert r.status_code == 200, r.text
        assert captured["config"].get("scopes") == custom

    def test_no_duplicate_scopes_in_union(self, client, monkeypatch):
        """Union must not introduce duplicates if a mail scope is already in default_scopes."""
        captured = self._capture_configure(monkeypatch)

        client.post(
            "/v1/email/connectors/google/configure",
            json={"client_id": "cid", "client_secret": "sec"},
        )
        scopes = captured["config"].get("scopes", [])
        assert len(scopes) == len(
            set(scopes)
        ), f"Duplicate scopes in the union: {scopes}"


class TestListEmailConnectorsCanSend:
    """Fix 3: list_email_connectors must include can_send per provider."""

    def _make_client(self) -> TestClient:
        from gaia_agent_email.connector_routes import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_can_send_false_when_no_grant(self, monkeypatch):
        """Provider connected but has no installed:email grant → can_send=False."""
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: ["google"])
        monkeypatch.setattr(
            capi, "get_connection", lambda p: {"scopes": [], "account_email": None}
        )
        monkeypatch.setattr(capi, "list_agent_grants", lambda p: {})

        client = self._make_client()
        body = client.get("/v1/email/connectors").json()
        by = {p["provider"]: p for p in body["providers"]}
        assert by["google"]["connected"] is True
        assert by["google"]["can_send"] is False

    def test_can_send_false_for_identity_only_grant(self, monkeypatch):
        """Grant exists but only has openid/email scopes, not gmail.send → can_send=False."""
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: ["google"])
        monkeypatch.setattr(
            capi,
            "get_connection",
            lambda p: {"scopes": [], "account_email": "me@g.com"},
        )
        monkeypatch.setattr(
            capi,
            "list_agent_grants",
            lambda p: {"installed:email": ["openid", "email", "profile"]},
        )

        client = self._make_client()
        body = client.get("/v1/email/connectors").json()
        by = {p["provider"]: p for p in body["providers"]}
        assert by["google"]["can_send"] is False

    def test_can_send_true_when_gmail_send_in_grant(self, monkeypatch):
        """Grant contains gmail.send → can_send=True."""
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: ["google"])
        monkeypatch.setattr(
            capi,
            "get_connection",
            lambda p: {"scopes": [], "account_email": "me@g.com"},
        )
        monkeypatch.setattr(
            capi,
            "list_agent_grants",
            lambda p: {
                "installed:email": [
                    "openid",
                    "https://www.googleapis.com/auth/gmail.send",
                    "https://www.googleapis.com/auth/gmail.modify",
                ]
            },
        )

        client = self._make_client()
        body = client.get("/v1/email/connectors").json()
        by = {p["provider"]: p for p in body["providers"]}
        assert by["google"]["can_send"] is True

    def test_can_send_true_when_mail_send_in_outlook_grant(self, monkeypatch):
        """Outlook grant with Mail.Send → can_send=True."""
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: ["microsoft"])
        monkeypatch.setattr(
            capi,
            "get_connection",
            lambda p: {"scopes": [], "account_email": "me@live.com"},
        )
        monkeypatch.setattr(
            capi,
            "list_agent_grants",
            lambda p: {
                "installed:email": [
                    "https://graph.microsoft.com/Mail.Send",
                    "https://graph.microsoft.com/Mail.ReadWrite",
                ]
            },
        )

        client = self._make_client()
        body = client.get("/v1/email/connectors").json()
        by = {p["provider"]: p for p in body["providers"]}
        assert by["microsoft"]["can_send"] is True

    def test_can_send_false_for_microsoft_without_mail_send(self, monkeypatch):
        """Outlook grant missing Mail.Send → can_send=False."""
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: ["microsoft"])
        monkeypatch.setattr(
            capi,
            "get_connection",
            lambda p: {"scopes": [], "account_email": "me@live.com"},
        )
        monkeypatch.setattr(
            capi,
            "list_agent_grants",
            lambda p: {
                "installed:email": [
                    "https://graph.microsoft.com/Mail.ReadWrite",
                ]
            },
        )

        client = self._make_client()
        body = client.get("/v1/email/connectors").json()
        by = {p["provider"]: p for p in body["providers"]}
        assert by["microsoft"]["can_send"] is False

    def test_list_does_not_500_when_list_agent_grants_raises(self, monkeypatch):
        """Defensive: if list_agent_grants raises, can_send=False, no 500."""
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: ["google"])
        monkeypatch.setattr(
            capi, "get_connection", lambda p: {"scopes": [], "account_email": None}
        )
        monkeypatch.setattr(
            capi,
            "list_agent_grants",
            lambda p: (_ for _ in ()).throw(RuntimeError("store unavailable")),
        )

        client = self._make_client()
        resp = client.get("/v1/email/connectors")
        assert resp.status_code == 200, resp.text
        by = {p["provider"]: p for p in resp.json()["providers"]}
        assert by["google"]["can_send"] is False

    def test_can_send_field_present_for_all_providers(self, monkeypatch):
        """Every provider entry must have a can_send field."""
        import gaia.connectors.api as capi

        monkeypatch.setattr(capi, "connected_mailbox_providers", lambda: [])
        monkeypatch.setattr(capi, "get_connection", lambda p: None)
        monkeypatch.setattr(capi, "list_agent_grants", lambda p: {})

        client = self._make_client()
        body = client.get("/v1/email/connectors").json()
        for p in body["providers"]:
            assert "can_send" in p, f"can_send missing for provider {p['provider']}"
