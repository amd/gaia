# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Stopping a relayed turn keeps the answer text that already streamed in.

Drives the real ``_stream_chat_impl`` and ``ChatDatabase``; only the relay is
scripted, to do what ``relay_query`` does on Stop: relay ``chunk`` events, see
``handler.cancelled``, and return with a "Cancelled." status and no answer.
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
from gaia.ui.models import ChatRequest


class _FakeProxy:
    def init(self):
        return 200, {}


class _FakeHandle:
    api_version = "2.4"

    def proxy(self):
        return _FakeProxy()


async def _noop_retitle(**kwargs):
    return None


def _wait_drained(handler, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not handler.event_queue.empty() and time.monotonic() < deadline:
        time.sleep(0.01)


@pytest.mark.parametrize("stopped", [True, False])
def test_a_relayed_turn_saves_its_streamed_text(monkeypatch, stopped):
    def _fake_relay_query(handler, proxy, *, query, context, model_id=None, **kw):
        handler._emit({"type": "chunk", "content": "Once upon a time, "})
        handler._emit({"type": "chunk", "content": "a fox"})
        # The user saw the opening before pressing Stop.
        _wait_drained(handler)
        if stopped:
            handler.cancelled.set()
            handler._emit({"type": "status", "message": "Cancelled."})
            return
        handler._emit({"type": "answer", "content": "Once upon a time, a fox."})

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
        session_id=session["id"], message="a story", stream=True, agent_type="email"
    )
    sent = []

    async def _drive():
        async for frame in _stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            if frame.startswith("data: "):
                sent.append(json.loads(frame.split("\n", 1)[0][len("data: ") :]))

    asyncio.run(_drive())

    saved = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    assert len(saved) == 1
    done = [e for e in sent if e.get("type") == "done"]
    assert len(done) == 1, sent
    if stopped:
        assert saved[0]["content"] == "Once upon a time, a fox"
        assert done[0].get("cancelled") is True, done
    else:
        assert saved[0]["content"] == "Once upon a time, a fox."
        assert not done[0].get("cancelled"), done
