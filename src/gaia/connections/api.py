# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Public coordination layer for ``gaia.connections``.

Each public function here is a thin orchestration over the per-module
primitives:

- ``start_authorization`` / ``complete_authorization`` → ``flow.py``
- ``get_access_token`` / ``get_access_token_sync`` → ``tokens.py`` +
  per-agent grant check via ``grants.py``
- ``list_connections`` / ``get_connection`` / ``revoke_connection`` →
  ``store.py``
- ``grant_agent`` / ``revoke_agent_grant`` / ``list_agent_grants`` →
  ``grants.py``
- ``tripwire_check`` → ``store.load_connection`` for every known provider

This is the only file that combines tokens with grants — the per-module
primitives are deliberately decoupled.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from gaia.connections.context import current_agent_id
from gaia.connections.errors import (
    AuthRequiredError,
    ConfigurationError,
    ScopeMismatchError,
)
from gaia.connections.flow import (
    cancel_flow,
    complete_authorization,
    start_authorization,
)
from gaia.connections.grants import (
    check_agent_grant,
    grant_agent,
    list_agent_grants,
    load_grants,
    revoke_agent_grant,
)
from gaia.connections.providers import get as get_provider
from gaia.connections.store import (
    DEFAULT_ACCOUNT,
    delete_connection,
)
from gaia.connections.store import list_connections as _store_list
from gaia.connections.store import (
    load_connection,
)
from gaia.connections.tokens import get_or_refresh, get_or_refresh_sync

logger = logging.getLogger(__name__)


async def get_access_token(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str] = None,
    account_email: str = DEFAULT_ACCOUNT,
) -> str:
    """
    Return a short-lived bearer access token for ``provider``.

    Agent-id resolution order (per AC8 explicit opt-out clause):
      1. Explicit ``agent_id`` kwarg, if non-None.
      2. Active contextvar (``current_agent_id()``), set by the agent runtime.
      3. ``None``, which BYPASSES the per-agent grant check.

    The contextvar path is the production path: ``Agent.process_query``
    enters ``_agent_context(self.namespaced_agent_id)`` before invoking
    tools. The kwarg path is for SDK callers who manage their own
    identity, and the None path is for CLI/debug callers.

    Two layers of authorization gate the call:
      a. Per-agent grant — the user must have explicitly granted this
         agent the required scopes via Settings → Connections, or
         ``gaia connections grants grant``.
      b. OAuth scopes — the stored connection's actual scopes must
         cover the requested ones; otherwise reconnect with the
         missing scopes.
    """
    resolved_agent = agent_id if agent_id is not None else current_agent_id()

    # Eager check for per-agent grant — surface the error BEFORE any
    # network round-trip so the caller can prompt the user immediately.
    if resolved_agent is not None:
        if not check_agent_grant(provider, resolved_agent, scopes):
            raise AuthRequiredError(
                AuthRequiredError.Reason.AGENT_NOT_GRANTED,
                provider=provider,
                agent_id=resolved_agent,
                missing_scopes=scopes,
            )

    # Eager check for OAuth scope coverage — once we know the agent is
    # granted, look at what the underlying OAuth connection actually
    # carries. The store load also fires the client_id_hash tripwire.
    try:
        prov = get_provider(provider)
    except ConfigurationError:
        raise

    stored = load_connection(
        provider,
        current_client_id_hash=prov.client_id_hash,
        account_email=account_email,
    )
    if stored is None:
        raise AuthRequiredError(
            AuthRequiredError.Reason.NOT_CONNECTED, provider=provider
        )
    granted_scopes = set(stored.get("scopes", []))
    missing = [s for s in scopes if s not in granted_scopes]
    if missing:
        raise AuthRequiredError(
            AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES,
            provider=provider,
            agent_id=resolved_agent,
            missing_scopes=missing,
        )

    # All checks passed — fetch (or refresh) the access token.
    return await get_or_refresh(provider, account_email=account_email)


def get_access_token_sync(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str] = None,
    account_email: str = DEFAULT_ACCOUNT,
) -> str:
    """
    Synchronous wrapper around ``get_access_token``.

    Used by sync agent tool bodies (``Agent.process_query`` runs in a
    ``ThreadPoolExecutor`` worker thread). ``asyncio.run`` inherits the
    calling thread's contextvars into the new event loop's context, so
    the agent-id contextvar set by the agent runtime is visible to the
    async refresh code.

    Must NOT be called from a thread that already has a running event
    loop — ``asyncio.run`` would raise ``RuntimeError``. The runtime
    guard turns this into an actionable error rather than a confusing
    crash. Use ``await get_access_token(...)`` directly from async code.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        raise RuntimeError(
            "get_access_token_sync was called from a thread with a running "
            "asyncio event loop. Call `await get_access_token(...)` "
            "directly from async code instead, or schedule this call on a "
            "worker thread without a running loop."
        )
    return asyncio.run(
        get_access_token(
            provider=provider,
            scopes=scopes,
            agent_id=agent_id,
            account_email=account_email,
        )
    )


def list_connections() -> List[Dict[str, Any]]:
    """
    Return all stored connections as a list of summary dicts.

    Each entry: ``{provider, account_email, scopes, connected_at}``.
    Refresh tokens are NEVER included in the return value — only the
    metadata callers need to display "Connected as <email>".
    """
    out: List[Dict[str, Any]] = []
    for provider in _store_list():
        try:
            prov = get_provider(provider)
        except ConfigurationError:
            # Provider configured to point at this store but the env
            # var isn't set right now. Surface the row with a
            # configuration warning rather than hide it.
            out.append(
                {
                    "provider": provider,
                    "account_email": "",
                    "scopes": [],
                    "connected_at": None,
                    "error": "configuration",
                }
            )
            continue
        try:
            blob = load_connection(provider, current_client_id_hash=prov.client_id_hash)
        except AuthRequiredError:
            # Tripwire fired — the entry has been cleared. Skip.
            continue
        if blob is None:
            continue
        out.append(
            {
                "provider": provider,
                "account_email": blob.get("account_email"),
                "scopes": blob.get("scopes", []),
                "connected_at": blob.get("connected_at"),
            }
        )
    return out


def get_connection(provider: str) -> Optional[Dict[str, Any]]:
    """Return one connection's metadata, or None if missing."""
    for entry in list_connections():
        if entry["provider"] == provider:
            return entry
    return None


def revoke_connection(provider: str) -> None:
    """Remove the stored connection for ``provider``. Idempotent."""
    delete_connection(provider)
    logger.info("api: revoked connection provider=%s", provider)


def tripwire_check() -> None:
    """
    Iterate every known provider and call ``load_connection`` to fire
    the tripwire eagerly at startup. Exceptions from individual
    providers are logged but do not abort the sweep.
    """
    for provider_id in _store_list():
        try:
            prov = get_provider(provider_id)
        except ConfigurationError as e:
            logger.warning("tripwire: provider %s misconfigured: %s", provider_id, e)
            continue
        try:
            load_connection(provider_id, current_client_id_hash=prov.client_id_hash)
        except AuthRequiredError:
            # Tripwire fired — load_connection already cleared the
            # entry; nothing else to do here.
            logger.info("tripwire: provider %s entry cleared by tripwire", provider_id)
        except Exception as e:
            logger.warning("tripwire: provider %s check failed: %s", provider_id, e)


__all__ = [
    "cancel_flow",
    "complete_authorization",
    "get_access_token",
    "get_access_token_sync",
    "get_connection",
    "grant_agent",
    "list_agent_grants",
    "list_connections",
    "load_grants",
    "revoke_agent_grant",
    "revoke_connection",
    "start_authorization",
    "tripwire_check",
]
