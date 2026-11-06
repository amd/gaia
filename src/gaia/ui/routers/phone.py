# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Settings › Phone for the browser: a pass-through to the daemon's phone routes.

The daemon owns phone, so alerts fire whether or not the Agent UI is open.
Phone settings change who GAIA may call, so they are refused over mobile
access: they can only be changed at the PC.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from gaia.logger import get_logger
from gaia.ui.security import require_local_pc

logger = get_logger(__name__)

router = APIRouter(tags=["phone"])

_TIMEOUT = 30.0

#: (method, browser path, daemon path). Anything not listed isn't forwarded.
_ROUTES = (
    ("GET", "status", "status"),
    ("GET", "settings", "settings"),
    ("PUT", "settings", "settings"),
    ("POST", "keys", "keys"),
    ("DELETE", "keys", "keys"),
    ("POST", "simulated", "simulated"),
    ("GET", "simulated/log", "simulated/log"),
    ("POST", "contacts", "contacts"),
    ("POST", "contacts/confirm", "contacts/confirm"),
    ("POST", "contacts/remove/confirm", "contacts/remove/confirm"),
    ("GET", "alerts", "alerts"),
    ("POST", "test", "test"),
)


def _daemon():
    from gaia.daemon.client import attach

    inst = attach(timeout=1.5, patient=False)
    if inst is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daemon_unavailable",
                "message": "GAIA's background service isn't running, so phone "
                "settings can't be reached. Restart GAIA.",
            },
        )
    return inst


async def _forward(method: str, path: str, request: Request) -> JSONResponse:
    inst = _daemon()
    body: Optional[Any] = None
    if method in ("POST", "PUT"):
        raw = await request.body()
        body = await request.json() if raw else None
    url = f"http://{inst.host}:{inst.port}/daemon/v1/phone/{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(
                method,
                url,
                json=body,
                params=dict(request.query_params),
                headers={"Authorization": f"Bearer {inst.token}"},
            )
    except httpx.HTTPError as e:
        logger.warning("phone: daemon request %s %s failed: %s", method, path, e)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daemon_unavailable",
                "message": "GAIA's background service didn't answer. Restart GAIA.",
            },
        ) from e
    if response.status_code == 404:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daemon_outdated",
                "message": "GAIA's background service is from an older version. "
                "Restart GAIA to update it.",
            },
        )
    return JSONResponse(status_code=response.status_code, content=response.json())


def _make(method: str, browser_path: str, daemon_path: str):
    async def handler(request: Request) -> JSONResponse:
        return await _forward(method, daemon_path, request)

    handler.__name__ = f"phone_{method.lower()}_{browser_path.replace('/', '_')}"
    return handler


for _method, _path, _daemon_path in _ROUTES:
    # Reads are fine over mobile access; every change happens at the PC. The
    # Simulated phone's log holds live codes, so it stays on the PC too.
    local_only = _method != "GET" or _path == "simulated/log"
    deps = [Depends(require_local_pc)] if local_only else []
    router.add_api_route(
        f"/api/phone/{_path}",
        _make(_method, _path, _daemon_path),
        methods=[_method],
        dependencies=deps,
    )


@router.post(
    "/api/phone/contacts/{contact_id}/remove", dependencies=[Depends(require_local_pc)]
)
async def remove_contact(contact_id: int, request: Request) -> JSONResponse:
    return await _forward("POST", f"contacts/{contact_id}/remove", request)


@router.post("/api/phone/alerts/{alert_id}/handled")
async def alert_handled(alert_id: int, request: Request) -> JSONResponse:
    # Allowed from the phone too: opening an alert there should stop the calls.
    return await _forward("POST", f"alerts/{alert_id}/handled", request)
