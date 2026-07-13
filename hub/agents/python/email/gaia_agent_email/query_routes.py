# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``POST /v1/email/query`` — the canonical streaming agent-loop surface (#2016).

This is the v2 keystone: the email sidecar becomes a complete agent product.
A natural-language request goes in; the agent reasons and chains its tools into a
multi-step workflow; the **seven canonical SSE event types** (the frozen #2015
``/query`` wire contract) come out —

    status | token | tool_call | tool_result | needs_confirmation | final | error

— terminated by exactly one ``final`` or ``error``. Every v2 front-door (the Agent
UI relay, the ``gaia email`` CLI, ``gaia api``) later relays to **this one loop**
instead of inventing a private dialect.

How it works
------------
Running the loop with SSE is not net-new instrumentation: it reuses the same seam
the core chat router uses — ``agent.console = SSEOutputHandler()`` then
``process_query`` on a worker thread — and drains the handler's queue through the
reusable :class:`~gaia_agent_email.sse_translation.CanonicalTranslator` (spec §6),
so the client only ever sees the canonical vocabulary.

Distinctions from the stateful ``/v1/email/agent/*`` surface
------------------------------------------------------------
- **Host-minted ``run_id``** (spec §2.3): cancellation keys off it, so a run is
  cancellable from the instant the request is sent, before any event streams back.
- **Context is pushed, never pulled** (spec §2.4): the host owns the transcript and
  passes the relevant slice in the request body; the sidecar stays stateless.
- **Canonical vocabulary**, not the in-process handler's raw events.

Confirmation (epic decision D1, UNSIGNED — stateless stub)
----------------------------------------------------------
Stateful server-side *resume* is intentionally NOT wired here. A step that needs
confirmation (a destructive/external tool such as ``send_now``) emits a
``needs_confirmation`` event (specced shape) and then the run ends with a ``final``
refusal that points the caller at the deterministic fixed-function route (mint a
token via ``POST /v1/email/draft``, then ``POST /v1/email/send``). ``confirm_url``
is omitted (spec §5 / Q4). When D1 is signed off, the resume model can be wired
without changing this event's shape.

Auth rides the existing per-session bearer (#1980): this router is mounted under
the same ``require_caller_token`` gate as the rest of ``/v1/email/*`` — no new
scheme.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gaia.logger import get_logger

logger = get_logger(__name__)

# No prefix: this router is included INTO ``api_routes.router`` (prefix
# ``/v1/email``), so the routes resolve to ``/v1/email/query`` and
# ``/v1/email/query/{run_id}/cancel`` and appear in the exported OpenAPI contract.
router = APIRouter(tags=["email-query"])

# Providers the local-only email agent (AC3: local Lemonade inference) accepts.
_ALLOWED_PROVIDERS = frozenset({"lemonade"})


# ---------------------------------------------------------------------------
# Agent construction seam (swapped by tests)
# ---------------------------------------------------------------------------


def build_query_agent(**config_kwargs: Any):
    """Construct a live ``EmailTriageAgent`` for one ``/query`` run.

    Delegates to the shared ``agent_routes.build_session_agent`` seam (lazy import
    keeps this module — and the OpenAPI export — dependency-light until a run
    actually starts). Tests monkeypatch this attribute to inject a fake agent,
    exercising the surface without Lemonade or Gmail.
    """
    from gaia_agent_email.agent_routes import build_session_agent

    return build_session_agent(**config_kwargs)


# ---------------------------------------------------------------------------
# Per-run_id state (spec §2.3 — cancellable by run_id)
# ---------------------------------------------------------------------------


class _QueryRun:
    """The live state for one in-flight ``/query`` run, keyed by ``run_id``."""

    def __init__(self, run_id: str, agent: Any, handler: Any) -> None:
        self.run_id = run_id
        self.agent = agent
        self.handler = handler
        self.cancel_event = threading.Event()


class _RunRegistry:
    """Process-local map of ``run_id`` → :class:`_QueryRun` for cancellation."""

    def __init__(self) -> None:
        self._runs: Dict[str, _QueryRun] = {}
        self._lock = threading.Lock()

    def add(self, run: _QueryRun) -> None:
        with self._lock:
            if run.run_id in self._runs:
                raise KeyError(run.run_id)
            self._runs[run.run_id] = run

    def get(self, run_id: str) -> Optional[_QueryRun]:
        with self._lock:
            return self._runs.get(run_id)

    def remove(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)


registry = _RunRegistry()


# ---------------------------------------------------------------------------
# Request / response models (canonical /query contract — spec §2)
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryContextItem(_Strict):
    """One prior turn pushed in the request body (spec §2.4)."""

    role: str = Field(
        ...,
        description="Transcript role: 'user', 'assistant', 'system', or 'tool'.",
    )
    content: str = Field(..., description="The message text for this turn.")

    @field_validator("role")
    @classmethod
    def _role_known(cls, v: str) -> str:
        allowed = {"user", "assistant", "system", "tool"}
        if v not in allowed:
            raise ValueError(f"role must be one of {sorted(allowed)}, got {v!r}")
        return v


class QueryRequest(_Strict):
    """``POST /v1/email/query`` request body (frozen #2015 contract, spec §2.2)."""

    query: str = Field(
        ...,
        min_length=1,
        description="The natural-language request driving the agent loop.",
    )
    run_id: str = Field(
        ...,
        description=(
            "Host-minted streaming-run handle (UUIDv4). Cancellation "
            "(POST /v1/email/query/{run_id}/cancel) keys off it, so the run is "
            "cancellable from the instant the request is sent."
        ),
    )
    context: List[QueryContextItem] = Field(
        ...,
        description=(
            "The relevant transcript slice, pushed in the body. May be an empty "
            "array for a fresh conversation, but the field must be present."
        ),
    )
    model: Optional[str] = Field(
        default=None,
        description="Model id override. Omitted → the sidecar's default.",
    )
    provider: Optional[str] = Field(
        default=None,
        description=(
            "LLM provider override. The email agent runs local inference only, so "
            "only 'lemonade' is accepted; any other value is rejected (400)."
        ),
    )
    max_steps: Optional[int] = Field(
        default=None,
        ge=1,
        description="Agent-loop step ceiling. Omitted → the agent's configured default.",
    )

    @field_validator("run_id")
    @classmethod
    def _run_id_is_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except (ValueError, AttributeError, TypeError) as e:
            raise ValueError(f"run_id must be a UUID, got {v!r}") from e
        return v


class QueryCancelResponse(_Strict):
    """Result of ``POST /v1/email/query/{run_id}/cancel``."""

    run_id: str = Field(..., description="The run that was signalled to cancel.")
    cancelled: bool = Field(
        default=True, description="True once the cancel was delivered to the run."
    )
    status: str = Field(default="ok", description="Always 'ok' on success.")


# ---------------------------------------------------------------------------
# SSE framing helpers
# ---------------------------------------------------------------------------


def _sse(event: Dict[str, Any]) -> str:
    """Frame one canonical event as a single SSE ``data:`` line."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _confirmation_refusal(action: str) -> Dict[str, Any]:
    """The terminal ``final`` that ends the stateless-stub confirmation flow (D1)."""
    return {
        "type": "final",
        "answer": (
            f"This step needs your confirmation to run '{action}', which the "
            "/query endpoint does not perform yet (it runs stateless, per epic "
            "decision D1 — no server-side resume). To complete a destructive or "
            "external action, use the fixed-function route: POST /v1/email/draft "
            "to mint a single-use confirmation token, then POST /v1/email/send "
            "(or the matching /archive, /quarantine, /calendar route) with it."
        ),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


# Documented streaming response: the wire is text/event-stream, one canonical
# event per SSE data line (spec §3–§5). Declared here so the OpenAPI contract
# references the SSE stream rather than an application/json body.
_QUERY_SSE_RESPONSES = {
    200: {
        "description": (
            "Server-Sent Events stream (text/event-stream). Each `data:` line is "
            "one canonical event discriminated on `type`, one of: status "
            "{message} | token {delta} | tool_call {tool, args} | tool_result "
            "{tool, render?, data} | needs_confirmation {run_id, action, summary} "
            "| final {answer, usage?} | error {detail, status}. The stream is "
            "terminated by exactly one `final` or one `error`."
        ),
        "content": {
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "example": (
                        'data: {"type": "status", "message": "Processing..."}\n\n'
                        'data: {"type": "tool_call", "tool": "triage_inbox", '
                        '"args": {}}\n\n'
                        'data: {"type": "tool_result", "tool": "triage_inbox", '
                        '"data": {}}\n\n'
                        'data: {"type": "final", "answer": "Triaged 5 emails."}\n\n'
                    ),
                }
            }
        },
    }
}


@router.post("/query", responses=_QUERY_SSE_RESPONSES)
async def query(request: QueryRequest) -> StreamingResponse:
    """Run the email agent loop for one request and stream canonical SSE events.

    Builds an agent, injects the pushed ``context`` as conversation history, runs
    ``process_query(query)`` on a worker thread with an ``SSEOutputHandler``, and
    relays the loop as the seven canonical event types (spec §4). The stream ends
    with exactly one ``final`` or ``error``. A confirmation-requiring step ends the
    stream with a ``needs_confirmation`` followed by a ``final`` refusal (the
    stateless D1 stub — see module docstring).
    """
    # Lazy imports: keep module import (and the OpenAPI export) dependency-light.
    from gaia_agent_email.sse_translation import TERMINAL_TYPES, CanonicalTranslator

    from gaia.ui.sse_handler import SSEOutputHandler

    if request.provider is not None and request.provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider {request.provider!r} is not supported: the email agent "
                "runs local inference only. Omit 'provider' or set it to 'lemonade'."
            ),
        )

    config_kwargs: Dict[str, Any] = {}
    if request.model:
        config_kwargs["model_id"] = request.model

    try:
        agent = await asyncio.to_thread(build_query_agent, **config_kwargs)
    except Exception as exc:  # construction failure → fail loud, before the stream
        raise HTTPException(
            status_code=502,
            detail=f"Failed to start the email agent for this query: {exc}",
        ) from exc

    handler = SSEOutputHandler()
    run = _QueryRun(request.run_id, agent, handler)
    try:
        registry.add(run)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"run_id {request.run_id!r} is already in flight.",
        ) from exc

    # Push the transcript slice as the agent's conversation history (spec §2.4).
    agent.conversation_history = [
        {"role": item.role, "content": item.content} for item in request.context
    ]
    agent.console = handler
    # The base agent loop observes this at each step boundary (agent.py) — the
    # cancel endpoint sets it so tool execution stops between steps.
    agent._cancel_event = run.cancel_event

    max_steps = request.max_steps
    user_query = request.query

    def _run_agent() -> None:
        try:
            if max_steps is not None:
                agent.process_query(user_query, max_steps=max_steps)
            else:
                agent.process_query(user_query)
        except Exception as exc:  # surface loudly as a terminal error event
            logger.exception("email /query run failed for run_id=%s", run.run_id)
            handler._emit({"type": "agent_error", "content": str(exc)})
        finally:
            handler.signal_done()

    thread = threading.Thread(target=_run_agent, daemon=True)
    thread.start()

    async def _stream():
        translator = CanonicalTranslator(request.run_id)
        terminated = False
        try:
            while True:
                try:
                    event = handler.event_queue.get_nowait()
                except Exception:
                    if not thread.is_alive() and handler.event_queue.empty():
                        break
                    await asyncio.sleep(0.03)
                    continue

                if event is None:  # signal_done sentinel → stream close (spec §3)
                    break

                for canonical in translator.translate(event):
                    ctype = canonical.get("type")
                    yield _sse(canonical)
                    if ctype == "needs_confirmation":
                        # Stateless stub (D1): end the run with a final refusal
                        # and stop the loop so it doesn't block on approval.
                        yield _sse(_confirmation_refusal(canonical.get("action", "")))
                        handler.cancelled.set()
                        run.cancel_event.set()
                        terminated = True
                        return
                    if ctype in TERMINAL_TYPES:
                        terminated = True
                        return

            # Queue closed. Flush any buffered tool_call, then guarantee a
            # terminal event (the contract mandates exactly one).
            for canonical in translator.flush():
                yield _sse(canonical)
                if canonical.get("type") in TERMINAL_TYPES:
                    terminated = True
            if not terminated:
                # No final/error was produced — fail loud rather than close silently.
                yield _sse(
                    {
                        "type": "error",
                        "detail": "The agent finished without producing a final answer.",
                        "status": 500,
                    }
                )
        finally:
            # If the client disconnected mid-run, ask the loop to stop.
            handler.cancelled.set()
            run.cancel_event.set()
            registry.remove(run.run_id)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/query/{run_id}/cancel", response_model=QueryCancelResponse)
async def cancel_query(run_id: str) -> QueryCancelResponse:
    """Cancel an in-flight ``/query`` run — stops tool execution between steps.

    Cooperative, not a kill: it sets the run's cancel flag, which the agent loop
    observes at its next step boundary (per-tool timeouts keep each step bounded,
    so that point is always reached in finite time) and the handler observes while
    waiting on any confirmation. 404 if no run with that id is in flight.
    """
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"No in-flight run for run_id {run_id!r}."
        )
    run.handler.cancelled.set()
    run.cancel_event.set()
    return QueryCancelResponse(run_id=run_id, cancelled=True)


__all__ = [
    "router",
    "registry",
    "build_query_agent",
    "QueryRequest",
    "QueryContextItem",
    "QueryCancelResponse",
]
