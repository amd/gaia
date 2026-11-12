# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The chat's agent is told which folder the chat is working in.

A folder picked with the new-chat folder chip was granted, but the model was
never told about it: asked "what are we working on in this folder?", it
guessed the home folder. The note goes to the agent only; the chat keeps
what the user typed.
"""

import threading

import pytest
from fastapi.testclient import TestClient

import gaia.ui._chat_helpers as helpers
from gaia.security import PathValidator
from gaia.ui import session_environment
from gaia.ui.server import create_app
from gaia.ui.session_environment import with_folder_note

ASK = "what are we working on in this folder?"


def test_no_folders_leaves_the_message_alone():
    assert with_folder_note(ASK, []) == ASK


def test_one_folder_is_named_before_the_message():
    noted = with_folder_note(ASK, ["/home/u/Projects/Armada"])
    assert noted.startswith(
        "[This chat is working in the folder /home/u/Projects/Armada."
    )
    assert noted.endswith(f"\n\n{ASK}")


def test_several_folders_are_all_named():
    noted = with_folder_note(ASK, ["/a/one", "/b/two"])
    assert "these folders: /a/one, /b/two." in noted
    assert noted.endswith(f"\n\n{ASK}")


class _RecordingAgent:
    def __init__(self, scope):
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        self.indexed_files = set()
        self.conversation_history = []
        self.rag = None
        self.queries = []
        self._cancel_event = threading.Event()
        self.console = None

    def _register_tools(self):
        pass

    def process_query(self, message, **_kwargs):
        self.queries.append(message)
        return "ok"


@pytest.fixture
def chat_in_a_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    monkeypatch.setattr(session_environment, "relayed", lambda _session: False)
    helpers._agent_cache.clear()
    scope = tmp_path / "scope"
    scope.mkdir()
    folder = tmp_path / "Armada"
    folder.mkdir()

    client = TestClient(create_app(db_path=":memory:"))
    db = client.app.state.db
    sid = db.create_session(model="M-GGUF", agent_type="chat")["id"]
    session_environment.grant_folder(db, sid, str(folder))
    agent = _RecordingAgent(scope)
    helpers._store_agent(sid, "M-GGUF", [], agent, "chat")
    yield client, db, sid, agent, str(folder.resolve())
    helpers._agent_cache.clear()


@pytest.mark.parametrize("stream", [True, False])
def test_the_agent_is_told_the_folder_and_the_chat_keeps_the_question(
    chat_in_a_folder, stream
):
    client, db, sid, agent, folder = chat_in_a_folder

    sent = client.post(
        "/api/chat/send", json={"session_id": sid, "message": ASK, "stream": stream}
    )

    assert sent.status_code == 200, sent.text
    [query] = agent.queries
    assert query.startswith(f"[This chat is working in the folder {folder}.")
    assert query.endswith(f"\n\n{ASK}")
    [user_row] = [m for m in db.get_messages(sid) if m["role"] == "user"]
    assert user_row["content"] == ASK
