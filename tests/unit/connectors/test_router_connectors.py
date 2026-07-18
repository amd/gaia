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

from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UI_HEADER = {"x-gaia-ui": "1"}


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch, tmp_path):
    """Each test gets a fresh REGISTRY and isolated grants/state dirs.

    Registers two specs so tests can exercise both the OAuth path
    (``google``) and the MCP-server path (``mcp-test``) without colliding.
    Activations endpoints accept only ``mcp_server`` connectors (#1005),
    so tests targeting that ledger should use ``mcp-test``.
    """
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
            description="Stub MCP server for router tests",
            mcp_command="true",
            mcp_args=(),
        )
    )

    monkeypatch.setattr("gaia.ui.routers.connectors.REGISTRY", fresh)
    monkeypatch.setattr("gaia.connectors.handler.REGISTRY", fresh)
    # ``connectors.api._require_mcp_server_for_activation`` imports REGISTRY
    # lazily from its canonical home so the type guard applies to CLI/SDK
    # callers too — patch the canonical name so all three surfaces share
    # the same fresh catalog under test.
    monkeypatch.setattr("gaia.connectors.registry.REGISTRY", fresh)
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.activations.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
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


class TestOauthSetupFieldsExposed:
    """The summary surfaces ``oauth_setup_fields`` so the AgentUI can
    render a first-time setup form when ``configurable=false``. This is
    the user-facing self-onboarding path that replaces the env-var
    requirement."""

    def test_default_summary_omits_setup_fields_for_bare_spec(self, ui_api_client):
        # The fixture spec has no oauth_setup_fields (empty default).
        resp = ui_api_client.get("/api/connectors/google")
        assert resp.status_code == 200
        body = resp.json()
        assert "oauth_setup_fields" in body
        assert body["oauth_setup_fields"] == []

    def test_setup_fields_serialised_with_metadata(
        self, ui_api_client, isolated_registry
    ):
        # Replace the fixture spec with one that declares setup fields.
        from gaia.connectors.spec import ConfigField, ConnectorSpec

        spec_with_fields = ConnectorSpec(
            id="google",
            display_name="Google",
            icon="G",
            category="productivity",
            tier=1,
            type="oauth_pkce",
            description="Google OAuth",
            default_scopes=("openid",),
            oauth_provider_ref="google",
            oauth_setup_fields=(
                ConfigField(
                    key="client_id",
                    label="OAuth Client ID",
                    kind="text",
                    help_md="from Cloud Console",
                ),
                ConfigField(
                    key="client_secret",
                    label="OAuth Client Secret",
                    kind="secret",
                ),
            ),
        )
        # Replace and re-register.
        from gaia.connectors.registry import ConnectorRegistry

        fresh = ConnectorRegistry()
        fresh.register(spec_with_fields)
        # Substitute the registry the router reads.
        import gaia.ui.routers.connectors as router_mod

        router_mod.REGISTRY = fresh

        resp = ui_api_client.get("/api/connectors/google")
        body = resp.json()
        fields = body["oauth_setup_fields"]
        assert len(fields) == 2
        assert fields[0]["key"] == "client_id"
        assert fields[0]["kind"] == "text"
        assert fields[0]["help_md"] == "from Cloud Console"
        assert fields[1]["key"] == "client_secret"
        assert fields[1]["kind"] == "secret"


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
# POST /api/connectors/{connector_id}/authorize — grant-on-connect (#2117)
# ---------------------------------------------------------------------------


def _fake_registry(nsid: str, connector_id: str, scopes: list[str]):
    """Minimal AgentRegistry stand-in for the router's scope resolution."""
    from dataclasses import dataclass, field
    from typing import List

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

    cr = ConnectorRequirement(connector_id=connector_id, scopes=scopes)
    return FakeRegistry(
        _regs=[FakeReg(namespaced_agent_id=nsid, required_connections=[cr])]
    )


class TestAuthorizeGrantAgents:
    _SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

    def test_authorize_resolves_and_passes_grant_agents(
        self, ui_api_client, monkeypatch
    ):
        mock_start = AsyncMock(
            return_value={"flow_id": "f1", "authorization_url": "https://auth"}
        )
        monkeypatch.setattr("gaia.connectors.start_authorization", mock_start)
        ui_api_client.app.state.agent_registry = _fake_registry(
            "installed:email", "google", self._SCOPES
        )

        resp = ui_api_client.post(
            "/api/connectors/google/authorize",
            json={"scopes": ["openid"], "grant_agents": ["installed:email"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200, resp.text
        # The resolved {agent_id: scopes} map is threaded into the flow.
        _, kwargs = mock_start.call_args
        assert kwargs["grant_agents"] == {"installed:email": self._SCOPES}

    def test_authorize_without_grant_agents_passes_none(
        self, ui_api_client, monkeypatch
    ):
        mock_start = AsyncMock(
            return_value={"flow_id": "f1", "authorization_url": "https://auth"}
        )
        monkeypatch.setattr("gaia.connectors.start_authorization", mock_start)

        resp = ui_api_client.post(
            "/api/connectors/google/authorize",
            json={"scopes": ["openid"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200, resp.text
        _, kwargs = mock_start.call_args
        assert kwargs["grant_agents"] is None

    def test_authorize_unknown_agent_is_404(self, ui_api_client, monkeypatch):
        monkeypatch.setattr("gaia.connectors.start_authorization", AsyncMock())
        ui_api_client.app.state.agent_registry = _fake_registry(
            "installed:email", "google", self._SCOPES
        )
        resp = ui_api_client.post(
            "/api/connectors/google/authorize",
            json={"scopes": ["openid"], "grant_agents": ["installed:ghost"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 404

    def test_authorize_agent_declares_no_scopes_is_400(
        self, ui_api_client, monkeypatch
    ):
        monkeypatch.setattr("gaia.connectors.start_authorization", AsyncMock())
        # The agent declares microsoft, not google — no scopes for this connector.
        ui_api_client.app.state.agent_registry = _fake_registry(
            "installed:email", "microsoft", self._SCOPES
        )
        resp = ui_api_client.post(
            "/api/connectors/google/authorize",
            json={"scopes": ["openid"], "grant_agents": ["installed:email"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 400

    def test_configure_resolves_grant_agents_into_config(
        self, ui_api_client, monkeypatch
    ):
        captured = {}

        async def fake_configure(connector_id, config):
            captured["config"] = config
            return {"configured": True, "flow_id": "f1"}

        monkeypatch.setattr("gaia.ui.routers.connectors.configure", fake_configure)
        ui_api_client.app.state.agent_registry = _fake_registry(
            "installed:email", "google", self._SCOPES
        )
        resp = ui_api_client.post(
            "/api/connectors/google/configure",
            json={
                "config": {
                    "scopes": ["openid"],
                    "grant_agents": ["installed:email"],
                }
            },
            headers=UI_HEADER,
        )
        assert resp.status_code == 200, resp.text
        # The list of ids is resolved to the {id: scopes} map the flow expects.
        assert captured["config"]["grant_agents"] == {"installed:email": self._SCOPES}


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


# ---------------------------------------------------------------------------
# POST /api/connectors/{connector_id}/enable | /disable  (#1004)
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_spec_in_registry(isolated_registry):
    """Add a configured MCP spec to the test registry for #1004 tests."""
    from gaia.connectors.spec import ConfigField, ConnectorSpec

    spec = ConnectorSpec(
        id="mcp-github",
        display_name="GitHub MCP",
        icon="🐙",
        category="dev-tools",
        tier=1,
        type="mcp_server",
        description="GitHub MCP server",
        mcp_command="npx",
        mcp_args=("-y", "@modelcontextprotocol/server-github"),
        mcp_env_keys=("GITHUB_TOKEN",),
        config_schema=(
            ConfigField(key="GITHUB_TOKEN", label="Token", kind="secret", secret=True),
        ),
    )
    isolated_registry.register(spec)
    return spec


@pytest.fixture
def configured_mcp(mcp_spec_in_registry, tmp_path, monkeypatch):
    """Pre-write an mcp_servers.json entry so the toggle endpoints can find it."""
    import json

    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)

    gaia_dir = tmp_path / ".gaia"
    gaia_dir.mkdir(parents=True, exist_ok=True)
    (gaia_dir / "mcp_servers.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mcp-github": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                        "env": {
                            "GITHUB_TOKEN": {
                                "$keyring": "gaia.connections:mcp-github:GITHUB_TOKEN"
                            }
                        },
                        "disabled": False,
                    }
                }
            }
        )
    )
    return mcp_spec_in_registry


class TestEnableDisableCsrf:
    def test_enable_without_header_is_403(self, ui_api_client):
        resp = ui_api_client.post("/api/connectors/mcp-github/enable")
        assert resp.status_code == 403

    def test_disable_without_header_is_403(self, ui_api_client):
        resp = ui_api_client.post("/api/connectors/mcp-github/disable")
        assert resp.status_code == 403


class TestEnableDisableTypeGuard:
    def test_enable_on_oauth_returns_400(self, ui_api_client):
        resp = ui_api_client.post("/api/connectors/google/enable", headers=UI_HEADER)
        assert resp.status_code == 400
        assert "oauth_pkce" in (resp.json().get("detail") or "")

    def test_disable_on_oauth_returns_400(self, ui_api_client):
        resp = ui_api_client.post("/api/connectors/google/disable", headers=UI_HEADER)
        assert resp.status_code == 400


class TestEnableDisableUnknown:
    def test_enable_unknown_id_returns_404(self, ui_api_client):
        resp = ui_api_client.post(
            "/api/connectors/mcp-totally-fake/enable", headers=UI_HEADER
        )
        assert resp.status_code == 404

    def test_disable_unconfigured_returns_404(
        self, ui_api_client, mcp_spec_in_registry
    ):
        # Spec is in the registry but no mcp_servers.json entry yet.
        resp = ui_api_client.post(
            "/api/connectors/mcp-github/disable", headers=UI_HEADER
        )
        assert resp.status_code == 404
        assert "not configured" in (resp.json().get("detail") or "")


class TestEnableDisableHappyPath:
    def test_disable_returns_summary_with_enabled_false(
        self, ui_api_client, configured_mcp
    ):
        resp = ui_api_client.post(
            "/api/connectors/mcp-github/disable", headers=UI_HEADER
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "mcp-github"
        assert body["enabled"] is False
        assert body["configured"] is True

    def test_enable_returns_summary_with_enabled_true(
        self, ui_api_client, configured_mcp
    ):
        # First disable, then re-enable.
        ui_api_client.post("/api/connectors/mcp-github/disable", headers=UI_HEADER)
        resp = ui_api_client.post(
            "/api/connectors/mcp-github/enable", headers=UI_HEADER
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    def test_round_trip_persists_in_summary(self, ui_api_client, configured_mcp):
        # Toggle off → GET reflects it
        ui_api_client.post("/api/connectors/mcp-github/disable", headers=UI_HEADER)
        resp = ui_api_client.get("/api/connectors/mcp-github")
        assert resp.json()["enabled"] is False

        # Toggle back on → GET reflects that too
        ui_api_client.post("/api/connectors/mcp-github/enable", headers=UI_HEADER)
        resp = ui_api_client.get("/api/connectors/mcp-github")
        assert resp.json()["enabled"] is True


class TestEnableDisableSseEvents:
    def test_disable_emits_sse_event(self, ui_api_client, configured_mcp):
        from gaia.ui.routers.connectors import _emitter

        captured: list[dict] = []
        original_emit = _emitter.emit

        async def capture(event_type, payload):
            captured.append({"type": event_type, "payload": payload})
            await original_emit(event_type, payload)

        _emitter.emit = capture  # type: ignore[assignment]
        try:
            ui_api_client.post("/api/connectors/mcp-github/disable", headers=UI_HEADER)
        finally:
            _emitter.emit = original_emit  # type: ignore[assignment]

        assert any(
            e["type"] == "connector.disabled"
            and e["payload"] == {"connector_id": "mcp-github"}
            for e in captured
        )

    def test_enable_emits_sse_event(self, ui_api_client, configured_mcp):
        from gaia.ui.routers.connectors import _emitter

        captured: list[dict] = []
        original_emit = _emitter.emit

        async def capture(event_type, payload):
            captured.append({"type": event_type, "payload": payload})
            await original_emit(event_type, payload)

        _emitter.emit = capture  # type: ignore[assignment]
        try:
            ui_api_client.post("/api/connectors/mcp-github/disable", headers=UI_HEADER)
            ui_api_client.post("/api/connectors/mcp-github/enable", headers=UI_HEADER)
        finally:
            _emitter.emit = original_emit  # type: ignore[assignment]

        assert any(
            e["type"] == "connector.enabled"
            and e["payload"] == {"connector_id": "mcp-github"}
            for e in captured
        )


class TestEnabledFieldInSummary:
    def test_unconfigured_mcp_summary_has_enabled_true_default(
        self, ui_api_client, mcp_spec_in_registry
    ):
        """An MCP that's never been configured still reports enabled=true so
        the UI doesn't render a 'Disabled' pill on a fresh tile."""
        resp = ui_api_client.get("/api/connectors/mcp-github")
        body = resp.json()
        assert body["configured"] is False
        assert body["enabled"] is True

    def test_configured_mcp_summary_reflects_disabled_flag(
        self, ui_api_client, configured_mcp
    ):
        # Initially enabled
        resp = ui_api_client.get("/api/connectors/mcp-github")
        assert resp.json()["enabled"] is True

        # Disable, then re-fetch
        ui_api_client.post("/api/connectors/mcp-github/disable", headers=UI_HEADER)
        resp = ui_api_client.get("/api/connectors/mcp-github")
        assert resp.json()["enabled"] is False

    def test_oauth_summary_always_reports_enabled_true(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors/google")
        assert resp.json()["enabled"] is True


# ---------------------------------------------------------------------------
# Activations endpoints (issue #1005)
# ---------------------------------------------------------------------------


class TestActivationsCsrf:
    def test_put_without_header_is_403(self, ui_api_client):
        resp = ui_api_client.put(
            "/api/connectors/mcp-test/activations/builtin:chat",
            json={"scopes": ["use"]},
        )
        assert resp.status_code == 403

    def test_delete_without_header_is_403(self, ui_api_client):
        resp = ui_api_client.delete(
            "/api/connectors/mcp-test/activations/builtin:chat",
        )
        assert resp.status_code == 403


class TestActivationsEndpoints:
    def test_get_activations_returns_activations_key(self, ui_api_client):
        resp = ui_api_client.get("/api/connectors/mcp-test/activations")
        assert resp.status_code == 200
        assert resp.json() == {"activations": {}}

    def test_put_activation_with_existing_grant_succeeds(self, ui_api_client):
        # Grant first, then activate (the explicit two-step path).
        ui_api_client.put(
            "/api/connectors/mcp-test/grants/builtin:chat",
            json={"scopes": ["use"]},
            headers=UI_HEADER,
        )
        resp = ui_api_client.put(
            "/api/connectors/mcp-test/activations/builtin:chat",
            json={},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "connector_id": "mcp-test",
            "agent_id": "builtin:chat",
            "active": True,
            "auto_granted": False,
        }
        # GET reflects the new active state.
        listing = ui_api_client.get("/api/connectors/mcp-test/activations").json()
        assert listing == {"activations": {"builtin:chat": True}}

    def test_put_activation_with_no_grant_and_scopes_auto_grants(self, ui_api_client):
        resp = ui_api_client.put(
            "/api/connectors/mcp-test/activations/builtin:chat",
            json={"scopes": ["use", "read"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["auto_granted"] is True
        assert body["active"] is True
        # Grant landed too.
        grants = ui_api_client.get("/api/connectors/mcp-test/grants").json()
        assert grants == {"grants": {"builtin:chat": ["use", "read"]}}

    def test_put_activation_with_no_grant_and_no_scopes_is_400(self, ui_api_client):
        resp = ui_api_client.put(
            "/api/connectors/mcp-test/activations/builtin:chat",
            json={},
            headers=UI_HEADER,
        )
        assert resp.status_code == 400
        detail = resp.json().get("detail", "")
        assert "mcp-test" in detail
        assert "builtin:chat" in detail

    def test_delete_activation_succeeds_idempotently(self, ui_api_client):
        # No prior activation — delete is still 204 (idempotent).
        resp = ui_api_client.delete(
            "/api/connectors/mcp-test/activations/builtin:chat",
            headers=UI_HEADER,
        )
        assert resp.status_code == 204

    def test_delete_activation_preserves_grant(self, ui_api_client):
        ui_api_client.put(
            "/api/connectors/mcp-test/grants/builtin:chat",
            json={"scopes": ["use"]},
            headers=UI_HEADER,
        )
        ui_api_client.put(
            "/api/connectors/mcp-test/activations/builtin:chat",
            json={},
            headers=UI_HEADER,
        )
        resp = ui_api_client.delete(
            "/api/connectors/mcp-test/activations/builtin:chat",
            headers=UI_HEADER,
        )
        assert resp.status_code == 204
        # Activation cleared, grant survives — re-activate must be one click.
        grants = ui_api_client.get("/api/connectors/mcp-test/grants").json()
        assert grants == {"grants": {"builtin:chat": ["use"]}}
        listing = ui_api_client.get("/api/connectors/mcp-test/activations").json()
        assert listing == {"activations": {}}

    def test_connector_summary_exposes_activations(self, ui_api_client):
        ui_api_client.put(
            "/api/connectors/mcp-test/activations/builtin:chat",
            json={"scopes": ["use"]},
            headers=UI_HEADER,
        )
        resp = ui_api_client.get("/api/connectors/mcp-test")
        assert resp.status_code == 200
        assert resp.json()["activations"] == {"builtin:chat": True}


class TestActivationsRejectNonMcpServer:
    """#1005 follow-up — activations gate MCP tool visibility only.

    The router must reject PUT/DELETE for OAuth connectors so we don't
    expose a UI switch that silently does nothing. CSRF still wins over
    type-check (a 403 before a 400 is the safe ordering — type checks
    leak catalog membership; CSRF doesn't).
    """

    def test_put_on_oauth_connector_is_400(self, ui_api_client):
        resp = ui_api_client.put(
            "/api/connectors/google/activations/builtin:chat",
            json={"scopes": ["openid"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 400
        detail = resp.json().get("detail", "")
        assert "MCP-server" in detail
        assert "google" in detail
        # And nothing was written to the ledger.
        listing = ui_api_client.get("/api/connectors/google/activations").json()
        assert listing == {"activations": {}}

    def test_delete_on_oauth_connector_is_400(self, ui_api_client):
        resp = ui_api_client.delete(
            "/api/connectors/google/activations/builtin:chat",
            headers=UI_HEADER,
        )
        assert resp.status_code == 400
        assert "MCP-server" in resp.json().get("detail", "")

    def test_put_on_unknown_connector_is_404(self, ui_api_client):
        resp = ui_api_client.put(
            "/api/connectors/does-not-exist/activations/builtin:chat",
            json={"scopes": ["use"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 404
        assert "does-not-exist" in resp.json().get("detail", "")

    def test_delete_on_unknown_connector_is_404(self, ui_api_client):
        resp = ui_api_client.delete(
            "/api/connectors/does-not-exist/activations/builtin:chat",
            headers=UI_HEADER,
        )
        assert resp.status_code == 404

    def test_csrf_takes_precedence_over_type_check_put(self, ui_api_client):
        # Missing X-Gaia-UI header on an OAuth connector still returns 403,
        # not 400 — never leak catalog membership / spec metadata to an
        # unauthenticated caller.
        resp = ui_api_client.put(
            "/api/connectors/google/activations/builtin:chat",
            json={"scopes": ["openid"]},
        )
        assert resp.status_code == 403

    def test_namespaced_agent_id_with_colon_is_routed_correctly(self, ui_api_client):
        # The agent_id path parameter uses ``:path`` so colons in namespaced
        # ids (``builtin:chat``, ``custom:abc:chat``) survive routing.
        resp = ui_api_client.put(
            "/api/connectors/mcp-test/activations/custom:deadbeef:chat",
            json={"scopes": ["use"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_id"] == "custom:deadbeef:chat"
