# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The session panel's Environment (#5276) and Outputs (#5277) endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from gaia.agents.base import file_snapshots
from gaia.logger import get_logger

from .. import session_environment as env
from .. import session_outputs
from ..database import ChatDatabase
from ..dependencies import get_db

logger = get_logger(__name__)

router = APIRouter(tags=["environment"])


class FolderRequest(BaseModel):
    path: str = Field(..., min_length=1, description="An absolute folder path.")


class InternetRequest(BaseModel):
    on: bool


def _session(db: ChatDatabase, session_id: str) -> dict:
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/api/sessions/{session_id}/environment")
async def get_environment(session_id: str, db: ChatDatabase = Depends(get_db)):
    """The folders this chat may touch and whether it may use the internet."""
    session = _session(db, session_id)
    return await asyncio.to_thread(env.describe, db, session)


@router.post("/api/sessions/{session_id}/environment/folders")
async def add_folder(
    session_id: str, body: FolderRequest, db: ChatDatabase = Depends(get_db)
):
    """Let the chat read and write a folder from its next step."""
    session = _session(db, session_id)
    try:
        await asyncio.to_thread(env.add_folder, db, session_id, body.path)
    except env.EnvironmentChangeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return await asyncio.to_thread(env.describe, db, session)


@router.put("/api/sessions/{session_id}/environment/working-folder")
async def set_working_folder(
    session_id: str, body: FolderRequest, db: ChatDatabase = Depends(get_db)
):
    """Pick the folder a new chat works in, before its first message."""
    session = _session(db, session_id)
    try:
        await asyncio.to_thread(env.set_working_folder, db, session_id, body.path)
    except env.EnvironmentChangeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return await asyncio.to_thread(env.describe, db, session)


@router.post("/api/sessions/{session_id}/environment/folders/remove")
async def remove_folder(
    session_id: str, body: FolderRequest, db: ChatDatabase = Depends(get_db)
):
    """Take a folder or file away from the chat; refused from its next step."""
    session = _session(db, session_id)
    try:
        await asyncio.to_thread(env.remove_folder, db, session_id, body.path)
    except env.EnvironmentChangeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return await asyncio.to_thread(env.describe, db, session)


@router.put("/api/sessions/{session_id}/environment/internet")
async def set_internet(
    session_id: str, body: InternetRequest, db: ChatDatabase = Depends(get_db)
):
    """Turn web tools on or off for this chat from its next step."""
    session = _session(db, session_id)
    try:
        await asyncio.to_thread(env.set_internet, db, session_id, body.on)
    except env.EnvironmentChangeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return await asyncio.to_thread(env.describe, db, session)


@router.get("/api/sessions/{session_id}/outputs")
async def get_outputs(session_id: str, db: ChatDatabase = Depends(get_db)):
    """Everything the chat produced, newest first, each tied to its turn."""
    _session(db, session_id)
    try:
        return await asyncio.to_thread(session_outputs.list_outputs, db, session_id)
    except file_snapshots.SnapshotError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
