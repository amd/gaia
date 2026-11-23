# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Settings → Messaging: connect, pair and run the Discord bridge.

Every route calls the same functions as ``gaia discord`` (``gaia.messaging.
discord.cli``), so the app and the terminal can never disagree about what
"connected" or "paired" means.

**Every route is local-PC only**, reads included. The status carries the
pairing code, and whoever DMs that code becomes the only person the bot
answers; a phone on mobile access that could read it, or swap in its own bot
token, could make itself the approver of commands on this PC.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gaia.logger import get_logger

from ..security import require_local_pc, require_ui_header

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/messaging",
    tags=["messaging"],
    dependencies=[Depends(require_local_pc)],
)


class DiscordConnectRequest(BaseModel):
    token: str
    model: Optional[str] = None


class DiscordStartRequest(BaseModel):
    model: Optional[str] = None
    take_over: bool = False


def _cli():
    from gaia.messaging.discord import cli

    return cli


def _status() -> Dict[str, Any]:
    return _cli().status(detect=False)


@router.get("/discord")
def discord_status() -> Dict[str, Any]:
    """Connection, pairing and bridge state, plus every link setup opens."""
    return _status()


@router.post("/discord/connect", dependencies=[Depends(require_ui_header)])
def discord_connect(body: DiscordConnectRequest) -> Dict[str, Any]:
    """Check and store a bot token, then start the bridge in pairing mode."""
    from gaia.messaging.discord.transport import DiscordTokenError

    cli = _cli()
    try:
        result = cli.connect(body.token)
    except (ValueError, DiscordTokenError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    cli.start_detached(model=body.model, emit=lambda _line: None)
    return {"connect": result, "status": _status()}


@router.post("/discord/start", dependencies=[Depends(require_ui_header)])
def discord_start(body: DiscordStartRequest) -> Dict[str, Any]:
    """Start the bridge (or take the lease from another PC)."""
    cli = _cli()
    if body.take_over and not cli.stop():
        raise HTTPException(
            status_code=500,
            detail="The Discord bridge did not stop, so it could not restart. "
            + cli.stop_failed_hint(),
        )
    cli.start_detached(model=body.model, take_over=body.take_over, emit=lambda _l: None)
    return _status()


@router.post("/discord/stop", dependencies=[Depends(require_ui_header)])
def discord_stop() -> Dict[str, Any]:
    cli = _cli()
    if not cli.stop(stay_stopped=True):
        raise HTTPException(
            status_code=500,
            detail="The Discord bridge did not stop. " + cli.stop_failed_hint(),
        )
    return _status()


@router.post("/discord/restart", dependencies=[Depends(require_ui_header)])
def discord_restart() -> Dict[str, Any]:
    cli = _cli()
    if not cli.stop():
        raise HTTPException(
            status_code=500,
            detail="The Discord bridge did not stop, so it was not restarted. "
            + cli.stop_failed_hint(),
        )
    cli.start_detached(emit=lambda _line: None)
    return _status()


@router.post("/discord/forget-always", dependencies=[Depends(require_ui_header)])
def discord_forget_always() -> Dict[str, Any]:
    """Forget every "Always allow"; GAIA asks again from the next request."""
    from gaia.messaging.discord import state

    cli = _cli()
    # The running agent holds its own copy of the answers, so it is restarted.
    was_running = state.is_running()
    if was_running and not cli.stop():
        raise HTTPException(
            status_code=500,
            detail="The Discord bridge did not stop, so nothing was forgotten. "
            + cli.stop_failed_hint(),
        )
    state.forget_always()
    if was_running:
        cli.start_detached(emit=lambda _line: None)
    return _status()


@router.post("/discord/pair", dependencies=[Depends(require_ui_header)])
def discord_pair() -> Dict[str, Any]:
    """Forget the paired account and show a new code."""
    cli = _cli()
    if not cli.stop():
        raise HTTPException(
            status_code=500,
            detail="The Discord bridge did not stop, so the pairing was kept. "
            + cli.stop_failed_hint(),
        )
    cli.reset_pairing()
    cli.start_detached(emit=lambda _line: None)
    return _status()


@router.post("/discord/disconnect", dependencies=[Depends(require_ui_header)])
def discord_disconnect() -> Dict[str, Any]:
    """Stop the bridge and forget the token, the pairing and the status."""
    try:
        _cli().disconnect()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _status()


def autostart_discord() -> None:
    """Startup job: bring back a bridge that is set to autostart."""
    result = _cli().ensure_started()
    if result not in (None, 0):
        log.warning("The Discord bridge did not autostart: %s", _status().get("detail"))
