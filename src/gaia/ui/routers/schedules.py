# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Schedule management endpoints for GAIA Agent UI.

REST API for creating, managing, and monitoring recurring scheduled tasks.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..scheduler import Scheduler

logger = logging.getLogger(__name__)

router = APIRouter(tags=["schedules"])


# ── Request/Response Models ──────────────────────────────────────────────────


class CreateScheduleRequest(BaseModel):
    """Request to create a new scheduled task."""

    name: str = Field(..., description="Unique name for the scheduled task")
    interval: str = Field(
        ..., description="Interval string, e.g. 'every 6h', 'every 30m', 'daily'"
    )
    prompt: str = Field(..., description="The prompt to execute on each run")


class UpdateScheduleRequest(BaseModel):
    """Request to update a scheduled task."""

    status: Optional[str] = Field(
        None, description="New status: 'paused', 'active', or 'cancelled'"
    )


class ScheduleResponse(BaseModel):
    """A scheduled task."""

    id: str
    name: str
    interval_seconds: int
    prompt: str
    status: str
    created_at: Optional[str] = None
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    last_result: Optional[str] = None
    run_count: int = 0
    error_count: int = 0
    session_id: Optional[str] = None
    schedule_config: Optional[str] = None


class ScheduleListResponse(BaseModel):
    """List of scheduled tasks."""

    schedules: list
    total: int


class ScheduleResultResponse(BaseModel):
    """A single schedule execution result."""

    id: str
    task_id: str
    executed_at: str
    result: Optional[str] = None
    error: Optional[str] = None


class ScheduleResultsResponse(BaseModel):
    """List of schedule execution results."""

    results: list
    total: int


class ParseScheduleRequest(BaseModel):
    """Request to parse a natural language schedule description."""

    input: str = Field(..., description="Natural language schedule description")


class ParseScheduleResponse(BaseModel):
    """Parsed schedule configuration."""

    interval_seconds: int
    time_of_day: Optional[str] = None
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    days_of_week: Optional[List[int]] = None
    description: str
    next_run_at: Optional[str] = None
    valid: bool  # True if the schedule could be parsed


# ── Dependency ───────────────────────────────────────────────────────────────


def get_scheduler(request: Request) -> Scheduler:
    """Return the Scheduler instance stored on ``app.state``."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(
            status_code=503,
            detail="Scheduler not available. The server may still be starting up.",
        )
    return scheduler


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/api/schedules/parse", response_model=ParseScheduleResponse)
async def parse_schedule(request: ParseScheduleRequest):
    """Parse a natural language schedule description into structured config."""
    from datetime import datetime, timezone

    from ..scheduler import ScheduleConfig, compute_next_run, parse_schedule_input

    config = parse_schedule_input(request.input)
    next_run = None
    if config.interval_seconds > 0:
        next_dt = compute_next_run(config)
        next_run = next_dt.isoformat()

    return ParseScheduleResponse(
        interval_seconds=config.interval_seconds,
        time_of_day=config.time_of_day,
        start_hour=config.start_hour,
        end_hour=config.end_hour,
        days_of_week=config.days_of_week,
        description=config.description,
        next_run_at=next_run,
        valid=config.interval_seconds > 0,
    )


@router.post("/api/schedules", response_model=ScheduleResponse)
async def create_schedule(
    request: CreateScheduleRequest,
    scheduler: Scheduler = Depends(get_scheduler),
):
    """Create a new scheduled task."""
    try:
        task = await scheduler.create_task(
            name=request.name,
            interval=request.interval,
            prompt=request.prompt,
        )
        return task
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create schedule: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create schedule")


@router.get("/api/schedules", response_model=ScheduleListResponse)
async def list_schedules(
    scheduler: Scheduler = Depends(get_scheduler),
):
    """List all scheduled tasks."""
    tasks = scheduler.list_tasks()
    return ScheduleListResponse(schedules=tasks, total=len(tasks))


@router.get("/api/schedules/{name}", response_model=ScheduleResponse)
async def get_schedule(
    name: str,
    scheduler: Scheduler = Depends(get_scheduler),
):
    """Get a specific scheduled task."""
    task = scheduler.get_task(name)
    if not task:
        raise HTTPException(status_code=404, detail=f"Schedule '{name}' not found")
    return task


@router.put("/api/schedules/{name}", response_model=ScheduleResponse)
async def update_schedule(
    name: str,
    request: UpdateScheduleRequest,
    scheduler: Scheduler = Depends(get_scheduler),
):
    """Update a scheduled task (pause, resume, cancel)."""
    try:
        if request.status == "paused":
            return await scheduler.pause_task(name)
        elif request.status == "active":
            return await scheduler.resume_task(name)
        elif request.status == "cancelled":
            return await scheduler.cancel_task(name)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: '{request.status}'. Use 'paused', 'active', or 'cancelled'.",
            )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Schedule '{name}' not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/schedules/{name}")
async def delete_schedule(
    name: str,
    scheduler: Scheduler = Depends(get_scheduler),
):
    """Delete a scheduled task."""
    try:
        await scheduler.delete_task(name)
        return {"deleted": True}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Schedule '{name}' not found")


@router.get("/api/schedules/{name}/results", response_model=ScheduleResultsResponse)
async def get_schedule_results(
    name: str,
    limit: int = 20,
    scheduler: Scheduler = Depends(get_scheduler),
):
    """Get past execution results for a scheduled task."""
    task = scheduler.get_task(name)
    if not task:
        raise HTTPException(status_code=404, detail=f"Schedule '{name}' not found")

    limit = max(1, min(limit, 100))
    results = scheduler.get_task_results(name, limit=limit)
    return ScheduleResultsResponse(results=results, total=len(results))
