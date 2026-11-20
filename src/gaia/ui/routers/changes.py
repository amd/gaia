# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""List, review and undo the file changes each turn made.

The snapshots live with the agent under ``<gaia_home>/snapshots``, so any
client — the Agent UI or the terminal UI — can offer undo through these
endpoints.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gaia.agents.base import file_snapshots
from gaia.agents.tools.protected_folders import (
    ASKING,
    DENIED,
    macos_access,
    macos_guarded_folder,
)
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.logger import get_logger

from .. import session_git
from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import WireModel

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


class UndoSettingsResponse(WireModel):
    """The undo history limits and how much of it is used."""

    retention_days: int
    max_mb: int
    used_bytes: int
    turns: int
    path: str


FileChange = Literal["edited", "created", "deleted"]


class UntrackedWork(WireModel):
    """Work that may have changed files without GAIA keeping a copy."""

    kind: str
    detail: str
    paths: List[str]
    #: Absent from turns recorded before it was kept.
    may_write: Optional[bool] = None


class TurnFileChange(WireModel):
    path: str
    #: ``pending`` while the turn still runs.
    change: Literal["edited", "created", "deleted", "pending"]
    tool: Optional[str]


class TurnChanges(WireModel):
    """One turn's file changes."""

    turn_id: str
    session_id: str
    message_id: Optional[int]
    started_at: str
    finished_at: Optional[str]
    restored_at: Optional[str]
    files: List[TurnFileChange]
    untracked: List[UntrackedWork]
    incomplete: bool


class TurnChangesList(WireModel):
    """Every turn in the session that changed files, oldest first."""

    turns: List[TurnChanges]


class FileDiff(WireModel):
    path: str
    change: Literal["edited", "created", "deleted", "pending"]
    unified: str
    binary: bool
    too_large: bool
    truncated: bool
    before_size: int
    after_size: int


class TurnDiff(WireModel):
    """What the turn did to each file, as unified diffs."""

    turn_id: str
    files: List[FileDiff]


class UndoResult(WireModel):
    """What undoing the turn put back, removed, and could not account for."""

    turn_id: str
    restored: List[str]
    removed: List[str]
    kept_folders: List[str]
    untracked: List[UntrackedWork]
    incomplete: bool


class UndoAllResult(WireModel):
    """What undoing every turn of the chat put back, removed, and could not account for."""

    turn_ids: List[str]
    restored: List[str]
    removed: List[str]
    kept_folders: List[str]
    untracked: List[UntrackedWork]
    incomplete: bool


class ChatFileChange(WireModel):
    """A file from before the chat's first turn to after its last."""

    path: str
    change: FileChange
    unified: str
    binary: bool
    too_large: bool
    truncated: bool
    additions: Optional[int]
    deletions: Optional[int]
    before_size: int
    after_size: int
    message_ids: List[int]
    changed_since: Optional[str]


class ChangeTotals(WireModel):
    files: int
    additions: int
    deletions: int
    uncounted: int


class SessionChanges(WireModel):
    """Every file the chat changed; ``pending`` while a turn still runs."""

    files: List[ChatFileChange]
    totals: ChangeTotals
    turns: int
    pending: bool
    untracked: List[UntrackedWork]
    incomplete: bool


class GitDraft(WireModel):
    title: str
    body: str


class GitRepo(WireModel):
    """A repository holding the chat's changes, with a drafted message."""

    root: str
    branch: str
    remote: Optional[str]
    default_branch: Optional[str]
    gh: bool
    files: List[str]
    uncommitted: List[str]
    ignored: List[str]
    draft: GitDraft
    pr_blocked: Optional[str]


class SessionGit(WireModel):
    """The chat's repositories; ``reason`` says why git is unavailable."""

    available: bool
    repos: List[GitRepo]
    reason: Optional[str] = None


class GitCommitResult(WireModel):
    root: str
    commit: str
    files: List[str]
    ignored: List[str]


class GitPullRequestResult(WireModel):
    root: str
    url: str


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


async def _list_turns(session_id: str) -> list:
    try:
        return await asyncio.to_thread(file_snapshots.list_turns, session_id)
    except file_snapshots.SnapshotError as e:
        raise _snapshot_http_error(e) from e


def _refuse_if_macos_blocks(turns: list) -> None:
    """Answer now when macOS would hold an undo on its permission dialog.

    The undo runs in this process, which may lack the access the agent that
    made the change was given; its first read would then wait on the dialog
    for as long as nobody answers, holding the chat.
    """
    folders = {
        folder
        for turn in turns
        for entry in turn.get("files", [])
        if (folder := macos_guarded_folder(entry["path"])) is not None
    }
    if not folders:
        return
    access = macos_access(folders)
    home = Path.home().resolve()
    asking = sorted(
        f"~/{f.relative_to(home)}" for f, s in access.items() if s == ASKING
    )
    denied = sorted(
        f"~/{f.relative_to(home)}" for f, s in access.items() if s == DENIED
    )
    if asking:
        raise HTTPException(
            status_code=409,
            detail=(
                f"macOS is asking whether GAIA may use {', '.join(asking)}. Answer "
                "that dialog, then undo again."
            ),
        )
    if denied:
        raise HTTPException(
            status_code=409,
            detail=(
                f"macOS denied GAIA access to {', '.join(denied)}, so the change "
                "can't be undone. Allow it in System Settings > Privacy & Security "
                "> Files and Folders, then undo again."
            ),
        )


@router.get(
    "/api/sessions/{session_id}/changes",
    response_model=TurnChangesList,
    response_model_exclude_unset=True,
)
async def list_changes(session_id: str, db: ChatDatabase = Depends(get_db)):
    """Every turn in the session that changed files, oldest first."""
    _require_session(db, session_id)
    try:
        turns = await asyncio.to_thread(file_snapshots.list_turns, session_id)
    except file_snapshots.SnapshotError as e:
        raise _snapshot_http_error(e) from e
    return {"turns": turns}


@router.get(
    "/api/sessions/{session_id}/changes/{turn_id}/diff",
    response_model=TurnDiff,
    response_model_exclude_unset=True,
)
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


@router.post(
    "/api/sessions/{session_id}/changes/{turn_id}/undo",
    response_model=UndoResult,
    response_model_exclude_unset=True,
)
async def undo_changes(
    session_id: str,
    turn_id: str,
    body: UndoRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Put every file the turn changed back as it was; all or nothing."""
    _require_session(db, session_id)
    turns = await _list_turns(session_id)
    await asyncio.to_thread(
        _refuse_if_macos_blocks, [t for t in turns if t["turn_id"] == turn_id]
    )
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


@router.get(
    "/api/sessions/{session_id}/changes/total",
    response_model=SessionChanges,
    response_model_exclude_unset=True,
)
async def session_changes(session_id: str, db: ChatDatabase = Depends(get_db)):
    """Every file the chat changed, before its first turn against after its last."""
    _require_session(db, session_id)
    try:
        return await asyncio.to_thread(file_snapshots.session_changes, session_id)
    except file_snapshots.SnapshotError as e:
        raise _snapshot_http_error(e) from e


@router.post(
    "/api/sessions/{session_id}/changes/undo",
    response_model=UndoAllResult,
    response_model_exclude_unset=True,
)
async def undo_session_changes(
    session_id: str,
    body: UndoRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Put every file the chat changed back as it was; all or nothing."""
    _require_session(db, session_id)
    live = [t for t in await _list_turns(session_id) if not t.get("restored_at")]
    await asyncio.to_thread(_refuse_if_macos_blocks, live)
    lock = http_request.app.state.session_locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail="GAIA is still working in this chat. Undo once it finishes.",
        )
    async with lock:
        try:
            return await asyncio.to_thread(
                file_snapshots.restore_session, session_id, body.force
            )
        except file_snapshots.SnapshotError as e:
            logger.warning("Undo all in session %s refused: %s", session_id, e)
            raise _snapshot_http_error(e) from e


class CommitRequest(BaseModel):
    root: str
    message: str
    confirmed: bool = Field(
        False, description="True only after the user saw and confirmed the commit."
    )


class PullRequestRequest(BaseModel):
    root: str
    title: str
    body: str = ""
    confirmed: bool = Field(
        False, description="True only after the user saw and confirmed the PR."
    )


@router.get(
    "/api/sessions/{session_id}/changes/git",
    response_model=SessionGit,
    response_model_exclude_unset=True,
)
async def git_state(session_id: str, db: ChatDatabase = Depends(get_db)):
    """The git repositories holding the chat's changes, with drafted messages."""
    _require_session(db, session_id)
    try:
        return await asyncio.to_thread(session_git.describe, db, session_id)
    except (session_git.GitActionError, file_snapshots.SnapshotError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _git_action(http_request: Request, session_id: str, fn, *args):
    lock = http_request.app.state.session_locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail="GAIA is still working in this chat. Try again once it finishes.",
        )
    async with lock:
        try:
            return await asyncio.to_thread(fn, *args)
        except session_git.GitActionError as e:
            logger.warning("Git action in session %s refused: %s", session_id, e)
            raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/api/sessions/{session_id}/changes/git/commit",
    response_model=GitCommitResult,
    response_model_exclude_unset=True,
)
async def git_commit(
    session_id: str,
    body: CommitRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Commit the chat's files in one repository; needs the user's confirmation."""
    _require_session(db, session_id)
    return await _git_action(
        http_request,
        session_id,
        session_git.commit,
        db,
        session_id,
        body.root,
        body.message,
        body.confirmed,
    )


@router.post(
    "/api/sessions/{session_id}/changes/git/pr",
    response_model=GitPullRequestResult,
    response_model_exclude_unset=True,
)
async def git_pull_request(
    session_id: str,
    body: PullRequestRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """Push the branch and open a pull request; needs the user's confirmation."""
    _require_session(db, session_id)
    return await _git_action(
        http_request,
        session_id,
        session_git.create_pr,
        db,
        session_id,
        body.root,
        body.title,
        body.body,
        body.confirmed,
    )


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


@router.get(
    "/api/settings/undo",
    response_model=UndoSettingsResponse,
    response_model_exclude_unset=True,
)
async def get_undo_settings():
    """How long and how much undo history is kept, and how much is used."""
    try:
        return await asyncio.to_thread(_settings_payload)
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put(
    "/api/settings/undo",
    response_model=UndoSettingsResponse,
    response_model_exclude_unset=True,
)
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
