# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/followups``: the follow-ups an agent schedules for itself.

Mounted whenever the daemon has a clock. The agent's ``schedule_follow_up``
tools call these with the daemon client token; see :mod:`gaia.daemon.followups`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from gaia.daemon import followups
from gaia.daemon.constants import API_PREFIX


class CreateFollowUp(BaseModel):
    agent_id: str = Field(..., min_length=1)
    task: str
    fire_at: float
    origin: Dict[str, Any] = Field(default_factory=dict)
    model: Optional[str] = None


class MarkSeen(BaseModel):
    agent_id: str = Field(..., min_length=1)
    ids: List[str]


def build_followup_router(require_token, clock) -> APIRouter:
    router = APIRouter(prefix=f"{API_PREFIX}/followups")

    def _db():
        db = clock.open_db()
        followups.init_schema(db)
        return db

    @router.post("")
    def create(body: CreateFollowUp, _: None = Depends(require_token)) -> dict:
        db = _db()
        try:
            return followups.create(
                db,
                agent_id=body.agent_id,
                task=body.task,
                fire_at=body.fire_at,
                origin=body.origin,
                model=body.model,
            )
        except followups.FollowUpError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        finally:
            db.close_db()

    @router.get("")
    def list_all(agent_id: str, _: None = Depends(require_token)) -> dict:
        db = _db()
        try:
            return {
                "pending": followups.pending(db, agent_id=agent_id),
                "results": followups.unseen_results(db, agent_id=agent_id),
            }
        finally:
            db.close_db()

    @router.post("/{job_id}/cancel")
    def cancel(job_id: str, agent_id: str, _: None = Depends(require_token)) -> dict:
        db = _db()
        try:
            if not followups.cancel(db, agent_id=agent_id, job_id=job_id):
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"No waiting follow-up {job_id!r} for {agent_id!r}; it "
                        "may have fired or been cancelled. List them to see."
                    ),
                )
            return {"id": job_id, "status": "cancelled"}
        finally:
            db.close_db()

    @router.post("/seen")
    def seen(body: MarkSeen, _: None = Depends(require_token)) -> dict:
        db = _db()
        try:
            return {
                "seen": followups.mark_seen(
                    db, agent_id=body.agent_id, job_ids=body.ids
                )
            }
        finally:
            db.close_db()

    return router


def mount(app, require_token, clock: Optional[Any]) -> None:
    if clock is not None:
        app.include_router(build_followup_router(require_token, clock))
