# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/host/v1/models/*`` — the model-slot broker's lease route (V2-11 · §0.12).

This is the daemon's **callback plane** (``/host/v1/*``), distinct from the
client plane (``/daemon/v1/*``). A caller here is either:

- a **sidecar**, authenticating with the per-session launch token the manager
  minted for it (#1706) — resolved to its agent_id via
  ``SidecarRegistry.authenticate_callback``; or
- a **host-side** component (the UI server's embedder, host-custody RAG),
  authenticating with the daemon client token.

Either credential is accepted; anything else is a loud 401. The full per-agent
scoping of the callback API is V2-12 — V2-11 mounts only the broker's own two
routes (acquire + release).

Blocking by design: ``POST /host/v1/models/lease`` does not return until the
caller owns the slot (or its wait times out → 504). It runs in a threadpool so
the broker's blocking ``acquire`` never stalls the event loop. When the request
has to queue, the daemon logs a ``switching model…`` event and the grant
response carries ``waited``/``switching`` so the client can surface the same
status (§0.12 legibility).
"""

from __future__ import annotations

import secrets
from typing import Optional

# Module-level (like sidecars/routes.py): this module is imported lazily from
# create_app, and the endpoint annotations must resolve from module globals
# under PEP 563.
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from gaia.daemon.broker import (
    LeaseNotHeldError,
    LeasePriority,
    LeaseTimeoutError,
    ModelSlotBroker,
)
from gaia.daemon.constants import AUTH_SCHEME, HOST_API_PREFIX
from gaia.daemon.paths import instance_path
from gaia.logger import get_logger

logger = get_logger(__name__)

# Default ceiling on how long a lease request blocks before a 504. Long enough
# to outlast a cold model load ahead of it in the queue, short enough that a
# wedged slot surfaces as an actionable error rather than a hung client.
DEFAULT_LEASE_WAIT_TIMEOUT_S = 300.0


def _caller_from_credential(
    credential: str, daemon_token: str, registry
) -> Optional[str]:
    """Resolve a bearer credential to a caller label, or ``None`` if invalid.

    Host-side callers present the daemon client token → ``"host"``. Sidecars
    present their launch token → their agent_id. Constant-time compare on the
    host token so it cannot be timed.
    """
    if credential and secrets.compare_digest(credential, daemon_token):
        return "host"
    if registry is not None:
        return registry.authenticate_callback(credential)
    return None


def build_broker_router(
    daemon_token: str, registry, broker: ModelSlotBroker
) -> APIRouter:
    """Token-guarded APIRouter exposing the broker's lease/release routes.

    *daemon_token* authenticates host-side callers; *registry* resolves sidecar
    launch tokens to agent ids; *broker* is the shared :class:`ModelSlotBroker`.
    """

    def require_caller(authorization: Optional[str] = Header(default=None)) -> str:
        where = (
            f"the client token in {instance_path()} (host-side), or the "
            "sidecar's launch token"
        )
        if not authorization:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Missing broker credential. Send "
                    f"'Authorization: {AUTH_SCHEME} <token>' — {where}."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != AUTH_SCHEME.lower() or not credential:
            raise HTTPException(
                status_code=401,
                detail=(
                    f"Malformed Authorization header. Expected "
                    f"'{AUTH_SCHEME} <token>'."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        caller = _caller_from_credential(credential, daemon_token, registry)
        if caller is None:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Invalid broker credential: it matches neither the daemon "
                    "client token nor any running sidecar's launch token. If the "
                    "daemon restarted the tokens rotated — re-attach with "
                    "`gaia daemon status`, or re-ensure the sidecar."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        return caller

    router = APIRouter()

    @router.post(f"{HOST_API_PREFIX}/models/lease")
    async def lease(request: Request, caller: str = Depends(require_caller)) -> dict:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        model = body.get("model")
        if not model or not isinstance(model, str):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Broker lease requires a non-empty string 'model' in the "
                    'request body, e.g. {"model": "Gemma-4-E4B-it-GGUF", '
                    '"priority": "interactive"}.'
                ),
            )
        try:
            priority = LeasePriority.parse(
                body.get("priority", LeasePriority.BACKGROUND)
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        timeout = body.get("timeout", DEFAULT_LEASE_WAIT_TIMEOUT_S)

        waited = {"flag": False, "reason": ""}

        def _on_wait(reason: str) -> None:
            waited["flag"] = True
            waited["reason"] = reason
            logger.info("broker: %s — %s (switching model…)", caller, reason)

        try:
            granted = await run_in_threadpool(
                broker.acquire,
                model,
                priority=priority,
                holder=caller,
                timeout=timeout,
                on_wait=_on_wait,
            )
        except LeaseTimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e)) from e

        return {
            "lease_id": granted.lease_id,
            "model": granted.model,
            "priority": granted.priority.name.lower(),
            "holder": granted.holder,
            "granted_at": granted.granted_at,
            "waited": waited["flag"],
            "switching": waited["flag"] and "switching model" in waited["reason"],
        }

    @router.post(f"{HOST_API_PREFIX}/models/lease/{{lease_id}}/release")
    async def release(lease_id: str, _caller: str = Depends(require_caller)) -> dict:
        # _caller is unused beyond the auth it enforces via the dependency.
        try:
            broker.release(lease_id)
        except LeaseNotHeldError as e:
            # 409: the lease is not the one holding the slot (double-release or a
            # stale id after a TTL reclaim). Loud, not swallowed.
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"lease_id": lease_id, "state": "released"}

    return router
