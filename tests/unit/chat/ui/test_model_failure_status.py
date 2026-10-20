# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A turn a model failure ended is shown and recorded as failed, never done.

The bug: with a cloud model that stopped responding mid-turn, the Agent UI
listed the chat as done and its last message was the model's opening sentence
("I'll start by loading the skill…"). No error, and the deliverable silently
missing. These run a real streaming turn through the SSE producer, once per
way the news arrives: an in-process answer marked failed, an in-process agent
that only reports it on its result, and a relayed sidecar's terminal error.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.run_manager import RunManager

pytestmark = pytest.mark.asyncio

_OPENING = "I'll start by loading the office-docs skill. "
_STOPPED = (
    "The model stopped responding at step 13, so this request is unfinished. "
    "Fireworks AI: 5 attempts failed, the last with connection error."
)


class _Agent:
    """Cached agent whose turn is a script of SSE events plus a result."""

    def __init__(self, events, result):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self._session_id = None
        self.events = events
        self.result = result

    def _register_tools(self):
        pass

    def process_query(self, _message):
        handler = helpers._active_sse_handlers.get(self._session_id)
        assert handler is not None
        for event in self.events:
            handler._emit(event)
        return self.result


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", RunManager()),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield database
    helpers._agent_cache.clear()
    database.close()


async def _turn(db, agent):
    """Stream one turn; return its events and how the chat list records it."""
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent._session_id = session["id"]
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    request = ChatRequest(
        session_id=session["id"], message="Make the deck", stream=True
    )
    events, outcome = [], helpers._RunOutcome()

    async def consume():
        async for frame in helpers._stream_chat_response(db, session, request):
            outcome.observe(frame)
            for line in frame.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    await asyncio.wait_for(consume(), timeout=15)
    (done,) = [e for e in events if e.get("type") == "done"]
    saved = db.get_messages(session["id"])[-1]["content"]
    return done, outcome.status(), saved


async def test_an_answer_marked_failed_records_the_turn_failed(db):
    agent = _Agent(
        [
            {"type": "chunk", "content": _OPENING},
            {"type": "answer", "content": _STOPPED, "failed": True},
        ],
        {"result": _STOPPED, "status": "failed", "model_failed": True},
    )
    done, status, saved = await _turn(db, agent)

    assert status == "failed"
    assert done["failed"] is True
    assert done["content"] == _STOPPED == saved


async def test_a_failed_result_with_no_answer_event_still_explains_it(db):
    """An account refusal reports on the result and an error step only; the
    message must not stop at the opening sentence."""
    refusal = "`gone` isn't available on Fireworks AI (HTTP 404)."
    agent = _Agent(
        [
            {"type": "chunk", "content": _OPENING},
            {"type": "agent_error", "content": refusal},
        ],
        {"result": refusal, "status": "failed", "model_failed": True},
    )
    done, status, saved = await _turn(db, agent)

    assert status == "failed"
    assert done["failed"] is True
    assert saved.startswith(_OPENING.strip())
    assert saved.endswith(refusal)


async def test_a_relayed_terminal_error_records_the_turn_failed(db):
    """The relay's terminal error after the opening sentence, as the flagship
    sidecar sends it when its model gives up."""
    error = "Language model unreachable at step 13: 5 attempts failed"
    agent = _Agent(
        [
            {"type": "chunk", "content": _OPENING},
            {"type": "agent_error", "content": error, "fatal": True},
        ],
        {"result": ""},
    )
    done, status, saved = await _turn(db, agent)

    assert status == "failed"
    assert done["failed"] is True
    assert saved == f"{_OPENING.strip()}\n\n{error}"


async def test_an_ordinary_turn_is_still_done(db):
    agent = _Agent(
        [
            {"type": "chunk", "content": _OPENING},
            {"type": "answer", "content": "Saved deck.pptx."},
        ],
        {"result": "Saved deck.pptx.", "status": "success", "model_failed": False},
    )
    done, status, saved = await _turn(db, agent)

    assert status == "done"
    assert "failed" not in done
    assert saved == "Saved deck.pptx."
