# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Stateful agent surface for the Email Triage Agent sidecar (#1666 follow-up).

The triage/draft/send routes in ``api_routes.py`` are **stateless** — each call
runs a throwaway ``EmailTriageService`` with no memory, no conversation, and no
agent loop. That surface can't back the Agent UI's conversational email
experience, which today runs the full ``EmailTriageAgent`` *in-process*
(``gaia.ui._chat_helpers`` → ``email_factory`` → ``process_query``) with SSE
streaming, tool-confirmation, and memory.

This module moves that experience behind the sidecar's HTTP boundary so the UI
can talk to the packaged agent over the network instead of importing it. It
hosts a real, **session-scoped** ``EmailTriageAgent`` and exposes:

    POST   /v1/email/agent/query            — run a turn; SSE stream of the loop
    POST   /v1/email/agent/confirm-tool     — approve/deny a gated tool call
    POST   /v1/email/agent/cancel           — cancel an in-flight run
    POST   /v1/email/agent/session          — create/reset a session
    DELETE /v1/email/agent/session/{id}     — evict a session
    GET    /v1/email/agent/session/{id}/history — conversation history
    POST   /v1/email/agent/memory           — toggle memory (set_memory_enabled)
    GET    /v1/email/agent/memory/{id}       — memory status

Because ``/query`` runs the real agent loop, **every** agent tool (read,
organize, reply, summarize, delete, calendar, preferences, profiling, phishing,
memory) is reachable through natural language — no per-tool REST plumbing.

Design commitments
------------------
- **Reuse, don't reinvent.** Streaming + blocking tool-confirmation reuse
  ``gaia.ui.sse_handler.SSEOutputHandler`` (the same handler the core chat router
  drives); the integration contract — ``agent.console = handler`` then run
  ``process_query`` on a worker thread — mirrors ``_chat_helpers._run_agent``.
- **Fail loudly.** A build/connection failure surfaces as an actionable HTTP
  error, never a silent empty stream.
- **Injectable for tests.** ``build_session_agent`` is a module-level seam tests
  swap for a fake agent, so the surface is exercised without Lemonade or Gmail.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from gaia.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/email/agent", tags=["email-agent"])


# ---------------------------------------------------------------------------
# Agent construction seam (swapped by tests)
# ---------------------------------------------------------------------------


def build_session_agent(**config_kwargs: Any):
    """Construct a live ``EmailTriageAgent`` for a new session.

    Imported lazily so this module (and the OpenAPI export) stays dependency-light
    until an agent session is actually created. Tests monkeypatch this attribute
    to inject a fake agent, exercising the routes without Lemonade or Gmail.
    """
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    return EmailTriageAgent(config=EmailAgentConfig(**config_kwargs))


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------


class _AgentSession:
    """A live agent instance plus its per-session state.

    One turn runs at a time per session: ``run_lock`` rejects overlapping
    ``/query`` calls (mirrors the core chat router's per-session lock) so a
    second turn can't corrupt the cached agent's conversation state.
    """

    def __init__(self, session_id: str, agent: Any) -> None:
        self.session_id = session_id
        self.agent = agent
        self.run_lock = threading.Lock()
        # (user_message, assistant_answer) pairs, oldest first.
        self.history: List[Tuple[str, str]] = []
        # The handler for the CURRENT run — set on /query, read by /confirm-tool
        # and /cancel, cleared when the run ends.
        self.handler: Any = None

    def is_running(self) -> bool:
        return self.run_lock.locked()


class _SessionRegistry:
    """Process-local map of session_id → :class:`_AgentSession`.

    In-process and single-tenant by design (the sidecar hosts one user's agent).
    Agents are built lazily on first use and torn down on eviction.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, _AgentSession] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Optional[_AgentSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create(self, session_id: str, **config_kwargs: Any) -> _AgentSession:
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
        # Build outside the lock — construction is slow (memory init, backends)
        # and must not block other sessions. A racing creator for the SAME id is
        # resolved below by discarding the loser's agent.
        agent = build_session_agent(**config_kwargs)
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                _close_agent(agent)
                return existing
            session = _AgentSession(session_id, agent)
            self._sessions[session_id] = session
            return session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        _close_agent(session.agent)
        return True

    def reset(self, session_id: str, **config_kwargs: Any) -> _AgentSession:
        """Drop any existing session and build a fresh one (clears history+memory session)."""
        self.delete(session_id)
        return self.get_or_create(session_id, **config_kwargs)


def _close_agent(agent: Any) -> None:
    """Best-effort teardown of an agent's DB handles on eviction."""
    close = getattr(agent, "close_db", None)
    if callable(close):
        try:
            close()
        except Exception as exc:  # pragma: no cover - teardown must not raise
            logger.warning("agent session close_db failed: %s", exc)


registry = _SessionRegistry()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentQueryRequest(_Strict):
    """Run one conversational turn against the session's agent."""

    session_id: str = Field(..., description="Opaque session id; created on first use.")
    message: str = Field(..., description="The user's natural-language request.")
    memory_enabled: Optional[bool] = Field(
        default=None,
        description=(
            "Optional per-turn memory override (#1666). None leaves the session's "
            "current setting unchanged; True/False flips it before the turn runs."
        ),
    )


class SessionRequest(_Strict):
    session_id: str = Field(..., description="Session id to create or reset.")
    reset: bool = Field(
        default=False,
        description="When true, tears down any existing session and starts fresh.",
    )


class ToolConfirmRequest(_Strict):
    session_id: str = Field(..., description="Session with a pending confirmation.")
    approved: bool = Field(
        ..., description="True to allow the gated tool, False to deny."
    )


class CancelRequest(_Strict):
    session_id: str = Field(..., description="Session whose in-flight run to cancel.")


class MemoryToggleRequest(_Strict):
    session_id: str = Field(..., description="Session to toggle memory for.")
    enabled: bool = Field(..., description="True to enable memory, False to disable.")


class MemoryStatusResponse(_Strict):
    enabled: bool
    available: bool
    message: str


class SessionResponse(_Strict):
    session_id: str
    created: bool = Field(..., description="True when a new agent was built.")
    memory: MemoryStatusResponse


class HistoryTurn(_Strict):
    user: str
    assistant: str


class HistoryResponse(_Strict):
    session_id: str
    turns: List[HistoryTurn]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _memory_status(agent: Any) -> MemoryStatusResponse:
    """Read the agent's memory status, tolerating agents without the toggle."""
    status_fn = getattr(agent, "memory_status", None)
    if callable(status_fn):
        s = status_fn()
        return MemoryStatusResponse(
            enabled=bool(s.get("enabled")),
            available=bool(s.get("available")),
            message=str(s.get("message", "")),
        )
    # Agent predates the memory toggle — report unavailable rather than guess.
    return MemoryStatusResponse(
        enabled=False,
        available=False,
        message="This agent build does not expose a memory toggle.",
    )


@router.post("/session", response_model=SessionResponse)
async def create_session(request: SessionRequest) -> SessionResponse:
    """Create (or reset) a session and eagerly build its agent.

    Building the agent here (rather than lazily on first /query) lets the caller
    surface a construction failure immediately, and warms memory/backends before
    the first turn.
    """
    try:
        if request.reset:
            session = await asyncio.to_thread(registry.reset, request.session_id)
            created = True
        else:
            existing = registry.get(request.session_id)
            session = await asyncio.to_thread(
                registry.get_or_create, request.session_id
            )
            created = existing is None
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to start email agent session: {exc}",
        ) from exc
    return SessionResponse(
        session_id=session.session_id,
        created=created,
        memory=_memory_status(session.agent),
    )


@router.delete("/session/{session_id}")
async def delete_session(session_id: str) -> Dict[str, Any]:
    """Evict a session and tear down its agent."""
    removed = await asyncio.to_thread(registry.delete, session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No such session.")
    return {"status": "ok", "deleted": True, "session_id": session_id}


@router.get("/session/{session_id}/history", response_model=HistoryResponse)
async def session_history(session_id: str) -> HistoryResponse:
    """Return the session's conversation history (oldest first)."""
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No such session.")
    return HistoryResponse(
        session_id=session_id,
        turns=[HistoryTurn(user=u, assistant=a) for (u, a) in session.history],
    )


@router.post("/memory", response_model=MemoryStatusResponse)
async def toggle_memory(request: MemoryToggleRequest) -> MemoryStatusResponse:
    """Enable/disable the session agent's memory at runtime (#1666).

    Exposes ``EmailTriageAgent.set_memory_enabled`` over HTTP. Enabling memory
    that was never initialized this session (started with GAIA_MEMORY_DISABLED or
    Lemonade unreachable) cannot succeed — that is reported with an actionable
    message and HTTP 409, never silently ignored.
    """
    session = registry.get(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No such session.")
    setter = getattr(session.agent, "set_memory_enabled", None)
    if not callable(setter):
        raise HTTPException(
            status_code=501,
            detail="This agent build does not expose a memory toggle.",
        )
    result = setter(request.enabled)
    status = MemoryStatusResponse(
        enabled=bool(result.get("enabled")),
        available=bool(result.get("available")),
        message=str(result.get("message", "")),
    )
    if not result.get("ok", False):
        # Requested state could not be applied (e.g. enable while unavailable).
        raise HTTPException(status_code=409, detail=status.message)
    return status


@router.get("/memory/{session_id}", response_model=MemoryStatusResponse)
async def memory_status(session_id: str) -> MemoryStatusResponse:
    """Report the session agent's current memory state without changing it."""
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No such session.")
    return _memory_status(session.agent)


@router.post("/confirm-tool")
async def confirm_tool(request: ToolConfirmRequest) -> Dict[str, Any]:
    """Approve or deny a tool call the agent is blocking on.

    The agent thread blocks in ``SSEOutputHandler.confirm_tool_execution()`` after
    emitting a ``permission_request`` event; this releases it. Mirrors the core
    chat router's ``/api/chat/confirm-tool``.
    """
    session = registry.get(request.session_id)
    if session is None or session.handler is None:
        raise HTTPException(
            status_code=404, detail="No active run awaiting confirmation."
        )
    session.handler.resolve_tool_confirmation(request.approved)
    return {"status": "ok", "approved": request.approved}


@router.post("/cancel")
async def cancel_run(request: CancelRequest) -> Dict[str, Any]:
    """Request cancellation of an in-flight run (cooperative, not a kill)."""
    session = registry.get(request.session_id)
    if session is None or session.handler is None:
        raise HTTPException(status_code=404, detail="No active run for this session.")
    session.handler.cancelled.set()
    return {"status": "ok", "cancelled": True}


def _extract_answer(result: Any) -> str:
    """Pull the human-readable answer from a process_query result dict."""
    if isinstance(result, dict):
        return (
            result.get("answer") or result.get("response") or result.get("result") or ""
        )
    return str(result or "")


@router.post("/query")
async def query(request: AgentQueryRequest) -> StreamingResponse:
    """Run one conversational turn and stream the agent loop as SSE.

    Builds the session's agent if needed, optionally applies the per-turn memory
    override, then runs ``process_query`` on a worker thread with an
    ``SSEOutputHandler`` as ``agent.console`` — the same integration the core UI
    uses. Every event the loop emits (thoughts, steps, tool usage,
    permission requests, final answer) is relayed to the client; a terminal
    ``run_complete`` event closes the stream.
    """
    from gaia.ui.sse_handler import SSEOutputHandler

    try:
        session = await asyncio.to_thread(registry.get_or_create, request.session_id)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to start email agent session: {exc}"
        ) from exc

    # One turn at a time per session. Reject (409) rather than queue so the
    # caller controls retry — matches the core chat router.
    if not session.run_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="A turn is already in progress for this session.",
        )

    # Between acquiring run_lock and the worker thread taking ownership of it,
    # ANY failure must release the lock — otherwise the session deadlocks (every
    # future /query returns 409 with no recovery but /session reset). The
    # thread's own finally releases it on the happy path; this guard covers the
    # setup (memory override, handler build, thread start) that runs before it.
    try:
        # Apply the optional per-turn memory override before the run. A failed
        # enable (memory unavailable) is surfaced as a stream event, not a hard
        # error — the turn can still run without memory.
        memory_note: Optional[str] = None
        if request.memory_enabled is not None:
            setter = getattr(session.agent, "set_memory_enabled", None)
            if callable(setter):
                res = setter(request.memory_enabled)
                if not res.get("ok", False):
                    memory_note = str(res.get("message", ""))

        handler = SSEOutputHandler()
        session.handler = handler
        session.agent.console = handler

        def _run_agent() -> None:
            try:
                if memory_note:
                    handler._emit(
                        {"type": "status", "status": "warning", "message": memory_note}
                    )
                result = session.agent.process_query(request.message)
                answer = _extract_answer(result)
                session.history.append((request.message, answer))
                handler._emit({"type": "run_complete", "answer": answer})
            except Exception as exc:  # surface loudly into the stream
                logger.exception(
                    "email agent run failed for session %s", session.session_id
                )
                handler._emit({"type": "error", "message": str(exc)})
                handler._emit({"type": "run_complete", "answer": ""})
            finally:
                session.handler = None
                session.run_lock.release()

        thread = threading.Thread(target=_run_agent, daemon=True)
        thread.start()
    except Exception as exc:
        # Setup failed before the worker thread could take over the lock — release
        # it so the session isn't permanently wedged, then fail loudly.
        session.handler = None
        session.run_lock.release()
        raise HTTPException(
            status_code=500, detail=f"Failed to start agent turn: {exc}"
        ) from exc

    async def _sse():
        try:
            while True:
                # Drain everything currently queued without blocking the loop.
                drained = False
                while True:
                    try:
                        event = handler.event_queue.get_nowait()
                    except Exception:
                        break
                    drained = True
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") == "run_complete":
                        return
                if not thread.is_alive() and not drained:
                    # Thread finished and queue is empty but no terminal event
                    # was seen (defensive) — synthesize one so the client closes.
                    yield f'data: {json.dumps({"type": "run_complete", "answer": ""})}\n\n'
                    return
                await asyncio.sleep(0.05)
        finally:
            # If the client disconnected mid-run, ask the agent to stop; the
            # daemon thread still tears down the lock in its finally block.
            if session.handler is handler:
                handler.cancelled.set()

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = [
    "router",
    "registry",
    "build_session_agent",
    "AgentQueryRequest",
    "SessionRequest",
    "ToolConfirmRequest",
    "CancelRequest",
    "MemoryToggleRequest",
]
