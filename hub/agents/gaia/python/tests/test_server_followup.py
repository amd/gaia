# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``POST /v1/gaia/query/{run_id}/followup`` — handing a RUNNING turn a message.

Contract 2.13 (#3620). The turn keeps going on its existing SSE stream; the
agent folds the text in at its next agent-loop step boundary. This is the one
route that writes into a live run without stopping it, so what it must never do
is accept a message it cannot deliver — the caller empties the user's composer
on the strength of the 200, and a quiet drop there looks exactly like the
message being eaten.

Driven through the real ``build_app()`` for the same reason as
``test_server_query.py``: a mocked route proves the handler ran, not that the
run table found the live run or that the agent actually got the words.
"""

from __future__ import annotations

import threading
import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _ParkedAgent:
    """An agent whose turn sits in flight until the test releases it.

    ``queue_followup`` mirrors the base Agent's: the sidecar only ever reaches
    it through ``getattr``, so a faithful stand-in is what makes the 409 branch
    meaningful rather than an artefact of a missing attribute.
    """

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = 0
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._followup_queue = None
        self.refuse_followups = False
        #: Released by the test once it has finished poking the live run.
        self.release = threading.Event()
        #: Set once the turn is actually in flight and registered.
        self.running = threading.Event()

    def queue_followup(self, text: str) -> bool:
        text = (text or "").strip()
        if not text or self.refuse_followups or self._followup_queue is None:
            return False
        self._followup_queue.put(text)
        return True

    def drained(self):
        out = []
        while self._followup_queue is not None and not self._followup_queue.empty():
            out.append(self._followup_queue.get_nowait())
        return out

    def process_query(self, query, max_steps=None):
        self.running.set()
        self.release.wait(timeout=10)
        return {"answer": f"answered: {query}"}

    def close(self):
        self.closed += 1


@pytest.fixture
def built(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)

    agents: list[_ParkedAgent] = []

    def build(**kw):
        agent = _ParkedAgent(**kw)
        agents.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)

    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()

    client = TestClient(server_mod.build_app(), base_url=_BASE_URL)
    try:
        yield client, agents
    finally:
        for a in agents:
            a.release.set()
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _start_run(client, agents):
    """POST a query on a background thread and return (run_id, agent).

    The request is held open deliberately: a follow-up is only meaningful
    against a run that is still streaming, and TestClient's POST does not
    return until the stream ends.
    """
    run_id = str(uuid.uuid4())
    body = {"query": "triage my inbox", "run_id": run_id, "context": []}
    done = threading.Event()

    def post():
        try:
            client.post("/v1/gaia/query", json=body)
        finally:
            done.set()

    t = threading.Thread(target=post, daemon=True)
    t.start()
    assert agents or _wait(lambda: bool(agents)), "the run never built an agent"
    agent = agents[0]
    assert agent.running.wait(timeout=10), "the run never reached the agent loop"
    return run_id, agent, done


def _wait(predicate, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_the_running_turn_receives_the_message(built):
    client, agents = built
    run_id, agent, done = _start_run(client, agents)

    r = client.post(
        f"/v1/gaia/query/{run_id}/followup", json={"text": "only the unread ones"}
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"run_id": run_id, "delivered": True}
    assert agent.drained() == ["only the unread ones"]

    agent.release.set()
    assert done.wait(timeout=10)


def test_the_turn_is_not_interrupted_by_a_followup(built):
    """The point of the route: the agent keeps doing what it was doing."""
    client, agents = built
    run_id, agent, done = _start_run(client, agents)

    client.post(f"/v1/gaia/query/{run_id}/followup", json={"text": "and the calendar"})

    assert agent._cancel_event is not None and not agent._cancel_event.is_set()
    assert not done.is_set(), "the follow-up ended the run it was sent to"

    agent.release.set()
    assert done.wait(timeout=10)


def test_an_unknown_run_is_refused_loudly(built):
    """The caller emptied the composer on the strength of a 200.

    Accepting a message for a run that no longer exists would leave the user
    watching a conversation their words never entered.
    """
    client, _ = built

    r = client.post(
        f"/v1/gaia/query/{uuid.uuid4()}/followup", json={"text": "and the calendar"}
    )

    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "not delivered" in detail and "new query" in detail


def test_an_agent_that_cannot_take_one_is_refused_loudly(built):
    client, agents = built
    run_id, agent, done = _start_run(client, agents)
    agent.refuse_followups = True

    r = client.post(
        f"/v1/gaia/query/{run_id}/followup", json={"text": "and the calendar"}
    )

    assert r.status_code == 409
    assert "not delivered" in r.json()["detail"] or "cannot take" in r.json()["detail"]

    agent.release.set()
    assert done.wait(timeout=10)


def test_an_empty_message_is_rejected_by_the_schema(built):
    client, agents = built
    run_id, agent, done = _start_run(client, agents)

    assert (
        client.post(f"/v1/gaia/query/{run_id}/followup", json={"text": ""}).status_code
        == 422
    )
    assert client.post(f"/v1/gaia/query/{run_id}/followup", json={}).status_code == 422

    agent.release.set()
    assert done.wait(timeout=10)


def test_a_finished_run_stops_accepting_before_it_leaves_the_run_table(built):
    """A 200 must mean something read the message.

    The run leaves the table when the STREAM ends, which is later than when the
    agent stops draining. Left wired across that window, a follow-up would be
    accepted and then read by nothing at all.
    """
    client, agents = built
    run_id, agent, done = _start_run(client, agents)

    agent.release.set()
    assert done.wait(timeout=10)
    assert _wait(
        lambda: agent._followup_queue is None
    ), "the queue stayed wired past the run"

    r = client.post(f"/v1/gaia/query/{run_id}/followup", json={"text": "too late"})
    assert r.status_code in (404, 409), r.text
    assert agent.drained() == []


def test_the_version_advertises_the_capability(built):
    """The TUI gates the POST on this — an unbumped version means the feature
    is present and never used, which is the worse of the two failures."""
    client, _ = built

    reported = client.get("/v1/gaia/version").json()["apiVersion"]
    major, minor = (int(p) for p in reported.split(".")[:2])
    assert (major, minor) >= (
        2,
        13,
    ), f"apiVersion {reported} predates mid-turn follow-ups"
