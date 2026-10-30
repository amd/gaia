# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A stopped or cut-off turn reads to the model as withdrawn, not pending.

Left as a question with a blank reply, the model answers the stopped request
on the next turn: stop an essay, ask for a capital, get the essay back.
"""

import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gaia.agents.base.history import transcript_turns
from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.restart_recovery import (
    CANCELLED_TURN_NOTICE,
    ENDED_WITHOUT_REPLY_NOTICE,
    INTERRUPTED_TURN_NOTICE,
    tools_ran_notice,
)
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager

STOPPED = helpers._STOPPED_REPLY_FOR_MODEL
CUT_OFF = helpers._CUT_OFF_REPLY_FOR_MODEL


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


def _session(db):
    return db.create_session(model="M-GGUF", agent_type="chat")["id"]


def test_stopped_reply_without_text_reads_as_withdrawn(db):
    sid = _session(db)
    db.add_message(sid, "user", "Write an 800-word essay.")
    db.add_message(sid, "assistant", "Cancelled.")

    assert transcript_turns(helpers.model_context_messages(db, sid)) == [
        [
            {"role": "user", "content": "Write an 800-word essay."},
            {"role": "assistant", "content": STOPPED},
        ]
    ]
    # The chat itself still shows the stop marker.
    assert db.get_messages(sid)[-1]["content"] == "Cancelled."


def test_recorded_stopped_turn_replaces_its_blank_answer(db):
    sid = _session(db)
    recorded = [
        {"role": "user", "content": "Write an 800-word essay."},
        {"role": "assistant", "content": ""},
    ]
    db.add_message(sid, "user", "Write an 800-word essay.")
    db.add_message(sid, "assistant", "Cancelled.", model_messages=recorded)

    turns = transcript_turns(helpers.model_context_messages(db, sid))

    assert turns == [[recorded[0], {"role": "assistant", "content": STOPPED}]]


def test_recorded_turn_ending_on_a_tool_call_gets_the_note_appended(db):
    sid = _session(db)
    call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "search", "arguments": "{}"}}],
    }
    db.add_message(sid, "user", "Find it.")
    db.add_message(
        sid,
        "assistant",
        "Cancelled.",
        model_messages=[{"role": "user", "content": "Find it."}, call],
    )

    turn = transcript_turns(helpers.model_context_messages(db, sid))[0]

    assert turn[1] == call
    assert turn[-1] == {"role": "assistant", "content": STOPPED}


def test_turn_cut_off_when_gaia_closed_keeps_its_text_and_reads_as_withdrawn(db):
    sid = _session(db)
    question = db.begin_turn(sid, "Write an 800-word essay.")
    db.save_turn_partial(question, "The first paragraph.", ["web_search"])
    db.close_turn(question, INTERRUPTED_TURN_NOTICE, tools_notice=tools_ran_notice)

    reply = helpers.model_context_messages(db, sid)[-1]["content"]

    assert reply == (
        "The first paragraph.\n\n" + tools_ran_notice(["web_search"]) + "\n\n" + CUT_OFF
    )
    assert INTERRUPTED_TURN_NOTICE not in reply


def test_turn_that_ended_without_a_reply_reads_as_withdrawn(db):
    sid = _session(db)
    db.add_message(sid, "user", "Hi")
    db.add_message(sid, "assistant", ENDED_WITHOUT_REPLY_NOTICE)

    assert helpers.model_context_messages(db, sid)[-1]["content"] == CUT_OFF


def test_answered_turns_are_left_alone(db):
    sid = _session(db)
    db.add_message(sid, "user", "Capital of Mongolia?")
    db.add_message(sid, "assistant", "Ulaanbaatar. Cancelled.")

    assert helpers.model_context_messages(db, sid) == db.get_context_messages(sid)


def test_relayed_sidecar_history_carries_the_withdrawn_reply(db, monkeypatch):
    import gaia.agents.base.history as history

    monkeypatch.setattr(history, "relayed_history_budget", lambda *a, **k: 32768)
    sid = _session(db)
    db.add_message(sid, "user", "Write an 800-word essay.")
    db.add_message(sid, "assistant", "Cancelled.")

    pairs = helpers._build_history_pairs(helpers.model_context_messages(db, sid))

    assert helpers._relayed_history(pairs, "M-GGUF", "next", None, None) == [
        {"role": "user", "content": "Write an 800-word essay."},
        {"role": "assistant", "content": STOPPED},
    ]


# ── Through a real streamed turn ─────────────────────────────────────────────


class _EssayAgent:
    """Cached agent that streams *partial* text, then runs until Stop."""

    def __init__(self, partial="", records_turn=True):
        self.records_turn = records_turn
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.partial = partial
        self.started = threading.Event()
        self._session_id = None

    def _register_tools(self):
        pass

    def process_query(self, message):
        handler = helpers._active_sse_handlers.get(self._session_id)
        if self.partial:
            handler._emit({"type": "chunk", "content": self.partial})
        self.started.set()
        assert handler.cancelled.wait(10)
        if not self.records_turn:
            # Like a relayed sidecar turn: text only, no recorded messages.
            return {"result": ""}
        # As the agent loop records a cancelled turn: a blank closing answer.
        return {
            "result": "",
            "model_messages": [
                {"role": "user", "content": message},
                {"role": "assistant", "content": ""},
            ],
        }


@pytest.fixture
def runtime():
    database = ChatDatabase(":memory:")
    manager = RunManager()
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield database
    helpers._agent_cache.clear()
    database.close()


async def _stop_turn(db, agent):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent._session_id = session["id"]
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    request = ChatRequest(
        session_id=session["id"], message="Write an 800-word essay.", stream=True
    )
    # As the chat route does before it streams the turn.
    db.begin_turn(session["id"], request.message)
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    task = asyncio.create_task(consume())
    assert await asyncio.to_thread(agent.started.wait, 5)
    await chat.cancel_stream(chat.CancelStreamRequest(session_id=session["id"]))
    await asyncio.wait_for(task, timeout=15)
    done = [e for e in events if e.get("type") == "done"]
    assert len(done) == 1 and done[0]["cancelled"] is True, events
    return session["id"]


@pytest.mark.asyncio
async def test_turn_stopped_before_any_text_is_withdrawn_for_the_next_turn(runtime):
    agent = _EssayAgent()
    sid = await _stop_turn(runtime, agent)

    assert helpers.model_turns(agent, runtime, sid) == [
        [
            {"role": "user", "content": "Write an 800-word essay."},
            {"role": "assistant", "content": STOPPED},
        ]
    ]


@pytest.mark.asyncio
async def test_turn_stopped_mid_answer_shows_the_model_what_it_wrote(runtime):
    agent = _EssayAgent(partial="The first paragraph.")
    sid = await _stop_turn(runtime, agent)

    assert runtime.get_messages(sid)[-1]["content"] == "The first paragraph."
    assert helpers.model_turns(agent, runtime, sid)[-1][-1] == {
        "role": "assistant",
        "content": f"The first paragraph.\n\n{STOPPED}",
    }
    # A relayed sidecar reads the reply text, not the recorded turn.
    pairs = helpers._build_history_pairs(helpers.model_context_messages(runtime, sid))
    assert pairs == [("Write an 800-word essay.", f"The first paragraph.\n\n{STOPPED}")]


@pytest.mark.asyncio
async def test_turn_stopped_mid_answer_without_a_recorded_turn_keeps_the_stop_note(
    runtime,
):
    agent = _EssayAgent(partial="The first paragraph.", records_turn=False)
    sid = await _stop_turn(runtime, agent)

    assert runtime.get_messages(sid)[-1]["content"] == "The first paragraph."
    pairs = helpers._build_history_pairs(helpers.model_context_messages(runtime, sid))
    assert pairs == [("Write an 800-word essay.", f"The first paragraph.\n\n{STOPPED}")]


@pytest.mark.parametrize(
    "text",
    [
        helpers._CANCELLED_REPLY,
        INTERRUPTED_TURN_NOTICE,
        CANCELLED_TURN_NOTICE,
        ENDED_WITHOUT_REPLY_NOTICE,
    ],
)
def test_the_ui_matches_the_same_cut_off_text(text):
    """The footer matches these by text; a reworded notice would read as answered."""
    bubble = (
        Path(__file__).resolve().parents[4]
        / "src/gaia/apps/webui/src/components/MessageBubble.tsx"
    ).read_text(encoding="utf-8")
    assert f"'{text}'" in bubble, f"MessageBubble.tsx no longer lists {text!r}"
