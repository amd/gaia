# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""List, review and undo the file changes each turn made.

The snapshots live with the agent under ``<gaia_home>/snapshots``, so any
client — the Agent UI or the terminal UI — can offer undo through these
endpoints.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gaia.agents.base import file_snapshots
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.logger import get_logger

from ..database import ChatDatabase
from ..dependencies import get_db

logger = get_logger(__name__)

router = APIRouter(tags=["changes"])


class UndoRequest(BaseModel):
    force: bool = Field(
        False,
        description="Undo even files changed after the turn, losing those changes.",
    )


class UndoSettings(BaseModel):
    retention_days: int = Field(..., ge=1, le=365)
    max_mb: int = Field(..., ge=10, le=100_000)


def _require_session(db: ChatDatabase, session_id: str) -> None:
    if db.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")


def _snapshot_http_error(e: file_snapshots.SnapshotError) -> HTTPException:
    if isinstance(e, file_snapshots.RestoreConflict):
        return HTTPException(
            status_code=409,
            detail={"status": "conflict", "message": str(e), "conflicts": e.conflicts},
        )
    if isinstance(e, file_snapshots.SnapshotNotFound):
        return HTTPException(status_code=404, detail=str(e))
    return HTTPException(status_code=409, detail=str(e))


@router.get("/api/sessions/{session_id}/changes")
async def list_changes(session_id: str, db: ChatDatabase = Depends(get_db)):
    """Every turn in the session that changed files, oldest first."""
    _require_session(db, session_id)
    try:
        turns = await asyncio.to_thread(file_snapshots.list_turns, session_id)
    except file_snapshots.SnapshotError as e:
        raise _snapshot_http_error(e) from e
    return {"turns": turns}


@router.get("/api/sessions/{session_id}/changes/{turn_id}/diff")
async def diff_changes(
    session_id: str, turn_id: str, db: ChatDatabase = Depends(get_db)
):
    """What the turn did to each file, as unified diffs."""
    _require_session(db, session_id)
    try:
        files = await asyncio.to_thread(file_snapshots.diff_turn, session_id, turn_id)
    except file_snapshots.SnapshotError as e:
        raise _snapshot_http_error(e) from e
    return {"turn_id": turn_id, "files": files}


@router.post("/api/sessions/{session_id}/changes/{turn_id}/undo")
async def undo_changes(
    session_id: str,
    turn_id: str,
    body: UndoRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Put every file the turn changed back as it was; all or nothing."""
    _require_session(db, session_id)
    lock = http_request.app.state.session_locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail="GAIA is still working in this chat. Undo once it finishes.",
        )
    async with lock:
        try:
            return await asyncio.to_thread(
                file_snapshots.restore_turn, session_id, turn_id, body.force
            )
        except file_snapshots.SnapshotError as e:
            logger.warning("Undo of turn %s refused: %s", turn_id, e)
            raise _snapshot_http_error(e) from e


def _settings_payload() -> dict:
    config = GaiaConfig.load()
    used = file_snapshots.usage()
    return {
        "retention_days": config.undo_retention_days,
        "max_mb": config.undo_max_mb,
        "used_bytes": used["bytes"],
        "turns": used["turns"],
        "path": used["path"],
    }


@router.get("/api/settings/undo")
async def get_undo_settings():
    """How long and how much undo history is kept, and how much is used."""
    try:
        return await asyncio.to_thread(_settings_payload)
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/api/settings/undo")
async def put_undo_settings(body: UndoSettings):
    """Change the limits and trim history to fit them now."""

    def _apply() -> dict:
        GaiaConfig.save_key("undo_retention_days", body.retention_days)
        GaiaConfig.save_key("undo_max_mb", body.max_mb)
        file_snapshots.prune(body.retention_days, body.max_mb * 1024 * 1024)
        return _settings_payload()

    try:
        return await asyncio.to_thread(_apply)
    except (GaiaConfigError, OSError, file_snapshots.SnapshotError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
