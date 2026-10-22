# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``changes_turn_id`` field (contract 2.19).

The Agent UI relays desktop chats to this sidecar, so the files a relayed turn
wrote were never copied for undo and its Changes view stayed empty. With the
field, the run records its changes under that turn of the chat's session, in
the same store the Agent UI reads, and finishes the recording before the
terminal event so the UI never reads a half-written turn.
"""

from __future__ import annotations

import json
import threading
import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

from gaia.agents.base import file_snapshots as fs  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"
SESSION = "chat-1"


class _WritingAgent:
    """Writes a file the way the file tools do, answers, then keeps running."""

    target = None

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.release = threading.Event()

    def process_query(self, query, max_steps=None):
        fs.before_change(self.target, "write_file")
        self.target.write_bytes(b"hello")
        self.console.print_final_answer("Wrote it.")
        # Post-answer work: the terminal event must not wait for it.
        self.release.wait(timeout=10)
        return {"answer": "Wrote it."}

    def close(self):
        pass


@pytest.fixture
def built(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    _WritingAgent.target = tmp_path / "notes.txt"
    agents: list = []

    def build(**kw):
        agents.append(_WritingAgent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL), agents
    finally:
        for agent in agents:
            agent.release.set()
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _query(client, **fields):
    body = {"query": "write notes.txt", "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    return client.post("/v1/gaia/query", json=body)


def _events(response):
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_the_contract_advertises_changes():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 19)
    assert "changes_turn_id" in server_mod.QueryRequest.model_fields


def test_a_turns_changes_are_recorded_before_it_ends(built):
    client, agents = built
    turn_id = fs.new_turn_id()
    response = _query(client, session_id=SESSION, changes_turn_id=turn_id)
    assert response.status_code == 200
    assert _events(response)[-1]["type"] == "final"

    # The agent is still running its post-answer work, yet the turn is done.
    (turn,) = fs.list_turns(SESSION)
    assert turn["turn_id"] == turn_id
    assert turn["finished_at"] is not None
    assert turn["files"] == [
        {"path": str(_WritingAgent.target), "change": "created", "tool": "write_file"}
    ]
    agents[-1].release.set()


def test_nothing_is_recorded_unless_asked(built):
    client, agents = built
    agents_before = len(agents)
    assert _query(client, session_id=SESSION).status_code == 200
    agents[agents_before].release.set()
    assert _WritingAgent.target.read_bytes() == b"hello"
    assert fs.list_turns(SESSION) == []


def test_changes_need_a_session(built):
    client, _agents = built
    response = _query(client, changes_turn_id=fs.new_turn_id())
    assert response.status_code == 422
    assert "needs session_id" in response.text


@pytest.mark.parametrize("bad", ["../escape", "20260101000000", "x" * 40])
def test_a_turn_id_that_is_not_one_is_refused(built, bad):
    client, _agents = built
    assert _query(client, session_id=SESSION, changes_turn_id=bad).status_code == 422


class _AskingAgent(_WritingAgent):
    """Writes, then asks to run a gated tool nobody can approve."""

    def process_query(self, query, max_steps=None):
        fs.before_change(self.target, "write_file")
        self.target.write_bytes(b"hello")
        self.console._emit(
            {"type": "permission_request", "tool": "run_shell_command", "args": {}}
        )
        self.release.wait(timeout=10)
        return {"answer": "stopped"}


def test_a_refused_confirmation_still_finishes_the_recording(built, monkeypatch):
    client, agents = built

    def build(**kw):
        agents.append(_AskingAgent(**kw))
        return agents[-1]

    monkeypatch.setattr(sr, "build_session_agent", build)
    response = _query(
        client,
        session_id=SESSION,
        changes_turn_id=fs.new_turn_id(),
        can_answer_questions=False,
    )
    assert response.status_code == 200
    (turn,) = fs.list_turns(SESSION)
    assert turn["finished_at"] is not None
    assert [f["change"] for f in turn["files"]] == ["created"]


def test_an_unsaved_recording_is_reported_before_the_end(built, monkeypatch):
    client, _agents = built

    def fail(_self):
        raise OSError("disk full")

    monkeypatch.setattr(fs.TurnRecorder, "finish", fail)
    response = _query(client, session_id=SESSION, changes_turn_id=fs.new_turn_id())
    events = _events(response)
    assert events[-1]["type"] == "final"
    assert {
        "type": "status",
        "message": "Undo may not work for this turn: disk full",
    } in events
