# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
ConnectorHandler Protocol and get_credential dispatcher.

Every connector type (``oauth_pkce``, ``mcp_server``) implements the
``ConnectorHandler`` structural Protocol. The dispatcher in this module
routes ``get_credential`` / ``configure`` / ``disconnect`` / ``test``
calls to the right handler without knowing about handler internals.

Handler registration happens in type-specific modules (``oauth_pkce.py``,
``mcp_server.py``) that call ``register_handler`` at import time. The
dispatcher is type-agnostic; adding a new type only requires:
  1. A new handler class that satisfies the Protocol
  2. A ``register_handler(type_key, HandlerClass)`` call on import

The per-agent grant check lives here (not in handlers) because it is
type-agnostic: every connector type gates ``get_credential`` on whether
the calling agent has been granted the required scopes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from gaia.connectors.context import current_agent_id
from gaia.connectors.errors import AuthRequiredError, ConnectorsError
from gaia.connectors.grants import check_agent_grant, list_agent_grants
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import ConnectorSpec, ConnectorType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ConnectorHandler Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ConnectorHandler(Protocol):
    """
    Structural protocol every connector-type handler must satisfy.

    Handlers are instantiated per-call (stateless) or as singletons — the
    dispatcher does not prescribe lifetime. Handlers must NOT perform blocking
    I/O on the event loop; wrap filesystem operations in ``asyncio.to_thread``.

    All methods receive the resolved ``ConnectorSpec`` so handlers can access
    the full catalog metadata (scopes, mcp_command, etc.) without coupling to
    the registry.
    """

    async def get_credential(
        self,
        spec: ConnectorSpec,
        *,
        required_scopes: Optional[List[str]] = None,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return credential dict appropriate for this connector type."""
        ...

    async def configure(
        self,
        spec: ConnectorSpec,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply configuration for this connector. Returns updated state."""
        ...

    async def disconnect(
        self,
        spec: ConnectorSpec,
        *,
        account_id: Optional[str] = None,
    ) -> None:
        """Remove stored credentials for this connector."""
        ...

    async def test(self, spec: ConnectorSpec) -> Dict[str, Any]:
        """Return ``{"ok": bool, "detail": str}`` health check."""
        ...


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_HANDLER_REGISTRY: Dict[str, ConnectorHandler] = {}


def register_handler(connector_type: ConnectorType, handler: ConnectorHandler) -> None:
    """
    Register a handler instance for a connector type.

    Called at import time by each type module (oauth_pkce.py, mcp_server.py).
    Raises ``ValueError`` on duplicate registration so accidental double-import
    is caught immediately.
    """
    if connector_type in _HANDLER_REGISTRY:
        raise ValueError(
            f"Handler for connector type {connector_type!r} is already registered. "
            f"Existing: {_HANDLER_REGISTRY[connector_type]!r}"
        )
    _HANDLER_REGISTRY[connector_type] = handler
    logger.debug("handler: registered type=%s handler=%r", connector_type, handler)


def _get_handler(spec: ConnectorSpec) -> ConnectorHandler:
    """Look up the handler for spec.type. Raises ConnectorsError if missing."""
    handler = _HANDLER_REGISTRY.get(spec.type)
    if handler is None:
        registered = sorted(_HANDLER_REGISTRY)
        raise ConnectorsError(
            f"No handler registered for connector type {spec.type!r} "
            f"(connector_id={spec.id!r}). Registered types: {registered!r}. "
            "Import the handler module before calling get_credential / configure."
        )
    return handler


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


async def get_credential(
    connector_id: str,
    *,
    agent_id: Optional[str] = None,
    required_scopes: Optional[List[str]] = None,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return the credential dict for ``connector_id``.

    Agent-id resolution order:
      1. Explicit ``agent_id`` kwarg, if non-None.
      2. Active contextvar (``current_agent_id()``), set by the agent runtime.
      3. ``None`` → grant check is SKIPPED (CLI/debug callers).

    If an agent_id is resolved AND ``required_scopes`` is provided, the
    per-agent grant is verified before calling the handler.
    """
    spec = REGISTRY.get(connector_id)
    resolved_agent = agent_id or current_agent_id()

    if resolved_agent and required_scopes:
        if not check_agent_grant(connector_id, resolved_agent, required_scopes):
            granted = set(list_agent_grants(connector_id).get(resolved_agent, []))
            missing = [s for s in required_scopes if s not in granted]
            raise AuthRequiredError(
                AuthRequiredError.Reason.AGENT_NOT_GRANTED,
                provider=connector_id,
                agent_id=resolved_agent,
                missing_scopes=missing,
            )

    handler = _get_handler(spec)
    return await handler.get_credential(
        spec,
        required_scopes=required_scopes,
        account_id=account_id,
    )


def get_credential_sync(
    connector_id: str,
    *,
    agent_id: Optional[str] = None,
    required_scopes: Optional[List[str]] = None,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sync wrapper for ``get_credential``.

    Uses the same running-loop guard pattern as ``get_access_token_sync`` in
    ``tokens.py``: raises ``RuntimeError`` if called from inside a running loop
    (callers should use ``await get_credential(...)`` instead).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        raise RuntimeError(
            "get_credential_sync() called from inside a running event loop. "
            "Use 'await get_credential(...)' instead."
        )
    return asyncio.run(
        get_credential(
            connector_id,
            agent_id=agent_id,
            required_scopes=required_scopes,
            account_id=account_id,
        )
    )


async def configure(
    connector_id: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Configure a connector. Returns updated state dict."""
    spec = REGISTRY.get(connector_id)
    handler = _get_handler(spec)
    return await handler.configure(spec, config)


async def disconnect(
    connector_id: str,
    *,
    account_id: Optional[str] = None,
) -> None:
    """Disconnect a connector (remove stored credentials)."""
    spec = REGISTRY.get(connector_id)
    handler = _get_handler(spec)
    await handler.disconnect(spec, account_id=account_id)


async def health_check(connector_id: str) -> Dict[str, Any]:
    """Run the health-check for a connector. Returns ``{"ok": bool, "detail": str}``."""
    spec = REGISTRY.get(connector_id)
    handler = _get_handler(spec)
    return await handler.test(spec)
