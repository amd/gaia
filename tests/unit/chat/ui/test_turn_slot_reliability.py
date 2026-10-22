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
        # The prompt is posted just before the slot is handed back on the loop.
        await _until(lambda: rt.queue.holder is None)

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


async def test_a_turn_waiting_in_line_keeps_its_stream_alive(runtime):
    rt = runtime
    sid = rt.db.create_session()["id"]
    await rt.queue.acquire("another-chat")
    try:
        with patch.object(helpers, "_QUEUED_KEEPALIVE_SECONDS", 0.05, create=True):
            await _start_detached(await _send(rt, sid, "queued"))
            run = rt.manager.get(sid)
            q = run.subscribe()
            await asyncio.sleep(0.3)
            frames = []
            while not q.empty():
                frames.append(q.get_nowait())
            run.unsubscribe(q)
        assert any(f.startswith(": keepalive") for f in frames)
    finally:
        rt.queue.release()
        await _cleanup(rt, sid)


@pytest.mark.parametrize("model_still_loads", [True, False])
async def test_the_model_is_checked_again_before_an_approved_tool_runs(
    runtime, model_still_loads
):
    rt = runtime
    ran = []

    def a_turn(agent):
        allowed = agent.console.confirm_tool_execution("run_shell_command", {"c": 1})
        if allowed:
            ran.append("tool")
        return f"allowed={allowed}"

    a, _ = _seed(rt.db, a_turn)
    preflight = helpers._maybe_load_expected_model  # the fixture's mock
    try:
        await _start_detached(await _send(rt, a, "do it"))
        run_a = rt.manager.get(a)
        await _until(lambda: run_a.handler is not None and run_a.handler.waiting_on())
        assert preflight.call_count == 1
        if not model_still_loads:
            # Another chat unloaded it, and it no longer fits.
            preflight.side_effect = RuntimeError("Not enough free memory to load M")

        _answer_permission(run_a.handler)
        await asyncio.wait_for(run_a.task, 5)

        assert preflight.call_count == 2, "the model was not checked after the wait"
        events = _events(run_a)
        if model_still_loads:
            assert ran == ["tool"]
        else:
            assert ran == [], "the approved tool ran without its model"
            assert any(
                e["type"] == "agent_error" and "free memory" in e["content"]
                for e in events
            )
            assert events[-1].get("cancelled") is True
    finally:
        preflight.side_effect = None
        await _cleanup(rt, a)


async def test_waiting_for_agent_setup_never_deadlocks_with_the_model_holder(
    monkeypatch,
):
    """B waits for the setup lock with the model given up; a tick then takes
    the model and waits for the same lock. Neither may wait on the other."""
    from gaia.ui.turn_queue import TurnSlot

    loop = asyncio.get_running_loop()
    queue = TurnQueue()
    monkeypatch.setattr(helpers, "tool_registry_lock", threading.RLock())
    lock = helpers.tool_registry_lock
    handler_b = SimpleNamespace(
        turn_slot=TurnSlot(queue, "chat-b", loop),
        cancelled=threading.Event(),
        _emit=lambda _event: None,
    )
    await handler_b.turn_slot.acquire()  # B is answering.

    stuck_build_holds = threading.Event()
    stuck_build_done = threading.Event()

    def stopped_chats_build():  # Another chat's agent build, still going.
        with lock:
            stuck_build_holds.set()
            stuck_build_done.wait(10)

    threading.Thread(target=stopped_chats_build, daemon=True).start()
    assert await asyncio.to_thread(stuck_build_holds.wait, 5)

    b_built = threading.Event()

    def chat_b_builds():
        with helpers.writing_tool_registry(handler_b):
            b_built.set()

    threading.Thread(target=chat_b_builds, daemon=True).start()
    for _ in range(100):  # B gives the model up while it waits.
        if not queue.locked():
            break
        await asyncio.sleep(0.02)
    await queue.acquire("tick")  # A tick takes the model...
    stuck_build_done.set()  # ...the other build ends, and B gets the lock first.
    await asyncio.sleep(0.3)

    tick_built = threading.Event()

    def tick_builds():
        with helpers.writing_tool_registry():
            tick_built.set()

    threading.Thread(target=tick_builds, daemon=True).start()
    assert await asyncio.to_thread(tick_built.wait, 5), "deadlocked on the setup lock"
    queue.release()  # The tick's turn ends.
    assert await asyncio.to_thread(b_built.wait, 5)
    assert handler_b.turn_slot.held
    handler_b.turn_slot.release()
    assert not queue.locked()


async def test_time_spent_waiting_for_the_model_is_not_setup_time(monkeypatch):
    """Back from a long wait in line, one brief collision on the setup lock
    must not read as "another chat has been setting up for too long"."""
    from gaia.ui.turn_queue import TurnSlot

    monkeypatch.setattr(helpers, "_REGISTRY_WAIT_LIMIT_S", 1.0)
    loop = asyncio.get_running_loop()
    queue = TurnQueue()
    monkeypatch.setattr(helpers, "tool_registry_lock", threading.RLock())
    lock = helpers.tool_registry_lock
    handler = SimpleNamespace(
        turn_slot=TurnSlot(queue, "chat-b", loop),
        cancelled=threading.Event(),
        _emit=lambda _event: None,
    )
    await handler.turn_slot.acquire()

    release_first = threading.Event()
    held = threading.Event()

    def other_build(wait_for):
        with lock:
            held.set()
            wait_for.wait(10)

    threading.Thread(target=other_build, args=(release_first,), daemon=True).start()
    assert await asyncio.to_thread(held.wait, 5)

    built, failure = threading.Event(), []

    def chat_b_builds():
        try:
            with helpers.writing_tool_registry(handler):
                built.set()
        except Exception as exc:  # recorded for the assertion below
            failure.append(exc)

    threading.Thread(target=chat_b_builds, daemon=True).start()
    await asyncio.sleep(0.2)
    await queue.acquire("long-turn")  # B waits in line for 1.5 s, past the limit.
    release_first.set()
    await asyncio.sleep(1.5)
    # Just as B comes back, another build briefly holds the lock.
    held.clear()
    release_second = threading.Event()
    threading.Thread(target=other_build, args=(release_second,), daemon=True).start()
    assert await asyncio.to_thread(held.wait, 5)
    queue.release()
    await asyncio.sleep(0.3)
    release_second.set()

    assert await asyncio.to_thread(built.wait, 5), failure
    assert not failure
    handler.turn_slot.release()


# ── A relayed sidecar turn waiting on the user gives the model up too ────────


class _ParkingSidecar:
    """A flagship sidecar whose run parks on the user, then finishes.

    After the answer it streams one more step before its final answer, so the
    relayed turn has to take the model back before the run can go on.
    """

    agent_id = "gaia"

    def __init__(self, kind):
        self.kind = kind
        self.answer = None
        self.answered = threading.Event()

    def query_stream(self, body, read_timeout, on_response):  # noqa: ARG002
        if self.kind == "permission":
            yield {
                "type": "needs_confirmation",
                "action": "run_shell_command",
                "summary": "Run 'echo background-prompt-test'?",
                "confirm_id": "c-1",
            }
        else:
            yield {"type": "needs_input", "request_id": "q-1", "question": "Which?"}
        if not self.answered.wait(10):
            yield {"type": "error", "detail": "nobody answered"}
            return
        yield {"type": "token", "delta": "resumed "}
        yield {"type": "final", "answer": f"answer={self.answer}"}

    def tool_decision(self, run_id, decision, confirm_id):  # noqa: ARG002
        self.answer = decision
        self.answered.set()

    def respond_query(self, run_id, request_id, value):  # noqa: ARG002
        self.answer = value
        self.answered.set()


def _relay_through(sidecar):
    from gaia.ui.email_sidecar import relay
    from gaia.ui.email_sidecar.profiles import GAIA_PROFILE

    def dispatch(sse_handler, request, *_a, **_k):
        relay.relay_query(
            sse_handler,
            sidecar,
            query=request.message,
            context=[],
            profile=GAIA_PROFILE,
            answers_confirmations=True,
        )

    return patch.object(helpers, "_dispatch_sidecar_query", dispatch)


def _answer_relayed(handler, kind):
    if kind == "permission":
        assert handler.resolve_relay_confirmation(True, "c-1")
    else:
        assert handler.resolve_relay_input("q-1", "the second")


@pytest.mark.parametrize(
    "kind,expected",
    [("permission", "answer=allow"), ("question", "answer=the second")],
)
async def test_relayed_turn_waiting_on_the_user_lets_a_new_chat_run(
    runtime, kind, expected
):
    rt = runtime
    sidecar = _ParkingSidecar(kind)
    a = rt.db.create_session(model="M-GGUF", agent_type="gaia")["id"]
    # A brand-new chat: its first message must not wait behind A's prompt.
    b, _ = _seed(rt.db, lambda _agent: "42")
    try:
        with _relay_through(sidecar):
            await _start_detached(await _send(rt, a, "run echo"))
            run_a = rt.manager.get(a)
            await _until(
                lambda: run_a.handler is not None and run_a.handler.waiting_on()
            )
            assert run_a.handler.waiting_on() == kind
            assert run_a.handler.awaiting_user_input()
            await _until(lambda: rt.queue.holder is None)

            await _start_detached(await _send(rt, b, "what is 6 times 7?"))
            run_b = rt.manager.get(b)
            await asyncio.wait_for(run_b.task, 5)
            assert _events(run_b)[-1]["content"] == "42"
            assert not [e for e in _events(run_b) if e.get("phase") == "queued"]
            assert not sidecar.answered.is_set(), "A's sidecar run stays parked"

            _answer_relayed(run_a.handler, kind)
            await asyncio.wait_for(run_a.task, 5)
        assert _events(run_a)[-1]["content"] == expected
        assert run_a.handler.waiting_on() is None
        assert not rt.queue.locked()
    finally:
        sidecar.answered.set()
        await _cleanup(rt, a, b)


async def test_answered_relayed_turn_requeues_at_the_front_and_says_so(runtime):
    rt = runtime
    sidecar = _ParkingSidecar("permission")
    a = rt.db.create_session(model="M-GGUF", agent_type="gaia")["id"]
    b_release = threading.Event()
    b, _ = _seed(rt.db, lambda _agent: b_release.wait(5) and "B")
    c, _ = _seed(rt.db, lambda _agent: "C")
    try:
        with _relay_through(sidecar):
            await _start_detached(await _send(rt, a, "run echo"))
            run_a = rt.manager.get(a)
            await _until(
                lambda: run_a.handler is not None and run_a.handler.waiting_on()
            )
            await _until(lambda: rt.queue.holder is None)
            # A sits on its prompt a while: its next wait in line counts from 0.
            await asyncio.sleep(1.2)

            await _start_detached(await _send(rt, b, "long"))
            await _until(lambda: rt.queue.holder == b)
            await _start_detached(await _send(rt, c, "next"))
            await _until(lambda: rt.queue.waiting() == [c])
            others = [rt.manager.get(sid) for sid in (b, c)]

            _answer_relayed(run_a.handler, "permission")
            await _until(lambda: rt.queue.waiting() == [a, c])
            queued = [e for e in _events(run_a) if e.get("phase") == "queued"]
            assert queued[-1]["queue_position"] == 1
            assert queued[-1]["queue_waited_s"] == 0
            assert run_a.handler.waiting_on() is None, "answered: no longer needs you"

            b_release.set()
            await asyncio.wait_for(run_a.task, 5)
            events = _events(run_a)
            phases = [e.get("phase") for e in events if e.get("phase")]
            assert phases[-2:] == ["queued", "resumed"]
            assert events[-1]["content"] == "answer=allow"
            for run in others:
                await asyncio.wait_for(run.task, 5)
        assert not rt.queue.locked()
    finally:
        b_release.set()
        sidecar.answered.set()
        await _cleanup(rt, a, b, c)


async def test_stopping_a_relayed_turn_while_it_waits_on_the_user(runtime):
    rt = runtime
    sidecar = _ParkingSidecar("permission")
    a = rt.db.create_session(model="M-GGUF", agent_type="gaia")["id"]
    try:
        with _relay_through(sidecar):
            await _start_detached(await _send(rt, a, "run echo"))
            run_a = rt.manager.get(a)
            await _until(
                lambda: run_a.handler is not None and run_a.handler.waiting_on()
            )
            await _until(lambda: rt.queue.holder is None)
            await rt.queue.acquire("another-chat")
            await chat.cancel_stream(chat.CancelStreamRequest(session_id=a))
            sidecar.answered.set()
            await asyncio.wait_for(run_a.task, 5)
        assert _events(run_a)[-1].get("cancelled") is True
        assert rt.queue.holder == "another-chat", "a stopped turn never retakes it"
        assert rt.queue.waiting() == []
    finally:
        sidecar.answered.set()
        if rt.queue.holder == "another-chat":
            rt.queue.release()
        await _cleanup(rt, a)


# ── A cloud turn doesn't wait for the local model ───────────────────────────

_CLOUD = "fireworks.deepseek-v4p1-flash"


async def test_cloud_turn_skips_the_line_for_the_local_model(runtime):
    rt = runtime
    session = rt.db.create_session(model=_CLOUD, agent_type="chat")
    agent = _Agent(lambda _agent: "42")
    helpers._store_agent(session["id"], _CLOUD, [], agent, "chat")
    await rt.queue.acquire("local-chat")
    try:
        await _start_detached(await _send(rt, session["id"], "what is 6 times 7?"))
        run = rt.manager.get(session["id"])
        await asyncio.wait_for(run.task, 5)
        events = _events(run)
        assert events[-1]["content"] == "42"
        assert not [e for e in events if e.get("phase") == "queued"]
        assert rt.queue.holder == "local-chat" and rt.queue.waiting() == []
    finally:
        rt.queue.release()
        await _cleanup(rt, session["id"])


async def test_non_streamed_cloud_turn_skips_the_line(runtime):
    rt = runtime
    sid = rt.db.create_session(model=_CLOUD)["id"]
    rt.server._get_chat_response = AsyncMock(return_value="the answer")
    await rt.queue.acquire("local-chat")
    try:
        response = await asyncio.wait_for(_send(rt, sid, "hi", stream=False), 5)
        assert response.content == "the answer"
        assert rt.queue.holder == "local-chat" and rt.queue.waiting() == []
    finally:
        rt.queue.release()


async def test_local_turn_still_waits_in_line_for_the_gpu(runtime):
    rt = runtime
    sid, agent = _seed(rt.db, lambda _agent: "local")
    await rt.queue.acquire("another-chat")
    try:
        await _start_detached(await _send(rt, sid, "hi"))
        await _until(lambda: rt.queue.waiting() == [sid])
        assert not agent.started.is_set()
        queued = [e for e in _events(rt.manager.get(sid)) if e.get("phase") == "queued"]
        assert queued[-1]["message"].startswith("Waiting for the GPU")
    finally:
        rt.queue.release()
        await _cleanup(rt, sid)
