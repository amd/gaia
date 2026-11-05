# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Home Assistant setup for the Agent UI's Smart home panel.

A thin wrapper over :mod:`gaia.connectors.home_assistant`, which the flagship
sidecar serves to the TUI as well, so both apps say the same thing.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from gaia.connectors import home_assistant as ha
from gaia.connectors.errors import ConnectorsError

from ..security import require_ui_header as _require_ui_header

router = APIRouter(prefix="/api/home-assistant", tags=["home-assistant"])


class ConnectRequest(BaseModel):
    url: str = Field(..., description="Home Assistant base address, no path")
    token: str = Field(..., description="Long-lived access token")


@router.get("")
async def get_status() -> Dict[str, Any]:
    """Whether Home Assistant is connected. Never contacts Home Assistant."""
    return ha.status()


@router.post("", dependencies=[Depends(_require_ui_header)])
async def connect(body: ConnectRequest) -> Dict[str, Any]:
    """Check the address and token against Home Assistant, then save them."""
    try:
        return await ha.connect(body.url, body.token)
    except ConnectorsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/test", dependencies=[Depends(_require_ui_header)])
async def test() -> Dict[str, Any]:
    """Contact the saved Home Assistant: ``{"ok": bool, "detail": str}``."""
    return await ha.check()


@router.delete("", dependencies=[Depends(_require_ui_header)])
async def disconnect() -> Dict[str, Any]:
    """Forget the saved address and token."""
    try:
        return await ha.disconnect()
    except ConnectorsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
