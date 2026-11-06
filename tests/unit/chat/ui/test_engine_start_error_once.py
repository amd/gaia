# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat whose engine can't start says so once, as the chat's error."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.llm.lemonade_client import GaiaLemonadeStartError
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest

REASON = (
    "GAIA's Lemonade Server install is incomplete: "
    r"C:\gaia\lemonade\dist\2026.44.1\lemond.exe is missing"
)


class _EngineDownAgent:
    def __init__(self):
        self.indexed_files = set()
        self.conversation_history = []
        self.rag = None
        self.path_validator = None
        self._cancel_event = threading.Event()
        self.console = None

    def _register_tools(self):
        pass

    def process_query(self, _message, **_kwargs):
        raise GaiaLemonadeStartError(
            "Could not start GAIA's Lemonade Server: the daemon could not "
            f"start Lemonade Server: {REASON}",
            REASON,
            Path("daemon.log"),
        )


@pytest.fixture
def ui_chat(monkeypatch):
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    helpers._agent_cache.clear()
    db = ChatDatabase(":memory:")
    session = db.create_session(model="accounts/fireworks/models/x", agent_type="chat")
    helpers._store_agent(
        session["id"], "accounts/fireworks/models/x", [], _EngineDownAgent(), "chat"
    )
    yield db, db.get_session(session["id"])
    helpers._agent_cache.clear()
    db.close()


def _events(db, session):
    request = ChatRequest(session_id=session["id"], message="hello", stream=True)
    events = []

    async def drain():
        async for chunk in helpers._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

    asyncio.run(drain())
    return events


def test_the_engine_error_is_shown_once_as_the_failed_answer(ui_chat):
    db, session = ui_chat
    events = _events(db, session)

    assert not [e for e in events if e.get("type") == "error"], events
    errors = [e for e in events if e.get("type") == "agent_error"]
    assert len(errors) == 1 and errors[0].get("fatal") is True, events
    (done,) = [e for e in events if e.get("type") == "done"]
    assert done.get("failed") is True
    assert "failure" not in done, "a failed start must not offer to start it again"
    shown = done["content"]
    assert shown == errors[0]["content"]
    assert shown.startswith("GAIA's AI engine couldn't start: ")
    assert "Agent error" not in shown
    assert shown.count(REASON) == 1
    assert "Open Settings > Setup and press Run setup again" in shown

    (stored,) = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    assert stored["content"] == shown
    steps = [s for s in stored["agent_steps"] or [] if s["type"] == "error"]
    assert [s["detail"] for s in steps] == [shown]


class _CrashingAgent(_EngineDownAgent):
    def process_query(self, _message, **_kwargs):
        raise KeyError("missing_field")


def test_an_unexplained_crash_keeps_its_label_and_is_shown_once(ui_chat):
    db, session = ui_chat
    helpers._store_agent(
        session["id"], "accounts/fireworks/models/x", [], _CrashingAgent(), "chat"
    )
    events = _events(db, session)

    assert not [e for e in events if e.get("type") == "error"], events
    (done,) = [e for e in events if e.get("type") == "done"]
    assert done.get("failed") is True
    assert done["content"].startswith("Agent error: ")
    assert done["content"].count("missing_field") == 1


class _CutOffAgent(_EngineDownAgent):
    def process_query(self, _message, **_kwargs):
        self.console.print_streaming_text("The first half of the answer", True)
        raise KeyError("missing_field")


def test_a_crash_after_partial_text_still_sends_the_error_event(ui_chat):
    db, session = ui_chat
    helpers._store_agent(
        session["id"], "accounts/fireworks/models/x", [], _CutOffAgent(), "chat"
    )
    events = _events(db, session)

    assert any(e.get("type") == "chunk" for e in events), events
    assert not [e for e in events if e.get("type") == "error"], events
    errors = [e for e in events if e.get("type") == "agent_error"]
    assert len(errors) == 1 and errors[0].get("fatal") is True, events
    assert "missing_field" in errors[0]["content"]
    (done,) = [e for e in events if e.get("type") == "done"]
    assert done.get("failed") is True
    assert done["content"].startswith("The first half of the answer")
    assert "[Error: 'missing_field']" in done["content"]

    (stored,) = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    steps = [s for s in stored["agent_steps"] or [] if s["type"] == "error"]
    assert [s["detail"] for s in steps] == [errors[0]["content"]]
