# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Session management endpoints for GAIA Agent UI.

Handles session CRUD, message retrieval/deletion, session export,
and session-document attachments.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from .._chat_helpers import evict_session_agent
from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import (
    AttachDocumentRequest,
    CreateSessionRequest,
    MessageListResponse,
    SessionListResponse,
    SessionResponse,
    UpdateSessionRequest,
)
from ..utils import message_to_response, session_to_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"])


# ── Session CRUD ─────────────────────────────────────────────────────────────


@router.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    limit: int = 50, offset: int = 0, db: ChatDatabase = Depends(get_db)
):
    """List all chat sessions."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    sessions = db.list_sessions(limit=limit, offset=offset)
    total = db.count_sessions()
    return SessionListResponse(
        sessions=[session_to_response(s) for s in sessions],
        total=total,
    )


@router.post("/api/sessions", response_model=SessionResponse)
async def create_session(
    request: CreateSessionRequest, db: ChatDatabase = Depends(get_db)
):
    """Create a new chat session."""
    try:
        session = db.create_session(
            title=request.title,
            model=request.model,
            system_prompt=request.system_prompt,
            document_ids=request.document_ids,
        )
        return session_to_response(session)
    except Exception as e:
        logger.error("Failed to create session: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to create session. Check server logs for details.",
        )


@router.get("/api/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, db: ChatDatabase = Depends(get_db)):
    """Get session details."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session_to_response(session)


@router.put("/api/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    request: UpdateSessionRequest,
    db: ChatDatabase = Depends(get_db),
):
    """Update session title, system prompt, or linked documents."""
    session = db.update_session(
        session_id,
        title=request.title,
        system_prompt=request.system_prompt,
        document_ids=request.document_ids,
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session_to_response(session)


@router.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Delete a session and its messages."""
    if not db.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    # Remove the per-session lock to prevent memory leaks
    http_request.app.state.session_locks.pop(session_id, None)
    # Evict the cached ChatAgent for this session so a fresh one is created
    # if the session is ever recreated with the same ID.
    evict_session_agent(session_id)
    return {"deleted": True}


# ── Messages ─────────────────────────────────────────────────────────────────


@router.get("/api/sessions/{session_id}/messages", response_model=MessageListResponse)
async def get_messages(
    session_id: str,
    limit: int = 100,
    offset: int = 0,
    db: ChatDatabase = Depends(get_db),
):
    """Get messages for a session."""
    limit = max(1, min(limit, 10000))
    offset = max(0, offset)
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = db.get_messages(session_id, limit=limit, offset=offset)
    total = db.count_messages(session_id)

    return MessageListResponse(
        messages=[message_to_response(m) for m in messages],
        total=total,
    )


@router.delete("/api/sessions/{session_id}/messages/{message_id}")
async def delete_message(
    session_id: str, message_id: int, db: ChatDatabase = Depends(get_db)
):
    """Delete a single message from a session."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not db.delete_message(session_id, message_id):
        raise HTTPException(status_code=404, detail="Message not found")
    return {"deleted": True}


@router.delete("/api/sessions/{session_id}/messages/{message_id}/and-below")
async def delete_messages_from(
    session_id: str, message_id: int, db: ChatDatabase = Depends(get_db)
):
    """Delete a message and all subsequent messages in the session.

    Used by the "resend" feature: removes the target user message and
    everything below it so the conversation can be replayed.
    """
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    count = db.delete_messages_from(session_id, message_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"deleted": True, "count": count}


# ── Export ───────────────────────────────────────────────────────────────────


@router.get("/api/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    format: str = "markdown",  # noqa: A002
    db: ChatDatabase = Depends(get_db),
):
    """Export session to markdown or JSON."""
    export_format = format  # Avoid shadowing builtin in function body
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = db.get_messages(session_id, limit=10000)

    if export_format == "markdown":
        lines = [f"# {session['title']}\n"]
        lines.append(f"*Created: {session['created_at']}*\n")
        lines.append(f"*Model: {session['model']}*\n\n---\n")

        for msg in messages:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"**{role_label}:**\n\n{msg['content']}\n\n---\n")

        content = "\n".join(lines)
        return {"content": content, "format": "markdown"}
    elif export_format == "json":
        return {"session": session, "messages": messages, "format": "json"}
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: {export_format}",
        )


# ── Session-Document Attachments ─────────────────────────────────────────────


@router.post("/api/sessions/{session_id}/documents")
async def attach_document(
    session_id: str,
    request: AttachDocumentRequest,
    db: ChatDatabase = Depends(get_db),
):
    """Attach a document to a session."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    doc = db.get_document(request.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    db.attach_document(session_id, request.document_id)
    return {"attached": True}


@router.delete("/api/sessions/{session_id}/documents/{doc_id}")
async def detach_document(
    session_id: str, doc_id: str, db: ChatDatabase = Depends(get_db)
):
    """Detach a document from a session."""
    db.detach_document(session_id, doc_id)
    evict_session_agent(session_id)
    return {"detached": True}
