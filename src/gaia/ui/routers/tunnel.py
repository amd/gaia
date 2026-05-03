# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Mobile access tunnel endpoints for GAIA Agent UI.

Supports two modes:
- ``ngrok``: public-internet tunnel for off-LAN access.
- ``local``: LAN-only QR pairing — no external service, mints a token
  and points the QR at the host's LAN IP.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from ..dependencies import get_tunnel
from ..tunnel import TunnelManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tunnel"])


class TunnelStartRequest(BaseModel):
    mode: str = "ngrok"


@router.post("/api/tunnel/start")
async def start_tunnel(
    body: Optional[TunnelStartRequest] = Body(default=None),
    tunnel: TunnelManager = Depends(get_tunnel),
):
    """Start a mobile-access tunnel.

    Body: ``{"mode": "ngrok" | "local"}``. Default is ``ngrok`` for
    backward compat with older frontends that POST without a body.
    """
    mode = (body.mode if body else "ngrok") or "ngrok"
    if mode not in ("ngrok", "local"):
        raise HTTPException(status_code=400, detail=f"Unknown tunnel mode: {mode}")
    try:
        logger.info("Starting mobile access tunnel (mode=%s)...", mode)
        status = await tunnel.start(mode=mode)
        return status
    except Exception as e:
        logger.error("Failed to start tunnel: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to start tunnel. Check server logs for details.",
        )


@router.post("/api/tunnel/stop")
async def stop_tunnel(tunnel: TunnelManager = Depends(get_tunnel)):
    """Stop ngrok tunnel."""
    try:
        logger.info("Stopping mobile access tunnel...")
        await tunnel.stop()
        return {"active": False}
    except Exception as e:
        logger.error("Failed to stop tunnel: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to stop tunnel. Check server logs for details.",
        )


@router.get("/api/tunnel/status")
async def tunnel_status(tunnel: TunnelManager = Depends(get_tunnel)):
    """Get current tunnel status."""
    return tunnel.get_status()
