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
import sqlite3
import sys
import threading
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from gaia.agents.base.tool_grants import grant_scope

from .. import answer_retry, followups
from .. import permissions as session_permissions
from .._chat_helpers import _spawn_background, model_context_messages, stuck_producer
from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import ChatRequest, ChatResponse
from ..restart_recovery import CANCELLED_TURN_NOTICE
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
       in line; a non-streamed one waits here, its message already saved,
       until it reaches the model or is stopped with ``/api/chat/cancel``.
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
    if stuck_producer(sid) is not None:
        raise HTTPException(
            status_code=409,
            detail="GAIA is still stopping the previous reply in this chat: "
            "a tool it was running has not returned yet. Send your message "
            "again in a moment; other chats are not affected.",
        )
    if session_lock.locked() or run_manager.is_running(sid):
        raise HTTPException(
            status_code=409,
            detail="A chat request is already in progress for this session. "
            "Please wait for it to finish.",
        )
    await session_lock.acquire()

    # A streamed turn takes its place in line inside the run, so leaving the
    # chat while it waits doesn't drop it. A non-streamed one is saved first,
    # so a long wait never loses it.
    holds_turn = False
    question_id = None
    if not request.stream:
        try:
            question_id = db.begin_turn(sid, request.message)
            await turn_queue.acquire(sid)
            holds_turn = True
        except TurnCancelled:
            db.add_message(sid, "assistant", "Cancelled.", ends_turn=True)
            session_lock.release()
            raise HTTPException(
                status_code=409,
                detail="This message was stopped while it waited for another "
                "chat to finish. It is saved in the chat; send it again to retry.",
            )
        except asyncio.CancelledError:
            # The client left while waiting: close the turn it saved.
            db.add_message(sid, "assistant", "Cancelled.", ends_turn=True)
            session_lock.release()
            raise
        except BaseException:
            if question_id is not None:
                db.discard_turn(question_id)
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
                stuck = stuck_producer(sid)
                if stuck is not None:
                    # The stopped turn's thread still drives this chat's agent.
                    _spawn_background(_release_after(stuck, session_lock))
                    return
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
                    db.begin_turn(request.session_id, request.message)
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
                model_messages = []
                try:
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
                        ends_turn=True,
                    )
                    if "recording" in turn_box:
                        turn_box["recording"].link_message(msg_id)
                except Exception:
                    # The caller gets this error in the response itself.
                    db.discard_turn(question_id)
                    raise
                except asyncio.CancelledError:
                    # Shutdown or a dropped client: the reply is lost. Say so now;
                    # if the database has already closed, the next start does.
                    try:
                        db.close_turn(question_id, CANCELLED_TURN_NOTICE)
                    except (RuntimeError, sqlite3.Error) as exc:
                        logger.warning("Could not close the cancelled turn: %s", exc)
                    raise
                # Notify AgentLoop after the non-streaming response completes
                _notify_loop(request.session_id)

                # Fire-and-forget auto-titling — same hook the streaming
                # path uses. GAIA renames its own session after the first
                # answer, and never again; see
                # _maybe_update_session_title for the rules. We pass
                # model_id from the loaded session so the title call uses
                # whichever LLM is actually live in Lemonade.
                from gaia.ui._chat_helpers import _maybe_update_session_title

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


async def _release_after(thread: threading.Thread, session_lock: asyncio.Lock):
    """Free a chat once the agent thread of its stopped turn has exited."""
    # Polled, not joined: a join would hold a worker of the shared executor.
    # No limit: the thread still drives this chat's agent until it exits.
    waited = 0.0
    while thread.is_alive():
        await asyncio.sleep(0.5)
        waited += 0.5
        if waited % 300 == 0:
            logger.warning(
                "A stopped chat's agent thread is still running after %.0f s; "
                "the chat stays busy until it returns",
                waited,
            )
    session_lock.release()


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


class FollowUpRequest(BaseModel):
    session_id: str
    text: str


@router.post("/api/chat/followup")
async def send_followup(request: FollowUpRequest):
    """Hand a message to the session's running turn, picked up at its next step.

    404 means no turn is running, 409 that the running one cannot take it
    (not yet at the model, or relayed to an agent outside this process). Either
    way the message was not delivered, so the caller must queue it.
    """
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="A follow-up needs some text.")
    if not run_manager.is_running(request.session_id):
        raise HTTPException(
            status_code=404,
            detail="No reply is running in this chat; send it as a new message.",
        )
    if not followups.deliver(request.session_id, text):
        raise HTTPException(
            status_code=409,
            detail="The running reply cannot take a message right now; it is "
            "sent as the next message once the reply ends.",
        )
    return {"delivered": True}


@router.get("/api/chat/followup/{session_id}")
async def followup_outcome(session_id: str):
    """What the chat's last finished turn did with the messages added to it.

    ``taken`` were read by the turn and recorded in the chat; ``returned`` were
    not and should go out as their own messages. 409 while a turn still runs,
    since its follow-ups have no outcome yet.
    """
    if run_manager.is_running(session_id):
        raise HTTPException(
            status_code=409,
            detail="A reply is still running in this chat; ask again once it ends.",
        )
    return followups.last_outcome(session_id)


@router.get("/api/chat/context/{session_id}")
async def get_context_usage(session_id: str, db: ChatDatabase = Depends(get_db)):
    """How full the model's context is for this chat's next message, by kind."""
    if not db.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    from .._chat_helpers import cached_session_agent, model_turns

    agent = cached_session_agent(session_id)
    if agent is None:
        # Measuring needs the chat's agent, which lives in memory between turns.
        answered = db.latest_message_id(session_id, role="assistant") is not None
        return {
            "available": False,
            "reason": (
                "Measured again with your next message: GAIA restarted or "
                "switched models since this chat's last answer."
                if answered
                else "Measured once GAIA has answered in this chat."
            ),
        }
    from ..turn_insights import (
        context_categories,
        context_state,
        measure_context,
        tools_by_load,
    )

    def _measure():
        usage = measure_context(
            agent, model_turns(agent, db, session_id), getattr(agent, "model_id", None)
        )
        usage["categories"] = context_categories(usage)
        usage["tool_loading"] = tools_by_load(agent)
        usage["model"] = getattr(agent, "model_id", None)
        usage["context_state"] = context_state(agent)
        return usage

    return {"available": True, **(await asyncio.to_thread(_measure))}


@router.get("/api/chat/performance/{session_id}")
async def get_chat_performance(session_id: str, db: ChatDatabase = Depends(get_db)):
    """Where each turn's time went, the chat's totals, and the model's state now."""
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    from .._chat_helpers import (
        cached_session_agent,
        get_agent_registry,
        resolve_session_model,
    )
    from ..turn_insights import chat_totals, model_now

    agent = cached_session_agent(session_id)
    model = getattr(agent, "model_id", None) if agent is not None else None
    if not model:
        # Before the agent exists, the model the turn will run, not the stored default.
        model = resolve_session_model(
            session,
            session.get("agent_type") or "chat",
            db.get_setting("custom_model"),
            get_agent_registry(),
        )[0]

    def _read():
        turns = db.turn_performance(session_id)
        return {
            # The newest answer; "last turn" is unknown when it has no record.
            "latest_message_id": db.latest_message_id(session_id, role="assistant"),
            "turns": turns,
            "totals": chat_totals(turns),
            "now": model_now(model),
        }

    return await asyncio.to_thread(_read)


class RetryWithRequest(BaseModel):
    session_id: str
    message_id: int
    model: str


@router.post("/api/chat/retry-with")
async def retry_with(
    request: RetryWithRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Answer the latest question again on another local model, to compare.

    The answer is held until ``/api/chat/retry-with/choose`` keeps one.
    """
    from gaia.llm.lemonade_client import cloud_model_provider, resolve_ctx_size

    sid = request.session_id
    if not db.get_session(sid):
        raise HTTPException(status_code=404, detail="Session not found")
    if cloud_model_provider(request.model, None):
        raise HTTPException(
            status_code=400,
            detail="Retry with… compares models on this PC; pick a local model.",
        )
    if db.latest_message_id(sid) != request.message_id:
        raise HTTPException(
            status_code=409,
            detail="Only the latest answer can be retried with another model.",
        )
    record = db.get_message_record(sid, request.message_id)
    if record is None or record["role"] != "assistant":
        raise HTTPException(
            status_code=409, detail="Only an answer from GAIA can be retried."
        )

    session_lock = http_request.app.state.session_locks.setdefault(sid, asyncio.Lock())
    if session_lock.locked() or run_manager.is_running(sid):
        raise HTTPException(
            status_code=409,
            detail="GAIA is still answering in this chat. Retry once it finishes.",
        )
    # Loading the other model unloads the one any running chat is using.
    if turn_queue.locked():
        raise HTTPException(
            status_code=409,
            detail="GAIA is answering in another chat. Retry once it finishes.",
        )
    async with session_lock:
        # Free a moment ago, so this takes the slot at once and holds new turns back.
        await turn_queue.acquire(sid)
        try:
            earlier, question, recorded = answer_retry.split_latest_turn(
                model_context_messages(db, sid), record
            )
            messages = answer_retry.build_retry_messages(
                earlier, question, resolve_ctx_size(request.model)
            )
            result = await asyncio.to_thread(
                answer_retry.answer_with, request.model, messages
            )
        except answer_retry.RetryError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        finally:
            turn_queue.release()
    answer_retry.store_pending(
        sid,
        answer_retry.PendingRetry(
            message_id=request.message_id,
            model=request.model,
            content=result["content"],
            turn=question,
            recorded=recorded,
            stats=result["stats"],
        ),
    )
    return {"message_id": request.message_id, "model": request.model, **result}


class RetryChoiceRequest(BaseModel):
    session_id: str
    message_id: int
    keep: Literal["original", "retry"]


@router.post("/api/chat/retry-with/choose")
async def choose_retry(request: RetryChoiceRequest, db: ChatDatabase = Depends(get_db)):
    """Keep the original or the retried answer; the chat continues from it."""
    sid = request.session_id
    pending = answer_retry.take_pending(sid, request.message_id)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail="That comparison is no longer open (GAIA restarted, or a "
            "newer one replaced it). Run Retry with… again.",
        )
    if request.keep == "original":
        return {"kept": "original"}
    if db.latest_message_id(sid) != request.message_id:
        raise HTTPException(
            status_code=409,
            detail="The chat moved on since this comparison; only the latest "
            "answer can be replaced.",
        )
    model_messages = (
        [*pending.turn, {"role": "assistant", "content": pending.content}]
        if pending.recorded
        else None
    )
    record = db.get_message_record(sid, request.message_id)
    if record is None:
        raise HTTPException(status_code=404, detail="That answer no longer exists.")
    # The turn's steps stay, so keep their count and where they ran.
    stats = {**(record["inference_stats"] or {}), **pending.stats}
    if not db.replace_answer(
        sid, request.message_id, pending.content, model_messages, stats
    ):
        raise HTTPException(status_code=404, detail="That answer no longer exists.")
    return {"kept": "retry", "content": pending.content, "stats": stats}
