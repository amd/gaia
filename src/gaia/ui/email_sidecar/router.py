# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Sidecar-backed ``/v1/email/*`` REST router for the Agent UI backend.

This router is the **single** ``/v1/email`` surface: the UI server mounts it
instead of importing the email wheel in-process (#1768 / design decision 4). It
forwards the full schema-2.1 data contract (triage, batch triage, search, inbox
pre-scan, draft/send + confirm, archive/unarchive, quarantine/unquarantine,
calendar view/preview/create/respond, health, version, readiness init +
streamed provisioning) to the out-of-process sidecar, preserving the sidecar's
own status codes and actionable error detail.

Each request lazily starts the sidecar (off the event loop) and forwards to it.

Security: the sidecar's connector OAuth *write* routes are deliberately NOT
proxied — all connector writes stay on the Python backend's single-writer path
(design "Auth & grants"), so a UI request can never drive a cross-process grant
write.
"""

from __future__ import annotations

from typing import Optional

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
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


# -- Triage -----------------------------------------------------------------
@router.post("/triage")
async def triage(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.triage, body)


@router.post("/triage/batch")
async def triage_batch(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.triage_batch, body)


# -- Inbox read (search + pre-scan) -----------------------------------------
@router.post("/search")
async def search(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.search_inbox, body)


@router.post("/prescan")
async def prescan(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.pre_scan_inbox, body)


# -- Reply (draft + send) ----------------------------------------------------
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


# -- Destructive mailbox actions (confirm-gated) + undo ----------------------
@router.post("/confirm")
async def confirm(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.confirm, body)


@router.post("/archive")
async def archive(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.archive, body)


@router.post("/unarchive")
async def unarchive(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.unarchive, body)


@router.post("/quarantine")
async def quarantine(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.quarantine, body)


@router.post("/unquarantine")
async def unquarantine(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.unquarantine, body)


# -- Calendar ----------------------------------------------------------------
@router.get("/calendar/events")
async def calendar_events(
    request: Request,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    provider: Optional[str] = None,
):
    proxy = await _get_proxy(request)
    params = {
        k: v
        for k, v in (
            ("time_min", time_min),
            ("time_max", time_max),
            ("provider", provider),
        )
        if v is not None
    }
    return await _forward(proxy.calendar_events, params or None)


@router.post("/calendar/events/preview")
async def calendar_preview(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.calendar_preview, body)


@router.post("/calendar/events")
async def calendar_create(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.calendar_create, body)


@router.post("/calendar/events/respond")
async def calendar_respond(request: Request):
    proxy = await _get_proxy(request)
    body = await request.json()
    return await _forward(proxy.calendar_respond, body)


# -- Health / version / readiness ---------------------------------------------
@router.get("/health")
async def health(request: Request):
    proxy = await _get_proxy(request)
    return await _forward(proxy.health)


@router.get("/init")
async def init(request: Request):
    # Readiness (#1795): pass the sidecar's 200/503 + InitResponse body through
    # verbatim.
    proxy = await _get_proxy(request)
    status_code, body = await _forward(proxy.init)
    return JSONResponse(status_code=status_code, content=body)


@router.post("/init")
async def init_provision(request: Request):
    # Provisioning (#2054): stream the sidecar's text/plain progress through
    # chunk-by-chunk — a multi-minute model pull must never buffer in memory.
    # StreamingResponse iterates the sync chunk iterator in a threadpool.
    proxy = await _get_proxy(request)
    status_code, media_type, chunks = await _forward(proxy.provision)
    return StreamingResponse(chunks, media_type=media_type, status_code=status_code)


@router.get("/version")
async def version(request: Request):
    proxy = await _get_proxy(request)
    return await _forward(proxy.version)
