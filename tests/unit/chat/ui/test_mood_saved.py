# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The reaction GAIA named for a reply is saved with it.

It picks the icon her eyes settle on, which stays with the answer, so it has to
survive a reload. Runs real streamed turns through the SSE producer.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.run_manager import RunManager
from gaia.ui.utils import message_to_response

pytestmark = pytest.mark.asyncio


class _MoodAgent:
    """Cached agent that names the given moods, then answers."""

    def __init__(self, moods, answer):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.moods = moods
        self.answer = answer
        self._session_id = None

    def _register_tools(self):
        pass

    def process_query(self, message):
        handler = helpers._active_sse_handlers.get(self._session_id)
        for mood in self.moods:
            handler.print_mood(mood)
        handler.print_final_answer(self.answer)
        return {
            "result": self.answer,
            "model_messages": [
                {"role": "user", "content": message},
                {"role": "assistant", "content": self.answer},
            ],
        }


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


async def _run(db, moods, answer="Thank you, that means a lot."):
    agent = _MoodAgent(moods, answer)
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent._session_id = session["id"]
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    request = ChatRequest(
        session_id=session["id"], message="You're the best", stream=True
    )
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    await asyncio.wait_for(consume(), timeout=15)
    return session["id"], events


async def test_the_reply_keeps_the_last_mood_named(runtime):
    # The first came with a step; the reply's own is the last one.
    sid, events = await _run(runtime, ["curious", "love"])

    assert [e["mood"] for e in events if e.get("type") == "mood"] == ["curious", "love"]
    saved = runtime.get_messages(sid)[-1]
    assert saved["role"] == "assistant"
    assert saved["mood"] == "love"
    assert message_to_response(saved).mood == "love"


async def test_a_reply_without_a_mood_saves_none(runtime):
    sid, _ = await _run(runtime, [], answer="Here is the summary.")

    saved = runtime.get_messages(sid)[-1]
    assert saved["mood"] is None
    assert message_to_response(saved).mood is None


async def test_a_message_saved_without_a_mood_reads_none(runtime):
    session = runtime.create_session(model="M-GGUF", agent_type="chat")
    runtime.add_message(session["id"], "user", "hello")

    [saved] = runtime.get_messages(session["id"])
    assert saved["mood"] is None


async def test_an_unknown_mood_is_not_saved(runtime):
    sid, _ = await _run(runtime, ["love", "not-a-mood"])

    assert runtime.get_messages(sid)[-1]["mood"] == "love"
