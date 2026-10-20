# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``memory`` field (contract 2.18).

A private chat (or one with memory turned off) relayed from the Agent UI used to
reach the flagship with no word of it, so the agent went on saving errors and
lessons to memory. These drive the real app and check the agent's own memory
switch at the moment the turn runs.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _MemoryAgent:
    """Records the memory switch each turn ran under."""

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._incognito = False
        self._memory_store = object()
        self.seen: list = []

    def process_query(self, query, max_steps=None):
        self.seen.append((self._incognito, getattr(self, "_incognito_reason", None)))
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
        agents.append(_MemoryAgent(**kw))
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
    body = {"query": "hi", "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    return client.post("/v1/gaia/query", json=body)


def test_the_contract_advertises_memory():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 18)
    assert "memory" in server_mod.QueryRequest.model_fields


@pytest.mark.parametrize(
    "mode, reason", [("private", "private"), ("off", "memory_off")]
)
def test_a_private_or_memory_off_turn_runs_with_memory_writes_off(built, mode, reason):
    client, agents = built
    assert _query(client, memory=mode).status_code == 200
    assert agents[-1].seen == [(True, reason)]


def test_a_retained_session_follows_the_mode_each_turn(built):
    client, agents = built
    for mode in ("private", "on", None):
        fields = {"session_id": "chat-1"}
        if mode is not None:
            fields["memory"] = mode
        assert _query(client, **fields).status_code == 200
    assert len(agents) == 1
    # Omitting the field leaves the session as the last turn set it.
    assert [incognito for incognito, _ in agents[0].seen] == [True, False, False]


def test_on_never_turns_on_memory_the_agent_has_no_store_for(built, monkeypatch):
    client, agents = built

    def build(**kw):
        agent = _MemoryAgent(**kw)
        agent._memory_store = None
        agent._incognito = True
        agents.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    assert _query(client, memory="on").status_code == 200
    assert agents[-1].seen[0][0] is True


def test_an_unknown_mode_is_refused(built):
    client, _ = built
    assert _query(client, memory="sometimes").status_code == 422
