# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An Agent UI turn is the user's turn to the model-slot broker.

Its leases go ahead of queued background work, a wait for the model shows on
the turn's own stream instead of the backend's stdout, and the turn's Stop
ends that wait.
"""

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gaia.daemon import broker_client
from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager

pytestmark = pytest.mark.allow_network

_QUEUED = {
    "event": "queued",
    "scope": "daemon",
    "position": 2,
    "queue_length": 3,
    "holder": "email",
    "holder_model": "M-GGUF",
    "held_for_s": 12.0,
    "waited_s": 4.0,
    "model": "M-GGUF",
}


def _stopped_now() -> bool:
    try:
        broker_client.check_cancelled("probe")
    except broker_client.LeaseWaitCancelled:
        return True
    return False


class _QueuedAgent:
    """An agent whose turn reports a model-slot wait, as model_lease would."""

    def __init__(self):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.seen = {}

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.seen["priority"] = broker_client.current_priority()
        side = threading.Thread(
            target=lambda: self.seen.__setitem__(
                "side_priority", broker_client.current_priority()
            )
        )
        side.start()
        side.join()
        broker_client.notify_wait(_QUEUED)
        self.seen["live_before_stop"] = not _stopped_now()
        self.console.cancelled.set()  # what the UI's Stop button sets
        try:
            broker_client.check_cancelled("the model slot")
        except broker_client.LeaseWaitCancelled:
            self.seen["cancelled"] = True
        return "answer"


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    manager = RunManager()
    state = SimpleNamespace(
        session_locks={}, chat_semaphore=asyncio.BoundedSemaphore(1)
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={})
    server = SimpleNamespace(
        _stream_chat_response=helpers._stream_chat_response,
        _get_chat_response=helpers._get_chat_response,
    )
    helpers._agent_cache.clear()
    with (
        patch.object(chat, "run_manager", manager),
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(chat, "_server_mod", return_value=server),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield db, request
    helpers._agent_cache.clear()
    db.close()


@pytest.mark.asyncio
async def test_a_streaming_turn_is_interactive_and_shows_its_model_wait(runtime):
    db, http_request = runtime
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _QueuedAgent()
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")

    response = await chat.send_message(
        ChatRequest(session_id=session["id"], message="hi", stream=True),
        http_request,
        db,
    )
    body = []

    async def receive():
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.body":
            body.append(message.get("body", b"").decode())

    await asyncio.wait_for(
        response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send),
        timeout=15,
    )

    events = [
        json.loads(line[len("data: ") :])
        for line in "".join(body).splitlines()
        if line.startswith("data: ")
    ]
    queue = [e for e in events if e.get("phase") == "model_queue"]
    assert queue, f"no model-queue status in the stream: {events}"
    assert queue[0]["status"] == "working"
    assert "'email' is using it" in queue[0]["message"]
    assert "#2 of 3" in queue[0]["message"] and "(4s)" in queue[0]["message"]
    assert agent.seen == {
        "priority": "interactive",
        "side_priority": "background",
        "live_before_stop": True,
        "cancelled": True,
    }


def test_the_granted_line_says_how_long_the_turn_waited():
    line = helpers._model_queue_status(
        {"event": "granted", "scope": "daemon", "model": "m", "waited_s": 41.7}
    )
    assert line["message"] == "Got the model after waiting 41s"
