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

    Each spec is ``(agent_id, name)``, ``(agent_id, name, min_memory_gb)``,
    or ``(agent_id, name, min_memory_gb, required_connections)`` for tests
    that exercise the memory-requirement or required-connections fields.
    """
    registry = MagicMock(spec=AgentRegistry)
    registrations = []
    for spec in agent_specs:
        min_memory_gb = None
        required_connections = []
        if len(spec) == 4:
            agent_id, name, min_memory_gb, required_connections = spec
        elif len(spec) == 3:
            agent_id, name, min_memory_gb = spec
        else:
            agent_id, name = spec
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
            required_connections=required_connections or [],
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

    def test_hidden_agents_are_not_listed(self):
        """The picker must not offer a hidden agent. Retiring chat/doc/file
        rests entirely on this filter — they stay in the registry so stored
        sessions resolve, and only this endpoint's exclusion keeps them out of
        the UI.
        """
        registry = make_mock_registry(("gaia", "GAIA"), ("doc", "Doc Agent"))
        for reg in registry.list.return_value:
            if reg.id == "doc":
                reg.hidden = True
        app = create_app(db_path=":memory:")
        app.state.agent_registry = registry

        # The endpoint also unions in hub-installed sidecars (#2118), so a dev
        # machine with one installed would fail this on an unrelated id.
        with patch("gaia.hub.installer.list_installed", return_value={}):
            data = TestClient(app).get("/api/agents").json()
        ids = [a["id"] for a in data["agents"]]
        assert ids == ["gaia"]
        assert data["total"] == 1
        # Still resolvable by id — hidden removes the choice, not the route.
        assert registry.get("doc") is not None

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
        # Same name the catalog path gives above: the spec's display_name now
        # matches gaia-agent.yaml, so the card no longer depends on whether a
        # catalog happened to be cached (#4161).
        assert data["agents"][0]["name"] == "Email Triage"  # spec.display_name

    def test_registry_entry_wins_over_sidecar(self):
        """A registered (wheel) email is not duplicated by the sidecar merge.

        Gives the mock registration a non-empty required_connections (#2408)
        so this also locks in that a registry-sourced sidecar registration
        (e.g. from register_installed_sidecars) surfaces its connector
        requirements through this same union path, not just its name.
        """
        from gaia.connectors.providers.base import ConnectorRequirement

        cr = ConnectorRequirement(
            connector_id="google",
            scopes=["https://www.googleapis.com/auth/gmail.modify"],
        )
        app = create_app(db_path=":memory:")
        app.state.agent_registry = make_mock_registry(
            ("email", "Email (wheel)", None, [cr])
        )
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
        assert emails[0]["required_connections"] != []

    def test_uninstalled_sidecar_agent_absent(self):
        """No install sentinel → the agent is NOT phantom-listed."""
        client = self._client_with_empty_registry()
        with patch("gaia.hub.installer.list_installed", return_value={}):
            data = client.get("/api/agents").json()
        assert data["agents"] == []


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
