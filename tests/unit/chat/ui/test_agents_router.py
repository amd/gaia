# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the /api/agents endpoints."""

from unittest.mock import MagicMock

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
