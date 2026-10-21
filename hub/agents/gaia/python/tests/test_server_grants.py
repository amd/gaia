# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/v1/gaia/sessions/{session_id}/grants`` — listing and revoking "always".

Contract 2.19. An "always" answer over ``/tool_decision`` is held by the
session; without these routes a caller could grant one but never show the user
what it covers or take it back. Driven through the real ``build_app()`` and a
real ``SSEOutputHandler``, so the grant that is listed is the one the next
gated call is checked against.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402
from gaia_agent.stdio import PermissionState  # noqa: E402

from gaia.agents.base.tool_grants import grant_scope  # noqa: E402
from gaia.ui.sse_handler import SSEOutputHandler  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"
_WRITE = ("write_file", {"file_path": "notes.md", "content": "hi"})


class _GatedAgent:
    """Asks for one gated call per turn and records whether it was allowed."""

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._followup_queue = None
        self.results: list = []

    def process_query(self, query, max_steps=None):
        self.results.append(self.console.confirm_tool_execution(*_WRITE))
        return {"answer": f"approved={self.results[-1]}"}

    def close(self):
        pass


@pytest.fixture
def built(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    agents: list = []

    def build(**kw):
        agents.append(_GatedAgent(**kw))
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


def _turn(client, session_id, decision=None):
    """Run one turn; answer its prompt with *decision* if it asks."""
    run_id = str(uuid.uuid4())
    asked = []

    def answer():
        for _ in range(500):
            r = client.post(
                f"/v1/gaia/query/{run_id}/tool_decision", json={"decision": decision}
            )
            if r.status_code == 200:
                asked.append(True)
                return
            time.sleep(0.01)

    answerer = threading.Thread(target=answer, daemon=True)
    if decision is not None:
        answerer.start()
    response = client.post(
        "/v1/gaia/query",
        json={
            "query": "write it",
            "run_id": run_id,
            "context": [],
            "session_id": session_id,
        },
    )
    assert response.status_code == 200, response.text
    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    if decision is not None:
        answerer.join(timeout=10)
    prompted = any(e.get("type") == "needs_confirmation" for e in events)
    return prompted, events


def test_api_version_names_the_grants_routes():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 19)


def test_an_always_answer_is_listed_honoured_and_revocable(built):
    client, agents = built
    scope = grant_scope(*_WRITE)

    prompted, _ = _turn(client, "s-grants", decision="always")
    assert prompted
    listed = client.get("/v1/gaia/sessions/s-grants/grants")
    assert listed.status_code == 200, listed.text
    assert listed.json() == {
        "session_id": "s-grants",
        "grants": [{"key": scope.key, "label": scope.label}],
    }

    prompted, _ = _turn(client, "s-grants")
    assert not prompted, "a granted call must not ask again"
    assert agents[-1].results[-1] is True

    revoked = client.delete(
        "/v1/gaia/sessions/s-grants/grants", params={"key": scope.key}
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["grants"] == []

    prompted, _ = _turn(client, "s-grants", decision="deny")
    assert prompted, "a revoked grant must ask again"
    assert agents[-1].results[-1] is False


def test_revoking_all_without_a_key(built):
    client, _ = built
    _turn(client, "s-all", decision="always")
    r = client.delete("/v1/gaia/sessions/s-all/grants")
    assert r.status_code == 200
    assert r.json()["grants"] == []


def test_an_unknown_session_is_a_404_not_an_empty_list(built):
    client, _ = built
    assert client.get("/v1/gaia/sessions/nope/grants").status_code == 404
    assert client.delete("/v1/gaia/sessions/nope/grants").status_code == 404


def test_an_unknown_key_is_a_404(built):
    client, _ = built
    _turn(client, "s-key", decision="allow")
    r = client.delete("/v1/gaia/sessions/s-key/grants", params={"key": "nope"})
    assert r.status_code == 404
    assert "no grant 'nope'" in r.json()["detail"]


def test_a_malformed_session_id_is_a_400(built):
    client, _ = built
    assert client.get("/v1/gaia/sessions/bad%20id/grants").status_code == 400


# ── PermissionState: the live turn, and families two answers share ─────────


def _attached():
    state = PermissionState(lifts_shell_gates=False)
    handler = SSEOutputHandler()
    state.attach(handler)
    return state, handler


def test_a_grant_made_in_the_running_turn_is_listed_and_revoked_there():
    state, handler = _attached()
    handler.grant_call_for_session(*_WRITE)
    scope = grant_scope(*_WRITE)

    assert state.grants() == [{"key": scope.key, "label": scope.label}]
    assert state.revoke(scope.key) == 1
    assert not handler.call_is_granted(*_WRITE), "revoke reaches the live turn"
    state.detach(handler)
    assert state.grants() == []


def test_a_detached_grant_is_kept_for_the_next_turn_and_revoked_from_it():
    state, handler = _attached()
    handler.grant_call_for_session(*_WRITE)
    state.detach(handler)

    nxt = SSEOutputHandler()
    state.attach(nxt)
    assert nxt.call_is_granted(*_WRITE)
    assert state.revoke() == 1
    assert not nxt.call_is_granted(*_WRITE)


def test_a_family_another_answer_also_granted_stays_granted():
    state, handler = _attached()
    both = ("run_shell_command", {"command": "gh issue list && pytest -q"})
    tests_only = ("run_shell_command", {"command": "pytest -q"})
    issues_only = ("run_shell_command", {"command": "gh issue list"})
    handler.grant_call_for_session(*both)
    handler.grant_call_for_session(*tests_only)

    assert state.revoke(grant_scope(*both).key) == 1
    assert handler.call_is_granted(*tests_only), "still granted by its own answer"
    assert not handler.call_is_granted(*issues_only)
    assert state.grants() == [
        {"key": grant_scope(*tests_only).key, "label": grant_scope(*tests_only).label}
    ]
