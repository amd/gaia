# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/agents/{agent_id}/connections`` — the OAuth forward-out control
plane (issue #2154 / V2-14).

Token-guarded like the rest of ``/daemon/v1/*`` (same ``build_require_token`` so
the 401 contract never forks). Each route resolves the target sidecar's
``(base_url, bearer)`` from the registry — the sidecar bearer never travels
through a client — then drives :class:`gaia.daemon.forward.ConnectionForwarder`.

Loud, typed errors map to distinct statuses:

- unknown agent → 404
- provider not granted to the agent → 403 (the grant model is the source of
  truth; the daemon refuses to forward an ungranted connector)
- sidecar not running → 503
- sidecar rejected/dropped the forward → 502
"""

from __future__ import annotations

# Module-level on purpose (mirrors sidecars/routes.py): this module is imported
# lazily from create_app, and the endpoint annotations must resolve from module
# globals under PEP 563.
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from gaia.daemon.constants import API_PREFIX
from gaia.daemon.forward import ForwardDeliveryError, NotGrantedError
from gaia.daemon.sidecars.errors import SidecarNotRunningError, UnknownAgentError


def build_connections_router(token: str, registry, forwarder):
    """Token-guarded APIRouter driving *forwarder* against *registry*."""
    from gaia.daemon.app import build_require_token

    require_token = build_require_token(token)
    router = APIRouter(dependencies=[Depends(require_token)])

    def _connection(agent_id: str):
        try:
            return registry.connection(agent_id)
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except SidecarNotRunningError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @router.post(f"{API_PREFIX}/agents/{{agent_id}}/connections/forward")
    async def forward_all(agent_id: str) -> dict:
        base_url, bearer = _connection(agent_id)
        try:
            return await run_in_threadpool(
                forwarder.forward_all, agent_id, base_url=base_url, bearer=bearer
            )
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except NotGrantedError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e

    @router.post(f"{API_PREFIX}/agents/{{agent_id}}/connections/{{provider}}/forward")
    async def forward_provider(agent_id: str, provider: str) -> dict:
        base_url, bearer = _connection(agent_id)
        try:
            return await run_in_threadpool(
                forwarder.forward_provider,
                agent_id,
                provider,
                base_url=base_url,
                bearer=bearer,
            )
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except NotGrantedError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except ForwardDeliveryError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @router.delete(f"{API_PREFIX}/agents/{{agent_id}}/connections/{{provider}}")
    async def withdraw(agent_id: str, provider: str) -> dict:
        base_url, bearer = _connection(agent_id)
        try:
            return await run_in_threadpool(
                forwarder.withdraw,
                agent_id,
                provider,
                base_url=base_url,
                bearer=bearer,
            )
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ForwardDeliveryError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    return router
