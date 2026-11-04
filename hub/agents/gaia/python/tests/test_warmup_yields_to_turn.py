# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The start-up warm-up never holds up a turn on a model it isn't warming (#6582).

After a restart the sidecar warms the local default model. A chat on a cloud
model sat for the ~47 s that warm-up took, because the model server was busy
reading the local model's prompt. A turn on another model now stops the
warm-up; a turn on the warmed model still lets it finish and reuses its cache.
"""

from __future__ import annotations

import threading
import uuid

import pytest

pytest.importorskip("gaia_agent")

import gaia_agent.session_registry as registry_mod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402

import gaia.llm.lemonade_client as lc  # noqa: E402
from gaia.chat.sdk import LLMCallCancelled  # noqa: E402

LOCAL = "Qwen3.6-35B-A3B-GGUF"
CLOUD = "fireworks.deepseek-v4p1-flash"
WAIT_S = 10


class _ModelServer:
    """One busy flag, like Lemonade while it reads a prompt: a request for any
    model waits until the request ahead of it is done or abandoned."""

    def __init__(self):
        self.idle = threading.Event()
        self.idle.set()


class _WarmUpAgent:
    model_id = LOCAL

    def __init__(self, server: _ModelServer):
        self.server = server
        self.reading = threading.Event()
        self.finish = threading.Event()
        self.outcome = None

    def warm_up(self, cancel=None):
        self.server.idle.clear()
        self.reading.set()
        try:
            while not self.finish.wait(0.01):
                if cancel is not None and cancel.is_set():
                    self.outcome = "stopped"
                    raise LLMCallCancelled("warm-up stopped")
            self.outcome = "warmed"
            return {"seconds": 47.0, "prompt_tokens": 20339, "cached_tokens": 0}
        finally:
            self.server.idle.set()


class _TurnAgent:
    def __init__(self, server: _ModelServer, **kwargs):
        self.server = server
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.waited_for_server = None

    def process_query(self, query, max_steps=None):
        self.waited_for_server = not self.server.idle.is_set()
        if not self.server.idle.wait(WAIT_S):
            raise TimeoutError("the turn waited on the warm-up")
        return {"answer": "ok"}

    def close(self):
        pass


@pytest.fixture
def sidecar(monkeypatch):
    """A sidecar mid-warm-up of the local default, and a client for its turns."""
    monkeypatch.setattr(lc, "resolve_default_chat_model", lambda: LOCAL)
    monkeypatch.setattr(
        lc.LemonadeClient,
        "get_model_info",
        lambda self, name: {"id": name, "downloaded": True, "size_gb": 23},
    )
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    server = _ModelServer()
    warm_agent = _WarmUpAgent(server)
    turns = []

    def build_turn_agent(**kw):
        agent = _TurnAgent(server, **kw)
        turns.append(agent)
        return agent

    monkeypatch.setattr(registry_mod, "build_session_agent", lambda **k: warm_agent)
    monkeypatch.setattr(server_mod, "build_query_agent", build_turn_agent)
    monkeypatch.setattr(server_mod, "close_agent", lambda agent: None)
    monkeypatch.setattr(server_mod, "_startup_warm_up", server_mod._StartupWarmUp())
    server_mod._registry = server_mod._RunRegistry()

    warming = threading.Thread(target=server_mod._warmup_blocking, daemon=True)
    warming.start()
    assert warm_agent.reading.wait(WAIT_S), "the warm-up never reached the model"
    client = TestClient(server_mod.build_app(), base_url="http://127.0.0.1:8141")
    try:
        yield warm_agent, turns, client, warming
    finally:
        warm_agent.finish.set()
        warming.join(WAIT_S)
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _turn(client, model):
    body = {"query": "hello", "run_id": str(uuid.uuid4()), "context": []}
    if model is not None:
        body["model"] = model
    response = client.post("/v1/gaia/query", json=body)
    assert response.status_code == 200, response.text
    return response.text


def test_a_cloud_turn_does_not_wait_for_a_local_warm_up(sidecar):
    warm_agent, turns, client, warming = sidecar

    text = _turn(client, CLOUD)

    assert "waited on the warm-up" not in text
    assert warm_agent.outcome == "stopped"
    warming.join(WAIT_S)
    assert not warming.is_alive()


def test_a_turn_on_the_warmed_model_lets_the_warm_up_finish(sidecar):
    warm_agent, turns, client, warming = sidecar
    done = {}

    def turn():
        done["text"] = _turn(client, LOCAL)

    t = threading.Thread(target=turn, daemon=True)
    t.start()
    # The turn queues behind the warm-up instead of stopping it.
    assert not server_mod._startup_warm_up.cancel.wait(0.3)
    warm_agent.finish.set()
    t.join(WAIT_S)
    assert warm_agent.outcome == "warmed"
    assert "waited on the warm-up" not in done["text"]
    assert turns[0].waited_for_server is True


def test_a_turn_with_no_model_runs_the_default_and_keeps_the_warm_up(sidecar):
    warm_agent, _turns, client, _warming = sidecar
    done = {}
    t = threading.Thread(target=lambda: done.setdefault("t", _turn(client, None)))
    t.start()
    assert not server_mod._startup_warm_up.cancel.wait(0.3)
    warm_agent.finish.set()
    t.join(WAIT_S)
    assert warm_agent.outcome == "warmed"


def test_a_turn_before_the_warm_up_picks_its_model_still_stops_it(monkeypatch):
    """The warm-up must not load its model over one a turn already uses."""
    monkeypatch.setattr(lc, "resolve_default_chat_model", lambda: LOCAL)
    monkeypatch.setattr(
        lc.LemonadeClient,
        "get_model_info",
        lambda self, name: {"id": name, "downloaded": True, "size_gb": 23},
    )
    built = []
    monkeypatch.setattr(
        registry_mod, "build_session_agent", lambda **k: built.append(k) or 1 / 0
    )
    state = server_mod._StartupWarmUp()
    monkeypatch.setattr(server_mod, "_startup_warm_up", state)

    state.note_turn("Gemma-4-E4B-it-GGUF")
    server_mod._warmup_blocking()

    assert state.cancel.is_set()
    assert built == [], "the warm-up built an agent to load a model nobody uses"
    assert state.done.is_set()


def test_the_same_model_under_its_user_prefix_is_not_another_model():
    state = server_mod._StartupWarmUp()
    state.aim(LOCAL)
    state.note_turn(f"user.{LOCAL}")
    assert not state.cancel.is_set()
    state.note_turn(CLOUD)
    assert state.cancel.is_set()
