# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Schedule management endpoints for GAIA Agent UI.

REST API for creating, managing, and monitoring recurring scheduled tasks.
"""

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..scheduler import DuplicateScheduleName, Scheduler
from ..security import audit_approval

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
    timezone: Optional[str] = Field(
        None,
        description=(
            "IANA time zone the schedule's times are in, e.g. "
            "'America/Los_Angeles'. Defaults to the server machine's zone."
        ),
    )


class UpdateScheduleRequest(BaseModel):
    """Request to update a scheduled task."""

    status: Optional[str] = Field(
        None, description="New status: 'paused', 'active', or 'cancelled'"
    )


class ScheduleDescription(BaseModel):
    """When a schedule runs, as data the UI words in its own language.

    ``every``: count + unit, optional days. ``window``: that plus start/end
    hour. ``days``: named days, optional time. ``daily``: a time each day.
    """

    kind: Literal["every", "window", "days", "daily"]
    count: Optional[int] = None
    unit: Optional[Literal["second", "minute", "hour", "day", "week"]] = None
    days: Optional[List[int]] = Field(None, description="0=Monday … 6=Sunday")
    time: Optional[str] = Field(None, description='"HH:MM", 24-hour')
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None


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
    description: str
    description_parts: ScheduleDescription
    last_error: Optional[str] = None
    running: bool = False


class ScheduleListResponse(BaseModel):
    """List of scheduled tasks."""

    schedules: list
    total: int


class ScheduleResultsResponse(BaseModel):
    """List of schedule execution results."""

    results: list
    total: int


class ParseScheduleRequest(BaseModel):
    """Request to parse a natural language schedule description."""

    input: str = Field(..., description="Natural language schedule description")
    timezone: Optional[str] = Field(
        None,
        description=(
            "IANA time zone the schedule's times are in, e.g. "
            "'America/Los_Angeles'. Defaults to the server machine's zone."
        ),
    )


class ParseScheduleResponse(BaseModel):
    """Parsed schedule configuration."""

    interval_seconds: int
    time_of_day: Optional[str] = None
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    days_of_week: Optional[List[int]] = None
    description: str
    # None when the input was rejected; description then says what to fix.
    description_parts: Optional[ScheduleDescription] = None
    next_run_at: Optional[str] = None
    timezone: Optional[str] = None
    valid: bool  # True if the schedule could be parsed


# ── Dependency ───────────────────────────────────────────────────────────────


def get_scheduler(request: Request) -> Scheduler:
    """Return the Scheduler instance stored on ``app.state``."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Scheduler not running — the server may still be starting up, "
                "or the lifespan failed to start it; check the server logs."
            ),
        )
    return scheduler


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/api/schedules/parse", response_model=ParseScheduleResponse)
async def parse_schedule(request: ParseScheduleRequest):
    """Parse a natural language schedule description into structured config."""
    from ..scheduler import (
        compute_next_run,
        describe_parts,
        parse_schedule_input,
        resolve_zone,
    )

    config = parse_schedule_input(request.input)
    next_run = None
    parts = None
    if config.interval_seconds > 0:
        parts = describe_parts(config)
        try:
            config.timezone = resolve_zone(request.timezone).key
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        next_dt = compute_next_run(config)
        next_run = next_dt.isoformat()

    return ParseScheduleResponse(
        interval_seconds=config.interval_seconds,
        time_of_day=config.time_of_day,
        start_hour=config.start_hour,
        end_hour=config.end_hour,
        days_of_week=config.days_of_week,
        description=config.description,
        description_parts=parts,
        next_run_at=next_run,
        timezone=config.timezone,
        valid=config.interval_seconds > 0,
    )


@router.post("/api/schedules", response_model=ScheduleResponse)
async def create_schedule(
    request: CreateScheduleRequest,
    http_request: Request,
    scheduler: Scheduler = Depends(get_scheduler),
):
    """Create a new scheduled task."""
    try:
        task = await scheduler.create_task(
            name=request.name,
            interval=request.interval,
            prompt=request.prompt,
            tz=request.timezone,
        )
        audit_approval(http_request, f"schedule {request.name!r} created")
        return task
    except DuplicateScheduleName as e:
        raise HTTPException(status_code=409, detail=str(e))
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


# Before the bare GET: names arrive with "/" decoded, so it would match first.
@router.get(
    "/api/schedules/{name:path}/results", response_model=ScheduleResultsResponse
)
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


@router.get("/api/schedules/{name:path}", response_model=ScheduleResponse)
async def get_schedule(
    name: str,
    scheduler: Scheduler = Depends(get_scheduler),
):
    """Get a specific scheduled task."""
    task = scheduler.get_task(name)
    if not task:
        raise HTTPException(status_code=404, detail=f"Schedule '{name}' not found")
    return task


@router.put("/api/schedules/{name:path}", response_model=ScheduleResponse)
async def update_schedule(
    name: str,
    request: UpdateScheduleRequest,
    http_request: Request,
    scheduler: Scheduler = Depends(get_scheduler),
):
    """Update a scheduled task (pause, resume, cancel)."""
    try:
        if request.status == "paused":
            return await scheduler.pause_task(name)
        elif request.status == "active":
            resumed = await scheduler.resume_task(name)
            audit_approval(http_request, f"schedule {name!r} resumed")
            return resumed
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


@router.post("/api/schedules/{name:path}/run", response_model=ScheduleResponse)
async def run_schedule_now(
    name: str,
    http_request: Request,
    scheduler: Scheduler = Depends(get_scheduler),
):
    """Run a scheduled task once now; its next scheduled time stays as it was."""
    try:
        started = await scheduler.run_task_now(name)
        audit_approval(http_request, f"schedule {name!r} run now")
        return started
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Schedule '{name}' not found")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/api/schedules/{name:path}")
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
