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
