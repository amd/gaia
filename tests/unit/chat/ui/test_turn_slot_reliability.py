# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""One chat must not hold the model away from every other chat.

Each test runs real turns through the chat router or the SSE producer with a
fake agent, and checks what a second chat (or the stopped one) sees:

* a non-streamed message is saved before it waits, and waits without a 429;
* a turn paused on a permission prompt or a question gives up its place, so
  other chats run while it waits on the user;
* Stop ends the turn and frees the model even when the agent's thread is stuck
  where it cannot see the Stop;
* a turn waiting on the start-up warm-up says so, and Stop ends that wait.
"""

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from gaia.llm.lemonade_manager import LemonadeManager
from gaia.ui import _chat_helpers as helpers
from gaia.ui import warmup
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager
from gaia.ui.turn_queue import TurnQueue

pytestmark = [pytest.mark.asyncio, pytest.mark.allow_network]


class _Agent:
    """Cached agent whose turn is whatever *behaviour* does with it."""

    def __init__(self, behaviour):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.behaviour = behaviour
        self.started = threading.Event()

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.started.set()
        return {"result": self.behaviour(self)}


@pytest.fixture
def runtime(monkeypatch):
    # Nothing here may reach a real model server.
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.setattr(LemonadeManager, "get_base_url", lambda: None)

    class NoStats:
        # A dead port can take seconds to refuse on Windows; fail at once.
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url, **_k):
            raise httpx.ConnectError(f"no model server in this test ({url})")

    monkeypatch.setattr(httpx, "AsyncClient", NoStats)
    db = ChatDatabase(":memory:")
    manager = RunManager()
    queue = TurnQueue()
    state = SimpleNamespace(session_locks={})
    http_request = SimpleNamespace(app=SimpleNamespace(state=state), headers={})
    server = SimpleNamespace(_stream_chat_response=helpers._stream_chat_response)
    helpers._agent_cache.clear()
    with (
        patch.object(chat, "run_manager", manager),
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(chat, "turn_queue", queue),
        patch("gaia.ui.turn_queue.turn_queue", queue),
        patch.object(chat, "_server_mod", return_value=server),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
        patch.object(warmup, "_warmer", None),
    ):
        yield SimpleNamespace(
            db=db, manager=manager, queue=queue, http=http_request, server=server
        )
    helpers._agent_cache.clear()
    db.close()


def _seed(db, behaviour):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _Agent(behaviour)
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    return session["id"], agent


def _events(run):
    return [json.loads(d.split("\n", 1)[0][len("data: ") :]) for d in run.buffer]


async def _send(rt, sid, message, *, stream=True):
    return await chat.send_message(
        ChatRequest(session_id=sid, message=message, stream=stream), rt.http, rt.db
    )


async def _start_detached(response):
    """Read the first SSE frame, then disconnect; the run keeps going."""
    disconnected = asyncio.Event()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            disconnected.set()

    await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)


async def _until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


async def _cleanup(rt, *sids):
    for sid in sids:
        run = rt.manager.get(sid)
        if run is not None:
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)


# ── A non-streamed message is never lost to a busy model ─────────────────────


async def test_non_streamed_message_is_saved_then_waits_in_line(runtime):
    rt = runtime
    sid = rt.db.create_session()["id"]
    rt.server._get_chat_response = AsyncMock(return_value="the answer")
    await rt.queue.acquire("another-chat")

    real_wait_for = asyncio.wait_for

    async def impatient_wait_for(awaitable, timeout):  # noqa: ARG001
        # Any fixed admission deadline fires at once here.
        return await real_wait_for(awaitable, timeout=0.05)

    with patch("gaia.ui.routers.chat.asyncio.wait_for", impatient_wait_for):
        task = asyncio.create_task(_send(rt, sid, "queued question", stream=False))
        await asyncio.sleep(0.3)
        saved = [m["content"] for m in rt.db.get_messages(sid)]
        assert saved == ["queued question"], "the message must be saved before it waits"
        assert not task.done(), "a busy model must queue the turn, not refuse it"
        assert rt.queue.waiting() == [sid]

    rt.queue.release()
    response = await asyncio.wait_for(task, 5)
    assert response.content == "the answer"
    assert not rt.queue.locked()
    assert not rt.http.app.state.session_locks[sid].locked()


async def test_stopping_a_waiting_non_streamed_message_keeps_it_and_says_so(runtime):
    rt = runtime
    sid = rt.db.create_session()["id"]
    rt.server._get_chat_response = AsyncMock(return_value="never")
    await rt.queue.acquire("another-chat")

    task = asyncio.create_task(_send(rt, sid, "never mind", stream=False))
    await _until(lambda: rt.queue.waiting() == [sid])
    assert (await chat.cancel_stream(chat.CancelStreamRequest(session_id=sid)))[
        "cancelled"
    ]

    with pytest.raises(HTTPException) as stopped:
        await asyncio.wait_for(task, 5)
    assert stopped.value.status_code == 409
    roles = [(m["role"], m["content"]) for m in rt.db.get_messages(sid)]
    assert roles == [("user", "never mind"), ("assistant", "Cancelled.")]
    rt.server._get_chat_response.assert_not_called()
    assert not rt.http.app.state.session_locks[sid].locked()


# ── A turn waiting on the user gives the model to other chats ────────────────


def _ask_permission(agent):
    allowed = agent.console.confirm_tool_execution("run_shell_command", {"cmd": "ls"})
    return f"allowed={allowed}"


def _ask_question(agent):
    return agent.console.request_user_input_blocking("Which one?", timeout_seconds=60)


def _answer_permission(handler):
    with handler._confirm_lock:
        confirm_id = handler._confirm_id
    assert handler.resolve_tool_confirmation(True, confirm_id=confirm_id)


def _answer_question(handler):
    with handler._user_input_lock:
        request_id = handler._user_input_queue[0]
    assert handler.resolve_user_input(request_id, "the second")


@pytest.mark.parametrize(
    "ask,answer,expected",
    [
        (_ask_permission, _answer_permission, "allowed=True"),
        (_ask_question, _answer_question, "the second"),
    ],
    ids=["permission", "question"],
)
async def test_turn_waiting_on_the_user_lets_other_chats_run(
    runtime, ask, answer, expected
):
    rt = runtime
    a, _agent_a = _seed(rt.db, ask)
    b, _agent_b = _seed(rt.db, lambda _agent: "B answered")
    try:
        await _start_detached(await _send(rt, a, "do it"))
        run_a = rt.manager.get(a)
        await _until(lambda: run_a.handler is not None and run_a.handler.waiting_on())
        assert (
            rt.queue.holder is None
        ), "a turn waiting on the user must not hold the model"

        await _start_detached(await _send(rt, b, "quick question"))
        run_b = rt.manager.get(b)
        await asyncio.wait_for(run_b.task, 5)
        assert _events(run_b)[-1]["type"] == "done"
        assert _events(run_b)[-1]["content"] == "B answered"

        answer(run_a.handler)
        await asyncio.wait_for(run_a.task, 5)
        assert _events(run_a)[-1]["content"] == expected
        assert not rt.queue.locked()
    finally:
        await _cleanup(rt, a, b)


async def test_answered_turn_goes_ahead_of_chats_that_arrived_while_it_waited(
    runtime,
):
    rt = runtime
    order = []

    def a_turn(agent):
        order.append("A")
        answer = _ask_permission(agent)
        order.append("A resumed")
        return answer

    a, _ = _seed(rt.db, a_turn)
    b_release = threading.Event()
    b, _ = _seed(rt.db, lambda _agent: b_release.wait(5) and order.append("B") or "B")
    c, _ = _seed(rt.db, lambda _agent: order.append("C") or "C")
    try:
        await _start_detached(await _send(rt, a, "do it"))
        run_a = rt.manager.get(a)
        await _until(lambda: run_a.handler is not None and run_a.handler.waiting_on())

        await _start_detached(await _send(rt, b, "long"))
        await _until(lambda: rt.queue.holder == b)
        await _start_detached(await _send(rt, c, "next"))
        await _until(lambda: rt.queue.waiting() == [c])

        # A is answered while B runs: it resumes next, before C.
        _answer_permission(run_a.handler)
        await _until(lambda: rt.queue.waiting() == [a, c])
        runs = [rt.manager.get(sid) for sid in (a, b, c)]
        queued = [e for e in _events(run_a) if e.get("phase") == "queued"]
        assert queued and queued[-1]["queue_position"] == 1

        b_release.set()
        for run in runs:
            await asyncio.wait_for(run.task, 5)
        assert order == ["A", "B", "A resumed", "C"]
        assert not rt.queue.locked()
    finally:
        b_release.set()
        await _cleanup(rt, a, b, c)


# ── Stop really stops ─────────────────────────────────────────────────────────


async def test_stop_frees_the_model_even_when_the_agent_thread_is_stuck(runtime):
    rt = runtime
    unstick = threading.Event()
    # Blocked where it cannot see Stop, like a hung MCP read or a lease wait.
    a, agent_a = _seed(rt.db, lambda _agent: unstick.wait(30) and "late")
    b, _ = _seed(rt.db, lambda _agent: "B answered")
    try:
        with patch.object(helpers, "_PRODUCER_STOP_GRACE_SECONDS", 0.3, create=True):
            await _start_detached(await _send(rt, a, "hang"))
            run_a = rt.manager.get(a)
            assert await asyncio.to_thread(agent_a.started.wait, 5)

            await chat.cancel_stream(chat.CancelStreamRequest(session_id=a))
            await asyncio.wait_for(run_a.task, 5)
            done = _events(run_a)[-1]
            assert done["type"] == "done" and done["cancelled"] is True
            assert rt.queue.holder != a

            # Other chats run while the stuck thread winds down...
            await _start_detached(await _send(rt, b, "next"))
            run_b = rt.manager.get(b)
            await asyncio.wait_for(run_b.task, 5)
            assert _events(run_b)[-1]["content"] == "B answered"

            # ...but this chat's agent is still in use, so it says why it is busy.
            with pytest.raises(HTTPException) as busy:
                await _send(rt, a, "again")
            assert busy.value.status_code == 409
            assert "stopping" in busy.value.detail.lower()

            unstick.set()
            await _until(lambda: not rt.http.app.state.session_locks[a].locked())
    finally:
        unstick.set()
        await _cleanup(rt, a, b)


async def _read_to_the_end(response):
    """Stay connected, like an open chat tab, until the stream ends."""
    ended = asyncio.Event()

    async def receive():
        await ended.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and not message.get("more_body"):
            ended.set()

    await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)


async def test_stopped_chat_stays_busy_while_its_tab_stays_open(runtime):
    rt = runtime
    unstick = threading.Event()
    sid, agent = _seed(rt.db, lambda _agent: unstick.wait(30) and "late")
    try:
        with patch.object(helpers, "_PRODUCER_STOP_GRACE_SECONDS", 0.3):
            reading = asyncio.create_task(
                _read_to_the_end(await _send(rt, sid, "hang"))
            )
            assert await asyncio.to_thread(agent.started.wait, 5)
            await chat.cancel_stream(chat.CancelStreamRequest(session_id=sid))
            await asyncio.wait_for(reading, 5)

            # The stream ended, but its agent thread still runs: no new turn yet.
            with pytest.raises(HTTPException) as busy:
                await _send(rt, sid, "again")
            assert busy.value.status_code == 409
            assert "stopping" in busy.value.detail.lower()
            assert rt.http.app.state.session_locks[sid].locked()

            unstick.set()
            await _until(lambda: not rt.http.app.state.session_locks[sid].locked())
    finally:
        unstick.set()
        await _cleanup(rt, sid)


# ── A turn waiting on the warm-up says so and can be stopped ─────────────────


async def test_turn_waiting_on_the_warm_up_shows_it_and_stop_ends_the_wait(runtime):
    rt = runtime
    warm = threading.Event()
    warmer = warmup.PrefixWarmer(lambda: warm.wait(30) and None)
    sid, agent = _seed(rt.db, lambda _agent: "answered")
    try:
        with patch.object(warmup, "_warmer", warmer):
            assert warmer.request("test")
            await _start_detached(await _send(rt, sid, "hi"))
            run = rt.manager.get(sid)
            await _until(
                lambda: any(
                    e.get("phase") == "warm_up"
                    and e["message"].startswith("Finishing start-up")
                    for e in _events(run)
                )
            )
            await chat.cancel_stream(chat.CancelStreamRequest(session_id=sid))
            await asyncio.wait_for(run.task, 5)
            assert _events(run)[-1]["cancelled"] is True
            assert not agent.started.is_set()
    finally:
        warm.set()
        await _cleanup(rt, sid)
