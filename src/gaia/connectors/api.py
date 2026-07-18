# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Public coordination layer for ``gaia.connectors``.

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

from gaia.connectors.activation_watcher import note_local_write
from gaia.connectors.activations import (
    activate_agent,
    deactivate_agent,
    is_agent_active,
    list_agent_activations,
    load_activations,
)
from gaia.connectors.context import current_agent_id
from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectorsError,
    ScopeMismatchError,
)
from gaia.connectors.events import emit_change
from gaia.connectors.flow import (
    cancel_flow,
    complete_authorization,
    start_authorization,
)
from gaia.connectors.grants import (
    check_agent_grant,
    grant_agent,
    list_agent_grants,
    load_grants,
    revoke_agent_grant,
)
from gaia.connectors.providers import get as get_provider
from gaia.connectors.store import (
    DEFAULT_ACCOUNT,
    delete_connection,
)
from gaia.connectors.store import list_connections as _store_list
from gaia.connectors.store import (
    load_connection,
)
from gaia.connectors.tokens import get_or_refresh

logger = logging.getLogger(__name__)


# Per-provider minimum scopes a forwarded grant must cover when the caller
# passes no explicit ``required_scopes``. Authoritative validation is the
# caller's job (the UI router resolves the union from the granted agents'
# ``REQUIRED_CONNECTORS``); this map is a defense-in-depth default so a
# None-path forward never demands one provider's scopes of another. Unknown
# providers default to no requirement — the use-time gate in
# ``get_access_token`` still enforces per-agent scope coverage at the point an
# agent actually requests a token.
_DEFAULT_REQUIRED_SCOPES_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    # Built-in Email Triage Agent (#962) mailbox union.
    "google": (
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/calendar.events",
    ),
}


def _authorize_access(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str],
    account_email: str,
) -> None:
    """Run the two authorization gates that precede any token fetch.

    Raises ``AuthRequiredError`` (no grant / not connected / connection
    missing scopes) so the caller can prompt the user — never returns a
    partial/empty result. Shared by :func:`get_access_token` and
    :func:`get_access_token_with_expiry` so the grant + scope contract has one
    home and the two accessors cannot drift.

    Agent-id resolution order (per AC8 explicit opt-out clause):
      1. Explicit ``agent_id`` kwarg, if non-None.
      2. Active contextvar (``current_agent_id()``), set by the agent runtime.
      3. ``None``, which BYPASSES the per-agent grant check.
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
    prov = get_provider(provider)

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
         ``gaia connectors grants grant``.
      b. OAuth scopes — the stored connection's actual scopes must
         cover the requested ones; otherwise reconnect with the
         missing scopes.
    """
    _authorize_access(
        provider=provider,
        scopes=scopes,
        agent_id=agent_id,
        account_email=account_email,
    )
    # All checks passed — fetch (or refresh) the access token.
    return await get_or_refresh(provider, account_email=account_email)


async def get_access_token_with_expiry(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str] = None,
    account_email: str = DEFAULT_ACCOUNT,
) -> tuple[str, float]:
    """Grant-gated variant of :func:`get_access_token` that also returns expiry.

    Returns ``(access_token, wall_clock_expires_at)`` where ``expires_at`` is a
    ``time.time()``-based UNIX timestamp. This is the accessor the daemon's
    OAuth **forward-out** path (#2154) uses: it must hand the sidecar a
    short-lived access token *and* know when to re-forward, without duplicating
    either the grant/scope gate here or the refresh engine in ``tokens.py``.

    Same two authorization gates and loud-error contract as
    :func:`get_access_token`; the expiry is read from the token cache the
    refresh just populated (see ``tokens.get_token_with_expiry``).
    """
    _authorize_access(
        provider=provider,
        scopes=scopes,
        agent_id=agent_id,
        account_email=account_email,
    )
    from gaia.connectors.tokens import get_token_with_expiry

    return await get_token_with_expiry(provider, account_email=account_email)


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
    ``ThreadPoolExecutor`` worker thread).

    Must NOT be called from a thread that already has a running event
    loop. The runtime guard turns this into an actionable error rather
    than a confusing crash. Use ``await get_access_token(...)`` directly
    from async code.

    Submits the coroutine to the persistent connector event loop (see
    ``_loop.py``) and blocks with a bounded wait. The persistent loop avoids
    two bugs that ``asyncio.run`` caused (#1579): the cross-loop Lock error
    (Python ≤ 3.11) and the Windows ProactorEventLoop teardown hang on
    repeated create/destroy cycles.

    Contextvar propagation: the persistent-loop bridge captures
    ``copy_context()`` at submit time so the agent-id contextvar set by the
    agent runtime is visible inside the async refresh code.
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
    from gaia.connectors._loop import run_sync

    return run_sync(
        get_access_token(
            provider=provider,
            scopes=scopes,
            agent_id=agent_id,
            account_email=account_email,
        )
    )


def get_access_token_with_expiry_sync(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str] = None,
    account_email: str = DEFAULT_ACCOUNT,
) -> tuple[str, float]:
    """Synchronous wrapper around :func:`get_access_token_with_expiry`.

    Runs on the persistent connector event loop (see ``_loop.py``) and blocks,
    mirroring :func:`get_access_token_sync`. Used by the daemon's forward-out
    path, which runs synchronously inside the sidecar-registry ensure worker
    thread. Must NOT be called from a thread with a running event loop.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        raise RuntimeError(
            "get_access_token_with_expiry_sync was called from a thread with a "
            "running asyncio event loop. Call `await "
            "get_access_token_with_expiry(...)` directly from async code instead."
        )
    from gaia.connectors._loop import run_sync

    return run_sync(
        get_access_token_with_expiry(
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


def import_forwarded_connection(
    *,
    provider: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scopes: List[str],
    account_email: str = "",
    grant_agents: Optional[List[str]] = None,
    required_scopes: Optional[List[str]] = None,
    connected_at: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Persist a connection FORWARDED by a host app that already authenticated
    the user. No browser/consent step, no GAIA-run OAuth flow (#1292, Path A).

    The host app forwards the OAuth client it was issued under (``client_id``
    + ``client_secret``) and the user's ``refresh_token``. GAIA stores both and
    will later refresh AS THE HOST APP'S CLIENT — the connectors refresh engine
    is already client-neutral (the keyring-stored forwarded client beats the
    GAIA env client, and ``client_id_hash`` is recomputed from the forwarded
    id here).

    This is the single coordination point shared with the headless CLI import
    (#1084, not wired here). It mirrors what ``oauth_pkce.configure``'s
    "Save & Connect" path + ``flow._exchange_code_for_tokens`` do, minus the
    PKCE dance.

    Fails loudly (no fallbacks):
      - insecure keyring backend → ``ConnectorsError`` (via
        ``verify_keyring_backend``);
      - empty ``client_id`` / ``refresh_token`` → ``ConnectorsError``;
      - forwarded scopes don't cover ``required_scopes`` → ``ScopeMismatchError``.
        ``required_scopes is None`` falls back to the per-provider default
        (``_DEFAULT_REQUIRED_SCOPES_BY_PROVIDER``, empty for unknown providers);
        an explicit ``[]`` means "require nothing" at import time. GAIA cannot
        widen scope at refresh time, so a shortfall is unrecoverable without
        re-consent by the host app.

    Returns a metadata-only summary — NEVER the refresh token or client secret.
    """
    # Local imports keep the module-level dependency graph (and the lazy
    # keyring import contract in connectors/__init__.py) unchanged.
    from gaia.connectors.providers import _registry as _provider_registry
    from gaia.connectors.store import (
        save_connection,
        save_provider_credentials,
        verify_keyring_backend,
    )
    from gaia.connectors.tokens import _cache as _token_cache

    # 1. Insecure-keyring tripwire BEFORE any write (AC4). Raises loudly.
    verify_keyring_backend()

    # 2. Validate the forwarded grant up front so nothing is persisted on a
    #    bad input (the failure path must leave the keyring untouched).
    if not client_id:
        raise ConnectorsError(
            f"import_forwarded_connection({provider!r}): client_id is empty. "
            "The host app must forward the OAuth client_id it authenticated "
            "the user under. See docs/sdk/infrastructure/connectors.mdx."
        )
    if not refresh_token:
        raise ConnectorsError(
            f"import_forwarded_connection({provider!r}): refresh_token is empty. "
            "Forward the user's long-lived refresh_token (the host app must "
            "request offline access). See docs/sdk/infrastructure/connectors.mdx."
        )

    # 3. Scope coverage (AC3). Forwarded scopes must be a superset of what the
    #    agent needs — GAIA cannot add scope at refresh time. ``is not None`` so
    #    an explicit ``[]`` means "require nothing", not "use the default".
    if required_scopes is not None:
        required = list(required_scopes)
    else:
        required = list(_DEFAULT_REQUIRED_SCOPES_BY_PROVIDER.get(provider, ()))
    granted = set(scopes)
    missing = [s for s in required if s not in granted]
    if missing:
        raise ScopeMismatchError(
            required=required, granted=list(scopes), provider=provider
        )

    account = account_email or DEFAULT_ACCOUNT

    # 4. Persist the forwarded OAuth client → ``provider:<provider>`` slot.
    save_provider_credentials(
        provider, client_id=client_id, client_secret=client_secret
    )

    # 5. Evict the cached provider instance so the next ``get_provider`` reads
    #    the forwarded client and recomputes ``client_id_hash`` from it.
    _provider_registry.pop(provider, None)
    prov = get_provider(provider)

    # 6. Persist the connection (refresh_token + metadata) → ``<provider>:default``
    #    keyed by the forwarded client's hash so the tripwire passes coherently.
    save_connection(
        provider=provider,
        account_email=account,
        refresh_token=refresh_token,
        scopes=list(scopes),
        client_id_hash=prov.client_id_hash,
        connected_at=connected_at,
    )

    # 7. Evict any stale access-token cache entry so the next get_or_refresh
    #    refreshes against the forwarded client. The v1 store is single-slot
    #    (always keyed by DEFAULT_ACCOUNT), so evict both the display-account
    #    key and the DEFAULT_ACCOUNT key the refresh path actually reads.
    _token_cache.pop((provider, account), None)
    _token_cache.pop((provider, DEFAULT_ACCOUNT), None)

    # 8. Optionally grant the named agents the forwarded scopes so they can
    #    resolve the connection ambiently (no credentials on the request).
    granted_agents: List[str] = []
    for agent_id in grant_agents or []:
        grant_agent(provider, agent_id, list(scopes))
        granted_agents.append(agent_id)

    logger.info(
        "api: imported forwarded connection provider=%s account=%s scopes=%d "
        "grant_agents=%d client_id_hash=%s",
        provider,
        account,
        len(scopes),
        len(granted_agents),
        prov.client_id_hash,
    )

    return {
        "provider": provider,
        "account_email": account,
        "scopes": list(scopes),
        "connected_at": connected_at,
        "grant_agents": granted_agents,
        "forwarded": True,
    }


def _require_mcp_server_for_activation(connector_id: str) -> None:
    """Reject activation writes for non-MCP-server connectors (#1005).

    Activations gate MCP tool visibility (see
    ``MCPClientManager.tools_for_agent``). OAuth-only connectors have no
    MCP tool surface — per-agent access is governed by the per-scope grant
    ledger instead — so allowing a write here would create state nothing
    reads. Enforced at the orchestration layer so all callers (HTTP, CLI,
    SDK, future callers) get the same guarantee without duplicating the
    spec lookup at every boundary.

    Raises ``ConfigurationError`` with an actionable message. The router
    catches this and translates to HTTP 400; the CLI surfaces it as a
    non-zero exit with the message on stderr.
    """
    # Importing the catalog populates REGISTRY with the built-in specs.
    # Other CLI handlers (``_handle_list`` / ``_handle_configure`` / etc.)
    # do this explicitly; bare ``gaia connectors activations …`` did not
    # need it before this guard existed. Doing the import here protects
    # every caller — CLI, SDK, custom embedders — without each entry
    # point having to remember. Idempotent: Python's module cache makes
    # repeat imports a no-op, and tests that monkeypatch ``REGISTRY``
    # with a fresh instance see their substitute (the catalog only ever
    # mutates the original singleton bound at first-import time).
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.registry import REGISTRY

    try:
        spec = REGISTRY.get(connector_id)
    except KeyError as e:
        raise ConfigurationError(f"Unknown connector '{connector_id}'.") from e
    if spec.type != "mcp_server":
        raise ConfigurationError(
            f"Activations apply to MCP-server connectors only; "
            f"'{connector_id}' is type '{spec.type}'. Use per-agent grants "
            f"to control access for OAuth connectors."
        )


def activate(
    connector_id: str,
    agent_id: str,
    *,
    scopes_for_grant: Optional[List[str]] = None,
) -> bool:
    """
    Activate ``(connector_id, agent_id)`` and auto-grant if needed.

    The "one-click convenience" path from issue #1005: if no grant exists
    for the pair, this creates one using ``scopes_for_grant`` and then
    flips the activation bit. If ``scopes_for_grant`` is not provided AND
    no grant exists, raises ``ConfigurationError`` so the caller can
    surface an actionable message to the user.

    Returns True if a grant was auto-created, False otherwise (informative
    only — both paths complete the activation).

    Activations gate **MCP tool visibility**; grants gate **credential access**.
    Only ``mcp_server`` connectors accept activations — OAuth connectors
    have no MCP tool surface and their access is controlled entirely by
    grants. Calling this for an OAuth connector raises ``ConfigurationError``.
    See ``docs/sdk/infrastructure/connectors.mdx`` for the two-axis model.
    """
    _require_mcp_server_for_activation(connector_id)
    existing_scopes = list_agent_grants(connector_id).get(agent_id)
    auto_granted = False
    if existing_scopes is None:
        if scopes_for_grant is None:
            raise ConfigurationError(
                f"Cannot activate connector '{connector_id}' for agent "
                f"'{agent_id}': no grant exists and no scopes provided "
                "for auto-grant. Either pass --scopes explicitly or "
                "register the agent with a REQUIRED_CONNECTORS entry "
                "for this connector."
            )
        grant_agent(connector_id, agent_id, list(scopes_for_grant))
        auto_granted = True
        logger.info(
            "api: auto-granted scopes for activation connector_id=%s "
            "agent_id=%s scopes=%d",
            connector_id,
            agent_id,
            len(scopes_for_grant),
        )
    activate_agent(connector_id, agent_id)
    emit_change(
        "connector.activation.changed",
        {"connector_id": connector_id, "agent_id": agent_id, "active": True},
    )
    note_local_write(connector_id, agent_id, True)
    logger.info(
        "api: activated connector_id=%s agent_id=%s (auto_granted=%s)",
        connector_id,
        agent_id,
        auto_granted,
    )
    return auto_granted


def deactivate(connector_id: str, agent_id: str) -> None:
    """
    Deactivate ``(connector_id, agent_id)``.

    Only valid for ``mcp_server`` connectors — see :func:`activate` for
    the rationale. OAuth connectors raise ``ConfigurationError``.

    Non-destructive — the grant survives so a later re-activate is one
    click without re-consent. To wipe both, call
    :func:`gaia.connectors.grants.revoke_agent_grant` separately.
    """
    _require_mcp_server_for_activation(connector_id)
    deactivate_agent(connector_id, agent_id)
    emit_change(
        "connector.activation.changed",
        {"connector_id": connector_id, "agent_id": agent_id, "active": False},
    )
    note_local_write(connector_id, agent_id, False)


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


def connected_mailbox_providers() -> list[str]:
    """Return ids of OAuth PKCE connectors that have a stored connection.

    "Connected" means the user completed the OAuth flow and a connection blob
    exists in the keyring — regardless of any per-agent grant. The grant gate
    fires later at token-fetch time and raises loudly there if needed.

    Returns ids in registry order (google before microsoft). Only
    ``oauth_pkce`` connectors are considered; MCP-server connectors have no
    mailbox surface and are excluded.

    Read-only; never returns secrets.
    """
    # Importing the catalog populates REGISTRY with the built-in specs (google,
    # microsoft, mcp_servers). Idempotent: Python's module cache makes repeat
    # imports a no-op. Mirrors the pattern in _require_mcp_server_for_activation.
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.registry import REGISTRY
    from gaia.connectors.store import peek_connection

    result: list[str] = []
    for spec in REGISTRY.all():
        if spec.type != "oauth_pkce":
            continue
        if peek_connection(spec.id) is not None:
            result.append(spec.id)
    return result


__all__ = [
    "activate",
    "activate_agent",
    "cancel_flow",
    "complete_authorization",
    "connected_mailbox_providers",
    "deactivate",
    "deactivate_agent",
    "get_access_token",
    "get_access_token_sync",
    "get_access_token_with_expiry",
    "get_access_token_with_expiry_sync",
    "get_connection",
    "grant_agent",
    "import_forwarded_connection",
    "is_agent_active",
    "list_agent_activations",
    "list_agent_grants",
    "list_connections",
    "load_activations",
    "load_grants",
    "revoke_agent_grant",
    "revoke_connection",
    "start_authorization",
    "tripwire_check",
]
