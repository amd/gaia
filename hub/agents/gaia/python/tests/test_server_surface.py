# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``surface`` field (contract 2.19) reaches the agent's config."""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _Agent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None

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
        agents.append(_Agent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL), agents
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _query(client, **fields):
    body = {"query": "schedule a report", "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    return client.post("/v1/gaia/query", json=body)


def test_the_contract_advertises_surface():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 19)
    assert "surface" in server_mod.QueryRequest.model_fields


@pytest.mark.parametrize("session_id", [None, "s1"])
def test_the_surface_reaches_the_agent(built, session_id):
    client, agents = built
    fields = {"surface": "agent_ui"}
    if session_id:
        fields["session_id"] = session_id

    r = _query(client, **fields)

    assert r.status_code == 200, r.text
    [agent] = agents
    assert agent.kwargs["surface"] == "agent_ui"


def test_omitting_the_surface_leaves_the_agent_default(built):
    client, agents = built
    assert _query(client).status_code == 200
    [agent] = agents
    assert "surface" not in agent.kwargs


def test_an_unknown_surface_is_refused(built):
    client, agents = built
    assert _query(client, surface="browser").status_code == 422
    assert agents == []
