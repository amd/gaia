# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Agent registry endpoints for GAIA Agent UI.

Exposes the registered agents so the frontend can display an agent selector.
Also provides export/import endpoints for custom agent bundles.
"""

import os
import tempfile
import zipfile
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse

from gaia.logger import get_logger

from ..models import AgentInfo, AgentListResponse

logger = get_logger(__name__)

router = APIRouter(tags=["agents"])

# Maximum size of an uploaded import bundle (100 MB).
_MAX_IMPORT_BUNDLE_BYTES = 100 * 1024 * 1024

# Hosts treated as localhost for the purposes of export/import endpoints.
_LOCALHOST_HOSTS = {"127.0.0.1", "::1", "localhost", ""}


def _registry(request: Request):
    """Get the AgentRegistry from app.state."""
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not initialized")
    return registry


def _require_localhost(request: Request) -> None:
    """Reject requests that do not originate from localhost."""
    host = (request.client.host if request.client else "") or ""
    if host not in _LOCALHOST_HOSTS:
        raise HTTPException(
            status_code=403, detail="endpoint only available on localhost"
        )


def _require_ui_header(request: Request) -> None:
    """Require the custom ``X-Gaia-UI: 1`` header as a lightweight CSRF guard.

    Custom headers trigger a CORS preflight in browsers, so drive-by form
    POSTs from malicious tabs cannot supply this header.
    """
    if request.headers.get("x-gaia-ui") != "1":
        raise HTTPException(status_code=403, detail="missing X-Gaia-UI header")


def _require_tunnel_inactive(request: Request) -> None:
    """Block export/import while the ngrok tunnel is active.

    Streaming a bundle across a public tunnel would be a data-exfil footgun,
    so we refuse outright rather than trying to reason about auth.
    """
    tunnel = getattr(request.app.state, "tunnel", None)
    if tunnel is not None and getattr(tunnel, "active", False):
        raise HTTPException(
            status_code=503,
            detail="import/export not available while tunnel is active",
        )


def _reg_to_info(reg) -> AgentInfo:
    return AgentInfo(
        id=reg.id,
        name=reg.name,
        description=reg.description,
        source=reg.source,
        conversation_starters=reg.conversation_starters,
        models=reg.models,
    )


@router.get("/api/agents", response_model=AgentListResponse)
async def list_agents(request: Request):
    """List all registered agents visible to the UI (excludes hidden system agents)."""
    registry = _registry(request)
    registrations = [r for r in registry.list() if not r.hidden]
    return AgentListResponse(
        agents=[_reg_to_info(r) for r in registrations],
        total=len(registrations),
    )


@router.get("/api/agents/{agent_id:path}", response_model=AgentInfo)
async def get_agent(agent_id: str, request: Request):
    """Get details for a specific agent."""
    registry = _registry(request)
    reg = registry.get(agent_id)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return _reg_to_info(reg)


@router.post(
    "/api/agents/export",
    dependencies=[
        Depends(_require_localhost),
        Depends(_require_ui_header),
        Depends(_require_tunnel_inactive),
    ],
)
async def export_agents(background_tasks: BackgroundTasks):
    """Export all custom agents as a downloadable zip bundle."""
    from gaia.installer.export_import import export_custom_agents

    # Write to a per-request temp file so concurrent exports don't race on a
    # shared path, and the file is cleaned up after streaming completes.
    gaia_dir = Path.home() / ".gaia"
    gaia_dir.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix="gaia-export-", suffix=".zip", dir=str(gaia_dir)
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        export_custom_agents(tmp_path)
    except ValueError as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_tasks.add_task(lambda: tmp_path.unlink(missing_ok=True))
    return FileResponse(
        path=str(tmp_path),
        media_type="application/zip",
        filename="gaia-agents-export.zip",
        headers={
            "Content-Disposition": 'attachment; filename="gaia-agents-export.zip"',
        },
    )


@router.post(
    "/api/agents/import",
    dependencies=[
        Depends(_require_localhost),
        Depends(_require_ui_header),
        Depends(_require_tunnel_inactive),
    ],
)
async def import_agents(request: Request, bundle: UploadFile = File(...)):  # noqa: B008
    """Import a custom agent bundle from an uploaded zip file."""
    from gaia.installer.export_import import import_agent_bundle

    # Fast reject on declared content length before streaming bytes.
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_IMPORT_BUNDLE_BYTES:
                raise HTTPException(
                    status_code=413, detail="bundle exceeds 100 MB limit"
                )
        except ValueError:
            # Malformed header — ignore and fall through to streaming limit.
            pass

    # Stream upload into a temp file with a hard byte cap.
    tmp = tempfile.NamedTemporaryFile(
        prefix="gaia-import-", suffix=".zip", delete=False
    )
    tmp_path = Path(tmp.name)
    total_bytes = 0
    try:
        try:
            while True:
                chunk = await bundle.read(1024 * 1024)  # 1 MiB
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > _MAX_IMPORT_BUNDLE_BYTES:
                    raise HTTPException(
                        status_code=413, detail="bundle exceeds 100 MB limit"
                    )
                tmp.write(chunk)
        finally:
            tmp.close()

        try:
            result = import_agent_bundle(tmp_path)
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            tmp_path.unlink()
        except OSError as exc:
            logger.warning("Could not delete import temp file %s: %s", tmp_path, exc)

    # Hot-register imported agents into the LIVE server registry (app.state),
    # not a fresh AgentRegistry() instance which would be an orphan.
    live_registry = getattr(request.app.state, "agent_registry", None)
    if live_registry is not None:
        agents_root = Path.home() / ".gaia" / "agents"
        for agent_id in result.imported:
            try:
                live_registry.register_from_dir(agents_root / agent_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Hot-register failed for %s: %s", agent_id, exc)

    # Errors from ImportResult are "agent_id: message" strings. Convert to
    # structured objects so the frontend can display them per-agent without
    # re-parsing, and to avoid surfacing raw exception text as a flat string.
    structured_errors = []
    for err in result.errors:
        parts = err.split(": ", 1)
        structured_errors.append(
            {"id": parts[0], "error": parts[1] if len(parts) == 2 else err}
        )

    return {
        "imported": result.imported,
        "overwritten": result.overwritten,
        "errors": structured_errors,
        # Overwritten agents require a server restart to fully take effect —
        # Python module caching means existing sessions keep running old code.
        "requires_restart": len(result.overwritten) > 0,
    }
