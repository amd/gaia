# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Agent registry endpoints for GAIA Agent UI.

Exposes the registered agents so the frontend can display an agent selector.
"""

from fastapi import APIRouter, HTTPException, Request

from gaia.logger import get_logger

from ..models import AgentInfo, AgentListResponse

logger = get_logger(__name__)

router = APIRouter(tags=["agents"])


def _registry(request: Request):
    """Get the AgentRegistry from app.state."""
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not initialized")
    return registry


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
