# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Messages sent while a chat turn runs reach it, or come back to be sent next.

The end-to-end tests run a real streaming turn through the SSE producer with a
cached agent that drains its follow-up queue the way ``Agent._drain_followups``
does, and deliver through ``POST /api/chat/followup`` as the Agent UI does.
"""

import asyncio
import json
import queue
import threading
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from gaia.agents.base.agent import Agent
from gaia.ui import _chat_helpers as helpers
from gaia.ui import followups
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager


class _Agent:
    """Cached agent that waits for a follow-up, then drains it or ignores it."""

    queue_followup = Agent.queue_followup

    def __init__(self, drain: bool):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self._followup_queue = None
        self.console = None
        self.drain = drain
        self.started = threading.Event()
        self.may_finish = threading.Event()
        self.took: list[str] = []

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.started.set()
        assert self.may_finish.wait(10)
        if self.drain:
            while True:
                try:
                    self.took.append(self._followup_queue.get_nowait())
                except queue.Empty:
                    break
        return {"result": "answered"}


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    manager = RunManager()
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(chat, "run_manager", manager),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield db
    helpers._agent_cache.clear()
    db.close()


async def _turn_with_followup(db, agent, text):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    db.add_message(session["id"], "user", "first question")
    request = ChatRequest(
        session_id=session["id"], message="first question", stream=True
    )
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    task = asyncio.create_task(consume())
    assert await asyncio.to_thread(agent.started.wait, 5)
    delivered = await chat.send_followup(
        chat.FollowUpRequest(session_id=session["id"], text=text)
    )
    agent.may_finish.set()
    await asyncio.wait_for(task, timeout=15)
    return session, delivered, events


@pytest.mark.asyncio
async def test_a_taken_followup_is_recorded_before_the_answer(runtime):
    db = runtime
    agent = _Agent(drain=True)

    session, delivered, events = await _turn_with_followup(db, agent, "also the totals")

    assert delivered == {"delivered": True}
    assert agent.took == ["also the totals"]
    assert {"type": "followup_taken", "text": "also the totals"} in events
    assert not [e for e in events if e["type"] == "followup_returned"]
    saved = [(m["role"], m["content"]) for m in db.get_messages(session["id"])]
    assert saved == [
        ("user", "first question"),
        ("user", "also the totals"),
        ("assistant", "answered"),
    ]
    # Unwired once the turn ends, so the next message cannot land in a dead inbox.
    assert agent._followup_queue is None


@pytest.mark.asyncio
async def test_an_untaken_followup_comes_back_before_done(runtime):
    db = runtime
    agent = _Agent(drain=False)

    session, _, events = await _turn_with_followup(db, agent, "too late")

    types = [e["type"] for e in events]
    returned = [e for e in events if e["type"] == "followup_returned"]
    assert returned == [{"type": "followup_returned", "texts": ["too late"]}]
    assert types.index("followup_returned") < types.index("done")
    # Not recorded: the chat sends it as its own next message.
    assert [m["content"] for m in db.get_messages(session["id"])] == [
        "first question",
        "answered",
    ]


@pytest.mark.asyncio
async def test_followup_without_a_running_turn_is_a_404(runtime):
    with pytest.raises(HTTPException) as exc:
        await chat.send_followup(chat.FollowUpRequest(session_id="nope", text="hi"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_followup_a_running_turn_cannot_take_is_a_409(runtime):
    with patch.object(chat.run_manager, "is_running", return_value=True):
        with pytest.raises(HTTPException) as exc:
            await chat.send_followup(
                chat.FollowUpRequest(session_id="relayed", text="hi")
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_empty_followup_is_rejected():
    with pytest.raises(HTTPException) as exc:
        await chat.send_followup(chat.FollowUpRequest(session_id="s", text="   "))
    assert exc.value.status_code == 422


def test_closing_leaves_a_newer_turns_inbox_alone():
    agent = _Agent(drain=True)
    old = followups.open_inbox("s", agent, lambda _t: None)
    new = followups.open_inbox("s", agent, lambda _t: None)

    assert followups.close_inbox("s", agent, old) == []
    assert agent._followup_queue is new
    assert followups.deliver("s", "still live")
    assert followups.close_inbox("s", agent, new) == ["still live"]
    assert not followups.deliver("s", "after close")


def test_inbox_reports_each_message_as_the_agent_takes_it():
    taken = []
    inbox = followups.FollowUpInbox(taken.append)
    inbox.put("a")
    inbox.put("b")

    assert inbox.get_nowait() == "a"
    assert taken == ["a"]
    assert inbox.take_untaken() == ["b"]
    assert taken == ["a"]


def test_a_rerun_turn_is_offered_its_followups_again_without_a_second_report():
    taken = []
    inbox = followups.FollowUpInbox(taken.append)
    inbox.put("also the totals")
    assert inbox.get_nowait() == "also the totals"

    inbox.requeue_taken()

    assert inbox.get_nowait() == "also the totals"
    assert taken == ["also the totals"]
    assert inbox.take_untaken() == []
