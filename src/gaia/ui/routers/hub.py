# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent Hub endpoints: catalog, install, install-status, uninstall.

The Electron shell's ``gaia://`` deep-link install drives these. They are the
HTTP surface over :mod:`gaia.hub.catalog` (remote catalog + local merge) and
:mod:`gaia.hub.installer` (download/verify/install lifecycle).

Like the rest of the local-first UI backend, the mutating endpoints are guarded
to localhost + the ``X-Gaia-UI`` header (a lightweight CSRF guard — custom
headers force a CORS preflight that drive-by POSTs cannot satisfy).
"""

from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from gaia.hub import catalog as catalog_mod
from gaia.hub import installer as installer_mod
from gaia.logger import get_logger
from gaia.utils.file_lock import FileLockError

from ..security import require_ui_header as _require_ui_header

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


def _shutdown_email_sidecar(agent_id: str) -> None:
    """Stop a running email sidecar before mutating its install directory.

    The email agent's install dir doubles as the sidecar's own binary cache,
    and a warm sidecar holds the executable open — install/uninstall would
    hit a locked file (Windows) or mutate a live process's dir. Since #2142
    the daemon owns the sidecar: this asks it to stop (attach-only —
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
    # Explicit opt-in to install a non-verified agent (any language). The UI sets
    # this after the user accepts the "Trust & Install" confirmation. The wire
    # field name is kept as ``trust_native`` for client compatibility.
    trust_native: bool = False


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
    trusted: bool,
    manifest: Optional[dict],
    slot: installer_mod.InstallSlot,
) -> None:
    """Background install worker; owns *slot* and releases it however it ends.

    Errors are recorded in install progress.
    """
    try:
        installer_mod.install(
            agent_id,
            version=version,
            registry=registry,
            trusted=trusted,
            manifest=manifest,
            slot=slot,
        )
    except installer_mod.InstallError as exc:
        logger.warning("hub: install of %s failed: %s", agent_id, exc)
        _record_failure(agent_id, version, exc)
    except Exception as exc:  # noqa: BLE001 - record then swallow in the worker
        logger.exception("hub: unexpected error installing %s", agent_id)
        _record_failure(agent_id, version, exc)
    finally:
        slot.release()


def _claim_slot(agent_id: str) -> installer_mod.InstallSlot:
    """Claim *agent_id*'s install slot, or raise the HTTP error that says why not."""
    try:
        return installer_mod.claim_install_slot(agent_id)
    except installer_mod.InstallInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"An install or uninstall of '{agent_id}' is already in "
            "progress (here or in another GAIA process). Wait for it to finish, "
            "then retry.",
        ) from exc
    except installer_mod.InstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileLockError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _record_failure(agent_id: str, version: Optional[str], exc: Exception) -> None:
    """Give the poller a terminal state when install() failed before recording one."""
    state = installer_mod.get_install_status(agent_id)
    if state is None or state.get("status") not in ("failed", "completed"):
        installer_mod._set_progress(  # noqa: SLF001 - terminal state for the poller
            agent_id,
            status="failed",
            phase="failed",
            percent=0,
            version=version,
            error=str(exc),
        )


@router.post(
    "/api/agents/install",
    status_code=202,
    dependencies=[Depends(_require_localhost), Depends(_require_ui_header)],
)
def install_agent(request: Request, body: InstallRequest):
    """Start an install in the background; poll install-status for progress.

    Returns 202 immediately. A duplicate request while an install for the same
    id is running returns 409. A plain ``def`` so the slot wait, the manifest
    fetch and the sidecar stop run in a worker thread, not on the event loop.
    """
    registry = _registry(request)
    # Claimed before the email sidecar is stopped, so a second install can't
    # take the slot between that stop and this install's start.
    slot = _claim_slot(body.id)
    handed_off = False
    try:
        manifest = _prepare_install(body)
        worker = threading.Thread(
            target=_run_install,
            args=(body.id, body.version, registry),
            kwargs={"trusted": body.trust_native, "manifest": manifest, "slot": slot},
            name=f"hub-install-{body.id}",
        )
        # A thread, not BackgroundTasks: Starlette skips those when sending
        # the response fails or is cancelled, which would strand the slot.
        try:
            worker.start()
        except RuntimeError as exc:
            _record_failure(body.id, body.version, exc)
            raise HTTPException(
                status_code=500,
                detail=f"Could not start the install of '{body.id}': {exc}",
            ) from exc
        handed_off = True
    finally:
        if not handed_off:
            slot.release()
    return {"id": body.id, "status": "queued"}


def _prepare_install(body: InstallRequest) -> dict:
    """Run every check that may refuse the install, then stop the email sidecar.

    Returns the agent's manifest.
    """
    # Resolve the manifest up front so native-agent trust is enforced
    # synchronously (a clean 403) instead of failing in the background task.
    try:
        manifest = catalog_mod.fetch_manifest(body.id)
    except catalog_mod.CatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        installer_mod.ensure_trust_ack(body.id, manifest, trusted=body.trust_native)
    except installer_mod.TrustRequiredError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    # Before the sidecar shutdown: an update this core cannot run must not
    # take the running agent down.
    try:
        installer_mod.ensure_core_can_install(body.id, manifest, body.version)
    except installer_mod.CoreTooOldError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except installer_mod.InstallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _shutdown_email_sidecar(body.id)

    installer_mod.clear_progress(body.id)
    installer_mod._set_progress(  # noqa: SLF001 - seed state for the poller
        body.id, status="queued", phase="queued", percent=0, version=body.version
    )
    return manifest


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
def uninstall_agent(agent_id: str, request: Request):
    """Uninstall a hub-installed agent. Refuses builtins (400)."""
    registry = _registry(request)
    # Claimed before the email sidecar is stopped, as for install.
    with _claim_slot(agent_id) as slot:
        _shutdown_email_sidecar(agent_id)
        try:
            installer_mod.uninstall(agent_id, registry=registry, slot=slot)
        except installer_mod.NotInstalledError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except installer_mod.InstallError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": agent_id, "status": "uninstalled"}
