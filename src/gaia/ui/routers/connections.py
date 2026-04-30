# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
FastAPI router for ``/api/connections/*`` — thin presentation layer over
``gaia.connections``.

This router does NOT own connection state. Each handler is at most ~10
lines: parse the request, call the corresponding ``gaia.connections``
function, translate exceptions per the table below. The same operations
are reachable from the CLI (``gaia connections ...``) and SDK
(``import gaia.connections; ...``) without going through this layer.

Exception → HTTP mapping:
- ``AuthRequiredError(NOT_CONNECTED)``        → 401
- ``AuthRequiredError(AGENT_NOT_GRANTED)``    → 403
- ``AuthRequiredError(CONNECTION_MISSING_SCOPES)`` → 403 + missing_scopes
- ``AuthRequiredError(REAUTH_REQUIRED)``      → 401
- ``ConnectionRevokedError``                  → 401
- ``ScopeMismatchError``                      → 403
- ``ConfigurationError``                      → 503
- ``FlowInProgressError``                     → 409
- ``FlowTimeoutError``                        → 408
- ``ConsentDeniedError``                      → 400
- Any other ``ConnectionsError``              → 500

SSE (``GET /api/connections/events``) implements the EventEmitter Protocol
with a per-subscriber bounded ``asyncio.Queue(maxsize=100)`` and registers
itself via ``set_emitter`` on first subscription.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List

import keyring
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import gaia.connections as connections
from gaia.connections.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionRevokedError,
    ConnectionsError,
    ConsentDeniedError,
    FlowInProgressError,
    FlowTimeoutError,
    ScopeMismatchError,
)
from gaia.connections.events import EventEmitter, set_emitter
from gaia.connections.flow import _pending as _flow_pending
from gaia.connections.grants import GRANTS_FILE

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/connections", tags=["connections"])


# ─────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────


class AuthorizeRequest(BaseModel):
    scopes: List[str] = Field(default_factory=list)


class GrantRequest(BaseModel):
    scopes: List[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# SSE EventEmitter implementation
# ─────────────────────────────────────────────────────────────────


class _SseEmitter:
    """
    Multi-subscriber event broadcaster used by ``GET /api/connections/events``.

    Each subscriber owns a bounded ``asyncio.Queue(maxsize=100)``; events are
    fan-outed to every subscriber. A subscriber that falls behind drops
    events instead of leaking memory (slow-client memory-leak protection).

    The handler uses ``try/finally`` to remove the subscriber from the list
    on disconnect so the queue is freed when the EventSource closes.
    """

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        # Lock guards subscriber-list mutations; not held across emits.
        self._lock = asyncio.Lock()

    async def emit(self, event_type: str, payload: dict) -> None:
        envelope = {"type": event_type, "payload": payload}
        async with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                logger.warning(
                    "connections-sse: dropping event %s for slow subscriber",
                    event_type,
                )

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass


# Module-level singleton — installed at app startup via set_emitter.
_emitter = _SseEmitter()
set_emitter(_emitter)


# ─────────────────────────────────────────────────────────────────
# Exception → HTTP translation
# ─────────────────────────────────────────────────────────────────


def _raise_http_for(exc: ConnectionsError) -> "HTTPException":
    if isinstance(exc, ConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, AuthRequiredError):
        if exc.reason in (
            AuthRequiredError.Reason.NOT_CONNECTED,
            AuthRequiredError.Reason.REAUTH_REQUIRED,
        ):
            return HTTPException(
                status_code=401,
                detail={
                    "error": exc.reason.value,
                    "provider": exc.provider,
                    "agent_id": exc.agent_id,
                },
            )
        # AGENT_NOT_GRANTED, CONNECTION_MISSING_SCOPES → 403
        return HTTPException(
            status_code=403,
            detail={
                "error": exc.reason.value,
                "provider": exc.provider,
                "agent_id": exc.agent_id,
                "missing_scopes": list(exc.missing_scopes),
            },
        )
    if isinstance(exc, ConnectionRevokedError):
        return HTTPException(
            status_code=401,
            detail={"error": "connection_revoked", "provider": exc.provider},
        )
    if isinstance(exc, ScopeMismatchError):
        return HTTPException(
            status_code=403,
            detail={
                "error": "scope_mismatch",
                "missing_scopes": exc.missing_scopes,
            },
        )
    if isinstance(exc, FlowInProgressError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, FlowTimeoutError):
        return HTTPException(status_code=408, detail=str(exc))
    if isinstance(exc, ConsentDeniedError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────


@router.get("")
@router.get("/")
async def list_connections() -> Dict[str, List[Dict[str, Any]]]:
    rows = connections.list_connections()
    return {"connections": rows}


@router.get("/events")
async def connection_events() -> StreamingResponse:
    """
    Long-lived SSE stream of connection lifecycle events.

    Event types:
      - ``connection.connected``  ({provider, account_email})
      - ``connection.revoked``    ({provider})
      - ``grant.added``           ({provider, agent_id, scopes})
      - ``grant.removed``         ({provider, agent_id})
      - ``flow.started`` / ``flow.completed`` / ``flow.failed``
    """
    queue = await _emitter.subscribe()

    async def gen() -> AsyncIterator[bytes]:
        try:
            while True:
                envelope = await queue.get()
                yield f"data: {json.dumps(envelope)}\n\n".encode("utf-8")
        finally:
            await _emitter.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/_debug")
async def debug_state() -> Dict[str, Any]:
    """
    Diagnostics endpoint, gated by ``GAIA_DEBUG=1``.

    Returns structured state for triage when "Connect button does nothing":
    provider registration, env-var presence, keyring backend, grants-path
    writability, and in-flight flow count.
    """
    if os.environ.get("GAIA_DEBUG") != "1":
        raise HTTPException(status_code=404, detail="Not Found")

    from gaia.connections.providers import _registry as provider_registry

    grants_writable = False
    try:
        # Best-effort writability check — doesn't actually write data.
        GRANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        grants_writable = os.access(str(GRANTS_FILE.parent), os.W_OK)
    except OSError:
        pass

    return {
        "provider_registered": "google" in provider_registry,
        "env_var_present": bool(os.environ.get("GAIA_GOOGLE_CLIENT_ID")),
        "keyring_backend_class": type(keyring.get_keyring()).__name__,
        "grants_path": str(GRANTS_FILE),
        "grants_path_writable": grants_writable,
        "in_flight_flow_count": len(_flow_pending),
    }


@router.get("/{provider}")
async def get_connection(provider: str) -> Dict[str, Any]:
    row = connections.get_connection(provider)
    if row is None:
        raise HTTPException(status_code=404, detail="Not connected")
    return row


@router.delete("/{provider}", status_code=204)
async def revoke_connection(provider: str) -> Response:
    connections.revoke_connection(provider)
    await _emitter.emit("connection.revoked", {"provider": provider})
    return Response(status_code=204)


@router.post("/{provider}/authorize")
async def authorize(provider: str, body: AuthorizeRequest) -> Dict[str, Any]:
    try:
        return await connections.start_authorization(provider, scopes=body.scopes)
    except ConnectionsError as e:
        raise _raise_http_for(e) from e


@router.delete("/_flows/{flow_id}", status_code=204)
async def cancel_flow_endpoint(flow_id: str) -> Response:
    """Tear down a pending flow without waiting for the callback. Used by
    the AgentUI when the user dismisses the consent dialog."""
    await connections.cancel_flow(flow_id)
    return Response(status_code=204)


@router.get("/{provider}/grants")
async def get_grants(provider: str) -> Dict[str, Dict[str, List[str]]]:
    return {"grants": connections.list_agent_grants(provider)}


@router.put("/{provider}/grants/{agent_id:path}")
async def put_grant(provider: str, agent_id: str, body: GrantRequest) -> Dict[str, Any]:
    connections.grant_agent(provider, agent_id, body.scopes)
    await _emitter.emit(
        "grant.added",
        {"provider": provider, "agent_id": agent_id, "scopes": body.scopes},
    )
    return {"provider": provider, "agent_id": agent_id, "scopes": body.scopes}


@router.delete("/{provider}/grants/{agent_id:path}", status_code=204)
async def delete_grant(provider: str, agent_id: str) -> Response:
    connections.revoke_agent_grant(provider, agent_id)
    await _emitter.emit("grant.removed", {"provider": provider, "agent_id": agent_id})
    return Response(status_code=204)
