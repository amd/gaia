# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A cached session agent must not carry a previous turn's cancel signal (#4196).

A streaming turn attaches its cancel event and SSE console to the cached agent
and fires both on cleanup. Later non-streaming turns and AgentLoop ticks reuse
that agent, so they must start with a clear cancel event and a live console —
otherwise the agent loop stops at step 0 with the timeout message.
"""

import asyncio
import sys
import threading
import types

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.ui.agent_loop import AgentLoop
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler


class _RecordingAgent:
    """Cached agent stand-in that records the state process_query runs with."""

    def __init__(self, model_id):
        self.model_id = model_id
        self.conversation_history = []
        self.indexed_files = set()
        self.seen = {}
        # State a finished streaming turn leaves behind.
        self._cancel_event = threading.Event()
        self._cancel_event.set()
        dead_console = SSEOutputHandler()
        dead_console.cancelled.set()
        self.console = dead_console

    def _register_tools(self):
        pass

    def process_query(self, _message):
        event = self._cancel_event
        cancelled = getattr(self.console, "cancelled", None)
        self.seen = {
            "cancel_set": event is not None and event.is_set(),
            "console_cancelled": cancelled is not None and cancelled.is_set(),
        }
        return "ok"


@pytest.fixture
def clean_cache(monkeypatch):
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    helpers._agent_cache.clear()
    yield
    helpers._agent_cache.clear()


def test_non_streaming_cache_hit_gets_clear_cancel_and_live_console(clean_cache):
    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _RecordingAgent("M-GGUF")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")

    request = ChatRequest(session_id=session["id"], message="hi", stream=False)
    result = asyncio.run(helpers._get_chat_response(db, session, request))

    assert result == "ok"
    assert agent.seen == {"cancel_set": False, "console_cancelled": False}


async def test_agent_loop_tick_cache_hit_gets_clear_cancel(clean_cache, monkeypatch):
    # The tick imports ChatAgent before it checks the cache; a cache hit never uses it.
    fake_pkg = types.ModuleType("gaia_agent_chat")
    fake_mod = types.ModuleType("gaia_agent_chat.agent")
    fake_mod.ChatAgent = fake_mod.ChatAgentConfig = object
    monkeypatch.setitem(sys.modules, "gaia_agent_chat", fake_pkg)
    monkeypatch.setitem(sys.modules, "gaia_agent_chat.agent", fake_mod)

    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _RecordingAgent("M-GGUF")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")

    loop = AgentLoop()
    loop._db = db
    goal = types.SimpleNamespace(priority="high", title="t", description="")
    monkeypatch.setattr(AgentLoop, "_get_actionable_goals", lambda self: [])

    await loop._execute_tick(session["id"], session, [goal])

    assert agent.seen == {"cancel_set": False, "console_cancelled": False}
