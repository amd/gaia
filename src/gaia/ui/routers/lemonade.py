# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Which Lemonade Server GAIA uses — the Agent UI's "use my Lemonade" switch.

Same choice, same checks as ``gaia lemonade connect`` and ``gaia config set
lemonade_mode``: see :mod:`gaia.llm.lemonade_choice`. Switching tells a running
daemon, which stops GAIA's own server (or starts it again), and drops cached
agents so the next turn talks to the new server.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from gaia.logger import get_logger

from ..security import require_ui_header

logger = get_logger(__name__)

router = APIRouter(tags=["lemonade"])

#: What GAIA does on a server it does not own, shown before the user switches.
WHAT_GAIA_DOES = (
    "GAIA registers its embedding model (user.embeddinggemma-300m-GGUF) on "
    "your server and loads its chat and embedding models there. It does not "
    "change your server's own settings, and it never starts a Lemonade while "
    "set to use yours."
)


def _target() -> Dict[str, Any]:
    from gaia.config import GaiaConfigError
    from gaia.llm import lemonade_choice as choice
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    try:
        info = choice.describe()
    except (choice.LemonadeChoiceError, GaiaConfigError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {
        **info,
        "env_override": choice.env_override_note(),
        "gaia_installed": EmbeddedLemonade().is_installed(),
        # Nobody has chosen yet, and the environment isn't choosing for them.
        "offer": info["saved_mode"] is None and info["source"] != "env",
        "what_gaia_does": WHAT_GAIA_DOES,
    }


@router.get("/api/lemonade/target", dependencies=[Depends(require_ui_header)])
def get_target() -> Dict[str, Any]:
    """The Lemonade GAIA uses now, who chose it, and whether to offer a switch."""
    return _target()


# Probes local ports, so a cross-site GET must not reach it.
@router.get("/api/lemonade/detect", dependencies=[Depends(require_ui_header)])
def detect() -> Dict[str, Any]:
    """A Lemonade the user already runs on this PC's default ports, if any."""
    from gaia.llm.lemonade_choice import detect_running_lemonade

    found = detect_running_lemonade()
    return {"found": found.to_dict() if found else None}


class TargetRequest(BaseModel):
    """``mode`` "gaia" or "external"; the URL and key only for "external"."""

    mode: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None


def _via_tunnel(request: Request) -> bool:
    return any(
        h in request.headers
        for h in ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto")
    )


@router.post("/api/lemonade/target", dependencies=[Depends(require_ui_header)])
def set_target(body: TargetRequest, request: Request) -> Dict[str, Any]:
    """Switch servers. Nothing is saved unless the user's server checks out."""
    from gaia.config import GaiaConfigError
    from gaia.daemon.errors import DaemonError
    from gaia.llm import lemonade_choice as choice

    if _via_tunnel(request):
        raise HTTPException(
            status_code=403,
            detail="Change the model server on the PC running GAIA, not over "
            "mobile access.",
        )
    try:
        if body.mode == choice.MODE_EXTERNAL:
            if not (body.base_url or "").strip():
                raise choice.LemonadeChoiceError(
                    "Enter your Lemonade Server's address, e.g. "
                    "http://localhost:13305."
                )
            choice.use_external(body.base_url, body.api_key)
        else:
            choice.set_mode(body.mode)
    except choice.LemonadeChoiceError as e:
        raise HTTPException(
            status_code=422, detail={"message": str(e), "status": e.status}
        ) from e
    except GaiaConfigError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    _drop_cached_agents()
    applied = None
    try:
        applied = choice.apply_to_daemon()
    except DaemonError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Saved, but the GAIA daemon did not switch: {e}",
        ) from e
    return {**_target(), "daemon": applied}


def _drop_cached_agents() -> None:
    """Cached agents and the model manager point at the old server."""
    from gaia.llm.lemonade_manager import LemonadeManager

    from .._chat_helpers import evict_all_session_agents

    evicted = evict_all_session_agents()
    LemonadeManager.reset()
    logger.info("Lemonade switched; dropped %d cached agent(s)", evicted)
