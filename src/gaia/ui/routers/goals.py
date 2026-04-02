# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Goals & Tasks REST API for GAIA Agent UI."""

import logging
import threading
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["goals"])

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
            except Exception:
                pass
            finally:
                _store = None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


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
        if v not in ("low", "medium", "high"):
            raise ValueError("priority must be low, medium, or high")
        return v

    @field_validator("mode_required")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("manual", "goal_driven", "autonomous"):
            raise ValueError("mode_required must be manual, goal_driven, or autonomous")
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
        valid = {"queued", "in_progress", "completed", "failed", "blocked", "cancelled"}
        if v not in valid:
            raise ValueError(f"status must be one of {sorted(valid)}")
        return v


class GoalStatusUpdate(BaseModel):
    status: str
    progress_notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        valid = {
            "pending_approval", "queued", "in_progress",
            "completed", "failed", "rejected", "cancelled",
        }
        if v not in valid:
            raise ValueError(f"status must be one of {sorted(valid)}")
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
    goal = _get_store().approve_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return _goal_to_dict(goal)


@router.put("/api/goals/{goal_id}/reject")
def reject_goal(goal_id: str):
    """Reject an agent-inferred goal."""
    goal = _get_store().reject_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return _goal_to_dict(goal)


@router.put("/api/goals/{goal_id}/cancel")
def cancel_goal(goal_id: str):
    """Cancel a queued or in-progress goal."""
    goal = _get_store().cancel_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
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
    goal = _get_store().get_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    _get_store().delete_goal(goal_id)


@router.post("/api/goals/{goal_id}/tasks", status_code=201)
def add_task(goal_id: str, body: TaskCreate):
    """Add a task to an existing goal."""
    goal = _get_store().get_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    task = _get_store().add_task(goal_id, body.description, body.order_index)
    return _task_to_dict(task)


@router.put("/api/goals/{goal_id}/tasks/{task_id}")
def update_task(goal_id: str, task_id: str, body: TaskStatusUpdate):
    """Update a task's status and optionally record the result."""
    task = _get_store().update_task_status(task_id, body.status, result=body.result)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Auto-complete the goal when all tasks are done
    store = _get_store()
    if body.status in ("completed", "cancelled") and store.is_goal_complete(goal_id):
        store.update_goal_status(goal_id, "completed")
    return _task_to_dict(task)
