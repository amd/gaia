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

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from gaia.hub import catalog as catalog_mod
from gaia.hub import installer as installer_mod
from gaia.hub import lifecycle as lifecycle_mod
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


def _shutdown_email_sidecar(agent_id: str) -> None:
    """Stop a running email sidecar before mutating its install directory.

    The email agent's install dir doubles as the sidecar's own binary cache,
    and a warm sidecar holds the executable open — install/uninstall/rollback
    would hit a locked file (Windows) or mutate a live process's dir. Since
    #2142 the daemon owns the sidecar: this asks it to stop (attach-only —
    no daemon running is a genuine no-op). A stop failure (the process
    survived the tree-kill) raises 500 and ABORTS the mutation — proceeding
    would corrupt a live process's dir. Only the router may bridge the two
    layers (``gaia.hub`` never imports ``gaia.ui``).
    """
    if agent_id != "email":
        return
    from gaia.daemon.sidecars.errors import SidecarError
    from gaia.ui.email_sidecar import daemon_client

    logger.info(
        "hub: stopping any daemon-supervised email sidecar before mutating its dir"
    )
    try:
        daemon_client.stop_sidecar("email")
    except SidecarError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class InstallRequest(BaseModel):
    """Body for ``POST /api/agents/install``."""

    id: str
    version: Optional[str] = None
    # Explicit opt-in to install a non-verified native (C++) agent. The UI sets
    # this after the user accepts the "Trust & Install" confirmation.
    trust_native: bool = False


class ConfigRequest(BaseModel):
    """Body for ``POST /api/agents/{id}/config``."""

    config: Dict[str, Any]
    # Replace the whole config instead of merging into the existing one.
    replace: bool = False


class SetupRequest(BaseModel):
    """Body for ``POST /api/agents/setup`` (progressive multi-agent install)."""

    ids: List[str]
    max_parallel: int = installer_mod.DEFAULT_MAX_PARALLEL
    resume: bool = True


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("/api/agents/catalog")
async def get_catalog(
    request: Request, refresh: bool = False, include_deprecated: bool = False
):
    """Unified agent catalog: remote hub merged with the local registry.

    ``offline=true`` in the response means the live hub was unreachable and the
    list came from the on-disk cache. Deprecated, not-yet-installed agents are
    hidden unless ``include_deprecated=true``.
    """
    registry = _registry(request)
    try:
        unified = catalog_mod.build_catalog(
            registry,
            installed_versions=installer_mod.installed_versions(),
            force=refresh,
            include_deprecated=include_deprecated,
        )
    except catalog_mod.CatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return unified.to_dict()


# ---------------------------------------------------------------------------
# Install (async + polling)
# ---------------------------------------------------------------------------


def _run_install(
    agent_id: str,
    version: Optional[str],
    registry,
    *,
    trust_native: bool,
    manifest: Optional[dict],
) -> None:
    """Background install worker. Errors are recorded in install progress."""
    try:
        installer_mod.install(
            agent_id,
            version=version,
            registry=registry,
            trust_native=trust_native,
            manifest=manifest,
        )
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

    # Resolve the manifest up front so native-agent trust is enforced
    # synchronously (a clean 403) instead of failing in the background task.
    try:
        manifest = catalog_mod.fetch_manifest(body.id)
    except catalog_mod.CatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        installer_mod.ensure_native_trust(
            body.id, manifest, trust_native=body.trust_native
        )
    except installer_mod.TrustRequiredError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    _shutdown_email_sidecar(body.id)

    installer_mod.clear_progress(body.id)
    installer_mod._set_progress(  # noqa: SLF001 - seed state for the poller
        body.id, status="queued", phase="queued", percent=0, version=body.version
    )
    background_tasks.add_task(
        _run_install,
        body.id,
        body.version,
        registry,
        trust_native=body.trust_native,
        manifest=manifest,
    )
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
    _shutdown_email_sidecar(agent_id)
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
    _shutdown_email_sidecar(agent_id)
    try:
        restored = installer_mod.rollback(agent_id, registry=registry)
    except installer_mod.InstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": agent_id, "status": "rolled_back", "version": restored.version}


# ---------------------------------------------------------------------------
# Lifecycle: configure / health / status (issue #465)
# ---------------------------------------------------------------------------


@router.get("/api/agents/{agent_id}/config")
async def get_agent_config(agent_id: str):
    """Return the persisted per-agent config (``{}`` if none)."""
    try:
        config = lifecycle_mod.read_config(agent_id)
    except lifecycle_mod.LifecycleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": agent_id, "config": config}


@router.post(
    "/api/agents/{agent_id}/config",
    dependencies=[Depends(_require_localhost), Depends(_require_ui_header)],
)
async def set_agent_config(agent_id: str, body: ConfigRequest):
    """Persist per-agent config (model preference, settings). Merges by default."""
    try:
        merged = lifecycle_mod.configure(agent_id, body.config, merge=not body.replace)
    except lifecycle_mod.LifecycleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": agent_id, "config": merged}


@router.get("/api/agents/{agent_id}/health")
async def agent_health(agent_id: str, request: Request):
    """Health check: does the installed agent load + its entry point resolve?"""
    registry = _registry(request)
    try:
        return lifecycle_mod.health_check(agent_id, registry=registry).to_dict()
    except (installer_mod.InstallError, lifecycle_mod.LifecycleError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/agents/{agent_id}/status")
async def agent_status(agent_id: str, request: Request):
    """Aggregated status: installed version, health, config summary."""
    registry = _registry(request)
    try:
        return lifecycle_mod.status(agent_id, registry=registry).to_dict()
    except (installer_mod.InstallError, lifecycle_mod.LifecycleError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Setup executor: progressive, resumable, parallel multi-agent install (#468)
# ---------------------------------------------------------------------------


def _run_setup(ids: List[str], registry, *, max_parallel: int, resume: bool) -> None:
    """Background setup worker. Per-agent progress is in install-status."""
    try:
        manifests = {aid: catalog_mod.fetch_manifest(aid) for aid in ids}
    except catalog_mod.CatalogError as exc:
        logger.warning("hub: setup could not resolve manifests: %s", exc)
        return
    try:
        installer_mod.run_setup(
            manifests,
            max_parallel=max_parallel,
            resume=resume,
            registry=registry,
        )
    except installer_mod.InstallError as exc:
        logger.warning("hub: setup failed: %s", exc)
    except Exception:  # noqa: BLE001 - record then swallow in the worker
        logger.exception("hub: unexpected error during setup")


@router.post(
    "/api/agents/setup",
    status_code=202,
    dependencies=[Depends(_require_localhost), Depends(_require_ui_header)],
)
async def start_setup(
    request: Request, body: SetupRequest, background_tasks: BackgroundTasks
):
    """Start a progressive multi-agent install; poll setup-status for progress."""
    if not body.ids:
        raise HTTPException(status_code=400, detail="No agent ids provided.")
    registry = _registry(request)
    background_tasks.add_task(
        _run_setup,
        body.ids,
        registry,
        max_parallel=body.max_parallel,
        resume=body.resume,
    )
    return {"ids": body.ids, "status": "queued"}


@router.get("/api/agents/setup-status")
async def setup_status():
    """Poll the resumable setup state (per-step progress for a multi-install)."""
    state = installer_mod.get_setup_status()
    if state is None:
        raise HTTPException(status_code=404, detail="No setup in progress.")
    return state
