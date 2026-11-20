# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The session panel's Environment (#5276), Outputs (#5277) and Agent info (#440)."""

from __future__ import annotations

import asyncio
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gaia.agents.base import file_snapshots
from gaia.logger import get_logger
from gaia.security import AllowedPathsEnvError

from .. import session_capabilities
from .. import session_environment as env
from .. import session_outputs
from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import WireModel
from ..security import audit_approval

logger = get_logger(__name__)

router = APIRouter(tags=["environment"])


class FolderRequest(BaseModel):
    path: str = Field(..., min_length=1, description="An absolute folder path.")


class InternetRequest(BaseModel):
    on: bool


class ScopeEntry(WireModel):
    """A folder or file the chat may touch, and why."""

    path: str
    name: Optional[str]
    kind: Literal["folder", "file"]
    source: Literal[
        "added",
        "gaia_documents",
        "scratch",
        "document",
        "working_folder",
        "approved",
        "allowed_paths",
    ]
    removable: bool


class SessionEnvironment(WireModel):
    """The folders this chat may touch and whether it may use the internet."""

    session_id: str
    folders: List[ScopeEntry]
    removed: List[str]
    internet: bool
    enforced: bool
    internet_enforced: bool


class FolderCheckResponse(WireModel):
    """Why a chat can't be given the folder, or null when it can."""

    refusal: Optional[str]


class OutputItem(WireModel):
    """Something the chat produced, tied to the answer that made it."""

    id: str
    kind: Literal["file", "export", "image", "audio", "document", "memory", "schedule"]
    name: str
    path: Optional[str]
    detail: Optional[str]
    message_id: Optional[int]
    created_at: Optional[str]
    change: Optional[Literal["edited", "created", "deleted"]]
    state: Literal["present", "undone", "gone"]
    openable: bool
    revealable: bool


class SessionOutputs(WireModel):
    """Everything the chat produced, newest first."""

    session_id: str
    items: List[OutputItem]


class AgentTool(WireModel):
    """One tool the chat's agent can call."""

    name: str
    description: str
    category: str


class ActiveSkill(WireModel):
    """A skill loaded in the chat, and how it came to be."""

    name: str
    how: Literal["model", "auto", "always"]


class AgentCapabilities(WireModel):
    """The chat's agent, its tools, and its loaded skills (null when it runs elsewhere)."""

    session_id: str
    agent_type: str
    tools: List[AgentTool]
    skills: Optional[List[ActiveSkill]]


async def _describe(db: ChatDatabase, session: dict) -> dict:
    """The panel's view of *session*; a bad ``GAIA_ALLOWED_PATHS`` is a 500 naming it.

    An empty list would read as "this chat can open nothing" with no reason.
    """
    try:
        return await asyncio.to_thread(env.describe, db, session)
    except AllowedPathsEnvError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _session(db: ChatDatabase, session_id: str) -> dict:
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get(
    "/api/sessions/{session_id}/environment",
    response_model=SessionEnvironment,
    response_model_exclude_unset=True,
)
async def get_environment(session_id: str, db: ChatDatabase = Depends(get_db)):
    """The folders this chat may touch and whether it may use the internet."""
    session = _session(db, session_id)
    return await _describe(db, session)


@router.post(
    "/api/sessions/{session_id}/environment/folders",
    response_model=SessionEnvironment,
    response_model_exclude_unset=True,
)
async def add_folder(
    session_id: str,
    body: FolderRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Let the chat read and write a folder from its next step."""
    session = _session(db, session_id)
    try:
        await asyncio.to_thread(env.add_folder, db, session_id, body.path)
    except env.EnvironmentChangeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    audit_approval(http_request, f"folder {body.path} given to chat {session_id}")
    return await _describe(db, session)


@router.post(
    "/api/environment/folders/check",
    response_model=FolderCheckResponse,
    response_model_exclude_unset=True,
)
async def check_folder(body: FolderRequest) -> dict:
    """Why a chat can't be given a folder, or None: the add step's own rule."""
    try:
        await asyncio.to_thread(env.check_folder, body.path)
    except env.EnvironmentChangeError as e:
        return {"refusal": str(e)}
    return {"refusal": None}


@router.post(
    "/api/sessions/{session_id}/environment/folders/remove",
    response_model=SessionEnvironment,
    response_model_exclude_unset=True,
)
async def remove_folder(
    session_id: str, body: FolderRequest, db: ChatDatabase = Depends(get_db)
):
    """Take a folder or file away from the chat; refused from its next step."""
    session = _session(db, session_id)
    try:
        await asyncio.to_thread(env.remove_folder, db, session_id, body.path)
    except env.EnvironmentChangeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return await _describe(db, session)


@router.put(
    "/api/sessions/{session_id}/environment/internet",
    response_model=SessionEnvironment,
    response_model_exclude_unset=True,
)
async def set_internet(
    session_id: str, body: InternetRequest, db: ChatDatabase = Depends(get_db)
):
    """Turn web tools on or off for this chat from its next step."""
    session = _session(db, session_id)
    try:
        await asyncio.to_thread(env.set_internet, db, session_id, body.on)
    except env.EnvironmentChangeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except env.RelayedSwitchError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return await _describe(db, session)


@router.get(
    "/api/sessions/{session_id}/outputs",
    response_model=SessionOutputs,
    response_model_exclude_unset=True,
)
async def get_outputs(session_id: str, db: ChatDatabase = Depends(get_db)):
    """Everything the chat produced, newest first, each tied to its turn."""
    _session(db, session_id)
    try:
        return await asyncio.to_thread(session_outputs.list_outputs, db, session_id)
    except file_snapshots.SnapshotError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/api/sessions/{session_id}/capabilities",
    response_model=AgentCapabilities,
    response_model_exclude_unset=True,
)
async def get_capabilities(session_id: str, db: ChatDatabase = Depends(get_db)):
    """The tools this chat's agent has: name, description and category each.

    409 when the agent isn't running yet; never an empty list in its place.
    """
    session = _session(db, session_id)
    try:
        return await asyncio.to_thread(session_capabilities.describe, session)
    except session_capabilities.CapabilitiesError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
