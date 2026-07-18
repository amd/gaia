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

from ..models import AgentInfo, AgentListResponse, DiskAgentInfo, DiskAgentListResponse

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
    # required_connections may contain ConnectorRequirement objects (with
    # .connector_id/.scopes/.reason) or plain strings (legacy shorthand
    # used by connectors-demo). Normalise both into dicts for the API
    # response, keyed as "connector_id" to match the TypeScript
    # ConnectorRequirement interface.
    connections = []
    for cr in reg.required_connections:
        if isinstance(cr, str):
            connections.append({"connector_id": cr, "scopes": [], "reason": ""})
        else:
            connections.append(
                {
                    "connector_id": cr.connector_id,
                    "scopes": list(cr.scopes),
                    "reason": cr.reason,
                }
            )

    # Serialize DeviceConfig dataclasses into dicts for the API response.
    import dataclasses as _dc

    device_configs = [_dc.asdict(dc) for dc in getattr(reg, "device_configs", [])]

    # Serialize ModelTier dataclasses (issue #1162) for the size selector.
    model_tiers = [_dc.asdict(t) for t in getattr(reg, "model_tiers", [])]

    return AgentInfo(
        id=reg.id,
        name=reg.name,
        description=reg.description,
        source=reg.source,
        conversation_starters=reg.conversation_starters,
        models=reg.models,
        min_memory_gb=reg.min_memory_gb,
        required_connections=connections,
        consumes_mcp_servers=getattr(reg, "consumes_mcp_servers", False),
        namespaced_agent_id=reg.namespaced_agent_id,
        category=reg.category,
        tags=reg.tags,
        icon=reg.icon,
        tools_count=reg.tools_count,
        language=reg.language,
        device_configs=device_configs,
        model_tiers=model_tiers,
    )


def _installed_sidecar_agents(registry) -> list[AgentInfo]:
    """Hub-installed sidecar agents that the daemon supervises out-of-process.

    On a consumer machine with no pip-installed agent wheels the in-process
    registry is empty, so a freshly-installed *binary* agent (e.g. email) would
    never appear in the picker even though it is installed and healthy (#2118).
    Bridge the gap here — the router is the one layer allowed to see both
    ``gaia.daemon`` (which agents can be supervised) and ``gaia.hub`` (which are
    installed); ``gaia.hub`` and ``gaia.daemon`` never import ``gaia.ui``.

    Only agents that are BOTH daemon-supervisable AND have an install sentinel
    are surfaced, and only when the registry doesn't already carry them (a
    wheel/entry-point install wins — it has richer metadata). Metadata is
    enriched, offline, from the last-cached hub catalog; absent that we fall
    back to the daemon spec's display name so the picker still renders a real
    card instead of a dead entry.
    """
    from gaia.daemon.sidecars.spec import builtin_specs
    from gaia.hub import catalog as catalog_mod
    from gaia.hub import installer

    installed = installer.list_installed()
    if not installed:
        return []

    # id -> cached catalog entry (name/description/icon/category), offline-only.
    cached = {e["id"]: e for e in catalog_mod.cached_index_agents()}

    agents: list[AgentInfo] = []
    for agent_id, spec in builtin_specs().items():
        if agent_id not in installed:
            continue
        if registry.get(agent_id) is not None:
            # A registered (wheel/native) agent already covers this id.
            continue
        meta = cached.get(agent_id, {})
        sentinel = installed[agent_id]
        agents.append(
            AgentInfo(
                id=agent_id,
                name=meta.get("name") or spec.display_name,
                description=meta.get("description", ""),
                source="installed",
                conversation_starters=[],
                models=list(meta.get("models", [])),
                namespaced_agent_id=f"installed:{agent_id}",
                category=meta.get("category", "general"),
                tags=list(meta.get("tags", [])),
                icon=meta.get("icon", ""),
                tools_count=meta.get("tools_count", 0),
                language=meta.get("language") or sentinel.language,
            )
        )
    return agents


@router.get("/api/agents", response_model=AgentListResponse)
async def list_agents(request: Request):
    """List all agents the UI can launch (excludes hidden system agents).

    Unions the in-process registry with hub-installed *sidecar* agents so a
    consumer install with no agent wheels still shows its installed binary
    agents in the picker (#2118). Registry entries win on id collisions.
    """
    registry = _registry(request)
    infos = [_reg_to_info(r) for r in registry.list() if not r.hidden]
    infos.extend(_installed_sidecar_agents(registry))
    return AgentListResponse(agents=infos, total=len(infos))


@router.get(
    "/api/agents/disk",
    response_model=DiskAgentListResponse,
    dependencies=[Depends(_require_localhost), Depends(_require_ui_header)],
)
async def list_disk_agents(request: Request):
    """List custom agents present on disk using the export scanner."""
    from gaia.installer.export_import import list_exportable_custom_agent_dirs

    registry = _registry(request)
    registered_by_dir = {}
    for reg in registry.list():
        agent_dir = getattr(reg, "agent_dir", None)
        if agent_dir is not None:
            registered_by_dir[Path(agent_dir).resolve()] = reg

    agents: list[DiskAgentInfo] = []
    for agent_dir in list_exportable_custom_agent_dirs():
        resolved_dir = agent_dir.resolve()
        reg = registered_by_dir.get(resolved_dir)
        agents.append(
            DiskAgentInfo(
                id=agent_dir.name,
                name=reg.name if reg else agent_dir.name,
                registered=reg is not None,
                registered_agent_id=reg.id if reg else None,
                source=reg.source if reg else None,
            )
        )

    return DiskAgentListResponse(agents=agents, total=len(agents))


@router.get("/api/agents/{agent_id:path}", response_model=AgentInfo)
async def get_agent(agent_id: str, request: Request):
    """Get details for a specific agent (registry or hub-installed sidecar)."""
    registry = _registry(request)
    reg = registry.get(agent_id)
    if reg is not None:
        return _reg_to_info(reg)
    # Fall back to hub-installed sidecar agents (email etc.) so the picker's
    # detail view resolves them on consumer installs without agent wheels.
    for info in _installed_sidecar_agents(registry):
        if info.id == agent_id:
            return info
    raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")


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
