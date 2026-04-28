# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Orchestrator visibility and control endpoints for GAIA Agent UI.

Provides REST API endpoints for monitoring and controlling the
ProjectOrchestrator lifecycle:

Phase 1 - REST Endpoints:
- GET /api/v1/orchestrator/state — OrchestratorState + SupervisorState + project summary
- GET /api/v1/orchestrator/health — Health score composite
- GET /api/v1/orchestrator/objectives — List objectives with optional phase/status filter + pagination
- GET /api/v1/orchestrator/objectives/{objective_id} — Single objective detail with branch mapping
- GET /api/v1/orchestrator/history — Paginated execution history

Phase 2 - SSE Streaming:
- GET /api/v1/orchestrator/stream — SSE stream of all orchestrator events

Phase 3 - Control Endpoints:
- POST /api/v1/orchestrator/run — Start orchestrator in background (returns 202 Accepted)
- POST /api/v1/orchestrator/pause — Pause orchestrator (with reason)
- POST /api/v1/orchestrator/resume — Resume orchestrator
"""

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from gaia.orchestration.engine import ProjectOrchestrator
from gaia.orchestration.models import ObjectiveStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["orchestrator"])


# ── SSE Bridge ──────────────────────────────────────────────────────────────


class OrchestratorSSEBridge:
    """Fan-out SSE events to all connected clients.

    Each connected client gets its own asyncio.Queue (maxsize=1000).
    Events are broadcast to every queue. When a client disconnects,
    its queue is removed from the subscriber set.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        """Initialize the SSE bridge.

        Args:
            maxsize: Maximum events buffered per client before dropping oldest.
        """
        self._subscribers: set[asyncio.Queue] = set()
        self._maxsize = maxsize
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        """Register a new client and return its event queue.

        Returns:
            An asyncio.Queue that will receive all broadcast events.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        async with self._lock:
            self._subscribers.add(q)
        logger.debug("SSE client connected (total: %d)", len(self._subscribers))
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a client from the subscriber set.

        Args:
            q: The queue to remove.
        """
        async with self._lock:
            self._subscribers.discard(q)
        logger.debug("SSE client disconnected (total: %d)", len(self._subscribers))

    async def broadcast(self, event: Dict[str, Any]) -> None:
        """Push an event to all connected clients.

        Events that would overflow a client's queue are silently dropped
        (oldest-first via put_nowait on a full queue is avoided; instead
        we use put_nowait and catch QueueFull).

        Args:
            event: The event dictionary to broadcast.
        """
        async with self._lock:
            dead: set[asyncio.Queue] = set()
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(
                        "SSE client queue full, dropping event: %s",
                        event.get("type", "unknown"),
                    )
                except Exception:
                    dead.add(q)
            self._subscribers -= dead

    @property
    def client_count(self) -> int:
        """Number of currently connected SSE clients."""
        return len(self._subscribers)


# Global SSE bridge instance shared across requests
_sse_bridge = OrchestratorSSEBridge()


def _get_orchestrator(request: Request) -> ProjectOrchestrator:
    """Retrieve the orchestrator from app.state.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The ProjectOrchestrator instance.

    Raises:
        HTTPException: 503 if orchestrator is not initialized.
    """
    orchestrator: Optional[ProjectOrchestrator] = getattr(
        request.app.state, "orchestrator", None
    )
    if orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator not initialized. Configure objectives and restart.",
        )
    return orchestrator


# ── Request / Response Models ───────────────────────────────────────────────


class RunRequest(BaseModel):
    """Request body for starting the orchestrator."""

    template_name: Optional[str] = None


class PauseRequest(BaseModel):
    """Request body for pausing the orchestrator."""

    reason: Optional[str] = None


# ── Phase 1: REST Endpoints ─────────────────────────────────────────────────


@router.get("/api/v1/orchestrator/state")
async def get_orchestrator_state(request: Request):
    """Return the current orchestrator state with supervisor and project summary.

    Combines OrchestratorState, ProjectSupervisor state (if enabled),
    and a high-level project summary (total objectives, completion rate).
    """
    orchestrator = _get_orchestrator(request)
    state = orchestrator.state
    project = orchestrator.project

    # Build project summary
    total = len(project.objectives) if project else 0
    completed = sum(
        1 for o in (project.objectives or [])
        if o.status == ObjectiveStatus.COMPLETED
    )
    failed_count = sum(
        1 for o in (project.objectives or [])
        if o.status == ObjectiveStatus.BLOCKED
    )
    in_progress = sum(
        1 for o in (project.objectives or [])
        if o.status == ObjectiveStatus.IN_PROGRESS
    )

    response: Dict[str, Any] = {
        "orchestrator_state": state.to_dict(),
        "project_summary": {
            "project_id": project.project_id if project else None,
            "total_objectives": total,
            "completed": completed,
            "failed": failed_count,
            "in_progress": in_progress,
            "queued": total - completed - failed_count - in_progress,
            "completion_rate": round(completed / total, 3) if total > 0 else 0.0,
        },
    }

    # Include supervisor state if enabled
    if orchestrator.supervisor is not None:
        response["supervisor_state"] = {
            "aborted_reason": orchestrator.supervisor.state.aborted_reason,
            "paused_reason": orchestrator.supervisor.state.paused_reason,
            "quality_trend": orchestrator.supervisor.state.quality_trend,
        }

    return response


@router.get("/api/v1/orchestrator/health")
async def get_orchestrator_health(request: Request):
    """Return a composite health score for the orchestrator.

    Health is calculated as a composite of:
    - orchestrator_alive: Whether the orchestrator is initialized (0 or 1)
    - error_rate: Inverse of objective failure ratio
    - supervisor_healthy: Whether supervisor (if enabled) has no abort

    Returns a score from 0.0 (critical) to 1.0 (healthy).
    """
    orchestrator = _get_orchestrator(request)
    state = orchestrator.state

    total_processed = state.objectives_processed + state.objectives_failed
    error_rate = (
        state.objectives_failed / total_processed if total_processed > 0 else 0.0
    )

    supervisor_healthy = True
    if orchestrator.supervisor is not None:
        supervisor_healthy = (
            orchestrator.supervisor.state.aborted_reason is None
        )

    # Composite health: weighted average
    health_score = round(
        0.4 * 1.0  # orchestrator alive
        + 0.4 * (1.0 - error_rate)  # inverse error rate
        + 0.2 * (1.0 if supervisor_healthy else 0.0),  # supervisor health
        3,
    )

    if health_score >= 0.8:
        status = "healthy"
    elif health_score >= 0.5:
        status = "degraded"
    else:
        status = "critical"

    return {
        "status": status,
        "health_score": health_score,
        "components": {
            "orchestrator_alive": True,
            "error_rate": round(error_rate, 3),
            "supervisor_healthy": supervisor_healthy,
            "objectives_processed": state.objectives_processed,
            "objectives_failed": state.objectives_failed,
            "paused": state.paused,
        },
    }


@router.get("/api/v1/orchestrator/objectives")
async def list_objectives(
    request: Request,
    phase: Optional[str] = Query(None, description="Filter by phase name"),
    status: Optional[str] = Query(None, description="Filter by objective status"),
    limit: int = Query(50, ge=1, le=200, description="Maximum objectives to return"),
    offset: int = Query(0, ge=0, description="Number of objectives to skip"),
):
    """List objectives with optional phase/status filter and pagination.

    Returns objectives in their defined order with pagination controls.
    """
    orchestrator = _get_orchestrator(request)
    project = orchestrator.project

    if project is None:
        return {"objectives": [], "total": 0, "limit": limit, "offset": offset}

    objectives = project.objectives

    # Apply filters
    if phase is not None:
        objectives = [o for o in objectives if o.phase == phase]
    if status is not None:
        try:
            target_status = ObjectiveStatus(status.lower())
            objectives = [o for o in objectives if o.status == target_status]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status filter: {status}. "
                f"Valid values: {[s.value for s in ObjectiveStatus]}",
            )

    total = len(objectives)
    page = objectives[offset : offset + limit]

    return {
        "objectives": [
            {
                "objective_id": o.objective_id,
                "title": o.title,
                "phase": o.phase,
                "status": o.status.value,
                "priority": o.priority,
            }
            for o in page
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/v1/orchestrator/objectives/{objective_id}")
async def get_objective(request: Request, objective_id: str):
    """Get detailed information for a single objective.

    Includes branch mapping (worktree branch if created), artifacts,
    and dependency information.
    """
    orchestrator = _get_orchestrator(request)
    project = orchestrator.project

    if project is None:
        raise HTTPException(status_code=404, detail="No project loaded")

    objective = project.get_objective(objective_id)
    if objective is None:
        raise HTTPException(status_code=404, detail=f"Objective not found: {objective_id}")

    branch = orchestrator.state.objective_branches.get(objective_id)

    return {
        "objective_id": objective.objective_id,
        "title": objective.title,
        "description": objective.description,
        "phase": objective.phase,
        "status": objective.status.value,
        "priority": objective.priority,
        "dependencies": list(objective.depends_on) if hasattr(objective, "depends_on") and objective.depends_on else [],
        "artifacts": objective.artifacts if hasattr(objective, "artifacts") else [],
        "branch": branch,
        "error_message": getattr(objective, "error_message", None),
    }


@router.get("/api/v1/orchestrator/history")
async def get_execution_history(
    request: Request,
    limit: int = Query(20, ge=1, le=200, description="Maximum history entries"),
    offset: int = Query(0, ge=0, description="Number of entries to skip"),
):
    """Return paginated execution history.

    Returns execution records in reverse chronological order (newest first).
    """
    orchestrator = _get_orchestrator(request)
    history = list(reversed(orchestrator.state.execution_history))
    total = len(history)
    page = history[offset : offset + limit]

    return {
        "history": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── Phase 2: SSE Streaming ─────────────────────────────────────────────────


async def _stream_orchestrator_events() -> AsyncGenerator[str, None]:
    """SSE generator that yields events from the broadcast bridge.

    Handles asyncio.CancelledError gracefully on client disconnect.
    """
    q = await _sse_bridge.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                data = json.dumps(event)
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                # Send keepalive to prevent proxy timeouts
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        logger.debug("SSE client disconnected (cancelled)")
        raise
    finally:
        await _sse_bridge.unsubscribe(q)


@router.get("/api/v1/orchestrator/stream")
async def stream_events(request: Request):
    """Server-Sent Events stream of all orchestrator lifecycle events.

    Event types emitted:
    - orchestrator_start, orchestrator_complete, orchestrator_error
    - objective_start, objective_complete, objective_failed
    - cycle_complete, phase_complete
    - orchestrator_paused, orchestrator_resumed

    Headers prevent proxy buffering and client caching.
    """
    return StreamingResponse(
        _stream_orchestrator_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def register_orchestrator_hooks(orchestrator: ProjectOrchestrator) -> None:
    """Register hook callbacks on the orchestrator's HookRegistry.

    Each orchestrator lifecycle event fires a corresponding SSE broadcast
    so that connected /api/v1/orchestrator/stream clients receive
    real-time updates.

    This function is idempotent -- subsequent calls are no-ops.

    Args:
        orchestrator: The ProjectOrchestrator instance to hook into.
    """
    # Guard against duplicate registration across multiple /run calls
    if getattr(orchestrator, "_sse_hooks_registered", False):
        logger.debug("SSE hooks already registered, skipping")
        return
    orchestrator._sse_hooks_registered = True

    from gaia.hooks.base import BaseHook, HookContext, HookResult
    from gaia.orchestration.engine import (
        OBJECTIVE_COMPLETE,
        OBJECTIVE_FAILED,
        OBJECTIVE_START,
        PHASE_COMPLETE,
        CYCLE_COMPLETE,
        ORCHESTRATOR_START,
        ORCHESTRATOR_COMPLETE,
    )

    def _make_hook_class(event_name: str) -> type:
        """Factory to create a hook class bound to a specific event."""

        class _SSEEventHook(BaseHook):
            """Hook that broadcasts a single orchestrator event to SSE clients."""

            @property
            def name(self) -> str:
                return f"orchestrator_sse_bridge_{event_name.lower()}"

            @property
            def event(self) -> str:
                return event_name

            async def execute(self, context: HookContext) -> HookResult:
                """Broadcast the hook event to all SSE subscribers."""
                event_type = context.event.lower()
                payload: Dict[str, Any] = {
                    "type": event_type,
                    "pipeline_id": context.pipeline_id,
                    "timestamp": time.time(),
                }

                if context.state and "objective_id" in context.state:
                    payload["objective_id"] = context.state["objective_id"]
                    payload["objective_status"] = context.state.get("objective_status")

                if context.phase:
                    payload["phase"] = context.phase

                if context.data:
                    payload["data"] = context.data

                await _sse_bridge.broadcast(payload)
                return HookResult.continue_execution()

        return _SSEEventHook

    for evt in [
        ORCHESTRATOR_START,
        ORCHESTRATOR_COMPLETE,
        OBJECTIVE_START,
        OBJECTIVE_COMPLETE,
        OBJECTIVE_FAILED,
        PHASE_COMPLETE,
        CYCLE_COMPLETE,
    ]:
        hook_class = _make_hook_class(evt)
        orchestrator.hook_registry.register(hook_class())

    logger.info("SSE bridge hooks registered on orchestrator")


# ── Phase 3: Control Endpoints ──────────────────────────────────────────────


@router.post("/api/v1/orchestrator/run", status_code=202)
async def run_orchestrator(request: Request, body: RunRequest = RunRequest()):
    """Start the orchestrator in a background task.

    Returns 202 Accepted if the orchestrator was started.
    Returns 409 Conflict if the orchestrator is already running.
    Returns 503 if the orchestrator is not initialized.
    """
    orchestrator = _get_orchestrator(request)

    # Guard against concurrent run() calls
    if getattr(request.app.state, "_orchestrator_running", False):
        raise HTTPException(
            status_code=409,
            detail="Orchestrator is already running. "
            "Pause it first or wait for completion.",
        )

    # Load objectives if not yet loaded
    if orchestrator.project is None:
        try:
            orchestrator.load_objectives()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to load objectives: {e}",
            )

    # Register SSE hooks on first run
    register_orchestrator_hooks(orchestrator)

    # Mark as running BEFORE create_task to prevent race condition
    # where a second request could pass the guard before the task starts
    request.app.state._orchestrator_running = True

    async def _run_background():
        """Background task that runs the orchestrator loop."""
        try:
            await _sse_bridge.broadcast({
                "type": "orchestrator_start",
                "pipeline_id": f"orchestrator-{orchestrator.project.project_id if orchestrator.project else 'unknown'}",
                "timestamp": time.time(),
            })

            result = await orchestrator.run()

            await _sse_bridge.broadcast({
                "type": "orchestrator_complete",
                "pipeline_id": f"orchestrator-{orchestrator.project.project_id if orchestrator.project else 'unknown'}",
                "timestamp": time.time(),
                "state": result.to_dict(),
            })
        except Exception as e:
            logger.error("Orchestrator background run failed: %s", e, exc_info=True)
            await _sse_bridge.broadcast({
                "type": "orchestrator_error",
                "error": str(e),
                "timestamp": time.time(),
            })
        finally:
            request.app.state._orchestrator_running = False

    # Launch in background
    asyncio.create_task(_run_background())

    return {
        "status": "accepted",
        "message": "Orchestrator started in background",
        "template_name": body.template_name,
    }


@router.post("/api/v1/orchestrator/pause")
async def pause_orchestrator(request: Request, body: PauseRequest = PauseRequest()):
    """Pause the orchestrator.

    Idempotent: returns success even if already paused.
    """
    orchestrator = _get_orchestrator(request)

    reason = body.reason or "User requested pause"
    orchestrator.pause(reason=reason)

    # Broadcast pause event
    await _sse_bridge.broadcast({
        "type": "orchestrator_paused",
        "reason": reason,
        "timestamp": time.time(),
    })

    return {
        "status": "paused",
        "reason": reason,
        "was_already_paused": orchestrator.state.paused,
    }


@router.post("/api/v1/orchestrator/resume")
async def resume_orchestrator(request: Request):
    """Resume the orchestrator.

    Idempotent: returns success even if already running.
    """
    orchestrator = _get_orchestrator(request)

    was_paused = orchestrator.state.paused
    orchestrator.resume()

    # Broadcast resume event
    await _sse_bridge.broadcast({
        "type": "orchestrator_resumed",
        "timestamp": time.time(),
    })

    return {
        "status": "resumed",
        "was_already_running": not was_paused,
    }
