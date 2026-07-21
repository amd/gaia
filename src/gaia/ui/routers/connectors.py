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
import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.activations import list_agent_activations
from gaia.connectors.api import activate as activate_connector_for_agent
from gaia.connectors.api import deactivate as deactivate_connector_for_agent
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
from gaia.connectors.events import set_emitter
from gaia.connectors.flow import _pending as _flow_pending
from gaia.connectors.grants import (
    GRANTS_FILE,
    grant_agent,
    list_agent_grants,
    revoke_agent_grant,
)
from gaia.connectors.handler import (
    _HANDLER_REGISTRY,
    configure,
    disconnect,
    health_check,
)
from gaia.connectors.mcp_server import (
    _read_mcp_servers_json,
    is_mcp_server_configured,
)
from gaia.connectors.registry import REGISTRY
from gaia.connectors.store import peek_connection, peek_provider_credentials

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/connectors", tags=["connectors"])

# Issue #1292 — forwarded pre-authenticated connections. Separate prefix
# (``/v1/connections``) per the issue's REST contract: a host app that already
# authenticated a user POSTs the forwarded grant here; GAIA persists it and
# acts on the mailbox as the host app's client — no second OAuth. The UI
# server binds localhost by default and gates remote/tunnel access behind a
# token (see ``TunnelAuthMiddleware``); mutating routes also require the
# ``X-Gaia-UI`` CSRF header below.
forwarded_router = APIRouter(prefix="/v1/connections", tags=["connections"])


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


def _require_mcp_server(connector_id: str) -> None:
    """Reject activation writes for non-MCP-server connectors.

    Activations gate MCP tool visibility via ``MCPClientManager.tools_for_agent``
    (see issue #1005). OAuth-only connectors have no MCP tool surface — their
    agent access is gated by per-scope grants, not by this ledger — so allowing
    a write here would create a switch that does nothing.
    """
    try:
        spec = REGISTRY.get(connector_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown connector: {connector_id!r}"
        )
    if spec.type != "mcp_server":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Activations apply to MCP-server connectors only; "
                f"{connector_id!r} is type {spec.type!r}. Use per-agent grants "
                "to control access for OAuth connectors."
            ),
        )


# ─────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────


class AuthorizeRequest(BaseModel):
    scopes: List[str] = Field(default_factory=list)
    # #2117 — namespaced agent ids to grant this connector once OAuth
    # completes, so connecting a mailbox grants it to the email agent in the
    # same flow. The router resolves each agent's required scopes from its
    # REQUIRED_CONNECTORS declaration (single source of truth) before handing
    # the map to the flow.
    grant_agents: List[str] = Field(default_factory=list)


class GrantRequest(BaseModel):
    scopes: List[str] = Field(default_factory=list)


class ActivationRequest(BaseModel):
    """Body for ``PUT /api/connectors/{id}/activations/{agent_id}``.

    Valid only for ``mcp_server`` connectors — see ``_require_mcp_server``.

    ``scopes`` is optional: when present and no grant exists for the pair,
    it is used to auto-create the grant (one-click convenience). When the
    pair already has a grant the body is ignored — see issue #1005.
    """

    scopes: Optional[List[str]] = None


class ConfigureRequest(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)


class ForwardConnectionRequest(BaseModel):
    """Body for ``POST /v1/connections/{provider}`` (#1292).

    A host app forwards the OAuth client it authenticated the user under
    (``client_id`` + ``client_secret``) plus the user's ``refresh_token``.
    GAIA persists both and refreshes AS THE HOST APP'S CLIENT — no second
    OAuth, no consent step.

    ``refresh_token`` / ``client_secret`` are secret INPUTS — they are never
    echoed back in any response. ``account_email`` is display-only in v1
    (the keyring slot is single-account per provider). ``grant_agents`` is
    the list of namespaced agent ids (e.g. ``installed:email``) to grant the
    forwarded scopes so they can resolve the connection ambiently.
    """

    client_id: str = Field(min_length=1)
    client_secret: str = Field(default="")
    refresh_token: str = Field(min_length=1)
    scopes: List[str] = Field(default_factory=list)
    account_email: str = Field(default="")
    grant_agents: List[str] = Field(default_factory=list)


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


def _resolve_grant_scopes(
    request: Request, connector_id: str, agent_ids: List[str]
) -> Dict[str, List[str]]:
    """Resolve ``[namespaced_agent_id]`` → ``{agent_id: required_scopes}`` (#2117).

    Each agent's scopes come from its ``REQUIRED_CONNECTORS`` declaration for
    ``connector_id`` — the same single source of truth the forwarded-connection
    route uses. Fails loudly (no silent skips): an unknown agent → 404; an
    agent that declares no requirement for this connector → 400. Both would
    otherwise produce a connect that silently grants nothing — the exact
    dead-end this flow removes.
    """
    if not agent_ids:
        return {}
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not initialized")

    by_nsid = {reg.namespaced_agent_id: reg for reg in registry.list()}
    unknown = [nsid for nsid in agent_ids if nsid not in by_nsid]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_agent", "agent_ids": unknown},
        )

    resolved: Dict[str, List[str]] = {}
    for nsid in agent_ids:
        reg = by_nsid[nsid]
        scopes: set[str] = set()
        for cr in reg.required_connections:
            if cr.connector_id == connector_id:
                scopes.update(cr.scopes)
        if not scopes:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "agent_declares_no_scopes",
                    "agent_id": nsid,
                    "connector_id": connector_id,
                },
            )
        resolved[nsid] = sorted(scopes)
    return resolved


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
    oauth_client: Optional[Dict[str, Any]] = None

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
            logger.info("connectors: provider %s not configured: %s", provider_ref, e)
            _pref = provider_ref.upper()
            config_error = (
                f"OAuth credentials for {provider_ref!r} are not configured. "
                f"Set GAIA_{_pref}_CLIENT_ID and GAIA_{_pref}_CLIENT_SECRET, "
                "or use Settings → Connections to configure them."
            )
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

        # OAuth *client* status (#2104): which client the provider would
        # resolve, so Settings can render/rotate it. client_id is not a
        # secret (it ships inside desktop apps); the secret itself is
        # NEVER included — only whether one is stored.
        _env_prefix = f"GAIA_{provider_ref.upper()}"
        stored_creds = peek_provider_credentials(provider_ref) or {}
        if stored_creds.get("client_id"):
            oauth_client = {
                "source": "keyring",
                "client_id": stored_creds["client_id"],
                "has_secret": bool(stored_creds.get("client_secret")),
            }
        elif os.environ.get(f"{_env_prefix}_CLIENT_ID"):
            oauth_client = {
                "source": "env",
                "client_id": os.environ[f"{_env_prefix}_CLIENT_ID"],
                "has_secret": bool(os.environ.get(f"{_env_prefix}_CLIENT_SECRET")),
            }
        else:
            oauth_client = {"source": None, "client_id": None, "has_secret": False}

    # ``enabled`` is meaningful only for ``mcp_server`` connectors. We
    # default to ``True`` for both not-configured connectors AND OAuth so
    # the UI doesn't render a "Disabled" pill where the concept doesn't
    # apply.
    enabled = True

    if spec.type == "mcp_server":
        configured = is_mcp_server_configured(spec.id)
        if configured:
            try:
                entry = _read_mcp_servers_json().get(spec.id, {})
                enabled = not entry.get("disabled", False)
            except ConnectorsError as e:
                # Corrupt mcp_servers.json — log loudly so the user has
                # a path to a fix, but don't crash the whole catalog
                # list (one bad entry would make every tile unavailable).
                logger.warning(
                    "connectors-summary: cannot read mcp_servers.json for "
                    "%s (%s); rendering tile with default enabled=true",
                    spec.id,
                    e,
                )

    # Activations ledger snapshot (issue #1005). Read-only here; the
    # frontend toggles each entry via PUT/DELETE
    # /api/connectors/{id}/activations/{agent_id} — mutating routes
    # accept ``mcp_server`` connectors only (see ``_require_mcp_server``).
    # The dict is always returned (empty ``{}`` for OAuth) so the response
    # shape stays uniform across connector types.
    activations = list_agent_activations(spec.id)

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
        "oauth_client": oauth_client,
        "account_id": account_id,
        "scopes": scopes,
        "enabled": enabled,
        "activations": activations,
        "mcp_env_keys": list(spec.mcp_env_keys),
        "default_scopes": list(spec.default_scopes),
        "available_scopes": list(spec.available_scopes),
        # Device-code capability (#1275): lets the UI offer "sign in with a
        # code" (no browser redirect / no Azure app registration).
        "supports_device_code": spec.supports_device_code,
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
        except Exception as exc:
            logger.warning(
                "connectors-list: summary failed for %s (%s)", s.id, type(exc).__name__
            )
            summaries.append({"id": s.id, "error": "unavailable"})
    return {"connectors": summaries}


@router.get("/events")
async def connector_events() -> StreamingResponse:
    """Long-lived SSE stream of connector lifecycle events.

    Event types:
      - ``connector.configured``        ({connector_id, account_id})
      - ``connector.disconnected``      ({connector_id})
      - ``connector.tested``            ({connector_id, ok, detail})
      - ``connector.enabled``           ({connector_id})
      - ``connector.disabled``          ({connector_id})
      - ``connector.oauth.completed``   ({connector_id, account_email})
      - ``connector.oauth.error``       ({connector_id, error})
      - ``connector.grant.changed``     ({connector_id, agent_id, scopes})
      - ``connector.activation.changed`` ({connector_id, agent_id, active})
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


@router.get("/agent-mcps")
async def list_agent_mcps(request: Request) -> Dict[str, Any]:
    """Return MCP servers declared by custom-Python agents (#1020).

    Scans each registered custom agent's ``mcp_servers.json`` (if present) and
    returns a flat sorted list of server entries.  Servers are sorted: enabled
    first (alphabetical), then disabled (alphabetical).

    These entries are read-only — they are controlled by each agent's local
    config file, not the global connectors framework.  The UI renders them in a
    separate "Custom agent servers" section with no toggle or disconnect action.
    """
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        # Registry not yet initialised (e.g. test client without lifespan).
        return {"agent_mcps": []}

    servers: List[Dict[str, Any]] = []

    for reg in registry.list():
        if reg.source != "custom_python" or reg.agent_dir is None:
            continue
        config_path = reg.agent_dir / "mcp_servers.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                raise ValueError(
                    f"top-level JSON must be an object, got {type(raw).__name__}"
                )
            mcp_servers_data = raw.get("mcpServers", raw.get("servers", {}))
            if not isinstance(mcp_servers_data, dict):
                raise ValueError("'mcpServers' must be an object")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agent-mcps: failed to read %s for agent %r: %s",
                config_path,
                reg.id,
                exc,
            )
            continue

        for server_name, server_cfg in mcp_servers_data.items():
            if not isinstance(server_cfg, dict):
                logger.warning(
                    "agent-mcps: skipping non-object server %r in %s",
                    server_name,
                    config_path,
                )
                continue
            raw_args = server_cfg.get("args", [])
            args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
            servers.append(
                {
                    "agent_id": reg.id,
                    "agent_name": reg.name,
                    "config_path": str(config_path),
                    "server_name": server_name,
                    "command": str(server_cfg.get("command", "")),
                    "args": args,
                    "disabled": bool(server_cfg.get("disabled", False)),
                }
            )

    # Enabled (disabled=False) first, then disabled; alphabetical within each group.
    servers.sort(key=lambda s: (s["disabled"], s["server_name"].lower()))

    return {"agent_mcps": servers}


@router.get("/{connector_id}/grants")
async def get_grants(connector_id: str) -> Dict[str, Any]:
    return {"grants": list_agent_grants(connector_id)}


@router.get("/{connector_id}/activations")
async def get_activations(connector_id: str) -> Dict[str, Any]:
    return {"activations": list_agent_activations(connector_id)}


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
    except Exception as exc:
        logger.warning(
            "connectors-get: summary failed for %s (%s)",
            connector_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Connector unavailable")


# ─────────────────────────────────────────────────────────────────
# Mutating endpoints (CSRF-guarded, plan amendment A8)
# ─────────────────────────────────────────────────────────────────


@router.post("/{connector_id}/configure", dependencies=[Depends(_require_ui_header)])
async def configure_connector(
    request: Request, connector_id: str, body: ConfigureRequest
) -> Dict[str, Any]:
    """Configure a connector — stores credentials and (for MCP servers) writes mcp_servers.json.

    For the first-run OAuth "Save & Connect" path, ``config.grant_agents`` (a
    list of namespaced agent ids) is resolved to a ``{agent_id: scopes}`` map so
    the mailbox is granted to those agents when the flow completes (#2117) — the
    same behaviour as the plain ``authorize`` path.
    """
    config = dict(body.config)
    raw_grant_agents = config.get("grant_agents")
    if raw_grant_agents:
        config["grant_agents"] = _resolve_grant_scopes(
            request, connector_id, list(raw_grant_agents)
        )
    try:
        result = await configure(connector_id, config)
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


async def _set_connector_enabled(connector_id: str, enabled: bool) -> Dict[str, Any]:
    """Shared implementation for ``POST /{id}/enable`` and ``POST /{id}/disable``.

    The toggle is meaningful only for ``mcp_server`` connectors that have
    already been configured. Unknown ids → 404; non-MCP types → 400; not-yet-
    configured ids → bubble up the handler's ``ConnectorsError`` as 500.

    On success, emits ``connector.enabled`` or ``connector.disabled`` on the
    SSE stream and returns the updated connector summary.
    """
    try:
        spec = REGISTRY.get(connector_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown connector: {connector_id!r}"
        )

    if spec.type != "mcp_server":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Connector type {spec.type!r} does not support enable/disable. "
                "Only mcp_server connectors can be toggled."
            ),
        )

    if not is_mcp_server_configured(connector_id):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Connector {connector_id!r} is not configured. "
                "Configure it before toggling its enabled state."
            ),
        )

    handler = _HANDLER_REGISTRY.get("mcp_server")
    if handler is None:  # pragma: no cover — handler registers at import time
        raise HTTPException(status_code=500, detail="MCP handler not registered")

    try:
        await handler.set_enabled(connector_id, enabled)
    except ConnectorsError as e:
        raise _raise_http_for(e) from e

    event_name = "connector.enabled" if enabled else "connector.disabled"
    await _emitter.emit(event_name, {"connector_id": connector_id})
    return _connector_summary(connector_id)


@router.post("/{connector_id}/enable", dependencies=[Depends(_require_ui_header)])
async def enable_connector(connector_id: str) -> Dict[str, Any]:
    """Enable a previously-disabled MCP connector. Tools materialize live."""
    return await _set_connector_enabled(connector_id, True)


@router.post("/{connector_id}/disable", dependencies=[Depends(_require_ui_header)])
async def disable_connector(connector_id: str) -> Dict[str, Any]:
    """Disable a configured MCP connector without clearing its credentials."""
    return await _set_connector_enabled(connector_id, False)


@router.post("/{connector_id}/authorize", dependencies=[Depends(_require_ui_header)])
async def authorize(
    request: Request, connector_id: str, body: AuthorizeRequest
) -> Dict[str, Any]:
    """Start an OAuth PKCE flow. Returns {flow_id, authorization_url}.

    When ``grant_agents`` is present, the named agents are granted this
    connector (with their declared REQUIRED_CONNECTORS scopes) the moment the
    OAuth exchange succeeds — connecting a mailbox grants it to the email agent
    in the same flow (#2117). Scope resolution happens up front so an unknown
    or non-declaring agent fails the request before any browser step.
    """
    grant_map = _resolve_grant_scopes(request, connector_id, body.grant_agents)
    try:
        return await connections.start_authorization(
            connector_id, scopes=body.scopes, grant_agents=grant_map or None
        )
    except ConnectorsError as e:
        raise _raise_http_for(e) from e


# Keeps background device-poll tasks referenced so they aren't garbage
# collected mid-flight (asyncio holds only weak refs to bare tasks).
_device_poll_tasks: set = set()


@router.post(
    "/{connector_id}/authorize-device", dependencies=[Depends(_require_ui_header)]
)
async def authorize_device(
    request: Request, connector_id: str, body: AuthorizeRequest
) -> Dict[str, Any]:
    """Start a device-code flow (no browser redirect / no Azure app needed).

    Returns ``{user_code, verification_uri, expires_in, interval, message}`` for
    the UI to display. Unlike the browser flow there is no loopback callback, so
    the server kicks off a background poller; on completion ``poll_device_flow``
    emits ``connector.oauth.completed`` (and ``connection.connected``) over the
    same SSE stream the UI already watches, and this endpoint emits
    ``connector.oauth.error`` if it fails. ``grant_agents`` are resolved up front
    and committed atomically on success, mirroring ``authorize``.

    The ``device_code`` is intentionally NOT returned — it is a bearer-equivalent
    for polling and the server owns the poll loop.
    """
    grant_map = _resolve_grant_scopes(request, connector_id, body.grant_agents)
    try:
        info = await connections.start_device_flow(connector_id, scopes=body.scopes)
    except ConnectorsError as e:
        raise _raise_http_for(e) from e

    async def _poll_and_emit() -> None:
        try:
            # poll_device_flow emits connector.oauth.completed itself on success.
            await connections.poll_device_flow(
                connector_id,
                info["device_code"],
                scopes=info["scopes"],
                interval=info["interval"],
                expires_in=info["expires_in"],
                grant_agents=grant_map or None,
            )
        except ConnectorsError as e:
            await _emitter.emit(
                "connector.oauth.error",
                {"connector_id": connector_id, "error": str(e)},
            )
        except Exception as e:  # noqa: BLE001 — surface, don't crash the loop
            logger.exception("device-flow poll failed for %s", connector_id)
            await _emitter.emit(
                "connector.oauth.error",
                {"connector_id": connector_id, "error": str(e)},
            )

    task = asyncio.create_task(_poll_and_emit())
    _device_poll_tasks.add(task)
    task.add_done_callback(_device_poll_tasks.discard)

    return {
        "user_code": info["user_code"],
        "verification_uri": info["verification_uri"],
        "expires_in": info["expires_in"],
        "interval": info["interval"],
        "message": info["message"],
    }


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


# ─────────────────────────────────────────────────────────────────
# Activations endpoints (issue #1005)
#
# Activations gate MCP tool visibility (``MCPClientManager.tools_for_agent``).
# The PUT/DELETE routes accept ``mcp_server`` connectors only — OAuth
# connectors have no MCP tool surface and are rejected with HTTP 400 via
# ``_require_mcp_server``. The GET route is type-agnostic so frontends
# that fetch indiscriminately keep working (returns ``{}`` for OAuth).
# ─────────────────────────────────────────────────────────────────


@router.put(
    "/{connector_id}/activations/{agent_id:path}",
    dependencies=[Depends(_require_ui_header)],
)
async def put_activation(
    connector_id: str, agent_id: str, body: ActivationRequest
) -> Dict[str, Any]:
    """Activate ``agent_id`` for ``connector_id``.

    Auto-grants when no grant exists and ``body.scopes`` is provided.
    Returns 400 if no grant exists and the body omits scopes — the
    frontend must look up REQUIRED_CONNECTORS scopes for the agent and
    pass them in.
    """
    _require_mcp_server(connector_id)
    try:
        auto_granted = activate_connector_for_agent(
            connector_id, agent_id, scopes_for_grant=body.scopes
        )
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ConnectorsError as e:
        raise _raise_http_for(e) from e

    if auto_granted:
        # Auto-grant fired — also surface the grant change so subscribed
        # UIs refresh both panels.
        await _emitter.emit(
            "connector.grant.changed",
            {
                "connector_id": connector_id,
                "agent_id": agent_id,
                "scopes": list(body.scopes or []),
            },
        )
    # ``connector.activation.changed`` is emitted by ``api.activate`` so CLI,
    # SDK, and HTTP callers all notify through one path (#1226).
    return {
        "connector_id": connector_id,
        "agent_id": agent_id,
        "active": True,
        "auto_granted": auto_granted,
    }


@router.delete(
    "/{connector_id}/activations/{agent_id:path}",
    status_code=204,
    dependencies=[Depends(_require_ui_header)],
)
async def delete_activation(connector_id: str, agent_id: str) -> Response:
    """Deactivate ``agent_id`` for ``connector_id``. Idempotent.

    Non-destructive — the grant is preserved so a later re-activate is
    one click. To wipe the grant call ``DELETE
    /api/connectors/{id}/grants/{agent_id}`` instead.
    """
    # Route through ``api.deactivate`` so the MCP-only guard and the
    # ``connector.activation.changed`` emit fire on one path for all callers
    # (#1226). Previously this called the bare ledger function and emitted
    # inline, bypassing the guard.
    _require_mcp_server(connector_id)
    deactivate_connector_for_agent(connector_id, agent_id)
    return Response(status_code=204)


# ─────────────────────────────────────────────────────────────────
# Forwarded pre-authenticated connections — /v1/connections (#1292)
#
# A consuming app that ALREADY authenticated a user FORWARDS that connection
# to GAIA. GAIA persists it and acts on the mailbox as the host app's client —
# no second OAuth. Read routes return metadata only (secrets masked); the POST
# requires the X-Gaia-UI CSRF header; the whole surface is localhost-bound /
# tunnel-token-gated by the UI server.
# ─────────────────────────────────────────────────────────────────


@forwarded_router.post(
    "/{provider}", status_code=201, dependencies=[Depends(_require_ui_header)]
)
async def forward_connection(
    request: Request, provider: str, body: ForwardConnectionRequest
) -> Dict[str, Any]:
    """Persist a forwarded grant — no browser/consent step (AC1, AC3, AC4).

    Fails loudly: empty client_id/refresh_token → 422 (pydantic ``min_length``);
    insecure keyring backend → 500; missing required scopes → 403 +
    ``missing_scopes``. Returns a metadata-only summary — never the refresh
    token or client secret.

    Required scopes are resolved from the granted agents' ``REQUIRED_CONNECTORS``
    declarations (single source of truth). This means scope requirements
    auto-tighten as agents add new ``ConnectorRequirement`` entries — no
    duplication in the router.
    """
    required: set[str] = set()
    if body.grant_agents:
        registry = getattr(request.app.state, "agent_registry", None)
        if registry is None:
            raise HTTPException(
                status_code=503, detail="Agent registry not initialized"
            )
        by_nsid = {reg.namespaced_agent_id: reg for reg in registry.list()}
        unknown_agents = [nsid for nsid in body.grant_agents if nsid not in by_nsid]
        if unknown_agents:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "unknown_agent",
                    "agent_ids": unknown_agents,
                },
            )
        for nsid in body.grant_agents:
            reg = by_nsid[nsid]
            for cr in reg.required_connections:
                if cr.connector_id == provider:
                    required.update(cr.scopes)

    try:
        summary = connections.import_forwarded_connection(
            provider=provider,
            client_id=body.client_id,
            client_secret=body.client_secret,
            refresh_token=body.refresh_token,
            scopes=body.scopes,
            account_email=body.account_email,
            grant_agents=body.grant_agents,
            required_scopes=sorted(required),
        )
    except ConnectorsError as e:
        raise _raise_http_for(e) from e

    await _emitter.emit(
        "connector.configured",
        {"connector_id": provider, "account_id": summary.get("account_email")},
    )
    return summary


@forwarded_router.get("")
@forwarded_router.get("/")
async def list_forwarded_connections() -> Dict[str, Any]:
    """List persisted connections — metadata only, secrets omitted (AC4)."""
    return {"connections": connections.list_connections()}


@forwarded_router.get("/{provider}")
async def get_forwarded_connection(provider: str) -> Dict[str, Any]:
    """Return one connection's metadata, or 404 — secrets omitted (AC4)."""
    entry = connections.get_connection(provider)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=f"No connection for provider {provider!r}"
        )
    return entry


@forwarded_router.delete(
    "/{provider}", status_code=204, dependencies=[Depends(_require_ui_header)]
)
async def revoke_forwarded_connection(provider: str) -> Response:
    """Revoke a forwarded connection: clear the refresh token, the forwarded
    OAuth client credentials, every per-agent grant, and the cached provider /
    token state (AC4). Idempotent."""
    from gaia.connectors.grants import revoke_all_grants_for
    from gaia.connectors.providers import _registry as _provider_registry
    from gaia.connectors.store import clear_provider_credentials
    from gaia.connectors.tokens import _cache as _token_cache

    connections.revoke_connection(provider)
    clear_provider_credentials(provider)
    revoke_all_grants_for(provider)
    _provider_registry.pop(provider, None)
    for key in [k for k in _token_cache if k[0] == provider]:
        _token_cache.pop(key, None)

    await _emitter.emit("connector.disconnected", {"connector_id": provider})
    return Response(status_code=204)
