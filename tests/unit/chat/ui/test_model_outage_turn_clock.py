# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Time an agent spends waiting for a model endpoint to come back does not
count against the Agent UI's per-turn timeout.

The agent may wait out an outage for up to an hour; the Agent UI's own turn
budget (600 s by default) would otherwise end that turn as "timed out" while
the agent was about to carry on.
"""

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager
from gaia.ui.sse_handler import SSEOutputHandler

# ── the SSE handler tracks the wait from the status it already emits ─────


def test_the_handler_reports_a_model_wait_until_anything_else_happens():
    handler = SSEOutputHandler()
    assert handler.awaiting_model_endpoint() is False

    handler.print_warning("The model service is busy (HTTP 429) — retrying in 45s")
    handler.report_phase("model_retry", "The model service is busy — retrying")
    assert handler.awaiting_model_endpoint() is True

    handler.report_phase("reading", "Reading your request")
    assert handler.awaiting_model_endpoint() is False


@pytest.mark.parametrize(
    "event",
    [
        {"type": "chunk", "content": "Hel"},
        {"type": "tool_start", "tool": "read_file"},
        {"type": "answer", "content": "done"},
        {"type": "status", "status": "working", "message": "Working"},
    ],
    ids=["chunk", "tool", "answer", "status"],
)
def test_any_other_event_ends_the_wait(event):
    handler = SSEOutputHandler()
    handler.report_phase("model_retry", "retrying")
    handler._emit(event)
    assert handler.awaiting_model_endpoint() is False


def test_a_relayed_model_retry_status_counts_too():
    """A sidecar's status arrives through the relay; its phase must survive."""
    from gaia.ui.email_sidecar.relay import _dispatch_one

    handler = SSEOutputHandler()
    _dispatch_one(
        handler,
        {
            "type": "status",
            "message": "The model service is busy",
            "phase": "model_retry",
        },
    )
    assert handler.awaiting_model_endpoint() is True


# ── the turns ────────────────────────────────────────────────────────────


class _OutageAgent:
    """Cached agent whose turn waits out an outage longer than the turn budget."""

    def __init__(self, outage_seconds):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.outage_seconds = outage_seconds
        self._waiting = False

    def _register_tools(self):
        pass

    def awaiting_model_endpoint(self):
        return self._waiting

    def process_query(self, _message):
        report = getattr(self.console, "report_phase", None)
        if report is not None:
            report("model_retry", "The model service is busy — retrying in 1s")
        self._waiting = True
        time.sleep(self.outage_seconds)
        self._waiting = False
        if report is not None:
            report("reading", "Reading your request")
        return {"result": "recovered answer", "status": "success"}


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


def _seed(db, agent):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    return session["id"]


@pytest.mark.asyncio
@pytest.mark.allow_network
async def test_non_streaming_turn_outlasts_its_budget_while_the_model_is_down(
    runtime,
):
    db, http_request = runtime
    agent = _OutageAgent(outage_seconds=0.6)
    sid = _seed(db, agent)

    with patch.object(helpers, "_CHAT_TIMEOUT_SECONDS", 0.2):
        response = await asyncio.wait_for(
            chat.send_message(
                ChatRequest(session_id=sid, message="hi", stream=False),
                http_request,
                db,
            ),
            timeout=10,
        )

    assert "recovered answer" in response.content
    assert "took too long" not in response.content
    assert agent._cancel_event is None or not agent._cancel_event.is_set()


@pytest.mark.asyncio
@pytest.mark.allow_network
async def test_non_streaming_turn_still_times_out_when_the_model_is_not_down(
    runtime,
):
    db, http_request = runtime
    agent = _OutageAgent(outage_seconds=0.6)
    agent.awaiting_model_endpoint = lambda: False
    sid = _seed(db, agent)

    with patch.object(helpers, "_CHAT_TIMEOUT_SECONDS", 0.2):
        response = await asyncio.wait_for(
            chat.send_message(
                ChatRequest(session_id=sid, message="hi", stream=False),
                http_request,
                db,
            ),
            timeout=10,
        )
    assert "took too long" in response.content


async def _drain(response):
    """Run a streaming response to completion; return its SSE data events."""
    body = []
    never = asyncio.Event()

    async def receive():
        await never.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            body.append(message["body"].decode())

    await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    events = []
    for line in "".join(body).splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


@pytest.mark.asyncio
@pytest.mark.allow_network
@pytest.mark.parametrize("down", [True, False], ids=["model-down", "model-up"])
async def test_streaming_turn_clock_excludes_only_the_model_wait(runtime, down):
    db, http_request = runtime
    agent = _OutageAgent(outage_seconds=1.0)
    if not down:
        # The same silence with no outage reported is still a hung turn.
        original = agent.process_query

        def _quiet(message):
            agent.console.report_phase = lambda *a, **k: None
            return original(message)

        agent.process_query = _quiet
    sid = _seed(db, agent)

    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 0.4):
        response = await chat.send_message(
            ChatRequest(session_id=sid, message="hi", stream=True), http_request, db
        )
        events = await asyncio.wait_for(_drain(response), timeout=15)

    timed_out = any(
        e.get("type") == "agent_error" and "timed out" in e.get("content", "")
        for e in events
    )
    assert timed_out is (not down)
    if down:
        answers = [e for e in events if e.get("type") == "answer"]
        assert not answers or "recovered answer" in answers[-1].get("content", "")


def test_the_wait_clock_counts_model_waits_like_user_waits():
    clock = helpers._UserWaitClock()
    assert clock.update(True, 10.0) == 0.0
    assert clock.update(True, 70.0) == 60.0
    assert clock.update(False, 80.0) == 70.0


def test_threads_do_not_race_the_wait_flag():
    """The agent thread writes the flag while the event loop reads it."""
    handler = SSEOutputHandler()
    stop = threading.Event()

    def flip():
        while not stop.is_set():
            handler.report_phase("model_retry", "retrying")
            handler._emit({"type": "chunk", "content": "x"})

    worker = threading.Thread(target=flip, daemon=True)
    worker.start()
    try:
        for _ in range(10000):
            assert handler.awaiting_model_endpoint() in (True, False)
    finally:
        stop.set()
        worker.join(timeout=5)


# ── unattended runs: scheduled prompts and autonomous ticks ──────────────


def _fake_gaia_agent_module(monkeypatch, outage_seconds, reports_outage):
    import sys
    import types

    class FakeConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeGaiaAgent(_OutageAgent):
        def __init__(self, _config):
            super().__init__(outage_seconds)
            if not reports_outage:
                self.awaiting_model_endpoint = lambda: False

    fake = types.ModuleType("gaia_agent.agent")
    fake.GaiaAgent = FakeGaiaAgent
    fake.GaiaAgentConfig = FakeConfig
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", fake)


@pytest.mark.allow_network
@pytest.mark.parametrize("down", [True, False], ids=["model-down", "model-up"])
def test_scheduled_run_timeout_pauses_while_the_model_is_down(monkeypatch, down):
    from fastapi.testclient import TestClient

    from gaia.llm import lemonade_service
    from gaia.ui import server

    _fake_gaia_agent_module(monkeypatch, outage_seconds=1.0, reports_outage=down)
    monkeypatch.setattr(lemonade_service, "ensure_daemon_owns_lemonade", lambda: None)
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setenv("GAIA_SCHEDULE_TIMEOUT", "0.3")

    with TestClient(server.create_app(db_path=":memory:")) as client:
        executor = client.app.state.scheduler._executor
        if down:
            assert asyncio.run(executor("summarize")) == "recovered answer"
        else:
            with pytest.raises(asyncio.TimeoutError, match="GAIA_SCHEDULE_TIMEOUT"):
                asyncio.run(executor("summarize"))


@pytest.mark.asyncio
@pytest.mark.allow_network
@pytest.mark.parametrize("down", [True, False], ids=["model-down", "model-up"])
async def test_autonomous_tick_timeout_pauses_while_the_model_is_down(
    runtime, monkeypatch, tmp_path, down
):
    import sys
    import types

    import gaia.ui.agent_loop as agent_loop_mod

    db, _http_request = runtime
    agent = _OutageAgent(outage_seconds=1.0)
    if not down:
        original = agent.process_query

        def _quiet(message):
            agent.console.report_phase = lambda *a, **k: None
            return original(message)

        agent.process_query = _quiet
    sid = _seed(db, agent)
    db.set_setting("agent_mode", "goal_driven")
    session = db.get_session(sid)

    # The tick imports GaiaAgent before it checks the cache; a cache hit never uses it.
    fake_mod = types.ModuleType("gaia_agent.agent")
    fake_mod.GaiaAgent = fake_mod.GaiaAgentConfig = object
    monkeypatch.setitem(sys.modules, "gaia_agent", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", fake_mod)
    (tmp_path / ".gaia" / "chat").mkdir(parents=True)
    (tmp_path / ".gaia" / "chat" / "initialized").touch()
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / ".gaia"))
    goal = SimpleNamespace(priority="high", title="t", description="")
    monkeypatch.setattr(
        agent_loop_mod.AgentLoop, "_get_actionable_goals", lambda s: [goal]
    )
    monkeypatch.setattr(agent_loop_mod, "_TICK_TIMEOUT", 0.3)

    loop = agent_loop_mod.AgentLoop()
    loop._db = db
    loop._app_state = SimpleNamespace(
        tunnel=None, session_locks={}, chat_semaphore=asyncio.Semaphore(1)
    )
    await asyncio.wait_for(
        loop._run_step(agent_loop_mod.AgentTrigger("idle_tick", session["id"])),
        timeout=15,
    )
    # A timed-out tick is cancelled; one that only waited on its model is not.
    assert agent._cancel_event.is_set() is (not down)
