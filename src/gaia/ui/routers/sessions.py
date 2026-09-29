# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Session management endpoints for GAIA Agent UI.

Handles session CRUD, message retrieval/deletion, session export,
and session-document attachments.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from gaia.config import GaiaConfigError

from .._chat_helpers import (
    _SIDECAR_AGENT_TYPES,
    _agent_type_unknown,
    evict_session_agent,
    get_agent_registry,
    resolve_device_model,
)
from ..database import (
    SESSION_DEFAULT_MODEL,
    ChatDatabase,
    is_placeholder_title,
    resolved_default_model,
)
from ..dependencies import get_db
from ..email_sidecar.profiles import profile_for
from ..models import (
    AttachDocumentRequest,
    CreateSessionRequest,
    MessageListResponse,
    SessionListResponse,
    SessionResponse,
    UpdateSessionRequest,
)
from ..run_manager import run_manager
from ..utils import message_to_response, session_to_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"])


def _reject_if_turn_running(http_request: Request, session_id: str) -> None:
    """409 when a turn is using the session's cached agent, which eviction would break."""
    lock = http_request.app.state.session_locks.get(session_id)
    if (lock is not None and lock.locked()) or run_manager.is_running(session_id):
        raise HTTPException(
            status_code=409,
            detail="A chat request is in progress for this session. "
            "Wait for it to finish (or stop it), then try again.",
        )


def _is_gaia_config_error(exc: Exception) -> bool:
    """True if exc is a GaiaConfigError.

    Checked by isinstance first; falls back to matching the exception
    class's qualified name so this still works if gaia.config ever ends up
    imported under two different module identities (seen once in CI, not
    yet root-caused) — a plain isinstance/except-clause match silently
    took the generic branch instead of this one.
    """
    if isinstance(exc, GaiaConfigError):
        return True
    exc_type = type(exc)
    return (
        f"{exc_type.__module__}.{exc_type.__qualname__}"
        == "gaia.config.GaiaConfigError"
    )


class _SystemSseEmitter:
    """Fan-out SSE broadcaster for system-level UI events."""

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def emit(self, event: dict) -> None:
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop for slow clients

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass


_system_emitter = _SystemSseEmitter()


# ── Session CRUD ─────────────────────────────────────────────────────────────


def _reject_unknown_agent_type(agent_type: str | None) -> None:
    """Raise HTTP 422 when *agent_type* names no registered agent.

    A session bound to an unknown id can never answer — every turn would get
    the canned "couldn't load the agent" reply — so refuse it up front and
    name the ids that do resolve. Legacy aliases (``doc-lite``) still pass.
    """
    if not agent_type:
        return
    registry = get_agent_registry()
    if not _agent_type_unknown(agent_type, registry):
        return
    # Every id _agent_type_unknown accepts, legacy aliases aside. Only the
    # ALWAYS-relay sidecars are added unconditionally: since #4161 the others
    # must resolve in the registry like any agent, so listing every sidecar id
    # here told the user 'gaia' was both unknown and registered.
    valid_ids = sorted(
        {reg.id for reg in registry.list()}
        | {"chat"}
        | {aid for aid in _SIDECAR_AGENT_TYPES if profile_for(aid).always_relay}
    )
    load_error = registry.get_load_error(agent_type)
    reason = f" It failed to load: {load_error}." if load_error else ""
    raise HTTPException(
        status_code=422,
        detail=(
            f"Unknown agent_type '{agent_type}'.{reason} Registered agent ids: "
            f"{', '.join(valid_ids)}. Pick one of these, or install the agent "
            "from the Agent Hub (`gaia hub`) and restart the server."
        ),
    )


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
    _reject_unknown_agent_type(request.agent_type)
    try:
        session = db.create_session(
            title=request.title,
            model=request.model,
            system_prompt=request.system_prompt,
            document_ids=request.document_ids,
            private=request.private,
            agent_type=request.agent_type,
            device=request.device,
            mail_provider=request.mail_provider,
        )
        return session_to_response(session)
    except Exception as e:
        logger.error("Failed to create session: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=(
                str(e)
                if _is_gaia_config_error(e)
                else "Failed to create session. Check server logs for details."
            ),
        )


@router.get("/api/sessions/events")
async def session_events():
    """SSE stream for system-level session events (activation etc.).

    Frontend subscribes on mount; events are emitted by activate_session().
    Event shape: {"type": "set_active_session", "session_id": "<id>"}
    """
    from fastapi.responses import StreamingResponse

    async def generate():
        q = await _system_emitter.subscribe()
        try:
            yield ": keepalive\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # prevent proxy timeout
        finally:
            await _system_emitter.unsubscribe(q)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/api/sessions/{session_id}/activate", status_code=200)
async def activate_session(session_id: str, db: ChatDatabase = Depends(get_db)):
    """Signal the frontend to switch to this session (MCP automation support).

    Emits a set_active_session SSE event that App.tsx consumes to call
    setCurrentSession. Used by open_session_in_browser() in the MCP bridge.
    """
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await _system_emitter.emit({"type": "set_active_session", "session_id": session_id})
    return {"activated": True, "session_id": session_id}


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
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Update session title, system prompt, or linked documents."""
    _reject_unknown_agent_type(request.agent_type)
    if (
        request.agent_type is not None
        or request.device is not None
        or request.mail_provider is not None
    ):
        _reject_if_turn_running(http_request, session_id)
        evict_session_agent(session_id)

    # On a device switch, rewrite the session's model to that device's
    # registered model so the agent rebuilt after eviction loads the right
    # model and the model dropdown reflects reality. Only rewrite when the
    # device model differs and the session isn't pinned to a non-default
    # model on the default GPU device. This "not pinned" test deliberately
    # differs from _build_create_kwargs's own default check in
    # _chat_helpers: a configured default_model counts as "not pinned" here
    # (so it still follows a device switch) but as "session-explicit" there
    # (so it still reaches the agent as model_id) — the two guards answer
    # different questions about the same value on purpose.
    device_model = None
    if request.device is not None:
        existing = db.get_session(session_id)
        agent_type = request.agent_type or (existing or {}).get("agent_type") or "chat"
        resolved, _ = resolve_device_model(agent_type, request.device)
        if resolved:
            current_model = (existing or {}).get("model")
            try:
                configured_default = resolved_default_model()
            except Exception as e:
                if not _is_gaia_config_error(e):
                    raise
                raise HTTPException(status_code=500, detail=str(e))
            is_default_model = current_model in (
                None,
                SESSION_DEFAULT_MODEL,
                configured_default,
            )
            device_is_explicit = request.device != "gpu"
            if resolved != current_model and (is_default_model or device_is_explicit):
                device_model = resolved

    # A PUT that sets the title is an explicit rename → pin it against the
    # auto-retitler (#2165), unless the caller says otherwise (the webui's
    # client-side auto-title sends title_is_custom=false) or the new title
    # is itself a placeholder (renaming to "New Chat" re-enables auto-title).
    title_is_custom = request.title_is_custom
    if request.title is not None and title_is_custom is None:
        title_is_custom = not is_placeholder_title(request.title)

    session = db.update_session(
        session_id,
        title=request.title,
        title_is_custom=title_is_custom,
        system_prompt=request.system_prompt,
        document_ids=request.document_ids,
        private=request.private,
        agent_type=request.agent_type,
        device=request.device,
        model=device_model,
        mail_provider=request.mail_provider,
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
    """Delete a session and its messages.

    A turn still running on the session is stopped first, and the delete waits
    for it to finish: the turn is still using the session's agent and will
    persist its answer to the session row.
    """
    if db.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    run_manager.cancel(session_id)
    session_locks = http_request.app.state.session_locks
    # Every turn and goal tick holds this lock for its whole run.
    async with session_locks.setdefault(session_id, asyncio.Lock()):
        if not db.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        session_locks.pop(session_id, None)
        evict_session_agent(session_id)
    return {"deleted": True}


@router.patch("/api/sessions/{session_id}/private", response_model=SessionResponse)
async def toggle_session_privacy(
    session_id: str,
    db: ChatDatabase = Depends(get_db),
):
    """Toggle a session's private (incognito) mode on or off."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    current = bool(session.get("private", 0))
    updated = db.update_session(session_id, private=not current)
    return session_to_response(updated)


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
    session_id: str,
    doc_id: str,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Detach a document from a session."""
    _reject_if_turn_running(http_request, session_id)
    db.detach_document(session_id, doc_id)
    evict_session_agent(session_id)
    return {"detached": True}
