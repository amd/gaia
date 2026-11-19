# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GET /v1/gaia/sessions/{session_id}/tools`` — a session's tools (contract 2.27).

Driven through the real ``build_app()``: the list is read from the session's
own agent, so a tool registered between turns (a loaded skill) shows up, and a
session that does not exist is a 404 rather than a freshly built agent's list.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

from gaia.agents.base.agent import Agent  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


def _rag_tool():
    """Search documents."""


_rag_tool.__module__ = "gaia.agents.tools.rag_tools"


class _ToolAgent:
    """Answers every turn; its tools are a plain registry, as on a real agent."""

    describe_tools = Agent.describe_tools

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._followup_queue = None
        self._tools_registry = {
            "query_documents": {
                "description": "Search documents.",
                "function": _rag_tool,
            },
        }

    def process_query(self, query, max_steps=None):
        return {"answer": "ok"}

    def close(self):
        pass


@pytest.fixture
def built(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    agents: list = []

    def build(**kw):
        agents.append(_ToolAgent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    client = TestClient(server_mod.build_app(), base_url=_BASE_URL)
    try:
        yield client, agents
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _turn(client, session_id):
    r = client.post(
        "/v1/gaia/query",
        json={
            "query": "hi",
            "run_id": str(uuid.uuid4()),
            "context": [],
            "session_id": session_id,
        },
    )
    assert r.status_code == 200, r.text


def test_api_version_names_the_tools_route():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 27)


def test_lists_the_session_agents_tools(built):
    client, agents = built
    _turn(client, "s-tools")

    r = client.get("/v1/gaia/sessions/s-tools/tools")

    assert r.status_code == 200, r.text
    assert r.json() == {
        "session_id": "s-tools",
        "tools": [
            {
                "name": "query_documents",
                "description": "Search documents.",
                "category": "rag",
            }
        ],
    }
    assert len(agents) == 1, "listing must not build another agent"


def test_a_tool_added_after_the_turn_is_listed(built):
    """Skills load mid-session; the list is read live, not cached at build."""
    client, agents = built
    _turn(client, "s-live")
    agents[0]._tools_registry["rss-digest/fetch_rss"] = {
        "description": "Fetch a feed.",
        "function": None,
    }

    tools = client.get("/v1/gaia/sessions/s-live/tools").json()["tools"]

    assert [(t["name"], t["category"]) for t in tools] == [
        ("query_documents", "rag"),
        ("rss-digest/fetch_rss", "skill"),
    ]


def test_an_unknown_session_is_a_404_and_builds_no_agent(built):
    client, agents = built

    r = client.get("/v1/gaia/sessions/nope/tools")

    assert r.status_code == 404
    assert "Send a query on that session first" in r.json()["detail"]
    assert agents == []


def test_a_malformed_session_id_is_a_400(built):
    client, _ = built
    assert client.get("/v1/gaia/sessions/bad%20id/tools").status_code == 400


def test_sessions_do_not_see_each_others_tools(built):
    client, agents = built
    _turn(client, "s-a")
    _turn(client, "s-b")
    agents[0]._tools_registry["only_a"] = {"description": "", "function": None}

    names_b = [
        t["name"] for t in client.get("/v1/gaia/sessions/s-b/tools").json()["tools"]
    ]

    assert "only_a" not in names_b
