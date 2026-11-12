# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Stopping a relayed turn while its documents load says how many were left.

Stop closes the relayed stream at once, so the agent's own closing line for the
step never arrives. The count comes back on the cancel call instead, and the
relay finishes the open "Indexing documents" step with it.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as chat_helpers_module
import gaia.ui.email_sidecar.daemon_client as daemon_client_module
import gaia.ui.email_sidecar.relay as relay_module
from gaia.ui._chat_helpers import _stream_chat_impl
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.utils import message_to_response

LOADING = {
    "type": "tool_call",
    "tool": "index_documents",
    "args": {"documents": ["a.txt", "b.txt"]},
}


class _StoppedMidLoad:
    """A sidecar whose stream is cut by Stop while it loads documents."""

    agent_id = "gaia"

    def __init__(self, handler, documents_left):
        self._handler = handler
        self._left = documents_left
        self.cancelled = []

    def query_stream(self, body, *, read_timeout=300.0, on_response=None):
        yield LOADING
        self._handler.cancelled.set()
        # What the forced close does to a parked read.
        raise ConnectionError("stream closed")

    def cancel_query(self, run_id):
        self.cancelled.append(run_id)
        return self._left


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _relay(documents_left):
    handler = SSEOutputHandler()
    proxy = _StoppedMidLoad(handler, documents_left)
    relay_module.relay_query(
        handler,
        proxy,
        query="hi",
        context=[],
        run_id="r-1",
        profile=GAIA_PROFILE,
        documents=["C:/lib/a.txt", "C:/lib/b.txt"],
    )
    assert proxy.cancelled == ["r-1"]
    return _drain(handler)


@pytest.mark.parametrize(
    "left, said",
    [
        (413, "Stopped: 413 documents were not loaded."),
        (1, "Stopped: 1 document was not loaded."),
    ],
)
def test_a_stopped_load_ends_its_step_with_the_count(left, said):
    events = _relay(left)

    results = [e for e in events if e.get("type") == "tool_result"]
    assert len(results) == 1, events
    assert results[0]["tool"] == "index_documents"
    assert results[0]["success"] is False
    assert said in results[0]["summary"]
    assert "load with your next message" in results[0]["summary"]
    # The step is closed before the turn says it was cancelled.
    kinds = [e.get("type") for e in events]
    assert kinds.index("tool_result") < len(kinds) - 1
    assert events[-1] == {"type": "status", "message": "Cancelled."}


@pytest.mark.parametrize("left", [None, 0])
def test_a_stop_with_nothing_left_to_load_adds_no_step_result(left):
    events = _relay(left)

    assert [e for e in events if e.get("type") == "tool_result"] == []
    assert events[-1] == {"type": "status", "message": "Cancelled."}


class _FakeProxy:
    def init(self):
        return 200, {}


class _FakeHandle:
    api_version = "2.4"

    def proxy(self):
        return _FakeProxy()


async def _noop_retitle(**kwargs):
    return None


def test_the_stopped_turn_is_saved_with_the_count_on_its_step(monkeypatch):
    """The whole UI-server path: the reloaded chat shows how many were left."""

    def _fake_relay_query(handler, proxy, *, query, context, **kw):
        relay_module._dispatch_one(handler, LOADING, GAIA_PROFILE, False, False)
        deadline = time.monotonic() + 5
        while not handler.event_queue.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        handler.cancelled.set()
        relay_module._emit_documents_left(handler, 413, GAIA_PROFILE)
        handler._emit({"type": "status", "message": "Cancelled."})

    monkeypatch.setattr(
        daemon_client_module, "acquire_handle", lambda agent_id="email": _FakeHandle()
    )
    monkeypatch.setattr(relay_module, "relay_query", _fake_relay_query)
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )
    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="email")
    request = ChatRequest(
        session_id=session["id"], message="hello", stream=True, agent_type="email"
    )
    sent = []

    async def _drive():
        async for frame in _stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            if frame.startswith("data: "):
                sent.append(json.loads(frame.split("\n", 1)[0][len("data: ") :]))

    asyncio.run(_drive())

    live = [e for e in sent if e.get("type") == "tool_result"]
    assert live and "413 documents were not loaded" in live[0]["summary"], sent
    saved = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    assert len(saved) == 1
    steps = message_to_response(saved[0]).agent_steps or []
    assert any(
        "413 documents were not loaded" in json.dumps(step.model_dump())
        for step in steps
    ), steps
