# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared "does this connector already have state a blind reconnect could
silently destroy" predicate (#2730 D0).

One definition, used by every connect/authorize/configure entry point that
must decide whether an empty scope request is a genuine first-time connect
(safe to fall back to the provider's default scopes) or a reconnect of
something that already carries consent (which must fail loudly instead of
guessing). Checking connection-existence and grant-existence independently
at each call site is how a connected-but-not-yet-granted account raised at
one path and silently narrowed at another.
"""

from __future__ import annotations

import logging
from typing import Iterable, List

logger = logging.getLogger(__name__)


def has_prior_state(provider_id: str) -> bool:
    """True when *provider_id* already has a stored connection OR any agent
    grant.

    Either one alone is enough: a connection with no grant yet is still
    consented state a silent scope substitution would blow away, and a
    grant surviving a revoked/cleared connection is state a reconnect must
    not silently narrow either.
    """
    from gaia.connectors.grants import load_grants
    from gaia.connectors.store import peek_connection

    if peek_connection(provider_id) is not None:
        return True
    return bool(load_grants().get(provider_id))


def _current_scope_count(provider_id: str) -> int:
    """Best-effort size of the scope set *provider_id* currently carries,
    across its stored connection and every agent's grant — used only to make
    the D0 rejection message concrete, never to decide anything."""
    from gaia.connectors.grants import load_grants
    from gaia.connectors.store import peek_connection

    current: set = set()
    conn = peek_connection(provider_id)
    if conn:
        current.update(conn.get("scopes", []))
    for scopes in load_grants().get(provider_id, {}).values():
        current.update(scopes)
    return len(current)


def resolve_or_reject_empty_scopes(
    provider_id: str, requested_scopes: Iterable[str], default_scopes: Iterable[str]
) -> List[str]:
    """Replace ``list(scopes) or list(provider.default_scopes)`` at every
    (re)connect entry point (#2730 D0).

    An empty *requested_scopes* used to silently fall back to the provider's
    identity-only *default_scopes* — including on a RECONNECT of a provider
    that already carries real mailbox scopes, gutting the connection with no
    warning. Now: an empty request against a provider with prior state (an
    existing connection or agent grant) is a loud, actionable error instead
    of a guess. An empty request with no prior state is a genuine first-time
    connect, so the ``default_scopes`` fallback still applies there — logged,
    not silent.
    """
    from gaia.connectors.errors import ConnectorsError

    scopes_list = list(requested_scopes)
    if scopes_list:
        return scopes_list

    if has_prior_state(provider_id):
        raise ConnectorsError(
            f"Reconnecting {provider_id!r} with no scopes would drop the "
            f"{_current_scope_count(provider_id)} scope(s) this connection "
            "currently carries. Specify the full scope list explicitly, "
            "e.g.:\n"
            f"  gaia connectors connect {provider_id} --scopes <scope> ... "
            "--grant-agent <agent-id>\n"
            "See docs/sdk/infrastructure/connections.mdx."
        )

    default_list = list(default_scopes)
    logger.info(
        "connectors: no scopes requested and no prior state for %s; falling "
        "back to default_scopes (%d)",
        provider_id,
        len(default_list),
    )
    return default_list


__all__ = ["has_prior_state", "resolve_or_reject_empty_scopes"]
