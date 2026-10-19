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
import os
import sys
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from gaia.agents.base.tool_grants import grant_scope

from .. import permissions as session_permissions
from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import ChatRequest, ChatResponse
from ..run_manager import run_manager
from ..security import flagship_only
from ..sse_handler import (
    _RAG_RESULT_JSON_SUB_RE,
    _THOUGHT_JSON_SUB_RE,
    _TOOL_CALL_JSON_SUB_RE,
    _clean_answer_json,
    _fix_double_escaped,
)
from ..turn_queue import TurnCancelled, turn_queue
from ..utils import resolve_upload_links

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _notify_loop(session_id: str) -> None:
    """Notify the AgentLoop that a user message was processed.

    Imported lazily to avoid a circular import at module level. A failure is
    logged rather than raised: the user's message is already answered and
    saved, so the request must not fail over a missed follow-up trigger.
    """
    try:
        from gaia.ui.agent_loop import agent_loop

        agent_loop.notify_user_message(session_id)
    except Exception:  # noqa: BLE001 - logged with context, response already saved
        logger.warning(
            "Could not notify the agent loop for session %s; approved goals "
            "will not continue until the next message",
            session_id,
            exc_info=True,
        )


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
    1. Every turn waits in one visible line for the model (``turn_queue``).
       A streamed turn waits inside its detached run and reports its place
       in line; a non-streamed one waits here for up to 60 s.
    2. A per-session lock (session_locks) prevents the same session
       from having overlapping requests that would corrupt conversation
       state.
    """
    # Verify session exists
    session = db.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    request.agent_type = flagship_only(
        http_request, request.agent_type, session.get("agent_type") or "chat"
    )

    # ── Per-session lock ─────────────────────────────────────────────
    # setdefault is atomic: safe under concurrent requests for the same session
    session_locks = http_request.app.state.session_locks
    sid = request.session_id
    session_lock = session_locks.setdefault(sid, asyncio.Lock())

    # Reject overlapping turns for the same session. Force-releasing an
    # asyncio.Lock held by another coroutine is unsafe because the lock
    # has no ownership tracking.
    #
    # The session lock and run registry cover the entire producer lifetime,
    # including detached runs, to protect the cached agent's conversation state.
    if session_lock.locked() or run_manager.is_running(sid):
        raise HTTPException(
            status_code=409,
            detail="A chat request is already in progress for this session. "
            "Please wait for it to finish.",
        )
    await session_lock.acquire()

    # A streamed turn takes its place in line inside the run, so leaving the
    # chat while it waits doesn't drop it.
    holds_turn = False
    if not request.stream:
        try:
            await asyncio.wait_for(turn_queue.acquire(sid), timeout=60.0)
            holds_turn = True
        except asyncio.TimeoutError:
            session_lock.release()
            raise HTTPException(
                status_code=429,
                detail="The model is busy with another chat. Try again when it "
                "finishes, or use streaming to wait in line.",
            )
        except TurnCancelled:
            session_lock.release()
            raise HTTPException(
                status_code=409,
                detail="This message was stopped while it waited for another "
                "chat to finish. Send it again to retry.",
            )
        except asyncio.CancelledError:
            session_lock.release()
            raise

    # Resolve the patchable functions through gaia.ui.server so tests
    # that patch("gaia.ui.server._stream_chat_response") work correctly.
    srv = _server_mod()

    # The transcript keeps the browser link; the agent gets the file's path.
    agent_request = request.model_copy(
        update={"message": resolve_upload_links(request.message)}
    )

    try:
        if request.stream:
            stream_resources_released = False

            def _release_stream_locks(_task=None):
                nonlocal stream_resources_released
                if stream_resources_released:
                    return
                stream_resources_released = True
                session_lock.release()

            async def _release_stream_resources():
                # A disconnected subscriber must not admit a second producer.
                run = run_manager.get(sid)
                if run is not None and run.task is not None and not run.done.is_set():
                    run.task.add_done_callback(_release_stream_locks)
                else:
                    # Includes disconnect/failure before the producer started.
                    _release_stream_locks()

            async def _stream():
                try:
                    db.add_message(request.session_id, "user", request.message)
                    # The detached lifecycle owns producer + persistence;
                    # this subscriber only relays buffered and live events.
                    async for chunk in srv._stream_chat_response(
                        db, session, agent_request
                    ):
                        yield chunk
                finally:
                    await _release_stream_resources()

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
            from .._chat_helpers import _record_run_outcome

            run_status = "failed"
            try:
                db.add_message(request.session_id, "user", request.message)
                model_messages = []
                turn_box: dict = {}
                response_text = await srv._get_chat_response(
                    db,
                    session,
                    agent_request,
                    model_messages=model_messages,
                    turn_box=turn_box,
                )
                # Clean LLM output artifacts (same pipeline as streaming path)
                if response_text:
                    response_text = _clean_answer_json(response_text)
                    response_text = _TOOL_CALL_JSON_SUB_RE.sub("", response_text)
                    response_text = _THOUGHT_JSON_SUB_RE.sub("", response_text)
                    response_text = _RAG_RESULT_JSON_SUB_RE.sub("", response_text)
                    response_text = _fix_double_escaped(response_text)
                    response_text = response_text.strip()
                msg_id = db.add_message(
                    request.session_id,
                    "assistant",
                    response_text,
                    model_messages=model_messages or None,
                )
                if "recording" in turn_box:
                    turn_box["recording"].link_message(msg_id)
                # Notify AgentLoop after the non-streaming response completes
                _notify_loop(request.session_id)

                # Fire-and-forget auto-titling — same hook the streaming
                # path uses. GAIA renames its own session after the first
                # answer, and never again; see
                # _maybe_update_session_title for the rules. We pass
                # model_id from the loaded session so the title call uses
                # whichever LLM is actually live in Lemonade.
                from gaia.ui._chat_helpers import (
                    _maybe_update_session_title,
                    _spawn_background,
                )

                _spawn_background(
                    _maybe_update_session_title(
                        db=db,
                        session_id=request.session_id,
                        user_msg=request.message,
                        assistant_msg=response_text or "",
                        model_id=session.get("model"),
                    )
                )
                run_status = "done"
                return ChatResponse(
                    message_id=msg_id,
                    content=response_text,
                    sources=[],
                )
            finally:
                _record_run_outcome(db, request.session_id, run_status)
                session_lock.release()
    finally:
        if holds_turn:
            turn_queue.release()


# A late answer to a turn that already ended: say so, not "no session".
_PROMPT_TURN_ENDED = (
    "That permission prompt is no longer waiting for an answer: the reply it "
    "belonged to has ended. Send a new message to ask GAIA to try again."
)
_QUESTION_TURN_ENDED = (
    "That question is no longer waiting for an answer: the reply it belonged "
    "to has ended. Send your answer as a new message instead."
)


class ToolConfirmRequest(BaseModel):
    """Request body for the tool confirmation endpoint.

    ``always`` answers "allow" and grants this exact invocation for the rest of
    the chat (see ``gaia.agents.base.tool_grants``). ``confirm_id`` echoes the
    prompt being answered, so a late click cannot approve a newer prompt.
    """

    session_id: str
    approved: bool
    always: bool = False
    confirm_id: Optional[str] = None


@router.post("/api/chat/confirm-tool")
async def confirm_tool(request: ToolConfirmRequest):
    """Answer the tool confirmation the agent is blocked on.

    The agent waits in ``SSEOutputHandler.confirm_tool_execution()`` until this
    endpoint resolves it with allow once, always allow in this chat, or deny.
    """
    from .._chat_helpers import _active_sse_handlers

    handler = _active_sse_handlers.get(request.session_id)
    if not handler:
        raise HTTPException(status_code=404, detail=_PROMPT_TURN_ENDED)
    stale = HTTPException(
        status_code=409,
        detail="That permission prompt is no longer waiting for an answer.",
    )
    if handler.relay_confirmation_pending():
        return await _confirm_relayed_tool(handler, request, stale)
    always = request.always and request.approved
    pending_tool, pending_args = None, {}
    if always:
        with handler._confirm_lock:
            live_id = handler._confirm_id
            pending_tool = handler._confirm_tool
            pending_args = handler._confirm_args or {}
        # A late click must not grant whatever prompt replaced the one it answered.
        if request.confirm_id is not None and request.confirm_id != live_id:
            raise stale
        if not pending_tool or grant_scope(pending_tool, pending_args) is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This call has no scope narrow enough to allow always; "
                    "answer it with allow once or deny."
                ),
            )
    delivered = handler.resolve_tool_confirmation(
        request.approved, always=always, confirm_id=request.confirm_id
    )
    if not delivered:
        raise stale
    granted = None
    if always:
        # Only now: a prompt that expired meanwhile must not leave a grant behind.
        granted = session_permissions.for_session(request.session_id).grant(
            pending_tool, pending_args
        )
    return {"status": "ok", "approved": request.approved, "granted": granted}


async def _confirm_relayed_tool(
    handler, request: ToolConfirmRequest, stale: HTTPException
):
    """Deliver allow-once or deny to the sidecar a relayed run is parked on."""
    from starlette.concurrency import run_in_threadpool

    from gaia.ui.email_sidecar.errors import SidecarError

    if request.always and request.approved:
        raise HTTPException(
            status_code=409,
            detail=(
                "This agent runs as a separate process, so it can't be allowed "
                "always from here; answer with allow once or deny."
            ),
        )
    try:
        # Off the event loop: this is an HTTP round trip to the sidecar.
        delivered = await run_in_threadpool(
            handler.resolve_relay_confirmation, request.approved, request.confirm_id
        )
    except SidecarError as exc:
        raise HTTPException(
            status_code=409, detail=f"Could not deliver the decision: {exc}"
        ) from exc
    if not delivered:
        raise stale
    return {"status": "ok", "approved": request.approved, "granted": None}


class PermissionModeRequest(BaseModel):
    """``ask`` prompts for every gated tool; ``full_access`` runs them unasked."""

    mode: str


def _permissions_view(session_id: str) -> dict:
    perms = session_permissions.get(session_id)
    if perms is None:
        return {
            "session_id": session_id,
            "mode": session_permissions.MODE_ASK,
            "grants": [],
        }
    return {"session_id": session_id, "mode": perms.mode, "grants": perms.grants()}


def _require_session(db: ChatDatabase, session_id: str) -> None:
    if db.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"No chat {session_id!r}.")


@router.get("/api/chat/permissions")
async def list_permissions():
    """Every chat with full access on or "always allow" grants, for Settings.

    ``default_mode`` is the mode a new chat starts in (``full_access`` in config).
    """
    return {
        "default_mode": session_permissions.default_mode(),
        "sessions": [
            {"session_id": sid, "mode": p.mode, "grants": p.grants()}
            for sid, p in session_permissions.all_sessions().items()
            if p.grants() or p.mode != session_permissions.MODE_ASK
        ],
    }


@router.get("/api/chat/permissions/{session_id}")
async def get_permissions(session_id: str, db: ChatDatabase = Depends(get_db)):
    """This chat's permission mode and its "always allow" grants."""
    _require_session(db, session_id)
    return _permissions_view(session_id)


@router.put("/api/chat/permissions/{session_id}")
async def set_permission_mode(
    session_id: str, body: PermissionModeRequest, db: ChatDatabase = Depends(get_db)
):
    """Switch between ask and full access; applies to the next gated tool."""
    _require_session(db, session_id)
    try:
        session_permissions.for_session(session_id).set_mode(body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _sync_relayed_mode(
        session_id, body.mode == session_permissions.MODE_FULL_ACCESS
    )
    return _permissions_view(session_id)


async def _sync_relayed_mode(session_id: str, enabled: bool) -> None:
    """Carry a mode switch to the sidecar session of a relayed run in flight.

    The next turn sets the mode before it starts either way. If this switch
    can't reach the sidecar and was to Ask, the run is stopped: it can't be
    left running with full access the user just turned off.
    """
    from starlette.concurrency import run_in_threadpool

    from gaia.ui.email_sidecar.errors import SidecarError

    from .._chat_helpers import _active_sse_handlers

    handler = _active_sse_handlers.get(session_id)
    proxy = getattr(handler, "active_relay_proxy", None)
    sidecar_session = getattr(handler, "active_relay_mode_session", None)
    if proxy is None or sidecar_session is None:
        return
    try:
        await run_in_threadpool(proxy.set_bypass, sidecar_session, enabled)
    except SidecarError as exc:
        if not enabled:
            handler.cancelled.set()
            handler.close_active_relay_response()
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Ask is saved, but the running agent couldn't be told ({exc}), "
                    "so its turn was stopped. Send the message again."
                ),
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=(
                f"Full access is saved, but the running agent couldn't be told "
                f"({exc}), so this turn keeps asking. It applies from the next "
                "message."
            ),
        ) from exc


@router.delete("/api/chat/permissions/{session_id}/grants")
async def revoke_grants(
    session_id: str, key: Optional[str] = None, db: ChatDatabase = Depends(get_db)
):
    """Revoke one "always allow" grant (``?key=``), or all of them."""
    _require_session(db, session_id)
    perms = session_permissions.get(session_id)
    removed = perms.revoke(key) if perms is not None else 0
    if key is not None and removed == 0:
        raise HTTPException(status_code=404, detail=f"No grant {key!r} in this chat.")
    return _permissions_view(session_id)


class UserInputRequest(BaseModel):
    """Request body for answering a mid-run ``needs_input`` question."""

    session_id: str
    request_id: str
    value: str


@router.post("/api/chat/user-input")
async def user_input(request: UserInputRequest):
    """Answer a mid-run ``needs_input`` question from the agent (#2595).

    Two run shapes can be waiting on this: an in-process agent blocked in
    ``SSEOutputHandler.request_user_input_blocking()`` (resolved via
    ``resolve_user_input``), or an email-relay run blocked on the sidecar's
    own ``/query`` loop (resolved via ``resolve_relay_input``, which posts to
    the sidecar). Tried in that order; a session with neither pending is a
    404, never a silent no-op accept.
    """
    from gaia.ui.email_sidecar.errors import SidecarError

    from .._chat_helpers import _active_sse_handlers

    handler = _active_sse_handlers.get(request.session_id)
    if not handler:
        raise HTTPException(status_code=404, detail=_QUESTION_TURN_ENDED)
    delivered = handler.resolve_user_input(request.request_id, request.value)
    if not delivered:
        try:
            delivered = handler.resolve_relay_input(request.request_id, request.value)
        except SidecarError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Could not deliver the answer: {exc}",
            ) from exc
    if not delivered:
        raise HTTPException(
            status_code=404,
            detail="That question is no longer waiting for an answer: it was "
            "already answered or its wait ran out.",
        )
    return {"status": "ok", "request_id": request.request_id}


class ScriptedUserRequest(BaseModel):
    """Commands the eval's scripted user declines; empty turns it off."""

    decline_commands: List[str] = []


@router.post("/api/eval/scripted-user")
async def set_scripted_user(request: ScriptedUserRequest):
    """Script a user who declines these commands and answers nothing (#4447).

    Eval-only: 403 unless the backend runs with ``GAIA_EVAL_SCRIPTED_USER=1``,
    because a scripted user overrides real approvals for every session.
    """
    from gaia.ui import scripted_user

    if os.environ.get(scripted_user.ENV_VAR) != "1":
        raise HTTPException(
            status_code=403,
            detail=(
                "The scripted eval user is disabled. Restart the Agent UI backend "
                f"with {scripted_user.ENV_VAR}=1 to let `gaia eval agent` run "
                "scenarios that set setup.decline_commands."
            ),
        )
    try:
        declined = scripted_user.set_declined_commands(request.decline_commands)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "decline_commands": declined}


class CancelStreamRequest(BaseModel):
    session_id: str


@router.post("/api/chat/cancel")
async def cancel_stream(request: CancelStreamRequest):
    """Cancel an active streaming chat session by setting its SSE handler cancelled flag.

    This allows the frontend's Cancel button to gracefully request cancellation
    without tearing down the HTTP connection.
    """
    from .._chat_helpers import _active_sse_handlers

    # A turn still waiting in line has no handler yet; withdraw it instead.
    if turn_queue.cancel(request.session_id):
        return {"status": "ok", "cancelled": True}
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
    """Return the chats with a turn in flight, and what each is waiting on.

    The Agent UI polls this for the chat list: runs continue server-side
    after the user navigates away, so this is the source of truth
    independent of any open SSE connection (#1580 follow-up).

    * ``session_ids`` — every chat with a turn running or waiting in line.
    * ``queued`` — chats waiting for the model, first to run next first.
    * ``awaiting_user`` — chat id → ``"permission"`` or ``"question"`` for
      turns paused on the user (#5221).
    """
    session_ids = run_manager.active_sessions()
    awaiting = {}
    for sid in session_ids:
        run = run_manager.get(sid)
        handler = run.handler if run is not None else None
        waiting_on = handler.waiting_on() if handler is not None else None
        if waiting_on:
            awaiting[sid] = waiting_on
    return {
        "session_ids": session_ids,
        "queued": [sid for sid in turn_queue.waiting() if run_manager.is_running(sid)],
        "awaiting_user": awaiting,
    }


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
