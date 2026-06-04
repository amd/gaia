# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent Hub endpoints: catalog, install, install-status, uninstall, rollback.

These endpoints drive the Agent UI's discover/install panel. They are the HTTP
surface over :mod:`gaia.hub.catalog` (remote catalog + local merge) and
:mod:`gaia.hub.installer` (download/verify/install lifecycle).

Like the rest of the local-first UI backend, the mutating endpoints are guarded
to localhost + the ``X-Gaia-UI`` header (a lightweight CSRF guard — custom
headers force a CORS preflight that drive-by POSTs cannot satisfy).

NOTE on route ordering: this router MUST be included *before*
``routers/agents.py`` in the app, because that router defines a greedy
``GET /api/agents/{agent_id:path}`` that would otherwise swallow
``/api/agents/catalog`` and ``/api/agents/{id}/install-status``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from gaia.hub import catalog as catalog_mod
from gaia.hub import installer as installer_mod
from gaia.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["hub"])

_LOCALHOST_HOSTS = {"127.0.0.1", "::1", "localhost", ""}


def _registry(request: Request):
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not initialized")
    return registry


def _require_localhost(request: Request) -> None:
    host = (request.client.host if request.client else "") or ""
    if host not in _LOCALHOST_HOSTS:
        raise HTTPException(
            status_code=403, detail="endpoint only available on localhost"
        )


def _require_ui_header(request: Request) -> None:
    if request.headers.get("x-gaia-ui") != "1":
        raise HTTPException(status_code=403, detail="missing X-Gaia-UI header")


class InstallRequest(BaseModel):
    """Body for ``POST /api/agents/install``."""

    id: str
    version: Optional[str] = None


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("/api/agents/catalog")
async def get_catalog(request: Request, refresh: bool = False):
    """Unified agent catalog: remote hub merged with the local registry.

    ``offline=true`` in the response means the live hub was unreachable and the
    list came from the on-disk cache.
    """
    registry = _registry(request)
    try:
        unified = catalog_mod.build_catalog(
            registry,
            installed_versions=installer_mod.installed_versions(),
            force=refresh,
        )
    except catalog_mod.CatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return unified.to_dict()


# ---------------------------------------------------------------------------
# Install (async + polling)
# ---------------------------------------------------------------------------


def _run_install(agent_id: str, version: Optional[str], registry) -> None:
    """Background install worker. Errors are recorded in install progress."""
    try:
        installer_mod.install(agent_id, version=version, registry=registry)
    except installer_mod.InstallError as exc:
        logger.warning("hub: install of %s failed: %s", agent_id, exc)
    except Exception:  # noqa: BLE001 - record then swallow in the worker
        logger.exception("hub: unexpected error installing %s", agent_id)


@router.post(
    "/api/agents/install",
    status_code=202,
    dependencies=[Depends(_require_localhost), Depends(_require_ui_header)],
)
async def install_agent(
    request: Request, body: InstallRequest, background_tasks: BackgroundTasks
):
    """Start an install in the background; poll install-status for progress.

    Returns 202 immediately. A duplicate request while an install for the same
    id is running returns 409.
    """
    registry = _registry(request)
    if installer_mod.is_installing(body.id):
        raise HTTPException(
            status_code=409,
            detail=f"An install for '{body.id}' is already in progress.",
        )
    installer_mod.clear_progress(body.id)
    installer_mod._set_progress(  # noqa: SLF001 - seed state for the poller
        body.id, status="queued", phase="queued", percent=0, version=body.version
    )
    background_tasks.add_task(_run_install, body.id, body.version, registry)
    return {"id": body.id, "status": "queued"}


@router.get("/api/agents/{agent_id}/install-status")
async def install_status(agent_id: str):
    """Poll the progress of an in-flight or completed install."""
    state = installer_mod.get_install_status(agent_id)
    if state is None:
        raise HTTPException(
            status_code=404, detail=f"No install status for '{agent_id}'."
        )
    return state


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


@router.delete(
    "/api/agents/{agent_id}",
    dependencies=[Depends(_require_localhost), Depends(_require_ui_header)],
)
async def uninstall_agent(agent_id: str, request: Request):
    """Uninstall a hub-installed agent. Refuses builtins (400)."""
    registry = _registry(request)
    try:
        installer_mod.uninstall(agent_id, registry=registry)
    except installer_mod.NotInstalledError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except installer_mod.InstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": agent_id, "status": "uninstalled"}


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


@router.post(
    "/api/agents/{agent_id}/rollback",
    dependencies=[Depends(_require_localhost), Depends(_require_ui_header)],
)
async def rollback_agent(agent_id: str, request: Request):
    """Roll an agent back to its pre-update snapshot in ``.backup/``."""
    registry = _registry(request)
    try:
        restored = installer_mod.rollback(agent_id, registry=registry)
    except installer_mod.InstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": agent_id, "status": "rolled_back", "version": restored.version}
