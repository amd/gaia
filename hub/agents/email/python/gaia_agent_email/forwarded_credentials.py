# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Forwarded-credential store for the email sidecar (issue #2154 / V2-14).

Role inversion (design §0.6): under the Agent UI daemon deployment the sidecar
does NOT read the machine keyring/grants store. Instead the daemon (custody
home) owns the long-lived OAuth refresh token and forwards SHORT-LIVED access
tokens OUT to the sidecar's ``/v1/connections/{provider}`` intake. This module
is where those forwarded tokens live — **in memory only**, never persisted, and
never carrying a refresh token or OAuth client secret.

Two runtime modes, selected by ``GAIA_EMAIL_FORWARDED_CREDENTIALS``:

- **Forwarded mode** (env set to a truthy value — the daemon sets it on spawn):
  token resolvers read from this in-memory store. A missing / expired /
  scope-short forwarded token is a LOUD, actionable error — never a silent fall
  back to the keyring (that would defeat the whole point of forward-out).
- **Standalone mode** (env unset — a developer/integrator running the sidecar
  with its own OAuth): resolvers use the normal grant-checked connectors path.
  This preserves the standalone-integrator posture the playground relies on.

Fail loudly (CLAUDE.md): every read that cannot be satisfied raises
:class:`gaia.connectors.errors.ConnectorsError` so the agent's existing
per-mailbox ``except ConnectorsError`` handler surfaces a clean, actionable
notice instead of an empty inbox.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from gaia.connectors.errors import ConnectorsError
from gaia.logger import get_logger

logger = get_logger(__name__)

# The private env channel the daemon (AgentSidecarManager) sets on spawn. MUST
# equal ``gaia.daemon.sidecars.spec._EMAIL_FORWARDED_MODE_ENV_VAR`` — the daemon
# keeps a literal copy so core never imports this hub wheel.
FORWARDED_MODE_ENV_VAR = "GAIA_EMAIL_FORWARDED_CREDENTIALS"

# Treat a token within this many seconds of expiry as already expired, so a
# resolver never hands a tool a token that dies mid-API-call. Mirrors the
# connectors refresh buffer intent; the daemon re-forwards before this window.
_EXPIRY_BUFFER_SECONDS = 30.0


@dataclass(frozen=True)
class _ForwardedCredential:
    """One provider's forwarded access token + metadata. No refresh token."""

    access_token: str
    scopes: frozenset
    expires_at: float  # wall-clock UNIX seconds
    account_email: str = ""


# Process-wide store. The sidecar is single-process; a lock still guards the
# map so the daemon's intake POST (a request-handler thread) and a tool's read
# (an agent worker thread) never race on it.
_store: Dict[str, _ForwardedCredential] = {}
_lock = threading.Lock()


def is_forwarding_enabled() -> bool:
    """True when the daemon booted this sidecar in forwarded-credentials mode."""
    return (os.environ.get(FORWARDED_MODE_ENV_VAR) or "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    )


def set_forwarded(
    provider: str,
    *,
    access_token: str,
    scopes: List[str],
    expires_at: float,
    account_email: str = "",
) -> _ForwardedCredential:
    """Store a forwarded access token for ``provider`` (daemon intake path).

    Loud on a malformed forward — an empty token or a non-positive expiry means
    the daemon sent something unusable, and storing it would only defer the
    failure to an opaque 401 deep in a mailbox call.
    """
    if not access_token:
        raise ConnectorsError(
            f"forwarded connection for '{provider}' carried an empty access "
            "token. The daemon must forward a non-empty short-lived token; this "
            "is a forward-out bug, not a user-fixable state."
        )
    if expires_at <= 0:
        raise ConnectorsError(
            f"forwarded connection for '{provider}' carried a non-positive "
            f"expires_at ({expires_at}). The daemon must forward the token's "
            "wall-clock expiry so the sidecar knows when to expect a re-forward."
        )
    cred = _ForwardedCredential(
        access_token=access_token,
        scopes=frozenset(scopes or ()),
        expires_at=float(expires_at),
        account_email=account_email or "",
    )
    with _lock:
        _store[provider] = cred
    logger.info(
        "forwarded-credentials: stored '%s' token (%d scopes, expires_at=%.0f)",
        provider,
        len(cred.scopes),
        cred.expires_at,
    )
    return cred


def withdraw(provider: str) -> bool:
    """Drop a forwarded credential (daemon withdrawal / revocation). Idempotent.

    Returns True when an entry was removed, False when there was nothing to drop.
    """
    with _lock:
        existed = _store.pop(provider, None) is not None
    if existed:
        logger.info("forwarded-credentials: withdrew '%s' token", provider)
    return existed


def peek(provider: str) -> Optional[_ForwardedCredential]:
    """Return the stored credential for ``provider`` (metadata + token) or None."""
    with _lock:
        return _store.get(provider)


def list_forwarded() -> List[dict]:
    """Metadata-only view of the forwarded credentials. NEVER returns tokens."""
    with _lock:
        items = list(_store.items())
    now = time.time()
    return [
        {
            "provider": provider,
            "scopes": sorted(cred.scopes),
            "account_email": cred.account_email or None,
            "expires_at": cred.expires_at,
            "expired": (cred.expires_at - now) < _EXPIRY_BUFFER_SECONDS,
        }
        for provider, cred in items
    ]


def get_forwarded_token(provider: str, scopes: List[str]) -> str:
    """Return the forwarded access token for ``provider`` covering ``scopes``.

    Raises :class:`ConnectorsError` (loud, actionable) when no token has been
    forwarded, the forwarded token has expired without a re-forward, or it does
    not cover the requested scopes. NEVER returns an empty/placeholder token and
    NEVER falls back to the keyring.
    """
    cred = peek(provider)
    if cred is None:
        raise ConnectorsError(
            f"no forwarded '{provider}' credential is available to the email "
            "sidecar. The connection may not be granted to this agent, or it was "
            "revoked/withdrawn. Connect and grant it in one command — no Agent UI "
            f"required: `gaia connectors connect {provider} --scopes <scopes> "
            "--grant-agent installed:email`, or use Settings -> Connections in "
            "the Agent UI. The daemon forwards a token on the next use."
        )
    if (cred.expires_at - time.time()) < _EXPIRY_BUFFER_SECONDS:
        raise ConnectorsError(
            f"the forwarded '{provider}' access token has expired and the daemon "
            "has not re-forwarded a fresh one yet. Retry in a moment; if it "
            f"persists, reconnect with `gaia connectors connect {provider} "
            "--scopes <scopes>` (or Settings -> Connections in the Agent UI)."
        )
    missing = [s for s in scopes if s not in cred.scopes]
    if missing:
        raise ConnectorsError(
            f"the forwarded '{provider}' token does not cover the required "
            f"scopes {missing}. Reconnect with those scopes in one command: "
            f"`gaia connectors connect {provider} --scopes {' '.join(missing)} "
            f"--grant-agent installed:email` (or Settings -> Connections in the "
            "Agent UI) so the daemon can forward a token that covers them."
        )
    return cred.access_token


def resolve_access_token(
    provider: str, scopes: List[str], *, live_fetch: Callable[[], str]
) -> str:
    """Return an access token for ``provider`` honoring the runtime mode.

    Forwarded mode → the daemon-forwarded token (loud on missing/expired/short).
    Standalone mode → ``live_fetch()`` (the existing grant-checked connectors
    path), so a sidecar with its own OAuth keeps working unchanged.
    """
    if is_forwarding_enabled():
        return get_forwarded_token(provider, scopes)
    return live_fetch()


def reset() -> None:
    """Clear the store (test-isolation seam)."""
    with _lock:
        _store.clear()


__all__ = [
    "FORWARDED_MODE_ENV_VAR",
    "is_forwarding_enabled",
    "set_forwarded",
    "withdraw",
    "peek",
    "list_forwarded",
    "get_forwarded_token",
    "resolve_access_token",
    "reset",
]
