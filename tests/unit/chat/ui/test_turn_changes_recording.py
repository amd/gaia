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
