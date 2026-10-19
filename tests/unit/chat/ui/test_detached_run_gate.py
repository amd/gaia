# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The model's turn slot belongs to the run, not to its SSE subscriber (#5226)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager
from gaia.ui.turn_queue import TurnQueue

pytestmark = [pytest.mark.asyncio, pytest.mark.allow_network]


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    manager = RunManager()
    queue = TurnQueue()
    state = SimpleNamespace(session_locks={})
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={})
    server = SimpleNamespace(_stream_chat_response=helpers._stream_chat_response)
    with (
        patch.object(chat, "run_manager", manager),
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(chat, "turn_queue", queue),
        patch("gaia.ui.turn_queue.turn_queue", queue),
        patch.object(chat, "_server_mod", return_value=server),
    ):
        yield db, manager, queue, request
    db.close()


def _events(run):
    return [json.loads(d.split("\n", 1)[0][len("data: ") :]) for d in run.buffer]


async def _send(db, http_request, sid, message):
    return await chat.send_message(
        ChatRequest(session_id=sid, message=message, stream=True), http_request, db
    )


async def _start_detached(response):
    """Consume the first SSE frame, then disconnect, leaving the run detached."""
    disconnected = asyncio.Event()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            disconnected.set()

    await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)


@pytest.mark.parametrize("outcome", ["complete", "error", "cancel"])
async def test_second_chat_waits_in_line_and_runs_after_the_first(runtime, outcome):
    db, manager, queue, http_request = runtime
    a = db.create_session()["id"]
    b = db.create_session()["id"]
    finish = {a: asyncio.Event(), b: asyncio.Event()}
    started = []

    async def impl(run, _db, _session, request):
        started.append(request.session_id)
        yield 'data: {"type":"status","content":"started"}\n\n'
        await finish[request.session_id].wait()
        if outcome == "error" and request.session_id == a:
            raise RuntimeError("controlled producer failure")
        yield 'data: {"type":"done","message_id":1,"content":"ok"}\n\n'

    with patch.object(helpers, "_stream_chat_impl", impl):
        response_a = await _send(db, http_request, a, "A")
        await _start_detached(response_a)
        run_a = manager.get(a)
        try:
            assert queue.holder == a

            with pytest.raises(HTTPException) as conflict:
                await _send(db, http_request, a, "duplicate")
            assert conflict.value.status_code == 409

            # B is admitted at once and waits inside its own run.
            response_b = await asyncio.wait_for(_send(db, http_request, b, "B"), 1)
            await _start_detached(response_b)
            run_b = manager.get(b)
            assert queue.waiting() == [b]
            assert started == [a]
            queued = [e for e in _events(run_b) if e.get("phase") == "queued"]
            assert queued[0]["queue_position"] == 1
            assert queued[0]["message"] == "Waiting for the GPU — 1st in line"

            active = await chat.list_active_runs()
            assert active["queued"] == [b]
            assert set(active["session_ids"]) == {a, b}

            if outcome == "cancel":
                run_a.task.cancel()
            else:
                finish[a].set()
            await asyncio.gather(run_a.task, return_exceptions=True)
            for _ in range(20):
                if started == [a, b]:
                    break
                await asyncio.sleep(0.01)
            assert started == [a, b]
            assert queue.holder == b

            finish[b].set()
            await run_b.task
            await response_a.background()
            await response_b.background()
            assert not queue.locked()
            assert not http_request.app.state.session_locks[a].locked()
            assert not http_request.app.state.session_locks[b].locked()
            assert db.get_session(b)["last_run_status"] == "done"
            assert db.get_session(a)["last_run_status"] == (
                "done" if outcome == "complete" else "failed"
            )
        finally:
            for ev in finish.values():
                ev.set()
            for sid in (a, b):
                run = manager.get(sid)
                if run is not None:
                    run.task.cancel()
                    await asyncio.gather(run.task, return_exceptions=True)


async def test_stop_while_waiting_in_line_withdraws_the_turn(runtime):
    db, manager, queue, http_request = runtime
    sid = db.create_session()["id"]
    await queue.acquire("another-chat")

    response = await _send(db, http_request, sid, "queued")
    await _start_detached(response)
    run = manager.get(sid)
    assert queue.waiting() == [sid]

    assert (await chat.cancel_stream(chat.CancelStreamRequest(session_id=sid))) == {
        "status": "ok",
        "cancelled": True,
    }
    await run.task
    await response.background()

    done = _events(run)[-1]
    assert done["type"] == "done" and done["cancelled"] is True
    assert queue.waiting() == []
    assert queue.holder == "another-chat"
    assert db.get_session(sid)["last_run_status"] == "stopped"
    assert not http_request.app.state.session_locks[sid].locked()


async def test_stream_failure_before_run_start_releases_slot(runtime):
    db, _manager, queue, http_request = runtime
    sid = db.create_session()["id"]
    response = await _send(db, http_request, sid, "A")
    with patch.object(db, "add_message", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError, match="write failed"):
            await anext(response.body_iterator)
    # Generator cleanup must work even when an exception prevents Starlette
    # from reaching the response's BackgroundTask.
    assert not queue.locked()
    assert not http_request.app.state.session_locks[sid].locked()


async def test_stop_on_a_waiting_non_streamed_turn_frees_the_chat(runtime):
    db, _manager, queue, http_request = runtime
    sid = db.create_session()["id"]
    await queue.acquire("another-chat")
    waiting = asyncio.create_task(
        chat.send_message(
            ChatRequest(session_id=sid, message="queued", stream=False),
            http_request,
            db,
        )
    )
    await asyncio.sleep(0.01)
    assert queue.waiting() == [sid]

    await chat.cancel_stream(chat.CancelStreamRequest(session_id=sid))
    with pytest.raises(HTTPException) as stopped:
        await waiting
    assert stopped.value.status_code == 409
    assert "stopped while it waited" in stopped.value.detail
    assert not http_request.app.state.session_locks[sid].locked()
    assert queue.holder == "another-chat"


async def test_a_waiting_turn_keeps_announcing_its_place(runtime):
    db, manager, queue, http_request = runtime
    sid = db.create_session()["id"]
    await queue.acquire("another-chat")
    impl_started = asyncio.Event()

    async def impl(run, _db, _session, _request):
        impl_started.set()
        yield 'data: {"type":"done","message_id":1,"content":"ok"}\n\n'

    with (
        patch.object(helpers, "_QUEUE_HEARTBEAT_SECONDS", 0.02),
        patch.object(helpers, "_stream_chat_impl", impl),
    ):
        response = await _send(db, http_request, sid, "queued")
        await _start_detached(response)
        run = manager.get(sid)
        await asyncio.sleep(0.15)

        queued = [e for e in _events(run) if e.get("phase") == "queued"]
        # The first announcement plus several heartbeats, never a silent stream.
        assert len(queued) >= 4
        assert all(e["queue_position"] == 1 for e in queued)
        assert all(e["message"] == "Waiting for the GPU — 1st in line" for e in queued)
        assert queued[-1]["queue_waited_s"] >= queued[0]["queue_waited_s"]

        queue.release()
        await impl_started.wait()
        await run.task
        await response.background()
        after = len([e for e in _events(run) if e.get("phase") == "queued"])
        await asyncio.sleep(0.1)
        # Once the turn has the GPU the heartbeat stops.
        assert len([e for e in _events(run) if e.get("phase") == "queued"]) == after
        assert _events(run)[-1]["type"] == "done"
