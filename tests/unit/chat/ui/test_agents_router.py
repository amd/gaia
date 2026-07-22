# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the /api/agents endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.agents.registry import AgentRegistration, AgentRegistry
from gaia.ui.server import create_app


def make_mock_registry(*agent_specs):
    """Create a mock AgentRegistry with the given agents.

    Each spec is ``(agent_id, name)`` or ``(agent_id, name, min_memory_gb)``
    for tests that exercise the memory-requirement field.
    """
    registry = MagicMock(spec=AgentRegistry)
    registrations = []
    for spec in agent_specs:
        if len(spec) == 3:
            agent_id, name, min_memory_gb = spec
        else:
            agent_id, name = spec
            min_memory_gb = None
        reg = AgentRegistration(
            id=agent_id,
            name=name,
            description=f"Description for {name}",
            source="builtin",
            conversation_starters=["Hello!"],
            factory=lambda **kw: None,
            agent_dir=None,
            models=[],
            min_memory_gb=min_memory_gb,
        )
        registrations.append(reg)

    registry.list.return_value = registrations
    registry.get.side_effect = lambda agent_id: next(
        (r for r in registrations if r.id == agent_id), None
    )
    return registry


@pytest.fixture
def app_with_registry():
    """Create app and inject a mock registry."""
    app = create_app(db_path=":memory:")
    registry = make_mock_registry(
        ("chat", "Chat Agent"),
        ("gaia", "GAIA"),
    )
    app.state.agent_registry = registry
    return app


@pytest.fixture
def client(app_with_registry):
    return TestClient(app_with_registry)


class TestListAgents:
    def test_returns_200(self, client):
        resp = client.get("/api/agents")
        assert resp.status_code == 200

    def test_returns_agent_list(self, client):
        data = client.get("/api/agents").json()
        assert "agents" in data
        assert "total" in data

    def test_lists_all_registered_agents(self, client):
        data = client.get("/api/agents").json()
        ids = [a["id"] for a in data["agents"]]
        assert "chat" in ids
        assert "gaia" in ids

    def test_total_matches_agents_count(self, client):
        data = client.get("/api/agents").json()
        assert data["total"] == len(data["agents"])

    def test_agent_has_required_fields(self, client):
        data = client.get("/api/agents").json()
        agent = data["agents"][0]
        for field in (
            "id",
            "name",
            "description",
            "source",
            "conversation_starters",
            "models",
            "min_memory_gb",
        ):
            assert field in agent

    def test_min_memory_gb_defaults_to_null(self, client):
        """Agents that don't declare a requirement expose null, not missing."""
        data = client.get("/api/agents").json()
        for agent in data["agents"]:
            assert agent["min_memory_gb"] is None


class TestAgentWithMemoryRequirement:
    """Agents that declare min_memory_gb must round-trip it through the API."""

    def test_min_memory_gb_surfaced(self):
        app = create_app(db_path=":memory:")
        app.state.agent_registry = make_mock_registry(
            ("chat", "Chat Agent"),
            ("gaia-lite", "Gaia Lite", 5.0),
        )
        client = TestClient(app)

        data = client.get("/api/agents/gaia-lite").json()
        assert data["min_memory_gb"] == 5.0

        # List endpoint surfaces it too.
        list_data = client.get("/api/agents").json()
        lite = next(a for a in list_data["agents"] if a["id"] == "gaia-lite")
        chat = next(a for a in list_data["agents"] if a["id"] == "chat")
        assert lite["min_memory_gb"] == 5.0
        assert chat["min_memory_gb"] is None


class TestInstalledSidecarAgentsMerge:
    """A hub-installed *binary* sidecar agent must appear in the picker even
    when the in-process registry is empty (consumer install, no wheels) — #2118.
    """

    @staticmethod
    def _email_sentinel():
        from gaia.hub.installer import InstalledAgent

        return {
            "email": InstalledAgent(
                id="email",
                version="0.5.0",
                language="python",
                installed_at="2026-01-01T00:00:00Z",
                artifact_kind="binary",
            )
        }

    def _client_with_empty_registry(self):
        app = create_app(db_path=":memory:")
        app.state.agent_registry = make_mock_registry()  # cold: no agents
        return TestClient(app)

    def test_installed_email_appears_with_empty_registry(self):
        client = self._client_with_empty_registry()
        with (
            patch(
                "gaia.hub.installer.list_installed", return_value=self._email_sentinel()
            ),
            patch(
                "gaia.hub.catalog.cached_index_agents",
                return_value=[
                    {
                        "id": "email",
                        "name": "Email Triage",
                        "description": "Triage Gmail locally",
                        "category": "productivity",
                        "icon": "mail",
                    }
                ],
            ),
        ):
            data = client.get("/api/agents").json()

        ids = [a["id"] for a in data["agents"]]
        assert ids == ["email"]
        email = data["agents"][0]
        assert email["name"] == "Email Triage"
        assert email["description"] == "Triage Gmail locally"
        assert email["source"] == "installed"
        assert email["icon"] == "mail"

    def test_falls_back_to_spec_name_without_catalog_cache(self):
        """No cached catalog → still a real card using the daemon spec name."""
        client = self._client_with_empty_registry()
        with (
            patch(
                "gaia.hub.installer.list_installed", return_value=self._email_sentinel()
            ),
            patch("gaia.hub.catalog.cached_index_agents", return_value=[]),
        ):
            data = client.get("/api/agents").json()

        assert [a["id"] for a in data["agents"]] == ["email"]
        assert data["agents"][0]["name"] == "Email"  # spec.display_name

    def test_registry_entry_wins_over_sidecar(self):
        """A registered (wheel) email is not duplicated by the sidecar merge."""
        app = create_app(db_path=":memory:")
        app.state.agent_registry = make_mock_registry(("email", "Email (wheel)"))
        client = TestClient(app)
        with (
            patch(
                "gaia.hub.installer.list_installed", return_value=self._email_sentinel()
            ),
            patch("gaia.hub.catalog.cached_index_agents", return_value=[]),
        ):
            data = client.get("/api/agents").json()

        emails = [a for a in data["agents"] if a["id"] == "email"]
        assert len(emails) == 1
        assert emails[0]["name"] == "Email (wheel)"

    def test_uninstalled_sidecar_agent_absent(self):
        """No install sentinel → the agent is NOT phantom-listed."""
        client = self._client_with_empty_registry()
        with patch("gaia.hub.installer.list_installed", return_value={}):
            data = client.get("/api/agents").json()
        assert data["agents"] == []

    def test_get_installed_sidecar_agent_by_id(self):
        client = self._client_with_empty_registry()
        with (
            patch(
                "gaia.hub.installer.list_installed", return_value=self._email_sentinel()
            ),
            patch("gaia.hub.catalog.cached_index_agents", return_value=[]),
        ):
            resp = client.get("/api/agents/email")
        assert resp.status_code == 200
        assert resp.json()["id"] == "email"

    def test_installed_email_surfaces_required_connections(self):
        """The sidecar's Google/Microsoft OAuth requirements must reach the
        picker so the Connectors panel lists it under PER-AGENT GRANTS instead
        of "No agents require access" (#2408)."""
        client = self._client_with_empty_registry()
        with (
            patch(
                "gaia.hub.installer.list_installed", return_value=self._email_sentinel()
            ),
            patch("gaia.hub.catalog.cached_index_agents", return_value=[]),
        ):
            data = client.get("/api/agents").json()

        email = data["agents"][0]
        by_connector = {rc["connector_id"]: rc for rc in email["required_connections"]}
        assert set(by_connector) == {"google", "microsoft"}
        assert (
            "https://www.googleapis.com/auth/gmail.modify"
            in by_connector["google"]["scopes"]
        )
        assert (
            "https://www.googleapis.com/auth/calendar.readonly"
            in by_connector["google"]["scopes"]
        )
        assert by_connector["google"]["reason"]


class TestGetAgent:
    def test_known_agent_returns_200(self, client):
        resp = client.get("/api/agents/chat")
        assert resp.status_code == 200

    def test_known_agent_returns_correct_data(self, client):
        data = client.get("/api/agents/chat").json()
        assert data["id"] == "chat"
        assert data["name"] == "Chat Agent"

    def test_unknown_agent_returns_404(self, client):
        resp = client.get("/api/agents/nonexistent-agent-xyz")
        assert resp.status_code == 404

    def test_slash_in_id_handled(self, client):
        # Test that path with slash is handled correctly (uses :path converter)
        # Since "my-company/support" doesn't exist, it should 404, not 500
        resp = client.get("/api/agents/my-company/support")
        assert resp.status_code == 404


class TestAgentsRouterWithoutRegistry:
    """Verify response when registry not yet initialized."""

    def test_list_agents_without_registry_returns_503(self):
        app = create_app(db_path=":memory:")
        # Don't inject registry — app.state.agent_registry will be absent
        if hasattr(app.state, "agent_registry"):
            del app.state.agent_registry

        client = TestClient(app)
        resp = client.get("/api/agents")
        assert resp.status_code == 503


class TestListDiskAgents:
    def test_lists_exportable_agent_missing_from_registry(
        self, app_with_registry, tmp_path
    ):
        from gaia.ui.routers.agents import _require_localhost

        agent_dir = tmp_path / ".gaia" / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text("class NotImportedYet: pass\n")

        app_with_registry.dependency_overrides[_require_localhost] = lambda: None
        try:
            with patch("gaia.installer.export_import.Path.home", return_value=tmp_path):
                client = TestClient(app_with_registry)
                resp = client.get("/api/agents/disk", headers={"X-Gaia-UI": "1"})
        finally:
            app_with_registry.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json() == {
            "agents": [
                {
                    "id": "test-agent",
                    "name": "test-agent",
                    "registered": False,
                    "registered_agent_id": None,
                    "source": None,
                }
            ],
            "total": 1,
        }

    def test_marks_disk_agent_registered_by_directory(self, tmp_path):
        from gaia.ui.routers.agents import _require_localhost

        agent_dir = tmp_path / ".gaia" / "agents" / "disk-dir"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.py").write_text("class AlreadyLoaded: pass\n")

        app = create_app(db_path=":memory:")
        registry = MagicMock(spec=AgentRegistry)
        registry.list.return_value = [
            AgentRegistration(
                id="runtime-id",
                name="Runtime Agent",
                description="Loaded from disk",
                source="custom_python",
                conversation_starters=[],
                factory=lambda **kw: None,
                agent_dir=agent_dir,
                models=[],
            )
        ]
        app.state.agent_registry = registry
        app.dependency_overrides[_require_localhost] = lambda: None

        try:
            with patch("gaia.installer.export_import.Path.home", return_value=tmp_path):
                client = TestClient(app)
                resp = client.get("/api/agents/disk", headers={"X-Gaia-UI": "1"})
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert resp.json()["agents"] == [
            {
                "id": "disk-dir",
                "name": "Runtime Agent",
                "registered": True,
                "registered_agent_id": "runtime-id",
                "source": "custom_python",
            }
        ]


class TestExportImportSecurityGuards:
    """Verify the three security guards on export/import endpoints.

    TestClient uses host="testclient" (not in _LOCALHOST_HOSTS), so the
    localhost guard fires naturally for non-localhost tests.
    """

    def test_non_localhost_export_returns_403(self, app_with_registry):
        client = TestClient(app_with_registry)
        resp = client.post("/api/agents/export", headers={"X-Gaia-UI": "1"})
        assert resp.status_code == 403

    def test_non_localhost_import_returns_403(self, app_with_registry):
        client = TestClient(app_with_registry)
        resp = client.post(
            "/api/agents/import",
            headers={"X-Gaia-UI": "1"},
            files={"bundle": ("x.zip", b"", "application/zip")},
        )
        assert resp.status_code == 403

    def test_missing_ui_header_export_returns_403(self, app_with_registry):
        from gaia.ui.routers.agents import _require_localhost

        app_with_registry.dependency_overrides[_require_localhost] = lambda: None
        try:
            client = TestClient(app_with_registry)
            resp = client.post("/api/agents/export")  # no X-Gaia-UI header
            assert resp.status_code == 403
        finally:
            app_with_registry.dependency_overrides.clear()

    def test_missing_ui_header_import_returns_403(self, app_with_registry):
        from gaia.ui.routers.agents import _require_localhost

        app_with_registry.dependency_overrides[_require_localhost] = lambda: None
        try:
            client = TestClient(app_with_registry)
            resp = client.post(
                "/api/agents/import",
                files={"bundle": ("x.zip", b"", "application/zip")},
            )
            assert resp.status_code == 403
        finally:
            app_with_registry.dependency_overrides.clear()

    def test_tunnel_active_export_returns_503(self, app_with_registry, monkeypatch):
        import gaia.ui.server as _srv
        from gaia.ui.routers.agents import _require_localhost

        # TestClient uses scope["client"] = ("testclient", 50000); treat it as
        # localhost so TunnelAuthMiddleware passes through and _require_tunnel_inactive
        # can fire its 503 instead of the middleware's 401.
        monkeypatch.setattr(_srv, "_LOCAL_HOSTS", _srv._LOCAL_HOSTS | {"testclient"})

        mock_tunnel = MagicMock()
        mock_tunnel.active = True
        app_with_registry.state.tunnel = mock_tunnel
        app_with_registry.dependency_overrides[_require_localhost] = lambda: None
        try:
            client = TestClient(app_with_registry)
            resp = client.post("/api/agents/export", headers={"X-Gaia-UI": "1"})
            assert resp.status_code == 503
        finally:
            app_with_registry.dependency_overrides.clear()
            del app_with_registry.state.tunnel

    def test_tunnel_active_import_returns_503(self, app_with_registry, monkeypatch):
        import gaia.ui.server as _srv
        from gaia.ui.routers.agents import _require_localhost

        monkeypatch.setattr(_srv, "_LOCAL_HOSTS", _srv._LOCAL_HOSTS | {"testclient"})

        mock_tunnel = MagicMock()
        mock_tunnel.active = True
        app_with_registry.state.tunnel = mock_tunnel
        app_with_registry.dependency_overrides[_require_localhost] = lambda: None
        try:
            client = TestClient(app_with_registry)
            resp = client.post(
                "/api/agents/import",
                headers={"X-Gaia-UI": "1"},
                files={"bundle": ("x.zip", b"", "application/zip")},
            )
            assert resp.status_code == 503
        finally:
            app_with_registry.dependency_overrides.clear()
            del app_with_registry.state.tunnel


class TestRouteShadowing:
    """Confirm that literal /export and /import routes shadow the {agent_id:path} wildcard.

    TestClient sends from host "testclient" (not localhost), so the localhost guard
    fires 403 — which proves the named route resolved first (405 would mean it didn't).
    """

    def test_post_export_resolves_named_route_not_wildcard(self, client):
        resp = client.post("/api/agents/export", headers={"X-Gaia-UI": "1"})
        assert resp.status_code == 403
        assert "method not allowed" not in resp.text.lower()

    def test_post_import_resolves_named_route_not_wildcard(self, client):
        resp = client.post(
            "/api/agents/import",
            headers={"X-Gaia-UI": "1"},
            files={"bundle": ("x.zip", b"", "application/zip")},
        )
        assert resp.status_code == 403
        assert "method not allowed" not in resp.text.lower()

    def test_get_export_returns_404_not_405(self, client):
        # GET /api/agents/export is handled by the GET /{agent_id:path} route;
        # "export" is not a registered agent, so 404 is expected — not 405.
        resp = client.get("/api/agents/export")
        assert resp.status_code == 404
