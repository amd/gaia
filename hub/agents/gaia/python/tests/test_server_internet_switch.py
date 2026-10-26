# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``internet_off`` field and ``/sessions/{id}/internet`` (2.22).

A chat with the Internet switch off, relayed from the Agent UI, used to reach the
flagship with no word of it, so the agent went on searching the web. These drive
the real app and put a web tool call through the agent's own guard at the moment
the turn runs, including a switch flipped while the turn is running.
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

from gaia.agents.base.agent import Agent  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _WebAgent:
    """Tries a web search each turn through the base agent's own guard."""

    internet_off = False

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._tools_registry = {}
        self.refusals: list = []

        self.started = threading.Event()
        #: When set by a test, the turn parks here before its web search.
        self.gate = None

    def process_query(self, query, max_steps=None):
        self.started.set()
        if self.gate is not None:
            self.gate.wait(timeout=10)
        refusal = Agent._policy_refusal(self, "search_web", {"query": "weather"})
        self.refusals.append(refusal and refusal["error_type"])
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
        agents.append(_WebAgent(**kw))
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


def test_the_contract_advertises_internet_off():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 22)
    assert "internet_off" in server_mod.QueryRequest.model_fields


def test_an_internet_off_turn_refuses_a_web_search(built):
    client, agents = built
    assert _query(client, internet_off=True).status_code == 200
    assert agents[-1].refusals == ["internet_off"]


def test_an_internet_on_turn_lets_the_web_search_through(built):
    client, agents = built
    assert _query(client, internet_off=False).status_code == 200
    assert agents[-1].refusals == [None]


def test_omitting_the_field_leaves_the_agent_as_it_is(built):
    client, agents = built
    assert _query(client).status_code == 200
    assert agents[-1].refusals == [None]


def test_a_retained_session_follows_the_switch_each_turn(built):
    client, agents = built
    for value in (True, False, True, None):
        fields = {"session_id": "chat-1"}
        if value is not None:
            fields["internet_off"] = value
        assert _query(client, **fields).status_code == 200
    assert len(agents) == 1
    # Omitting the field leaves the session as the last turn set it.
    assert agents[0].refusals == ["internet_off", None, "internet_off", "internet_off"]


def test_a_non_boolean_is_refused(built):
    client, _ = built
    assert _query(client, internet_off="sometimes").status_code == 422


def _switch(client, session_id, internet_off):
    return client.post(
        f"/v1/gaia/sessions/{session_id}/internet",
        json={"internet_off": internet_off},
    )


@pytest.mark.parametrize("turn_starts_off", [False, True])
def test_a_switch_flipped_mid_turn_reaches_the_next_web_call(built, turn_starts_off):
    client, agents = built
    assert _query(client, session_id="chat-1").status_code == 200
    agent = agents[0]
    agent.started.clear()
    agent.gate = threading.Event()
    result = {}

    def run():
        result["response"] = _query(
            client, session_id="chat-1", internet_off=turn_starts_off
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert agent.started.wait(timeout=10), "the turn never started"
        flipped = _switch(client, "chat-1", not turn_starts_off)
        assert flipped.status_code == 200, flipped.text
        assert flipped.json() == {
            "session_id": "chat-1",
            "internet_off": not turn_starts_off,
        }
    finally:
        agent.gate.set()
        worker.join(timeout=10)

    assert result["response"].status_code == 200
    assert agent.refusals[-1] == ("internet_off" if not turn_starts_off else None)


def test_switching_an_unknown_session_is_a_404_not_a_new_session(built):
    client, agents = built
    response = _switch(client, "never-seen", True)
    assert response.status_code == 404
    assert "Send a query on that session first" in response.json()["detail"]
    assert agents == [], "no agent may be built by an internet switch"


def test_the_switch_takes_only_a_boolean(built):
    client, _ = built
    assert _query(client, session_id="chat-1").status_code == 200
    assert _switch(client, "chat-1", "sometimes").status_code == 422
