# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Admission slots belong to producers, not their SSE subscribers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager

pytestmark = [pytest.mark.asyncio, pytest.mark.allow_network]


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    manager = RunManager()
    state = SimpleNamespace(
        session_locks={}, chat_semaphore=asyncio.BoundedSemaphore(1)
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    server = SimpleNamespace(_stream_chat_response=helpers._stream_chat_response)
    with (
        patch.object(chat, "run_manager", manager),
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(chat, "_server_mod", return_value=server),
    ):
        yield db, manager, request
    db.close()


@pytest.mark.parametrize("outcome", ["complete", "error", "cancel"])
async def test_disconnect_keeps_slot_until_producer_finishes(runtime, outcome):
    db, manager, http_request = runtime
    a = db.create_session()["id"]
    b = db.create_session()["id"]
    finish = asyncio.Event()

    async def lifecycle(run, _db, _session, _request):
        run.emit('data: {"type":"status","content":"started"}\n\n')
        await finish.wait()
        if outcome == "error":
            raise RuntimeError("controlled producer failure")

    with patch.object(helpers, "_run_chat_lifecycle", lifecycle):
        response = await chat.send_message(
            ChatRequest(session_id=a, message="A", stream=True), http_request, db
        )
        disconnected = asyncio.Event()

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                disconnected.set()

        await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
        run = manager.get(a)
        second = None
        try:
            assert manager.is_running(a)
            assert http_request.app.state.chat_semaphore.locked()

            with pytest.raises(HTTPException) as conflict:
                await chat.send_message(
                    ChatRequest(session_id=a, message="duplicate", stream=True),
                    http_request,
                    db,
                )
            assert conflict.value.status_code == 409

            attached = helpers._attach_chat_stream(a)
            assert "started" in await anext(attached)
            await attached.aclose()

            second = asyncio.create_task(
                chat.send_message(
                    ChatRequest(session_id=b, message="B", stream=True),
                    http_request,
                    db,
                )
            )
            await asyncio.sleep(0.01)
            assert not second.done()
            if outcome == "cancel":
                run.task.cancel()
            else:
                finish.set()
            await asyncio.gather(run.task, return_exceptions=True)
            next_response = await asyncio.wait_for(second, timeout=1)
            # No subscriber consumed B yet, so it has no producer to own its slot.
            await next_response.background()
            await response.background()
            assert not http_request.app.state.chat_semaphore.locked()
            assert not http_request.app.state.session_locks[a].locked()
            assert not http_request.app.state.session_locks[b].locked()
        finally:
            finish.set()
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
            if second is not None and not second.done():
                second.cancel()
                await asyncio.gather(second, return_exceptions=True)


async def test_stream_failure_before_run_start_releases_slot(runtime):
    db, _manager, http_request = runtime
    sid = db.create_session()["id"]
    response = await chat.send_message(
        ChatRequest(session_id=sid, message="A", stream=True), http_request, db
    )
    with patch.object(db, "add_message", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError, match="write failed"):
            await anext(response.body_iterator)
    # Generator cleanup must work even when an exception prevents Starlette
    # from reaching the response's BackgroundTask.
    assert not http_request.app.state.chat_semaphore.locked()
    assert not http_request.app.state.session_locks[sid].locked()
