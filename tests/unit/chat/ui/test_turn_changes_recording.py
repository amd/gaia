# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A chat turn's file changes are recorded and tied to its answer (#5225).

Drives the real streaming and non-streaming chat paths with an agent whose
turn edits a file, then checks the turn shows up under the saved assistant
message and undoes cleanly.
"""

import asyncio
import types

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.agents.base import file_snapshots as fs
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest


class _EditingAgent:
    def __init__(self, model_id, target):
        self.model_id = model_id
        self.target = target
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.streaming = True

    def _register_tools(self):
        pass

    def process_query(self, _message, **_kwargs):
        fs.before_change(self.target, "edit_file")
        self.target.write_bytes(b"edited by the turn")
        return "done"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", tmp_path / "home" / "c.json")
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    helpers._agent_cache.clear()
    target = tmp_path / "notes.txt"
    target.write_bytes(b"original")
    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _EditingAgent("M-GGUF", target)
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    yield db, session, target
    helpers._agent_cache.clear()


def test_streaming_turn_is_recorded_under_its_message(setup):
    db, session, target = setup
    request = ChatRequest(session_id=session["id"], message="edit it", stream=True)

    async def drain():
        run = types.SimpleNamespace(handler=None)
        async for _ in helpers._stream_chat_impl(run, db, session, request):
            pass

    asyncio.run(drain())

    (turn,) = fs.list_turns(session["id"])
    assistant = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    assert turn["message_id"] == assistant[-1]["id"]
    assert turn["files"] == [
        {"path": str(target), "change": "edited", "tool": "edit_file"}
    ]
    fs.restore_turn(session["id"], turn["turn_id"])
    assert target.read_bytes() == b"original"


def test_non_streaming_turn_hands_back_its_recording(setup):
    db, session, target = setup
    request = ChatRequest(session_id=session["id"], message="edit it", stream=False)
    box: dict = {}
    asyncio.run(helpers._get_chat_response(db, session, request, turn_box=box))
    box["recording"].link_message(99)

    (turn,) = fs.list_turns(session["id"])
    assert turn["message_id"] == 99
    assert turn["finished_at"] is not None


# ── a turn relayed to the agent's sidecar ──────────────────────────────────


def _relay_to_a_sidecar_that_edits(monkeypatch, api_version, created, deleted):
    """Relay the chat to a stand-in sidecar that creates one file and deletes
    another, recording them the way the real one does: on its own thread,
    under the turn id the request carries."""
    import threading

    from gaia.ui.email_sidecar import daemon_client, relay

    handle = types.SimpleNamespace(
        api_version=api_version,
        proxy=lambda: types.SimpleNamespace(init=lambda: (200, {})),
    )
    monkeypatch.setattr(daemon_client, "acquire_handle", lambda _agent: handle)
    monkeypatch.setattr(helpers, "_should_relay_to_sidecar", lambda *_a: True)
    monkeypatch.setattr(helpers, "_agent_type_unknown", lambda *_a: False)
    monkeypatch.setattr(helpers, "_replay_cloud_key_for_relay", lambda *_a: None)
    sent: list = []

    def sidecar(handler, _proxy, *, session_id=None, changes_turn_id=None, **_kw):
        sent.append(changes_turn_id)

        def run():
            if changes_turn_id is None:
                created.write_bytes(b"new")
                deleted.unlink()
                return
            with fs.recording_turn(session_id, changes_turn_id):
                fs.before_change(created, "write_file")
                created.write_bytes(b"new")
                fs.before_change(deleted, "delete_file")
                deleted.unlink()

        worker = threading.Thread(target=run)
        worker.start()
        worker.join()
        relay._dispatch_one(handler, {"type": "final", "answer": "done"})

    monkeypatch.setattr(relay, "relay_query", sidecar)
    return sent


def _run_relayed_turn(db, session):
    request = ChatRequest(session_id=session["id"], message="tidy up", stream=True)

    async def drain():
        run = types.SimpleNamespace(handler=None)
        async for _ in helpers._stream_chat_impl(run, db, session, request):
            pass

    asyncio.run(drain())
    return [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"][-1]


@pytest.fixture
def relayed(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", tmp_path / "home" / "c.json")
    monkeypatch.setattr(helpers, "_agent_registry", None)
    created, deleted = tmp_path / "notes.txt", tmp_path / "old.txt"
    deleted.write_bytes(b"old")
    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="gaia")
    return db, session, created, deleted


def test_a_relayed_turns_changes_are_listed_under_its_answer(relayed, monkeypatch):
    from gaia.ui import session_outputs

    db, session, created, deleted = relayed
    sent = _relay_to_a_sidecar_that_edits(monkeypatch, "2.19", created, deleted)
    answer = _run_relayed_turn(db, session)

    (turn,) = fs.list_turns(session["id"])
    assert turn["turn_id"] == sent[0]
    assert turn["message_id"] == answer["id"]
    assert turn["finished_at"] is not None
    assert {(f["path"], f["change"]) for f in turn["files"]} == {
        (str(created), "created"),
        (str(deleted), "deleted"),
    }
    changes = fs.session_changes(session["id"])
    assert {f["path"] for f in changes["files"]} == {str(created), str(deleted)}

    outputs = session_outputs.list_outputs(db, session["id"])["items"]
    assert {(i["path"], i["change"], i["message_id"]) for i in outputs} == {
        (str(created), "created", answer["id"]),
        (str(deleted), "deleted", answer["id"]),
    }

    fs.restore_turn(session["id"], turn["turn_id"])
    assert not created.exists() and deleted.read_bytes() == b"old"


def test_an_older_sidecar_is_not_asked_to_record(relayed, monkeypatch):
    db, session, created, deleted = relayed
    sent = _relay_to_a_sidecar_that_edits(monkeypatch, "2.18", created, deleted)
    _run_relayed_turn(db, session)
    assert sent == [None]
    assert fs.list_turns(session["id"]) == []


class _EmptyEditingAgent(_EditingAgent):
    def process_query(self, _message, **_kwargs):
        fs.before_change(self.target, "edit_file")
        self.target.write_bytes(b"edited before an empty reply")
        return ""


def _error_turn(db, session, target, monkeypatch, backend_problem):
    agent = _EmptyEditingAgent("M-GGUF", target)
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    monkeypatch.setattr(helpers, "_empty_answer_backend_problem", backend_problem)
    request = ChatRequest(session_id=session["id"], message="edit it", stream=True)

    async def drain():
        run = types.SimpleNamespace(handler=None)
        async for _ in helpers._stream_chat_impl(run, db, session, request):
            pass

    asyncio.run(drain())
    (turn,) = fs.list_turns(session["id"])
    assistant = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    assert assistant
    assert turn["message_id"] == assistant[-1]["id"]
    assert turn["files"][0]["path"] == str(target)


def test_empty_backend_error_turn_links_file_changes_for_undo(setup, monkeypatch):
    db, session, target = setup

    async def backend_problem(*_args):
        return "backend failed"

    _error_turn(db, session, target, monkeypatch, backend_problem)


def test_unexpected_error_turn_links_file_changes_for_undo(setup, monkeypatch):
    db, session, target = setup

    async def backend_problem(*_args):
        raise RuntimeError("health check failed")

    _error_turn(db, session, target, monkeypatch, backend_problem)


def _collect(db, session, request):
    events = []

    async def drain():
        run = types.SimpleNamespace(handler=None)
        async for line in helpers._stream_chat_impl(run, db, session, request):
            events.append(line)

    asyncio.run(drain())
    return events


def test_a_turn_that_fails_before_its_agent_starts_saves_its_error_quietly(
    setup, monkeypatch, caplog
):
    """No recording exists yet, so the error path must not trip over one."""
    db, session, _target = setup

    def broken_handler(*_a, **_k):
        raise RuntimeError("could not open the stream")

    monkeypatch.setattr("gaia.ui.sse_handler.SSEOutputHandler", broken_handler)
    request = ChatRequest(session_id=session["id"], message="edit it", stream=True)

    events = _collect(db, session, request)

    assert any('"type": "error"' in e for e in events)
    assistant = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert "Could not persist chat error" not in caplog.text
    assert fs.list_turns(session["id"]) == []


def test_unexpected_error_turn_warns_when_undo_cannot_be_linked(setup, monkeypatch):
    db, session, target = setup
    from gaia.ui.turn_changes import TurnRecording

    monkeypatch.setattr(
        TurnRecording, "link_message", lambda _self, _mid: "Undo won't be offered"
    )
    agent = _EmptyEditingAgent("M-GGUF", target)
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")

    async def backend_problem(*_args):
        raise RuntimeError("health check failed")

    monkeypatch.setattr(helpers, "_empty_answer_backend_problem", backend_problem)
    request = ChatRequest(session_id=session["id"], message="edit it", stream=True)

    events = _collect(db, session, request)

    warning = next(i for i, e in enumerate(events) if "Undo won't be offered" in e)
    error = next(i for i, e in enumerate(events) if '"type": "error"' in e)
    assert warning < error


def test_an_error_turn_that_changed_nothing_offers_no_undo(setup, monkeypatch):
    db, session, target = setup

    class _EmptyAgent(_EditingAgent):
        def process_query(self, _message, **_kwargs):
            return ""

    helpers._store_agent(
        session["id"], "M-GGUF", [], _EmptyAgent("M-GGUF", target), "chat"
    )

    async def backend_problem(*_args):
        return "backend failed"

    monkeypatch.setattr(helpers, "_empty_answer_backend_problem", backend_problem)
    request = ChatRequest(session_id=session["id"], message="hi", stream=True)

    events = _collect(db, session, request)

    assert any('"type": "error"' in e for e in events)
    assert not any("Undo won't be offered" in e for e in events)
    assert fs.list_turns(session["id"]) == []
    assert target.read_bytes() == b"original"
