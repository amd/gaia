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
from ..models import ChatRequest, ChatResponse

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
    # Get or create a lock for this session
    session_locks = http_request.app.state.session_locks
    chat_semaphore = http_request.app.state.chat_semaphore
    sid = request.session_id
    if sid not in session_locks:
        session_locks[sid] = asyncio.Lock()
    session_lock = session_locks[sid]

    if session_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="A request is already in progress for this session. "
            "Please wait for it to complete before sending another message.",
        )

    # ── Global concurrency gate ──────────────────────────────────────
    try:
        await asyncio.wait_for(chat_semaphore.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=429,
            detail="The server is busy processing other chat requests. "
            "Please try again in a few moments.",
        )

    # Track whether the semaphore was handed off to the stream generator
    sem_released = False

    # Resolve the patchable functions through gaia.ui.server so tests
    # that patch("gaia.ui.server._stream_chat_response") work correctly.
    srv = _server_mod()

    try:
        async with session_lock:
            # Save user message (inside lock so ordering is preserved)
            db.add_message(request.session_id, "user", request.message)

            if request.stream:
                # For streaming, we must release the semaphore when the
                # stream finishes, not when we return the StreamingResponse.
                # Wrap the generator to release on completion.
                async def _guarded_stream():
                    try:
                        async for chunk in srv._stream_chat_response(
                            db, session, request
                        ):
                            yield chunk
                    finally:
                        chat_semaphore.release()

                # Transfer semaphore ownership to the streaming generator
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
                # Non-streaming response
                response_text = await srv._get_chat_response(
                    db, session, request
                )
                msg_id = db.add_message(
                    request.session_id, "assistant", response_text
                )
                return ChatResponse(
                    message_id=msg_id,
                    content=response_text,
                    sources=[],
                )
    finally:
        # Release the semaphore for non-streaming requests (or if an
        # error occurred before the streaming generator took ownership).
        if not sem_released:
            chat_semaphore.release()
