# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Chat endpoint for GAIA Agent UI.

Provides the ``/api/chat/send`` endpoint with both streaming (SSE) and
non-streaming response modes.  The heavy chat logic (``_get_chat_response``,
``_stream_chat_response``) lives in ``gaia.ui._chat_helpers`` and is
accessed through ``gaia.ui.server`` so that test patches applied to
``gaia.ui.server._get_chat_response`` etc. take effect.
"""

import asyncio
import logging
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import ChatRequest, ChatResponse
from ..run_manager import run_manager
from ..sse_handler import (
    _RAG_RESULT_JSON_SUB_RE,
    _THOUGHT_JSON_SUB_RE,
    _TOOL_CALL_JSON_SUB_RE,
    _clean_answer_json,
    _fix_double_escaped,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _notify_loop(session_id: str) -> None:
    """Notify the AgentLoop that a user message was processed.

    Imported lazily to avoid a circular import at module level.
    Non-fatal: if the loop is not running, this is a no-op.
    """
    try:
        from gaia.ui.agent_loop import agent_loop

        agent_loop.notify_user_message(session_id)
    except Exception:
        pass


def _server_mod():
    """Lazily resolve the ``gaia.ui.server`` module.

    Router endpoints call patchable functions through this module reference
    so that ``@patch("gaia.ui.server._get_chat_response")`` in tests
    correctly intercepts the call.
    """
    return sys.modules["gaia.ui.server"]


@router.post("/api/chat/send")
async def send_message(
    request: ChatRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Send a message and get a response (streaming or non-streaming).

    Concurrency is controlled at two levels:
    1. A global semaphore (chat_semaphore) limits overall concurrent
       chat requests to avoid resource exhaustion.
    2. A per-session lock (session_locks) prevents the same session
       from having overlapping requests that would corrupt conversation
       state.
    """
    # Verify session exists
    session = db.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # ── Per-session lock ─────────────────────────────────────────────
    # setdefault is atomic: safe under concurrent requests for the same session
    session_locks = http_request.app.state.session_locks
    chat_semaphore = http_request.app.state.chat_semaphore
    sid = request.session_id
    session_lock = session_locks.setdefault(sid, asyncio.Lock())

    # Reject overlapping turns for the same session. Force-releasing an
    # asyncio.Lock held by another coroutine is unsafe because the lock
    # has no ownership tracking.
    #
    # The lock guards the synchronous request window; ``run_manager`` guards
    # the *background tail* — a streaming run keeps going (and persisting)
    # after the client disconnects and the HTTP lock is released (#1580), so
    # a new turn for the same session must also be rejected while that
    # background run is still active, or it would corrupt the cached agent's
    # conversation state.
    if session_lock.locked() or run_manager.is_running(sid):
        raise HTTPException(
            status_code=409,
            detail="A chat request is already in progress for this session. "
            "Please wait for it to finish.",
        )
    await session_lock.acquire()

    # ── Global concurrency gate ──────────────────────────────────────
    # Queue rather than immediately reject: wait up to 60 s for a slot.
    # This prevents spurious 429s for sequential workloads (eval runner,
    # multi-turn conversations) where the prior request is just wrapping up.
    try:
        await asyncio.wait_for(chat_semaphore.acquire(), timeout=60.0)
    except asyncio.TimeoutError:
        session_lock.release()
        raise HTTPException(
            status_code=429,
            detail="The server is busy processing other chat requests. "
            "Please try again in a few moments.",
        )

    # Both session_lock and chat_semaphore are now held by this coroutine.
    # Track whether ownership was transferred to the streaming generator.
    sem_released = False

    # Resolve the patchable functions through gaia.ui.server so tests
    # that patch("gaia.ui.server._stream_chat_response") work correctly.
    srv = _server_mod()

    try:
        if request.stream:
            # Use BackgroundTask to ensure locks are released even if the client
            # disconnects mid-stream (async generator finally block is unreliable
            # when FastAPI/Starlette drops the connection before first yield).
            async def _release_stream_resources():
                try:
                    session_lock.release()
                except RuntimeError:
                    pass
                try:
                    chat_semaphore.release()
                except ValueError:
                    pass

            async def _stream():
                db.add_message(request.session_id, "user", request.message)
                # The run's detached lifecycle owns producer + persistence and
                # fires the AgentLoop notify on real completion; this subscriber
                # just relays buffered + live events to the browser. Detaching
                # (client disconnect) no longer cancels the run (#1580).
                async for chunk in srv._stream_chat_response(db, session, request):
                    yield chunk

            sem_released = True
            return StreamingResponse(
                _stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
                background=BackgroundTask(_release_stream_resources),
            )
        else:
            try:
                db.add_message(request.session_id, "user", request.message)
                response_text = await srv._get_chat_response(db, session, request)
                # Clean LLM output artifacts (same pipeline as streaming path)
                if response_text:
                    response_text = _clean_answer_json(response_text)
                    response_text = _TOOL_CALL_JSON_SUB_RE.sub("", response_text)
                    response_text = _THOUGHT_JSON_SUB_RE.sub("", response_text)
                    response_text = _RAG_RESULT_JSON_SUB_RE.sub("", response_text)
                    response_text = _fix_double_escaped(response_text)
                    response_text = response_text.strip()
                msg_id = db.add_message(request.session_id, "assistant", response_text)
                # Notify AgentLoop after the non-streaming response completes
                _notify_loop(request.session_id)

                # Fire-and-forget auto-titling — same hook the streaming
                # path uses. GAIA renames its own session after the first
                # answer (and on topic shifts thereafter); see
                # _maybe_update_session_title for the rules. We pass
                # model_id from the loaded session so the title call uses
                # whichever LLM is actually live in Lemonade.
                try:
                    from gaia.ui._chat_helpers import _maybe_update_session_title

                    _title_task = asyncio.create_task(
                        _maybe_update_session_title(
                            db=db,
                            session_id=request.session_id,
                            user_msg=request.message,
                            assistant_msg=response_text or "",
                            model_id=session.get("model"),
                        )
                    )
                    # Pin a reference so GC doesn't kill the task before
                    # it finishes; the function is short and self-contained,
                    # so a stray reference is fine.
                    _title_task.add_done_callback(lambda _t: None)
                except Exception:  # pylint: disable=broad-except
                    pass  # auto-titling failures must never block a response
                return ChatResponse(
                    message_id=msg_id,
                    content=response_text,
                    sources=[],
                )
            finally:
                session_lock.release()
    finally:
        # Release the semaphore for non-streaming requests or on error before
        # the streaming generator took ownership.
        if not sem_released:
            chat_semaphore.release()


class ToolConfirmRequest(BaseModel):
    """Request body for the tool confirmation endpoint."""

    session_id: str
    approved: bool


@router.post("/api/chat/confirm-tool")
async def confirm_tool(request: ToolConfirmRequest):
    """Respond to a tool confirmation prompt from the agent.

    The agent blocks in ``SSEOutputHandler.confirm_tool_execution()`` until
    this endpoint is called.  The frontend triggers this when the user
    clicks Allow or Deny on the PermissionPrompt overlay.
    """
    from .._chat_helpers import _active_sse_handlers

    handler = _active_sse_handlers.get(request.session_id)
    if not handler:
        raise HTTPException(
            status_code=404,
            detail="No active chat session found for this session ID",
        )
    handler.resolve_tool_confirmation(request.approved)
    return {"status": "ok", "approved": request.approved}


class CancelStreamRequest(BaseModel):
    session_id: str


@router.post("/api/chat/cancel")
async def cancel_stream(request: CancelStreamRequest):
    """Cancel an active streaming chat session by setting its SSE handler cancelled flag.

    This allows the frontend's Cancel button to gracefully request cancellation
    without tearing down the HTTP connection.
    """
    from .._chat_helpers import _active_sse_handlers

    handler = _active_sse_handlers.get(request.session_id)
    if not handler:
        raise HTTPException(
            status_code=404, detail="No active chat session found for this session ID"
        )
    handler.cancelled.set()
    # #2109: an email-relay turn can be parked in a blocking socket read that
    # the cancelled flag alone can't interrupt — force it to error out now.
    handler.close_active_relay_response()
    return {"status": "ok", "cancelled": True}


@router.get("/api/chat/active")
async def list_active_runs():
    """Return the session ids with a currently-running chat turn.

    The Agent UI polls this to render a "still running" indicator on
    backgrounded sessions in the sidebar — runs continue server-side after
    the user navigates away, so this is the source of truth independent of
    any open SSE connection (#1580 follow-up).
    """
    return {"session_ids": run_manager.active_sessions()}


@router.get("/api/chat/attach")
async def attach_stream(session_id: str):
    """Re-attach to an in-flight background run and stream its events (SSE).

    Used when the user revisits a session whose turn is still running. The
    response replays every event emitted so far, then streams live events
    to completion. No session lock is taken — the originating ``/send`` run
    already owns the turn; this is a read-only subscriber.
    """
    if not run_manager.is_running(session_id):
        raise HTTPException(
            status_code=404,
            detail="No active run for this session.",
        )

    srv = _server_mod()
    return StreamingResponse(
        srv._attach_chat_stream(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
