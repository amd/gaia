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

from dataclasses import dataclass, field
from typing import List
from unittest.mock import MagicMock

import httpx
import pytest
import respx

UI_HEADER = {"x-gaia-ui": "1"}

FWD_CLIENT_ID = "forwarded-host-app.apps.googleusercontent.com"
FWD_CLIENT_SECRET = "FWD-SECRET-do-not-leak"
FWD_REFRESH = "FWD-REFRESH-TOKEN-do-not-leak"
# Includes all 4 scopes declared in EmailTriageAgent.REQUIRED_CONNECTORS (ALL_SCOPES).
# The router now resolves required_scopes from the granted agents' REQUIRED_CONNECTORS,
# so FULL_SCOPES must be a superset of that union for the forward to succeed.
FULL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
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


# ─── Helpers for provider-aware scope tests ───────────────────────────────────

MS_CLIENT_ID = "ms-app-client-id"
MS_CLIENT_SECRET = "ms-secret"
MS_REFRESH = "ms-refresh-token"
MS_SCOPES = [
    "openid",
    "offline_access",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Calendars.ReadWrite",
]


@pytest.fixture
def ms_provider(monkeypatch):
    """Inject a minimal fake Microsoft OAuth provider so the registry lookup
    succeeds without real env vars."""
    import zlib

    fake = MagicMock()
    fake.client_id = MS_CLIENT_ID
    fake.client_id_hash = format(zlib.crc32(MS_CLIENT_ID.encode()), "08x")

    from gaia.connectors.providers import _registry

    _registry["microsoft"] = fake
    monkeypatch.setenv("GAIA_MICROSOFT_CLIENT_ID", MS_CLIENT_ID)
    yield fake
    _registry.pop("microsoft", None)


def _ms_forward_body(**overrides):
    body = {
        "client_id": MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "refresh_token": MS_REFRESH,
        "scopes": MS_SCOPES,
        "account_email": "user@outlook.com",
        "grant_agents": [],
    }
    body.update(overrides)
    return body


def _make_fake_registry(provider: str, scopes: list[str], nsid: str = "builtin:test"):
    """Return an object that mimics AgentRegistry.list() for the router's
    scope-resolution code (``request.app.state.agent_registry``)."""
    from gaia.connectors.providers.base import ConnectorRequirement

    @dataclass
    class FakeReg:
        namespaced_agent_id: str
        required_connections: List[ConnectorRequirement] = field(default_factory=list)

    @dataclass
    class FakeRegistry:
        _regs: List[FakeReg]

        def list(self):
            return self._regs

    cr = ConnectorRequirement(connector_id=provider, scopes=scopes)
    return FakeRegistry(
        _regs=[FakeReg(namespaced_agent_id=nsid, required_connections=[cr])]
    )


# ─── New test classes ─────────────────────────────────────────────────────────


@pytest.mark.skip(
    reason=(
        "Microsoft OAuth provider not in this branch — requires the Outlook backend "
        "from PR #1358/#1275.  End-to-end Microsoft forward is validated against "
        "strx-halo once that PR is merged into the integration branch."
    )
)
class TestMicrosoftForward:
    """Microsoft connections must forward without demanding Gmail scopes.

    Skipped in this branch because the MicrosoftOAuthProvider is not yet
    registered in ``gaia.connectors.providers`` here — it lives in PR #1358.
    The unit-level proof (``TestProviderAwareScopeDefaults`` in
    ``test_forwarded_import.py``) covers the scope-default logic without needing
    the provider; this class covers the full HTTP path and should run after merge.
    """

    def test_microsoft_forward_returns_201(self, ui_api_client, ms_provider):
        resp = ui_api_client.post(
            "/v1/connections/microsoft",
            json=_ms_forward_body(),
            headers=UI_HEADER,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["provider"] == "microsoft"
        assert "refresh_token" not in data
        assert "client_secret" not in data

    def test_microsoft_listed_after_forward(self, ui_api_client, ms_provider):
        ui_api_client.post(
            "/v1/connections/microsoft",
            json=_ms_forward_body(),
            headers=UI_HEADER,
        )
        resp = ui_api_client.get("/v1/connections")
        assert resp.status_code == 200
        providers = [c["provider"] for c in resp.json()["connections"]]
        assert "microsoft" in providers


class TestRouterDrivenScopeResolution:
    """The router must resolve required scopes from the granted agents'
    REQUIRED_CONNECTORS.  When the forwarded scopes don't cover the agent's
    declared requirements, the forward fails loudly with 403 scope_mismatch."""

    def test_scope_mismatch_via_registry_fails_with_403(self, ui_api_client):
        """Inject a registry whose builtin:test agent requires
        gmail.modify for Google.  Forward Google scopes that exclude
        gmail.modify → should raise scope_mismatch via the router resolution."""
        fake_registry = _make_fake_registry(
            provider="google",
            scopes=["https://www.googleapis.com/auth/gmail.modify"],
            nsid="builtin:test",
        )
        ui_api_client.app.state.agent_registry = fake_registry

        resp = ui_api_client.post(
            "/v1/connections/google",
            json=_forward_body(
                scopes=["openid"],  # does NOT include gmail.modify
                grant_agents=["builtin:test"],
            ),
            headers=UI_HEADER,
        )
        assert resp.status_code == 403, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "scope_mismatch"
        assert any("gmail.modify" in s for s in detail["missing_scopes"])

    def test_scope_satisfied_via_registry_returns_201(self, ui_api_client):
        """When the forwarded scopes cover the agent's declared requirements,
        the forward succeeds even though the default map would demand more."""
        fake_registry = _make_fake_registry(
            provider="google",
            scopes=["https://www.googleapis.com/auth/gmail.modify"],
            nsid="builtin:test",
        )
        ui_api_client.app.state.agent_registry = fake_registry

        resp = ui_api_client.post(
            "/v1/connections/google",
            json=_forward_body(
                scopes=[
                    "https://www.googleapis.com/auth/gmail.modify",
                    "openid",
                ],
                grant_agents=["builtin:test"],
            ),
            headers=UI_HEADER,
        )
        assert resp.status_code == 201, resp.text

    def test_no_registry_no_grant_agents_accepts_any_scopes(self, ui_api_client):
        """When app.state has no agent_registry AND grant_agents is empty,
        required_scopes=[] is passed to api.py.  An explicit [] means
        "require nothing at import time" — the forward succeeds regardless
        of what scopes were provided.  Use-time gates (get_access_token)
        still enforce coverage when an agent actually requests a token."""
        # Ensure no registry on app.state.
        if hasattr(ui_api_client.app.state, "agent_registry"):
            del ui_api_client.app.state.agent_registry

        resp = ui_api_client.post(
            "/v1/connections/google",
            json=_forward_body(
                scopes=["openid"],
                grant_agents=[],  # no agents → required resolves to []
            ),
            headers=UI_HEADER,
        )
        # required_scopes=[] → api.py honours empty list → 201.
        assert resp.status_code == 201, resp.text
