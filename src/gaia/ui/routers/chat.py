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

from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import ChatRequest, ChatResponse, ToolConfirmRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


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

    # Acquire session lock with timeout → 409 if the session is already busy
    try:
        await asyncio.wait_for(session_lock.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=409,
            detail="A request is already in progress for this session. "
            "Please wait for it to complete before sending another message.",
        )

    # ── Global concurrency gate ──────────────────────────────────────
    # If the semaphore is full, release the session lock before raising
    try:
        await asyncio.wait_for(chat_semaphore.acquire(), timeout=0.5)
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
            # Transfer both locks to the streaming generator so they are
            # held for the full duration of the stream and released on exit.
            async def _guarded_stream():
                try:
                    db.add_message(request.session_id, "user", request.message)
                    async for chunk in srv._stream_chat_response(
                        db, session, request, http_request
                    ):
                        yield chunk
                finally:
                    session_lock.release()
                    chat_semaphore.release()

            sem_released = True
            return StreamingResponse(
                _guarded_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            try:
                db.add_message(request.session_id, "user", request.message)
                response_text = await srv._get_chat_response(db, session, request)
                msg_id = db.add_message(request.session_id, "assistant", response_text)
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


# Security note: confirm_id (UUID v4) provides request-scoping but not
# authentication. This endpoint is safe for local-only deployments.
# For network-exposed deployments (e.g., tunnel mode), consider adding
# session token validation.
@router.post("/api/chat/confirm")
async def confirm_tool_execution(
    request: ToolConfirmRequest,
    http_request: Request,
):
    """Resolve a pending tool execution confirmation.

    Called by the frontend PermissionPrompt when the user clicks Allow or Deny.
    Unblocks the agent thread waiting inside SSEOutputHandler.confirm_tool_execution().
    """
    active_handlers = getattr(http_request.app.state, "active_sse_handlers", {})
    handler = active_handlers.get(request.session_id)
    if handler is None:
        raise HTTPException(status_code=404, detail="No active session found")

    allowed = request.action == "allow"
    success = handler.resolve_confirmation(request.confirm_id, allowed)
    if not success:
        raise HTTPException(
            status_code=410, detail="Confirmation expired or ID mismatch"
        )

    return {"status": "ok"}
