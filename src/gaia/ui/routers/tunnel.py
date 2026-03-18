# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Mobile access tunnel endpoints for GAIA Agent UI.

Manages ngrok tunnels for remote/mobile access to the local server.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_tunnel
from ..tunnel import TunnelManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tunnel"])


@router.post("/api/tunnel/start")
async def start_tunnel(tunnel: TunnelManager = Depends(get_tunnel)):
    """Start ngrok tunnel for mobile access."""
    try:
        logger.info("Starting mobile access tunnel...")
        status = await tunnel.start()
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
