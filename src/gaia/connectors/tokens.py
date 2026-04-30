# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-provider access-token cache with double-checked locking and refresh.

Critical invariants (T-5b, plan amendments A6, A7):

- One ``asyncio.Lock`` per ``(provider, account_email)`` cache slot. The
  refresh path uses **explicit ``async with lock:`` (context-manager form)**
  so the lock is released on exception. Manual ``acquire``/``release``
  pairs are forbidden — they deadlock if a refresh raises.

- 60-second expiry buffer: a token whose ``expires_at`` is within the
  next 60 seconds is treated as already expired (AC4).

- Default ``expires_in = 3600`` if the token endpoint omits or returns
  zero (A6). Without this, the cache treats every token as immediately
  expired and refreshes on every call.

- Refresh-token rotation: if the token endpoint returns a new
  ``refresh_token`` in the response body, we persist it via
  ``store.save_connection``. The keyring's per-key atomic overwrite
  guarantees the new token is durably stored before we discard the old
  one in memory.

- One retry on ``401 invalid_token`` from the resource (clock skew).
  Bounded — no recursion, no loop, max 2 HTTP round-trips per call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

import httpx

from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionRevokedError,
    ConnectorsError,
)
from gaia.connectors.providers import get as get_provider
from gaia.connectors.store import (
    DEFAULT_ACCOUNT,
    delete_connection,
    load_connection,
    save_connection,
)

logger = logging.getLogger(__name__)


# 60s buffer per AC4: refresh proactively when the access token is within
# this many seconds of expiring. Prevents a tool from receiving a token
# that expires mid-API-call.
_EXPIRY_BUFFER_SECONDS = 60


@dataclass
class _AccessTokenCache:
    """Per-(provider, account) cache entry. Lock guards the refresh path."""

    access_token: Optional[str] = None
    expires_at: float = 0.0  # ``time.monotonic()``-based
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Module-level cache. Tests reset this between cases via the autouse
# fixture in ``tests/unit/connectors/conftest.py``.
_cache: dict[Tuple[str, str], _AccessTokenCache] = {}


def _cache_key(provider_id: str, account_email: str) -> Tuple[str, str]:
    return (provider_id, account_email)


def _is_expired(entry: _AccessTokenCache) -> bool:
    return entry.access_token is None or (
        entry.expires_at - time.monotonic() < _EXPIRY_BUFFER_SECONDS
    )


async def get_or_refresh(
    provider_id: str, *, account_email: str = DEFAULT_ACCOUNT
) -> str:
    """
    Return a fresh access token for ``provider_id``.

    Uses double-checked locking: the unlocked re-check inside the cache hit
    path keeps concurrent callers off the lock when the token is fresh; the
    second check inside the locked block prevents N+1 refreshes when 10
    callers race.
    """
    try:
        provider = get_provider(provider_id)
    except ConfigurationError:
        # Surfaced loudly to the caller; the router maps this to a 503.
        raise

    key = _cache_key(provider_id, account_email)
    entry = _cache.get(key)
    if entry is None:
        entry = _cache.setdefault(key, _AccessTokenCache())

    if not _is_expired(entry):
        return entry.access_token  # type: ignore[return-value]

    async with entry.lock:
        # Re-check inside the lock — a peer task may have refreshed
        # while we were waiting.
        if not _is_expired(entry):
            return entry.access_token  # type: ignore[return-value]

        # The store raises AuthRequiredError(REAUTH_REQUIRED) directly when
        # the client_id_hash tripwire fires; we let that propagate without
        # interpretation. ``None`` means the user never connected.
        stored = load_connection(
            provider_id,
            current_client_id_hash=provider.client_id_hash,
            account_email=account_email,
        )
        if stored is None:
            raise AuthRequiredError(
                AuthRequiredError.Reason.NOT_CONNECTED, provider=provider_id
            )

        new_access, new_refresh, expires_in = await _refresh_token(
            provider, stored["refresh_token"]
        )

        # Refresh-token rotation: if the provider returned a new refresh
        # token, persist it before exposing the access token to callers.
        if new_refresh and new_refresh != stored["refresh_token"]:
            save_connection(
                provider=provider_id,
                account_email=stored.get("account_email", DEFAULT_ACCOUNT),
                refresh_token=new_refresh,
                scopes=stored.get("scopes", []),
                client_id_hash=provider.client_id_hash,
                connected_at=stored.get("connected_at"),
            )

        entry.access_token = new_access
        entry.expires_at = time.monotonic() + expires_in
        return entry.access_token


async def _refresh_token(
    provider, refresh_token: str
) -> Tuple[str, Optional[str], int]:
    """
    Exchange a refresh token for a fresh access token.

    Returns ``(access_token, new_refresh_token_or_None, expires_in_seconds)``.
    Raises ``ConnectionRevokedError`` on ``invalid_grant``.
    """
    body = provider.refresh_request_body(refresh_token)

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(provider.token_url, data=body)

    if response.status_code == 400:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if payload.get("error") == "invalid_grant":
            # Clear the stored entry — the refresh token is no longer
            # accepted.
            delete_connection(provider.provider_id)
            raise ConnectionRevokedError(provider.provider_id)
        # Other 400s — actionable but not invalid_grant.
        raise ConnectorsError(
            f"Token endpoint refused refresh for {provider.provider_id}: "
            f"{payload.get('error', 'unknown')} (status 400). See "
            "docs/security/connections.mdx."
        )

    if response.status_code != 200:
        raise ConnectorsError(
            f"Token endpoint returned {response.status_code} for "
            f"{provider.provider_id} refresh. See "
            "docs/security/connections.mdx."
        )

    payload = response.json()
    access = payload.get("access_token")
    if not access:
        raise ConnectorsError(
            f"Token endpoint response for {provider.provider_id} omitted "
            "access_token. See docs/security/connections.mdx."
        )

    # A6: default expires_in to 3600 if absent or zero.
    expires_in = payload.get("expires_in") or 3600

    new_refresh = payload.get("refresh_token")
    return access, new_refresh, int(expires_in)


def get_or_refresh_sync(
    provider_id: str, *, account_email: str = DEFAULT_ACCOUNT
) -> str:
    """
    Synchronous wrapper around ``get_or_refresh`` for sync agent contexts.

    Must NOT be called from a thread that already has a running asyncio
    event loop — ``asyncio.run`` would raise ``RuntimeError``. Use
    ``await get_or_refresh(...)`` directly from async code instead. This
    guard makes the failure surface as an actionable error rather than a
    confusing crash deep inside the runtime.

    Inherits the calling thread's contextvars into the new event loop's
    context (via ``asyncio.run`` → ``contextvars.copy_context()``). This is
    the bridge from ``Agent.process_query`` (sync, runs in
    ``ThreadPoolExecutor``) to the async refresh code path. See
    ``tests/unit/connectors/test_agent_bridge.py``.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        raise RuntimeError(
            "get_or_refresh_sync was called from a thread with a running "
            "asyncio event loop. Call `await get_or_refresh(...)` directly "
            "from async code instead, or schedule this call on a worker "
            "thread without a running loop."
        )
    return asyncio.run(get_or_refresh(provider_id, account_email=account_email))
