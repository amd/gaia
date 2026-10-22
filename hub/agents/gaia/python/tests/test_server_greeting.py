# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``POST /v1/gaia/greeting`` (contract 2.19).

The Agent UI opens a new chat on a greeting the agent writes. An installed
GAIA runs the agent as this sidecar, so the greeting has to come from here:
streamed, under the chat's memory mode, and leaving no session behind.
"""

from __future__ import annotations

import json
import logging

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _GreetingAgent:
    """Streams a fixed greeting and records the memory switch it ran under."""

    words = ["Welcome back. ", "Finish the parser?"]
    fail_with: Exception | None = None

    def __init__(self, **_kwargs):
        self._incognito = False
        self._memory_store = object()
        self.seen: list = []
        self.closed = False

    def greet(self, on_text, instruction=None, progress=None, cancel=None):
        self.seen.append(self._incognito)
        if self.fail_with is not None:
            raise self.fail_with
        for word in self.words:
            on_text(word)
        return {"text": "".join(self.words).strip(), "seconds": 0.1}

    def close(self):
        self.closed = True


@pytest.fixture
def built(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    agents: list = []

    def build(**kw):
        agents.append(_GreetingAgent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    sr.registry.clear()
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL), agents
    finally:
        sr.registry.clear()
        caller_auth.reset()
        _GreetingAgent.fail_with = None


def _events(response):
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_the_contract_advertises_the_greeting():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 19)


def test_the_greeting_streams_and_leaves_no_session(built):
    client, agents = built

    response = client.post("/v1/gaia/greeting", json={})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    assert [e["phase"] for e in events if e["type"] == "status"] == [
        "building",
        "priming",
    ]
    assert [e for e in events if e["type"] != "status"] == [
        {"type": "chunk", "content": "Welcome back. "},
        {"type": "chunk", "content": "Finish the parser?"},
        {
            "type": "done",
            "text": "Welcome back. Finish the parser?",
            "seconds": 0.1,
            "prompt_tokens": None,
            "cached_tokens": None,
        },
    ]
    [agent] = agents
    assert agent.closed
    assert sr.registry._sessions == {}  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "mode, incognito", [("on", False), ("private", True), ("off", True)]
)
def test_the_greeting_follows_the_chats_memory_mode(built, mode, incognito):
    client, agents = built
    client.post("/v1/gaia/greeting", json={"memory": mode})
    assert agents[0].seen == [incognito]


def test_a_failed_greeting_says_why_on_the_stream(built, caplog):
    client, agents = built
    _GreetingAgent.fail_with = ConnectionError(
        "HTTPConnectionPool: Max retries exceeded (connection refused)"
    )
    with caplog.at_level(logging.WARNING):
        events = _events(client.post("/v1/gaia/greeting", json={}))
    assert events[-1]["type"] == "error"
    assert "Lemonade Server is not reachable" in events[-1]["message"]
    assert agents[0].closed


def test_the_greeting_is_not_logged(built, caplog):
    client, _agents = built
    with caplog.at_level(logging.DEBUG):
        client.post("/v1/gaia/greeting", json={})
    assert "Finish the parser?" not in caplog.text


def test_an_unknown_field_is_refused(built):
    client, _agents = built
    assert client.post("/v1/gaia/greeting", json={"name": "x"}).status_code == 422


def test_a_wait_for_the_model_reaches_the_stream(built, monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def queued_then_granted(on_wait_event=None, cancel=None):
        on_wait_event({"event": "queued", "position": 1, "waited_s": 2})
        on_wait_event({"event": "granted", "waited_s": 4})
        yield

    monkeypatch.setattr(server_mod, "interactive_turn", queued_then_granted)
    client, _agents = built
    events = _events(client.post("/v1/gaia/greeting", json={}))
    assert [e["phase"] for e in events if e["type"] == "status"] == [
        "building",
        "model_queue",
        "priming",
        "priming",
    ]
    assert events[-1]["type"] == "done"
