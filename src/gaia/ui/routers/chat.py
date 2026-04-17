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
from ..sse_handler import (
    _RAG_RESULT_JSON_SUB_RE,
    _THOUGHT_JSON_SUB_RE,
    _TOOL_CALL_JSON_SUB_RE,
    _clean_answer_json,
    _fix_double_escaped,
)

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

    # Acquire session lock — if a previous request is stuck (hung LLM
    # connection, crashed stream), force-release and proceed rather than
    # leaving the user permanently stuck with "request already in progress".
    try:
        await asyncio.wait_for(session_lock.acquire(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning(
            "Force-releasing stuck session lock for %s "
            "(previous request likely hung)",
            sid,
        )
        try:
            session_lock.release()
        except RuntimeError:
            pass  # Lock wasn't held — race condition, safe to ignore
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
