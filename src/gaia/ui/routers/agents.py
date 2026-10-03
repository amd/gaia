# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Agent registry endpoint for GAIA Agent UI: lists the launchable agents."""

from fastapi import APIRouter, HTTPException, Request

from ..models import AgentInfo, AgentListResponse

router = APIRouter(tags=["agents"])


def _registry(request: Request):
    """Get the AgentRegistry from app.state."""
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not initialized")
    return registry


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
