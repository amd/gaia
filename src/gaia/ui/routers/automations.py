# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Settings → Automations: list, pause, resume and remove what GAIA runs on its own.

Reads and writes the same store as ``gaia automation`` (``gaia.automations.
store``), so the app, the terminal and chat always show the same list. A
change made here is the user's own click, so it asks nothing further.

**Local-PC only**, like Settings → Messaging: a trigger's check is a command
that runs on this PC, and its prompts can carry private detail.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException

from gaia.automations import files, store
from gaia.logger import get_logger

from ..security import require_local_pc, require_ui_header

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/automations",
    tags=["automations"],
    dependencies=[Depends(require_local_pc)],
)

HEARTBEAT_NAME = "heartbeat"


def _row(item: store.Automation) -> Dict[str, Any]:
    """Raw values; the app words and formats them in the user's language."""
    return {
        "id": item.id,
        "name": item.name,
        "kind": item.kind,
        "heartbeat": item.kind == store.KIND_ALERT
        and item.name.strip().lower() == HEARTBEAT_NAME,
        "paused": item.paused,
        "every_seconds": item.every_seconds,
        "next_at": item.next_at,
        "last_run_at": item.last_run_at,
        "runs": item.runs,
        "prompt": item.prompt,
        "say": item.say,
        "check": item.check,
        "quiet": item.quiet,
        "days": item.days,
        "failures": item.failures,
    }


def _delivers() -> Optional[bool]:
    """Whether a paired Discord DM would receive what these send; None when
    the Discord state can't be read, so the app says it couldn't check."""
    from gaia.messaging.discord import state

    try:
        return state.load_config().paired
    except files.StateFileError as e:
        log.warning("Discord state unreadable, so delivery is unknown: %s", e)
        return None


def _load():
    try:
        return store.load()
    except files.StateFileError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("")
def list_automations() -> Dict[str, Any]:
    items = _load()
    return {"automations": [_row(a) for a in items], "delivers": _delivers()}


@router.post(
    "/{ref}/{action}",
    dependencies=[Depends(require_ui_header)],
)
def set_automation(ref: str, action: Literal["pause", "resume"]) -> Dict[str, Any]:
    try:
        item = store.update(ref, paused=action == "pause")
    except store.AutomationError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except files.StateFileError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _row(item)


@router.delete("/{ref}", dependencies=[Depends(require_ui_header)])
def remove_automation(ref: str) -> Dict[str, Any]:
    try:
        item = store.remove(ref)
    except store.AutomationError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except files.StateFileError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _row(item)
