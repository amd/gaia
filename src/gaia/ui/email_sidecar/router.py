# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Sidecar-backed ``/v1/email/*`` REST router for the Agent UI backend.

When ``GAIA_EMAIL_AGENT_MODE`` is set, the UI server mounts THIS router instead of
importing the email wheel in-process (#1768 / design decision 4: the sidecar is the
single ``/v1/email`` surface). Each request lazily starts the sidecar (off the event
loop) and forwards to it, preserving the sidecar's own status codes and actionable
error detail.

Security: only the triage / draft / send / health / version endpoints are exposed.
The sidecar's connector OAuth *write* routes are deliberately NOT proxied — all
connector writes stay on the Python backend's single-writer path (design "Auth &
grants"), so a UI request can never drive a cross-process grant write.
"""

from __future__ import annotations

import requests
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from gaia.logger import get_logger
from gaia.ui.email_sidecar.errors import SidecarError, SidecarHTTPError

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/email", tags=["email-sidecar"])


async def _get_proxy(request: Request):
    """Lazily start the sidecar (off the event loop) and return a bound proxy."""
    manager = getattr(request.app.state, "email_sidecar_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=500,
            detail="email sidecar manager not configured on app.state",
        )
    try:
        # The RLock inside manager.start() serializes concurrent lazy-start callers and re-checks is_running, so this unlocked pre-check is safe (not a TOCTOU race).
        if not manager.is_running:
            await run_in_threadpool(manager.start)
        return manager.proxy()
    except SidecarError as e:
        # Sidecar could not start (binary missing, dev env missing, health
        # timeout, version mismatch). Surface the actionable message loudly.
        raise HTTPException(status_code=503, detail=str(e)) from e


async def _forward(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except SidecarHTTPError as e:
        # Preserve the sidecar's own status + actionable detail verbatim.
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    except SidecarError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=503, detail=f"email sidecar unreachable: {e}"
        ) from e


@router.post("/triage")
async def triage(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.triage, body)


@router.post("/draft")
async def draft(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.draft, body)


@router.post("/send")
async def send(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.send, body)


@router.get("/health")
async def health(request: Request):
    proxy = await _get_proxy(request)
    return await _forward(proxy.health)


@router.get("/version")
async def version(request: Request):
    proxy = await _get_proxy(request)
    return await _forward(proxy.version)
