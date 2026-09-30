# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The Agent UI talks to the flagship only; other clients may pick any agent.

``X-Gaia-Client: agent-ui`` marks a request from the Agent UI frontend. With
it, a non-flagship ``agent_type`` is a 422 and a chat stored under another
agent is a 409; without it (eval harness, MCP bridge) nothing changes.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gaia.ui.security import flagship_only
from gaia.ui.server import create_app

pytestmark = pytest.mark.allow_network

UI = {"X-Gaia-UI": "1", "X-Gaia-Client": "agent-ui"}
OTHER_CLIENT = {"X-Gaia-UI": "1"}


def _request(headers):
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()})


class TestFlagshipOnly:
    def test_other_clients_pass_through_unchanged(self):
        req = _request(OTHER_CLIENT)
        assert flagship_only(req, "chat") == "chat"
        assert flagship_only(req, None) is None
        assert flagship_only(req, "email", stored="chat") == "email"

    def test_agent_ui_defaults_to_the_flagship(self):
        assert flagship_only(_request(UI), None) == "gaia"
        assert flagship_only(_request(UI), "gaia", stored="gaia") == "gaia"

    def test_agent_ui_refuses_another_agent(self):
        with pytest.raises(HTTPException) as exc:
            flagship_only(_request(UI), "chat")
        assert exc.value.status_code == 422
        assert "'chat'" in exc.value.detail

    def test_agent_ui_refuses_a_chat_stored_under_another_agent(self):
        with pytest.raises(HTTPException) as exc:
            flagship_only(_request(UI), None, stored="chat")
        assert exc.value.status_code == 409
        assert "Start a new chat" in exc.value.detail

    def test_only_the_exact_client_value_counts(self):
        req = _request({"X-Gaia-Client": "eval-harness"})
        assert flagship_only(req, "chat") == "chat"


@pytest.fixture
def client():
    with TestClient(create_app(db_path=":memory:")) as test_client:
        yield test_client


class TestRoutes:
    def test_a_chat_created_by_the_agent_ui_is_always_the_flagship(self, client):
        created = client.post("/api/sessions", json={}, headers=UI)
        assert created.status_code == 200, created.text
        assert created.json()["agent_type"] == "gaia"
        stored = client.get(f"/api/sessions/{created.json()['id']}")
        assert stored.json()["agent_type"] == "gaia"

    def test_agent_ui_cannot_create_another_agents_chat(self, client):
        resp = client.post("/api/sessions", json={"agent_type": "chat"}, headers=UI)
        assert resp.status_code == 422
        assert "GAIA agent only" in resp.json()["detail"]

    def test_other_clients_may_still_pick_an_agent(self, client):
        resp = client.post(
            "/api/sessions", json={"agent_type": "chat"}, headers=OTHER_CLIENT
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["agent_type"] == "chat"

    def test_agent_ui_cannot_send_as_another_agent(self, client):
        sid = client.post("/api/sessions", json={}, headers=UI).json()["id"]
        resp = client.post(
            "/api/chat/send",
            json={"session_id": sid, "message": "hi", "agent_type": "chat"},
            headers=UI,
        )
        assert resp.status_code == 422

    def test_agent_ui_cannot_send_into_a_retired_agents_chat(self, client):
        sid = client.post(
            "/api/sessions", json={"agent_type": "chat"}, headers=OTHER_CLIENT
        ).json()["id"]
        resp = client.post(
            "/api/chat/send",
            json={"session_id": sid, "message": "hi"},
            headers=UI,
        )
        assert resp.status_code == 409
        assert "read-only" in resp.json()["detail"]
