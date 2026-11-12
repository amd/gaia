# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Goals & Tasks REST API for GAIA Agent UI."""

import logging
import sqlite3
import threading
import time
from typing import Callable, Optional, get_args

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, field_validator

from gaia.agents.base.goal_store import (
    AgentMode,
    GoalStatus,
    GoalTransitionError,
    Priority,
    TaskStatus,
    is_damaged_store_error,
)

logger = logging.getLogger(__name__)


def _store_failure(exc: sqlite3.Error) -> HTTPException:
    """Why the goal list could not be read or saved, and what the user can do."""
    if is_damaged_store_error(exc):
        detail = (
            f"GAIA's goal list is damaged ({exc}), so goals can't be shown or "
            "saved. Start a new goal list on this page; the damaged file is kept."
        )
    else:
        detail = (
            f"GAIA couldn't open its goal list: {exc}. Close other GAIA windows "
            "using it, then try again."
        )
    return HTTPException(status_code=503, detail=detail)


class _GoalStoreRoute(APIRoute):
    """Answers a goal-store failure with what went wrong, not a bare 500."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                return await handler(request)
            except sqlite3.Error as exc:
                logger.error("Goal store request %s failed: %s", request.url.path, exc)
                raise _store_failure(exc) from exc

        return guarded


router = APIRouter(tags=["goals"], route_class=_GoalStoreRoute)

# ---------------------------------------------------------------------------
# GoalStore singleton  (same double-checked locking pattern as memory.py)
# ---------------------------------------------------------------------------

_store = None
_store_lock = threading.Lock()


def _get_store():
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from gaia.agents.base.goal_store import GoalStore

                _store = GoalStore()
    return _store


def close_store() -> None:
    """Close the singleton GoalStore — called from server lifespan shutdown."""
    global _store
    with _store_lock:
        if _store is not None:
            try:
                _store.close()
            except sqlite3.Error as exc:
                logger.debug("Closing the goal store failed: %s", exc)
            finally:
                _store = None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


_VALID_PRIORITIES = set(get_args(Priority))
_VALID_MODES = set(get_args(AgentMode))
_VALID_TASK_STATUSES = set(get_args(TaskStatus))
_VALID_GOAL_STATUSES = set(get_args(GoalStatus))


class GoalCreate(BaseModel):
    title: str
    description: str
    priority: str = "medium"
    mode_required: str = "goal_driven"

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be empty")
        return v.strip()

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        if v not in _VALID_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(_VALID_PRIORITIES)}")
        return v

    @field_validator("mode_required")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in _VALID_MODES:
            raise ValueError(f"mode_required must be one of {sorted(_VALID_MODES)}")
        return v


class TaskCreate(BaseModel):
    description: str
    order_index: int = 0

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("description must not be empty")
        return v.strip()


class TaskStatusUpdate(BaseModel):
    status: str
    result: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in _VALID_TASK_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_TASK_STATUSES)}")
        return v


class GoalStatusUpdate(BaseModel):
    status: str
    progress_notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in _VALID_GOAL_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_GOAL_STATUSES)}")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _goal_to_dict(goal) -> dict:
    return {
        "id": goal.id,
        "title": goal.title,
        "description": goal.description,
        "status": goal.status,
        "source": goal.source,
        "mode_required": goal.mode_required,
        "approved_for_auto": goal.approved_for_auto,
        "priority": goal.priority,
        "progress_notes": goal.progress_notes,
        "created_at": goal.created_at,
        "updated_at": goal.updated_at,
        "tasks": [_task_to_dict(t) for t in goal.tasks],
    }


def _task_to_dict(task) -> dict:
    return {
        "id": task.id,
        "goal_id": task.goal_id,
        "description": task.description,
        "status": task.status,
        "order_index": task.order_index,
        "result": task.result,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/goals/stats")
def get_goal_stats():
    """Aggregate counts by status for dashboard header cards."""
    return _get_store().get_stats()


@router.get("/api/goals/health")
def get_goal_store_health():
    """Why the background loop could not read the goal store, or null."""
    from gaia.ui.agent_loop import agent_loop

    return {
        "error": agent_loop.goal_store_error,
        "damaged": agent_loop.goal_store_damaged,
    }


@router.post("/api/goals/store/reset")
def start_new_goal_list():
    """Move a damaged goals.db aside and start an empty goal list in its place."""
    from gaia.config import gaia_home
    from gaia.ui.agent_loop import agent_loop

    path = gaia_home() / "goals.db"
    probe = sqlite3.connect(str(path))
    try:
        probe.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.Error as exc:
        if not is_damaged_store_error(exc):
            raise
    else:
        raise HTTPException(
            status_code=409,
            detail="GAIA's goal list opens fine, so there is nothing to replace.",
        )
    finally:
        probe.close()

    close_store()
    kept = path.with_name(f"goals.db.damaged-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        path.rename(kept)
        for suffix in ("-wal", "-shm"):
            side = path.with_name(path.name + suffix)
            if side.exists():
                side.rename(kept.with_name(kept.name + suffix))
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"GAIA couldn't move the damaged goal list aside ({exc}). Close "
                "other GAIA windows, then press Start a new goal list again."
            ),
        ) from exc
    _get_store()
    agent_loop.goal_store_error = None
    agent_loop.goal_store_damaged = False
    logger.warning("Started a new goal list; the damaged one is kept at %s", kept)
    return {"kept": kept.name}


@router.get("/api/goals/pending-approval")
def get_pending_approval():
    """Goals waiting for user approval (agent-inferred, not yet reviewed).

    Registered BEFORE /api/goals/{goal_id} so the literal path wins.
    """
    goals = _get_store().get_pending_approval()
    return {"goals": [_goal_to_dict(g) for g in goals], "total": len(goals)}


@router.get("/api/goals")
def list_goals(
    status: Optional[str] = None,
    source: Optional[str] = None,
    approved_only: bool = False,
):
    """List goals with optional filters."""
    goals = _get_store().list_goals(
        status=status,
        source=source,
        approved_only=approved_only,
    )
    return {"goals": [_goal_to_dict(g) for g in goals], "total": len(goals)}


@router.post("/api/goals", status_code=201)
def create_goal(body: GoalCreate):
    """Create a user goal (starts as queued, auto-approved)."""
    goal = _get_store().create_goal(
        title=body.title,
        description=body.description,
        source="user",
        mode_required=body.mode_required,
        priority=body.priority,
    )
    return _goal_to_dict(goal)


@router.get("/api/goals/{goal_id}")
def get_goal(goal_id: str):
    """Get a single goal with its tasks."""
    goal = _get_store().get_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return _goal_to_dict(goal)


@router.put("/api/goals/{goal_id}/approve")
def approve_goal(goal_id: str):
    """Approve an agent-inferred goal for autonomous execution."""
    try:
        goal = _get_store().approve_goal(goal_id)
    except GoalTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return _goal_to_dict(goal)


@router.put("/api/goals/{goal_id}/reject")
def reject_goal(goal_id: str):
    """Reject an agent-inferred goal."""
    try:
        goal = _get_store().reject_goal(goal_id)
    except GoalTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return _goal_to_dict(goal)


@router.put("/api/goals/{goal_id}/cancel")
def cancel_goal(goal_id: str):
    """Cancel a queued or in-progress goal."""
    try:
        goal = _get_store().cancel_goal(goal_id)
    except GoalTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    # Persist first, then stop an in-flight background tick if this goal owns it.
    from gaia.ui.agent_loop import agent_loop

    agent_loop.cancel_goal(goal_id)
    return _goal_to_dict(goal)


@router.put("/api/goals/{goal_id}/status")
def update_goal_status(goal_id: str, body: GoalStatusUpdate):
    """Update goal status and optionally append progress notes."""
    goal = _get_store().update_goal_status(
        goal_id, body.status, progress_notes=body.progress_notes
    )
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return _goal_to_dict(goal)


@router.delete("/api/goals/{goal_id}", status_code=204)
def delete_goal(goal_id: str):
    """Hard-delete a goal and all its tasks."""
    if not _get_store().delete_goal(goal_id):
        raise HTTPException(status_code=404, detail="Goal not found")
    return Response(status_code=204)


@router.post("/api/goals/{goal_id}/tasks", status_code=201)
def add_task(goal_id: str, body: TaskCreate):
    """Add a task to an existing goal."""
    store = _get_store()
    if store.get_goal(goal_id) is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    task = store.add_task(goal_id, body.description, body.order_index)
    return _task_to_dict(task)


@router.put("/api/goals/{goal_id}/tasks/{task_id}")
def update_task(goal_id: str, task_id: str, body: TaskStatusUpdate):
    """Update a task's status and optionally record the result."""
    store = _get_store()
    task = store.update_task_status(task_id, body.status, result=body.result)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if body.status in ("completed", "cancelled") and store.is_goal_complete(goal_id):
        store.update_goal_status(goal_id, "completed")
    return _task_to_dict(task)
