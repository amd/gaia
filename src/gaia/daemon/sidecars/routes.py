# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/agents`` — the daemon's token-guarded sidecar control plane
(#2142 D-3).

Built as a factory (``build_agents_router``) and included from inside
``create_app`` so the app's documented no-shared-global-state invariant holds —
nothing is smuggled through ``app.state``.

Only ``ensure`` responses carry the sidecar bearer token; the list route never
does (least exposure). Manager-level spawn failures surface as 502 with the
manager's actionable message verbatim (which embeds the sidecar log tail —
pre-first-health-success only, so it cannot contain mailbox data).
"""

from __future__ import annotations

from typing import Optional

# Module-level on purpose (unlike app.py's deferred imports): this module is
# itself imported lazily from create_app, and the endpoint annotations below
# must be resolvable from module globals under PEP 563.
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from gaia.daemon.constants import API_PREFIX
from gaia.daemon.sidecars.errors import (
    CapacityError,
    HealthTimeoutError,
    ModeConflictError,
    SidecarSpawnError,
    StopFailedError,
    UnknownAgentError,
    VersionMismatchError,
)


def build_agents_router(token: str, registry):
    """Token-guarded APIRouter over *registry* (``/daemon/v1/agents*``)."""
    from gaia.daemon.app import build_require_token

    require_token = build_require_token(token)
    router = APIRouter(dependencies=[Depends(require_token)])

    async def _body_mode(request: Request) -> Optional[str]:
        """``mode`` from an optional JSON body ({"mode": "user"|"dev"|null})."""
        try:
            body = await request.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        return body.get("mode")

    @router.get(f"{API_PREFIX}/agents")
    def list_agents() -> dict:
        return {"agents": registry.list_agents()}

    @router.post(f"{API_PREFIX}/agents/{{agent_id}}/ensure")
    async def ensure(agent_id: str, request: Request) -> dict:
        mode = await _body_mode(request)
        try:
            # manager.start() is sync-blocking (health poll, lazy fetch) —
            # keep it off the event loop.
            return await run_in_threadpool(registry.ensure, agent_id, mode=mode)
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (ModeConflictError, CapacityError) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except (SidecarSpawnError, HealthTimeoutError, VersionMismatchError) as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @router.post(f"{API_PREFIX}/agents/{{agent_id}}/stop")
    async def stop(agent_id: str) -> dict:
        try:
            return await run_in_threadpool(registry.stop, agent_id)
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except StopFailedError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    return router
