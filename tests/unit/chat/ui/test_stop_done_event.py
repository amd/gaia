# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A stopped turn's closing ``done`` event says it was stopped.

The Agent UI keeps a stopped turn on screen with a "Stopped" marker only when
the stream's last ``done`` event carries ``cancelled: true``. These tests run a
real streaming turn through the SSE producer and press Stop the way the UI
does (``/api/chat/cancel``), so they catch the flag going missing on the wire,
not just in the payload helper.
"""

import asyncio
import json
import threading
from unittest.mock import AsyncMock, patch

import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager

pytestmark = pytest.mark.asyncio


class _StoppableAgent:
    """Cached agent whose turn runs until Stop, or answers straight away."""

    def __init__(self, answer=None):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.answer = answer
        self.started = threading.Event()
        self._session_id = None

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.started.set()
        if self.answer is not None:
            return {"result": self.answer}
        # Like the agent loop: bail at the next step boundary once stopped.
        handler = helpers._active_sse_handlers.get(self._session_id)
        assert handler is not None and handler.cancelled.wait(10)
        return {"result": ""}


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    manager = RunManager()
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield db
    helpers._agent_cache.clear()
    db.close()


def _seed(db, agent):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent._session_id = session["id"]
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    return session


async def _run_turn(db, session, agent, *, stop):
    """Stream one turn; press Stop once the agent is running if *stop*."""
    request = ChatRequest(session_id=session["id"], message="hi", stream=True)
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    task = asyncio.create_task(consume())
    if stop:
        assert await asyncio.to_thread(agent.started.wait, 5)
        response = await chat.cancel_stream(
            chat.CancelStreamRequest(session_id=session["id"])
        )
        assert response["cancelled"] is True
    await asyncio.wait_for(task, timeout=15)
    return events


def _done(events):
    done = [e for e in events if e.get("type") == "done"]
    assert len(done) == 1, f"expected one done event, got {events!r}"
    return done[0]


async def test_stopped_turn_ends_with_a_cancelled_done_event(runtime):
    db = runtime
    agent = _StoppableAgent()
    session = _seed(db, agent)

    done = _done(await _run_turn(db, session, agent, stop=True))

    assert done["cancelled"] is True
    assert done["content"] == "Cancelled."
    saved = db.get_messages(session["id"])
    assert saved[-1]["id"] == done["message_id"]
    assert saved[-1]["content"] == "Cancelled."


async def test_finished_turn_is_not_labelled_stopped(runtime):
    db = runtime
    agent = _StoppableAgent(answer="Deleted 3 files.")
    session = _seed(db, agent)

    done = _done(await _run_turn(db, session, agent, stop=False))

    assert "cancelled" not in done
    assert done["content"] == "Deleted 3 files."
