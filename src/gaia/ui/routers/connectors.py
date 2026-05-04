# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
FastAPI router for ``/api/connectors/*`` — thin presentation layer over
``gaia.connectors``.

This router does NOT own connector state. Each handler is at most ~15
lines: parse the request, call the corresponding ``gaia.connectors``
function, translate exceptions per the table below. The same operations
are reachable from the CLI (``gaia connectors ...``) and SDK
(``import gaia.connectors; ...``) without going through this layer.

Exception → HTTP mapping:
- ``AuthRequiredError(NOT_CONNECTED)``             → 401
- ``AuthRequiredError(AGENT_NOT_GRANTED)``         → 403
- ``AuthRequiredError(CONNECTION_MISSING_SCOPES)`` → 403 + missing_scopes
- ``AuthRequiredError(REAUTH_REQUIRED)``           → 401
- ``ConnectionRevokedError``                       → 401
- ``ScopeMismatchError``                           → 403
- ``ConfigurationError``                           → 503
- ``FlowInProgressError``                          → 409
- ``FlowTimeoutError``                             → 408
- ``ConsentDeniedError``                           → 400
- Any other ``ConnectorsError``                    → 500

Mutating routes (POST/PUT/DELETE) require ``X-Gaia-UI: 1`` header (CSRF
guard, plan amendment A8).  Read-only GET routes are unguarded.

The catalog import at module load time triggers handler registration
for ``oauth_pkce`` and ``mcp_server`` types.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

import keyring
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import gaia.connectors as connections
import gaia.connectors.catalog  # noqa: F401 — triggers REGISTRY + handler registration
from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionRevokedError,
    ConnectorsError,
    ConsentDeniedError,
    FlowInProgressError,
    FlowTimeoutError,
    ScopeMismatchError,
)
from gaia.connectors.events import EventEmitter, set_emitter
from gaia.connectors.flow import _pending as _flow_pending
from gaia.connectors.grants import (
    GRANTS_FILE,
    grant_agent,
    list_agent_grants,
    revoke_agent_grant,
)
from gaia.connectors.handler import configure, disconnect, get_credential, health_check
from gaia.connectors.mcp_server import is_mcp_server_configured
from gaia.connectors.registry import REGISTRY
from gaia.connectors.store import peek_connection

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/connectors", tags=["connectors"])


# ─────────────────────────────────────────────────────────────────
# CSRF guard (plan amendment A8)
# ─────────────────────────────────────────────────────────────────


def _require_ui_header(request: Request) -> None:
    """Require ``X-Gaia-UI: 1`` header on mutating routes.

    Custom request headers trigger a CORS preflight in browsers, so
    drive-by form POSTs from malicious pages cannot forge this header.
    """
    if request.headers.get("x-gaia-ui") != "1":
        raise HTTPException(status_code=403, detail="missing X-Gaia-UI header")


# ─────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────


class AuthorizeRequest(BaseModel):
    scopes: List[str] = Field(default_factory=list)


class GrantRequest(BaseModel):
    scopes: List[str] = Field(default_factory=list)


class ConfigureRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────
# SSE EventEmitter implementation
# ─────────────────────────────────────────────────────────────────


class _SseEmitter:
    """
    Multi-subscriber event broadcaster used by ``GET /api/connectors/events``.

    Each subscriber owns a bounded ``asyncio.Queue(maxsize=100)``; events are
    fan-outed to every subscriber. A subscriber that falls behind drops
    events instead of leaking memory (slow-client memory-leak protection).
    """

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
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
                    "connectors-sse: dropping event %s for slow subscriber",
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


_emitter = _SseEmitter()
set_emitter(_emitter)


# ─────────────────────────────────────────────────────────────────
# Exception → HTTP translation
# ─────────────────────────────────────────────────────────────────


def _raise_http_for(exc: ConnectorsError) -> HTTPException:
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
                    "connector_id": exc.provider,
                    "agent_id": exc.agent_id,
                },
            )
        return HTTPException(
            status_code=403,
            detail={
                "error": exc.reason.value,
                "connector_id": exc.provider,
                "agent_id": exc.agent_id,
                "missing_scopes": list(exc.missing_scopes),
            },
        )
    if isinstance(exc, ConnectionRevokedError):
        return HTTPException(
            status_code=401,
            detail={"error": "connection_revoked", "connector_id": exc.provider},
        )
    if isinstance(exc, ScopeMismatchError):
        return HTTPException(
            status_code=403,
            detail={"error": "scope_mismatch", "missing_scopes": exc.missing_scopes},
        )
    if isinstance(exc, FlowInProgressError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, FlowTimeoutError):
        return HTTPException(status_code=408, detail=str(exc))
    if isinstance(exc, ConsentDeniedError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────


def _connector_summary(connector_id: str) -> Dict[str, Any]:
    """Build a summary dict for one connector: spec fields + live state.

    No state cache: ``configured`` / ``account_id`` / ``scopes`` are
    derived live from the source-of-truth store on every call —
    ``store.peek_connection`` (keyring) for ``oauth_pkce`` and
    ``mcp_servers.json`` for ``mcp_server``. This guarantees the catalog
    UI never shows stale data after an external change (e.g. the user
    cleared their keyring or edited mcp_servers.json by hand).

    For ``oauth_pkce`` we also probe the OAuth provider registry — if
    the provider can't be instantiated (e.g. ``GAIA_GOOGLE_CLIENT_ID``
    is unset), surface ``configurable=False`` + ``config_error="..."``
    so the AgentUI renders a friendly "needs setup" tile rather than
    letting the user click Connect and hit a 503.
    """
    try:
        spec = REGISTRY.get(connector_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown connector: {connector_id!r}"
        )

    configured = False
    account_id: Optional[str] = None
    scopes: list = []
    configurable = True
    config_error: Optional[str] = None

    # TODO: when a 3rd connector type lands, push this if/elif into a
    # Handler.summary(spec) method so this becomes a single polymorphic
    # call. The same dispatch lives in cli.py:_handle_list — refactor
    # both together.
    if spec.type == "oauth_pkce":
        # Lazy import to avoid pulling provider modules at router import time.
        from gaia.connectors.providers import get as get_provider

        provider_ref = spec.oauth_provider_ref or spec.id
        try:
            get_provider(provider_ref)
        except ConfigurationError as e:
            configurable = False
            config_error = str(e)
        except KeyError:
            configurable = False
            config_error = (
                f"OAuth provider {provider_ref!r} is not registered. "
                "This is a catalog/code mismatch; please file a bug."
            )

        # Derive configured/account/scopes from the keyring blob — that
        # IS the source of truth. peek_connection is read-only and never
        # raises on missing entries.
        blob = peek_connection(provider_ref)
        if blob is not None:
            configured = True
            account_id = blob.get("account_email")
            scopes = list(blob.get("scopes", []))

    elif spec.type == "mcp_server":
        configured = is_mcp_server_configured(spec.id)

    return {
        "id": spec.id,
        "display_name": spec.display_name,
        "icon": spec.icon,
        "category": spec.category,
        "tier": spec.tier,
        "type": spec.type,
        "description": spec.description,
        "product_url": spec.product_url,
        "docs_url": spec.docs_url,
        "configured": configured,
        "configurable": configurable,
        "config_error": config_error,
        "account_id": account_id,
        "scopes": scopes,
        "mcp_env_keys": list(spec.mcp_env_keys),
        "default_scopes": list(spec.default_scopes),
        # OAuth setup form (e.g. Google client_id/client_secret) — empty
        # tuple for connectors that don't need first-time provider creds.
        "oauth_setup_fields": [
            {
                "key": f.key,
                "label": f.label,
                "kind": f.kind,
                "required": f.required,
                "placeholder": f.placeholder,
                "help_md": f.help_md,
            }
            for f in spec.oauth_setup_fields
        ],
    }


# ─────────────────────────────────────────────────────────────────
# Read-only endpoints (no CSRF guard)
# ─────────────────────────────────────────────────────────────────


@router.get("")
@router.get("/")
async def list_connectors() -> Dict[str, Any]:
    """Return catalog specs merged with live state for all connectors."""
    specs = REGISTRY.all()
    summaries: List[Dict[str, Any]] = []
    for s in specs:
        try:
            summaries.append(_connector_summary(s.id))
        except Exception:
            logger.exception("connectors-list: failed to build summary for %s", s.id)
            summaries.append({"id": s.id, "error": "unavailable"})
    return {"connectors": summaries}


@router.get("/events")
async def connector_events() -> StreamingResponse:
    """Long-lived SSE stream of connector lifecycle events.

    Event types:
      - ``connector.configured``        ({connector_id, account_id})
      - ``connector.disconnected``      ({connector_id})
      - ``connector.tested``            ({connector_id, ok, detail})
      - ``connector.oauth.completed``   ({connector_id, account_email})
      - ``connector.oauth.error``       ({connector_id, error})
      - ``connector.grant.changed``     ({connector_id, agent_id, scopes})
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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/_debug")
async def debug_state() -> Dict[str, Any]:
    """Diagnostics endpoint, gated by ``GAIA_DEBUG=1``."""
    if os.environ.get("GAIA_DEBUG") != "1":
        raise HTTPException(status_code=404, detail="Not Found")

    from gaia.connectors.providers import _registry as provider_registry

    grants_writable = False
    try:
        GRANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        grants_writable = os.access(str(GRANTS_FILE.parent), os.W_OK)
    except OSError:
        pass

    # Derive configured ids live by walking the catalog and asking the
    # source-of-truth store for each type.
    configured_ids: list[str] = []
    for spec in REGISTRY.all():
        summary = _connector_summary(spec.id)
        if summary["configured"]:
            configured_ids.append(spec.id)

    return {
        "provider_registered": "google" in provider_registry,
        "env_var_present": bool(os.environ.get("GAIA_GOOGLE_CLIENT_ID")),
        "keyring_backend_class": type(keyring.get_keyring()).__name__,
        "grants_path": str(GRANTS_FILE),
        "grants_path_writable": grants_writable,
        "in_flight_flow_count": len(_flow_pending),
        "catalog_size": len(REGISTRY.all()),
        "configured_ids": configured_ids,
    }


@router.get("/{connector_id}/grants")
async def get_grants(connector_id: str) -> Dict[str, Any]:
    return {"grants": list_agent_grants(connector_id)}


@router.get("/{connector_id}")
async def get_connector(connector_id: str) -> Dict[str, Any]:
    try:
        return _connector_summary(connector_id)
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown connector: {connector_id!r}"
        )
    except Exception:
        logger.exception("connectors-get: failed to build summary for %s", connector_id)
        raise HTTPException(status_code=500, detail="Connector unavailable")


# ─────────────────────────────────────────────────────────────────
# Mutating endpoints (CSRF-guarded, plan amendment A8)
# ─────────────────────────────────────────────────────────────────


@router.post("/{connector_id}/configure", dependencies=[Depends(_require_ui_header)])
async def configure_connector(
    connector_id: str, body: ConfigureRequest
) -> Dict[str, Any]:
    """Configure a connector — stores credentials and (for MCP servers) writes mcp_servers.json."""
    try:
        result = await configure(connector_id, body.config)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown connector: {connector_id!r}"
        )
    except ConnectorsError as e:
        raise _raise_http_for(e) from e

    await _emitter.emit(
        "connector.configured",
        {"connector_id": connector_id, "account_id": result.get("account_id")},
    )
    return result


@router.post("/{connector_id}/test", dependencies=[Depends(_require_ui_header)])
async def test_connector(connector_id: str) -> Dict[str, Any]:
    """Run the health check for a connector."""
    try:
        result = await health_check(connector_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown connector: {connector_id!r}"
        )
    except ConnectorsError as e:
        raise _raise_http_for(e) from e

    await _emitter.emit(
        "connector.tested",
        {
            "connector_id": connector_id,
            "ok": result.get("ok"),
            "detail": result.get("detail"),
        },
    )
    return result


@router.delete(
    "/{connector_id}", status_code=204, dependencies=[Depends(_require_ui_header)]
)
async def disconnect_connector(connector_id: str) -> Response:
    """Disconnect a connector — removes credentials and (for MCP) removes from mcp_servers.json."""
    try:
        await disconnect(connector_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown connector: {connector_id!r}"
        )
    except ConnectorsError as e:
        raise _raise_http_for(e) from e

    await _emitter.emit("connector.disconnected", {"connector_id": connector_id})
    return Response(status_code=204)


@router.post("/{connector_id}/authorize", dependencies=[Depends(_require_ui_header)])
async def authorize(connector_id: str, body: AuthorizeRequest) -> Dict[str, Any]:
    """Start an OAuth PKCE flow. Returns {flow_id, authorization_url}."""
    try:
        return await connections.start_authorization(connector_id, scopes=body.scopes)
    except ConnectorsError as e:
        raise _raise_http_for(e) from e


@router.delete(
    "/_flows/{flow_id}", status_code=204, dependencies=[Depends(_require_ui_header)]
)
async def cancel_flow_endpoint(flow_id: str) -> Response:
    """Cancel a pending OAuth flow without waiting for the callback."""
    await connections.cancel_flow(flow_id)
    return Response(status_code=204)


@router.put(
    "/{connector_id}/grants/{agent_id:path}", dependencies=[Depends(_require_ui_header)]
)
async def put_grant(
    connector_id: str, agent_id: str, body: GrantRequest
) -> Dict[str, Any]:
    grant_agent(connector_id, agent_id, body.scopes)
    await _emitter.emit(
        "connector.grant.changed",
        {"connector_id": connector_id, "agent_id": agent_id, "scopes": body.scopes},
    )
    return {"connector_id": connector_id, "agent_id": agent_id, "scopes": body.scopes}


@router.delete(
    "/{connector_id}/grants/{agent_id:path}",
    status_code=204,
    dependencies=[Depends(_require_ui_header)],
)
async def delete_grant(connector_id: str, agent_id: str) -> Response:
    revoke_agent_grant(connector_id, agent_id)
    await _emitter.emit(
        "connector.grant.changed",
        {"connector_id": connector_id, "agent_id": agent_id, "scopes": []},
    )
    return Response(status_code=204)
