# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``DELETE /v1/gaia/sessions/{session_id}`` — ending a session (contract 2.30).

A session's agent holds the MCP servers it started until the agent is closed.
Without this route a host that deleted a chat left them running until the
session idled out, hours later.
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


class _Agent:
    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._followup_queue = None
        self.closed = 0

    def process_query(self, query, max_steps=None):
        return {"answer": "ok"}

    def close(self):
        self.closed += 1


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
    client = TestClient(server_mod.build_app(), base_url=_BASE_URL)
    try:
        yield client, agents
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _start(client, session_id):
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


def test_api_version_names_the_route():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 30)


def test_deleting_a_session_closes_its_agent(built):
    client, agents = built
    _start(client, "s-end")
    assert agents[-1].closed == 0

    r = client.delete("/v1/gaia/sessions/s-end")

    assert r.status_code == 200, r.text
    assert r.json() == {"session_id": "s-end", "deleted": True}
    assert agents[-1].closed == 1
    assert sr.registry.get("s-end") is None


def test_an_unknown_session_is_a_404(built):
    client, _ = built
    r = client.delete("/v1/gaia/sessions/nope")
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]


def test_a_session_running_a_turn_is_a_409_and_kept(built):
    client, agents = built
    _start(client, "s-busy")
    session = sr.registry.get("s-busy")
    assert session.run_lock.acquire(blocking=False)
    try:
        r = client.delete("/v1/gaia/sessions/s-busy")
    finally:
        session.run_lock.release()

    assert r.status_code == 409
    assert "Cancel it" in r.json()["detail"]
    assert agents[-1].closed == 0
    assert sr.registry.get("s-busy") is session


def test_a_malformed_session_id_is_a_400(built):
    client, _ = built
    assert client.delete("/v1/gaia/sessions/bad%20id").status_code == 400


def test_deleting_one_session_leaves_its_neighbours_running(built):
    client, _ = built
    for sid in ("s", "s-1", "S"):
        _start(client, sid)
    by_id = {sid: sr.registry.get(sid).agent for sid in ("s", "s-1", "S")}

    assert client.delete("/v1/gaia/sessions/s").status_code == 200

    assert by_id["s"].closed == 1
    assert by_id["s-1"].closed == 0 and sr.registry.get("s-1") is not None
    assert by_id["S"].closed == 0 and sr.registry.get("S") is not None


def test_a_second_delete_is_a_404_and_closes_nothing_again(built):
    client, agents = built
    _start(client, "s-twice")

    assert client.delete("/v1/gaia/sessions/s-twice").status_code == 200
    assert client.delete("/v1/gaia/sessions/s-twice").status_code == 404
    assert agents[-1].closed == 1


def test_a_turn_after_delete_gets_a_fresh_agent(built):
    client, _ = built
    _start(client, "s-again")
    first = sr.registry.get("s-again").agent
    assert client.delete("/v1/gaia/sessions/s-again").status_code == 200

    _start(client, "s-again")

    assert sr.registry.get("s-again").agent is not first
    assert first.closed == 1


@pytest.mark.parametrize("sid", ["..", "a/b", "%2e%2e"])
def test_a_traversal_shaped_id_ends_no_session(built, sid):
    client, agents = built
    _start(client, "s-victim")

    r = client.delete(f"/v1/gaia/sessions/{sid}")

    assert r.status_code in (400, 404, 405), r.text
    assert agents[-1].closed == 0
    assert sr.registry.get("s-victim") is not None


def test_an_unauthenticated_delete_is_refused_and_keeps_the_session(built, monkeypatch):
    _, agents = built
    caller_auth.reset()
    monkeypatch.setenv(caller_auth.TOKEN_ENV_VAR, "secret-token")
    authed = TestClient(server_mod.build_app(), base_url=_BASE_URL)
    headers = {"Authorization": "Bearer secret-token"}
    r = authed.post(
        "/v1/gaia/query",
        json={
            "query": "hi",
            "run_id": str(uuid.uuid4()),
            "context": [],
            "session_id": "s-guarded",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text

    assert authed.delete("/v1/gaia/sessions/s-guarded").status_code == 401
    assert agents[-1].closed == 0
    r = authed.delete("/v1/gaia/sessions/s-guarded", headers=headers)
    assert r.status_code == 200, r.text
    assert agents[-1].closed == 1
