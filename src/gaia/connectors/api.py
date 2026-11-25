# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Public coordination layer for ``gaia.connectors``.

Each public function here is a thin orchestration over the per-module
primitives:

- ``start_authorization`` / ``complete_authorization`` → ``flow.py``
- ``get_access_token`` / ``get_access_token_sync`` → ``tokens.py`` +
  per-agent grant check via ``grants.py``
- ``list_accounts`` / ``set_default_account`` → ``accounts.py``
- ``list_connections`` / ``get_connection`` / ``revoke_connection`` →
  ``accounts.py`` + ``store.py``
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

from gaia.connectors import accounts as _accounts
from gaia.connectors._refresh_lock import held_account_sync, held_sync, locked_write
from gaia.connectors.accounts import Account
from gaia.connectors.activation_watcher import note_local_write
from gaia.connectors.activations import (
    activate_agent,
    deactivate_agent,
    is_agent_active,
    list_agent_activations,
    load_activations,
)
from gaia.connectors.context import agent_runtime_active, current_agent_id
from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionBusyError,
    ConnectorsError,
    NoDeclaredScopesError,
    ScopeMismatchError,
    ScopeNotAllowedError,
    UnknownAgentError,
)
from gaia.connectors.events import emit_change
from gaia.connectors.flow import (
    cancel_flow,
    complete_authorization,
    poll_device_flow,
    start_authorization,
    start_device_flow,
)
from gaia.connectors.grants import (
    check_agent_grant,
    grant_agent,
    list_agent_grants,
    load_grants,
    revoke_agent_grant,
)
from gaia.connectors.store import (
    UninspectableKeyringError,
    WeakKeyringError,
    connection_slot_in_use,
    keyring_read_refusal,
)
from gaia.connectors.store import list_connections as _store_list
from gaia.connectors.store import (
    peek_connection,
)
from gaia.connectors.tokens import get_or_refresh, load_current_connection

logger = logging.getLogger(__name__)


# What a forwarded mailbox connection must carry when the caller passes no
# explicit ``required_scopes``: at least ONE of these mail read scopes,
# narrowest first. Authoritative validation is the caller's job (the UI router
# resolves the union from the granted agents' ``REQUIRED_CONNECTORS``); this
# is a defense-in-depth default. It is any-of, not all-of, because a flagship
# connection made before it could write holds only ``gmail.readonly`` and must
# keep reading — demanding modify + send rejected every such forwarded
# connection. Unknown providers default to no requirement; the use-time gate
# in ``get_access_token`` still enforces per-agent coverage.
#
# Mirrors ``gaia.agents.tools._email.scopes.READ_CAPABLE_SCOPES`` — duplicated
# because this module never imports ``gaia.agents``; a drift test pins them.
_DEFAULT_READ_SCOPES_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "google": (
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ),
    "microsoft": (
        "https://graph.microsoft.com/Mail.Read",
        "https://graph.microsoft.com/Mail.ReadWrite",
    ),
}


def resolve_declared_scopes(
    registry: Any,
    connector_id: str,
    agent_ids: List[str],
    *,
    enforced_only: bool = False,
) -> Dict[str, List[str]]:
    """Resolve ``[namespaced_agent_id]`` -> ``{agent_id: declared_scopes}`` (#2603).

    Each agent's scopes come from its ``REQUIRED_CONNECTORS`` declaration for
    ``connector_id`` — the single source of truth both the CLI
    (``gaia connectors connect --grant-agent``) and the Agent UI router's
    ``/authorize``, ``/authorize-device``, and ``/configure`` endpoints read,
    so the two surfaces cannot drift (#2117's original resolver, hoisted here
    out of ``gaia.ui.routers.connectors`` so it is FastAPI-free).

    ``registry`` is duck-typed — any object exposing ``.list()`` that returns
    entries with ``.namespaced_agent_id`` and ``.required_connections``
    (``AgentRegistry`` shaped). This module never imports ``gaia.agents``.

    Fails loudly (no silent skips or truncation):
      - an unknown agent id -> ``UnknownAgentError``;
      - an agent that declares no scopes for ``connector_id`` -> raises
        ``NoDeclaredScopesError`` rather than ever falling back to the
        provider's ``default_scopes``;
      - a declared scope outside ``REGISTRY.get(connector_id).available_scopes``
        -> ``ScopeNotAllowedError`` naming the offending scope(s). Without
        this ceiling, an agent's own declaration would flow straight into the
        OAuth consent screen with no check against the catalog (#2603 C4) —
        the registry already namespaces against a malicious custom agent
        claiming a built-in's identity, but not against bad scope values.
        Skipped (no ceiling enforced) only when ``connector_id`` itself is
        not in the connector catalog — an unrelated failure surfaces later
        at the connector lookup that every caller already performs.

    ``enforced_only`` returns each requirement's ``required_scopes`` instead: what
    the agent cannot work without, rather than everything consent asks for.

    Returns ``{}`` when ``agent_ids`` is empty — no resolution needed.
    """
    if not agent_ids:
        return {}

    by_nsid = {reg.namespaced_agent_id: reg for reg in registry.list()}
    unknown = [nsid for nsid in agent_ids if nsid not in by_nsid]
    if unknown:
        raise UnknownAgentError(unknown)

    from gaia.connectors.registry import REGISTRY as _CONNECTOR_REGISTRY

    try:
        available: Optional[set] = set(
            _CONNECTOR_REGISTRY.get(connector_id).available_scopes
        )
    except KeyError:
        available = None

    resolved: Dict[str, List[str]] = {}
    for nsid in agent_ids:
        reg = by_nsid[nsid]
        scopes: set = set()
        for cr in reg.required_connections:
            if cr.connector_id == connector_id:
                scopes.update(cr.required_scopes if enforced_only else cr.scopes)
        if not scopes:
            raise NoDeclaredScopesError(nsid, connector_id)
        if available is not None:
            disallowed = sorted(scopes - available)
            if disallowed:
                raise ScopeNotAllowedError(nsid, connector_id, disallowed)
        resolved[nsid] = sorted(scopes)
    return resolved


def _authorize_access(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str],
    slot: str,
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
      3. ``None``, which BYPASSES the per-agent grant check — but ONLY outside
         an agent turn. Inside one, a missing identity is a dropped context,
         not an opt-out, so the request is refused (#915).
    """
    resolved_agent = agent_id if agent_id is not None else current_agent_id()

    if resolved_agent is None and agent_runtime_active():
        raise AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider=provider,
            agent_id=None,
            missing_scopes=list(scopes),
        )

    if resolved_agent is not None and not scopes:
        # ``check_agent_grant(provider, agent, [])`` is True vacuously, and the
        # connection-coverage check below is vacuous too — so a scope-less
        # request would return a FULL-capability token with the ledger never
        # consulted. That is the whole bypass this control exists to close,
        # reached by simply not saying what you want. Same guard
        # ``handler.get_credential`` applies; this is the OAuth twin of it.
        raise ConfigurationError(
            f"get_access_token({provider!r}) named no scopes, so the per-agent "
            f"grant for {resolved_agent!r} cannot be verified and no token will "
            "be issued. Pass the scopes the caller actually needs, e.g. "
            "scopes=['https://www.googleapis.com/auth/gmail.readonly']."
        )

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
    _, stored = load_current_connection(provider, slot=slot)
    if stored is None:
        raise AuthRequiredError(
            AuthRequiredError.Reason.NOT_CONNECTED, provider=provider
        )
    granted_scopes = set(stored.get("scopes", []))
    missing = [s for s in scopes if s not in granted_scopes]
    if missing:
        raise AuthRequiredError(
            AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES,
            # granted ∪ missing — NEVER just the missing subset. `--scopes`
            # REPLACES the connection's scopes rather than adding to them
            # (flow.py: `list(scopes) or list(provider.default_scopes)`),
            # so a remedy naming only the gap would authorize an account
            # that has lost everything it already had (#2730 D0). Do not
            # "simplify" this to `missing` alone — that reintroduces the bug.
            full_scopes=sorted(granted_scopes | set(missing)),
            provider=provider,
            agent_id=resolved_agent,
            missing_scopes=missing,
        )


async def get_access_token(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """
    Return a short-lived bearer access token for ``provider``.

    Agent-id resolution order (per AC8 explicit opt-out clause):
      1. Explicit ``agent_id`` kwarg, if non-None.
      2. Active contextvar (``current_agent_id()``), set by the agent runtime.
      3. ``None``, which BYPASSES the per-agent grant check — but ONLY outside
         an agent turn. Inside one, a missing identity is a dropped context,
         not an opt-out, so the request is refused (#915).

    An empty ``scopes`` list is refused whenever an agent id resolves: it would
    satisfy the grant check vacuously and hand back a full-capability token.

    The contextvar path is the production path: ``Agent.process_query``
    enters ``_agent_context(self.namespaced_agent_id)`` before invoking
    tools. The kwarg path is for SDK callers who manage their own
    identity, and the None path is for CLI/debug callers.

    ``account`` names which of the provider's accounts to use, by its stable
    account id or its address; ``None`` uses the provider's default account.
    A name that matches no stored account raises ``UnknownAccountError``.

    Two layers of authorization gate the call:
      a. Per-agent grant — the user must have explicitly granted this
         agent the required scopes via Settings → Connectors, or
         ``gaia connectors grants grant``.
      b. OAuth scopes — the stored connection's actual scopes must
         cover the requested ones; otherwise reconnect with the
         missing scopes.
    """
    slot = _accounts.resolve_slot(provider, account)
    _authorize_access(provider=provider, scopes=scopes, agent_id=agent_id, slot=slot)
    # All checks passed — fetch (or refresh) the access token.
    return await get_or_refresh(provider, slot=slot)


async def get_access_token_with_expiry(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str] = None,
    account: Optional[str] = None,
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
    slot = _accounts.resolve_slot(provider, account)
    _authorize_access(provider=provider, scopes=scopes, agent_id=agent_id, slot=slot)
    from gaia.connectors.tokens import get_token_with_expiry

    return await get_token_with_expiry(provider, slot=slot)


def get_access_token_sync(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str] = None,
    account: Optional[str] = None,
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
            account=account,
        )
    )


def get_access_token_with_expiry_sync(
    *,
    provider: str,
    scopes: List[str],
    agent_id: Optional[str] = None,
    account: Optional[str] = None,
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
            account=account,
        )
    )


def list_accounts(connector_id: str) -> List[Account]:
    """Every account stored for ``connector_id``, oldest first, with its status
    (``ok`` / ``reauth_required`` / ``error``) and whether it is the default.
    Never returns tokens. See :class:`gaia.connectors.accounts.Account`."""
    return _accounts.list_accounts(connector_id)


def set_default_account(connector_id: str, account: str) -> Account:
    """Make ``account`` (stable account id or address) the connector's default:
    the account ``account=None`` means everywhere in this API."""
    chosen = _accounts.set_default_account(connector_id, account)
    logger.info("api: set the default %s account", connector_id)
    return chosen


def _connection_row(provider: str, acct: Account) -> Optional[Dict[str, Any]]:
    """One connected account's metadata row, or None when it is not connected."""
    base = {
        "provider": provider,
        "account_id": acct.account_id,
        "is_default": acct.is_default,
    }
    try:
        _, blob = load_current_connection(provider, slot=acct.slot)
    except ConfigurationError:
        # Provider configured to point at this store but the env
        # var isn't set right now. Surface the row with a
        # configuration warning rather than hide it.
        return {
            **base,
            "account_email": "",
            "scopes": [],
            "connected_at": None,
            "error": "configuration",
        }
    except ConnectionBusyError as e:
        return {
            **base,
            "account_email": "",
            "scopes": [],
            "connected_at": None,
            "account_type": None,
            **(e.connection or {}),
            "error": "busy",
        }
    except AuthRequiredError:
        # Tripwire fired (hash OR tenant) — the entry has been cleared. Skip.
        return None
    if blob is None:
        return None
    return {
        **base,
        "account_id": blob.get("account_id") or acct.account_id,
        "account_email": blob.get("account_email"),
        "scopes": blob.get("scopes", []),
        "connected_at": blob.get("connected_at"),
        "account_type": blob.get("account_type"),
    }


def list_connections() -> List[Dict[str, Any]]:
    """
    Return every connected account as a list of summary dicts, one per account.

    Each entry: ``{provider, account_id, account_email, is_default, scopes,
    connected_at, account_type}``. ``account_id`` is the provider's stable id
    for the account (``"default"`` for an account connected before GAIA
    recorded ids, until its next token refresh). Refresh tokens are NEVER
    included in the return value.

    ``account_type`` is ``"personal"`` / ``"work"`` when the provider could
    derive it at connect time (Microsoft, from the id_token ``tid`` claim,
    #2466), and ``None`` otherwise — including for every Google connection,
    which has no equivalent notion.

    A row with ``"error": "busy"`` is a connection another GAIA operation is
    writing right now: it is kept, because it is not known to be gone, with
    its display metadata as last read and ``scopes`` empty, because scopes
    that may be about to change can't be trusted for a decision. Asking
    again shortly gives its real state.

    Accounts whose sign-in expired are not connections; :func:`list_accounts`
    lists them with their status.
    """
    out: List[Dict[str, Any]] = []
    for provider in _store_list():
        for acct in _accounts.list_accounts(provider):
            if acct.status != "ok":
                continue
            row = _connection_row(provider, acct)
            if row is not None:
                out.append(row)
    return out


def get_connection(
    provider: str, account: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return one connected account's metadata row (see :func:`list_connections`),
    or None if it is not connected. ``account`` is an account id or address;
    ``None`` is the provider's default account.

    A connection another GAIA operation is writing comes back as its busy row,
    never as None.
    """
    slot = _accounts.resolve_slot(provider, account)
    for acct in _accounts.list_accounts(provider):
        if acct.slot == slot:
            return _connection_row(provider, acct) if acct.status == "ok" else None
    return None


async def _revoke_one(
    provider: str, acct: Account, *, refusal: Optional[WeakKeyringError] = None
) -> Dict[str, Any]:
    from gaia.connectors.flow import (
        not_revoked_on_weak_keyring,
        nothing_to_revoke,
        revoke_provider_token,
    )

    if refusal is not None and connection_slot_in_use(provider, slot=acct.slot):
        result = not_revoked_on_weak_keyring(
            provider,
            uninspectable=isinstance(refusal, UninspectableKeyringError),
            account_type=acct.account_type,
        )
    elif refusal is not None:
        result = nothing_to_revoke(provider)
    else:
        result = await revoke_provider_token(
            provider, slot=acct.slot, account_type=acct.account_type
        )
    remaining = await locked_write(
        provider, _accounts.remove_account, provider, acct.slot
    )
    logger.info(
        "api: revoked one %s account revoke_supported=%s revoked_remotely=%s "
        "nothing_stored=%s remaining=%d",
        provider,
        result["revoke_supported"],
        result["revoked_remotely"],
        result.get("nothing_stored", False),
        remaining,
    )
    return {
        **result,
        "account_id": acct.account_id,
        "account_email": acct.email,
        "remaining_accounts": remaining,
    }


async def revoke_connection_async(
    provider: str,
    account: Optional[str] = None,
    *,
    all_accounts: bool = False,
) -> Dict[str, Any]:
    """
    Revoke one of ``provider``'s accounts with the provider (when it exposes a
    public revoke endpoint), then remove its stored token. Idempotent: with
    nothing stored it says so instead of claiming a revoke.

    ``account`` (account id or address; ``None`` = the default account) picks
    the account. Revoking one Google account cancels only that account's
    sign-in; the others keep working. ``all_accounts=True`` revokes every
    account and returns the first account's outcome fields combined with an
    ``accounts`` list of each one's result.

    Returns ``{"revoke_supported": bool, "revoked_remotely": bool,
    "revoke_attempted": bool, "nothing_stored": bool, "revoke_error": str | None,
    "account_id", "account_email", "remaining_accounts": int}`` (see
    ``flow.revoke_provider_token``). Never bare success for what was only a
    local keyring delete. Reporting
    a full revoke when the provider-side grant is still live was the
    literal #2591 bug this replaces. On a keyring that refuses reads (weak
    or uninspectable) the provider revoke is skipped, since it needs the
    token, and the account is still removed locally; ``revoke_error`` says
    where to revoke by hand. Per-agent grants are not touched here;
    disconnecting through the connector handler clears them once the last
    account is gone. Call this directly from async code already on an event
    loop; use ``revoke_connection`` (sync) otherwise.
    """
    if all_accounts and account is not None:
        raise ConnectorsError(
            "revoke_connection: name one account or pass all_accounts=True, "
            "not both."
        )
    # Revoking reads the token, which a weak keyring refuses; delete anyway.
    refusal = keyring_read_refusal()
    if all_accounts:
        stored = _accounts.accounts_for_delete(provider) or [_nothing_stored(provider)]
        results = [
            await _revoke_one(provider, acct, refusal=refusal) for acct in stored
        ]
        await locked_write(provider, _accounts.remove_all_accounts, provider)
        return _combine_revokes(results)
    slot = _accounts.resolve_slot(provider, account)
    match = [a for a in _accounts.accounts_for_delete(provider) if a.slot == slot]
    return await _revoke_one(
        provider, match[0] if match else _nothing_stored(provider), refusal=refusal
    )


def _nothing_stored(provider: str) -> Account:
    """The account a revoke of an empty connector acts on: nothing, idempotently."""
    return Account(
        connector_id=provider,
        account_id=_accounts.UNIDENTIFIED,
        email="",
        status="error",
        is_default=True,
        slot=_accounts.LEGACY_SLOT,
    )


def _combine_revokes(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One outcome for several revokes: supported only if every account was,
    revoked only if a remote revoke succeeded and every account is either
    revoked or had nothing stored, and the first error any of them hit."""
    errors = [r["revoke_error"] for r in results if r.get("revoke_error")]
    any_revoked = any(r["revoked_remotely"] for r in results)
    all_resolved = all(
        r["revoked_remotely"] or r.get("nothing_stored", False) for r in results
    )
    return {
        "revoke_supported": all(r["revoke_supported"] for r in results),
        "revoked_remotely": any_revoked and all_resolved,
        "revoke_attempted": any(r.get("revoke_attempted", False) for r in results),
        "nothing_stored": bool(results)
        and all(r.get("nothing_stored", False) for r in results),
        "revoke_error": errors[0] if errors else None,
        "remaining_accounts": 0,
        "accounts": results,
    }


def revoke_connection(
    provider: str,
    account: Optional[str] = None,
    *,
    all_accounts: bool = False,
) -> Dict[str, Any]:
    """Synchronous wrapper around :func:`revoke_connection_async`.

    Must NOT be called from a thread with a running asyncio event loop —
    call ``await revoke_connection_async(...)`` directly from async code
    instead (mirrors ``get_access_token_sync``'s guard).
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        raise RuntimeError(
            "revoke_connection was called from a thread with a running "
            "asyncio event loop. Call `await revoke_connection_async(...)` "
            "directly from async code instead."
        )
    from gaia.connectors._loop import run_sync

    return run_sync(
        revoke_connection_async(provider, account, all_accounts=all_accounts)
    )


def import_forwarded_connection(
    *,
    provider: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scopes: List[str],
    account_email: str = "",
    account_type: Optional[str] = None,
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
      - a forwarded scope outside the connector's catalog ceiling, when
        ``grant_agents`` is set → ``ScopeNotAllowedError``;
      - forwarded scopes don't cover ``required_scopes`` → ``ScopeMismatchError``.
        ``required_scopes is None`` falls back to the per-provider default: a
        mailbox connection must carry at least one mail read scope
        (``_DEFAULT_READ_SCOPES_BY_PROVIDER``; no requirement for other
        providers). An explicit ``[]`` means "require nothing" at import time. GAIA cannot
        widen scope at refresh time, so a shortfall is unrecoverable without
        re-consent by the host app;
      - a keyring write that fails partway → the previous client config and
        connection are put back before the error propagates (and a failed
        put-back raises a ``ConnectorsError`` naming both failures).

    Returns a metadata-only summary — NEVER the refresh token or client secret.
    """
    # Local imports keep the module-level dependency graph (and the lazy
    # keyring import contract in connectors/__init__.py) unchanged.
    from gaia.connectors.store import (
        forward_recovery_slot,
        recover_forward_recovery,
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
    granted = set(scopes)
    if required_scopes is not None:
        required = list(required_scopes)
        missing = [s for s in required if s not in granted]
        if missing:
            raise ScopeMismatchError(
                required=required, granted=list(scopes), provider=provider
            )
    else:
        read_scopes = _DEFAULT_READ_SCOPES_BY_PROVIDER.get(provider, ())
        if read_scopes and not granted & set(read_scopes):
            raise ScopeMismatchError(
                # The narrowest one, so the remedy never asks for more.
                required=[read_scopes[0]],
                granted=list(scopes),
                provider=provider,
                message=(
                    f"The forwarded {provider} connection carries no mail read "
                    f"scope; it needs one of: {', '.join(read_scopes)}. GAIA "
                    "cannot add a scope at refresh time, so the host app must "
                    "re-consent the user with one of them, then forward again. "
                    "See docs/sdk/infrastructure/connectors.mdx."
                ),
            )

    # 3b. Grant ceiling (#915). Step 8 hands these scopes to ``grant_agent``,
    #     which refuses any the connector never advertised — check it here so a
    #     rejected forward still leaves the keyring untouched, per the
    #     up-front-validation contract above.
    if grant_agents:
        from gaia.connectors.grants import resolve_spec

        outside = sorted(set(scopes) - set(resolve_spec(provider).scope_ceiling()))
        if outside:
            raise ScopeNotAllowedError(None, provider, outside)

    account = account_email or _accounts.UNIDENTIFIED

    # Steps 4-6 share one hold of the provider lock and the account's lock, so
    # a refresh in another process never sees the new client next to the old
    # connection and clears it. A write that fails partway is undone before the
    # locks are released, for the same reason.
    with held_sync(provider):
        # A prior process may have died after changing only one of the two
        # keyring slots. Recover that pair before reading the accounts.
        marked = forward_recovery_slot(provider)
        if marked is not None:
            with held_account_sync(provider, marked):
                recover_forward_recovery(provider)
        target = _forward_target(provider, account_email, client_id)
        slot = target.slot if target else _accounts.LEGACY_SLOT
        same = target is not None and target.email.casefold() == account.casefold()
        kept_id = target.account_id if same else _accounts.UNIDENTIFIED
        with held_account_sync(provider, slot):
            prov = _write_forwarded(
                provider,
                slot,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                scopes=scopes,
                account=account,
                account_id=None if kept_id == _accounts.UNIDENTIFIED else kept_id,
                account_type=account_type,
                connected_at=connected_at,
            )
        _accounts.record_account(
            provider,
            slot,
            account_id=kept_id,
            email=account_email,
            state="ok",
            account_type=account_type or (target.account_type if target else None),
        )

    # 7. Evict any stale access-token cache entry so the next get_or_refresh
    #    refreshes against the forwarded client.
    _token_cache.pop((provider, slot), None)

    # 8. Optionally grant the named agents the forwarded scopes so they can
    #    resolve the connection ambiently (no credentials on the request).
    granted_agents: List[str] = []
    for agent_id in grant_agents or []:
        grant_agent(provider, agent_id, list(scopes))
        granted_agents.append(agent_id)

    logger.info(
        "api: imported forwarded connection provider=%s scopes=%d "
        "grant_agents=%d client_id_hash=%s",
        provider,
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


def _forward_target(
    provider: str, account_email: str, client_id: str
) -> Optional[Account]:
    """The stored account a forwarded connection replaces, or None for the
    connector's first. With one account stored it is replaced whatever its
    address; with several, the forward must name one. Refuses, rather than
    sign anyone out, when it would swap the OAuth client under the others.

    One OAuth client serves every account of a connector, and each account's
    token only refreshes under the client that minted it. Caller holds the
    provider lock.
    """
    from gaia.connectors.providers.base import client_id_hash

    stored = _accounts.list_accounts(provider)
    named = [
        a for a in stored if a.email.casefold() == (account_email or "").casefold()
    ]
    if not named and len(stored) == 1:
        named = stored  # One account: a forward replaces it, as it always has.
    if stored and len(named) != 1:
        raise ConnectorsError(
            f"GAIA already has {len(stored)} {provider} account(s) "
            f"({', '.join(a.email or a.account_id for a in stored)}). A "
            "forwarded connection can only replace the account with the same "
            "address, so nothing was changed. Forward it with account_email set "
            "to one of them, or disconnect the others first "
            f"(`gaia connectors disconnect {provider} --account <address>`)."
        )
    target = named[0] if named else None
    new_hash = client_id_hash(client_id)
    signed_out = [
        a.email or a.account_id
        for a in stored
        if a is not target
        and (peek_connection(provider, slot=a.slot) or {}).get("client_id_hash")
        not in (None, new_hash)
    ]
    if signed_out:
        raise ConnectorsError(
            f"This forwarded {provider} connection uses a different OAuth client "
            f"than GAIA's other {provider} account(s) ({', '.join(signed_out)}). "
            "Saving it would sign those accounts out, so nothing was changed. "
            "Disconnect them first, or forward a connection made with the same "
            "client."
        )
    return target


def _write_forwarded(
    provider: str,
    slot: str,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    scopes: List[str],
    account: str,
    account_id: Optional[str],
    account_type: Optional[str],
    connected_at: Optional[float],
):
    """Steps 4-6 of a forward, undone if any write fails. Caller holds the
    provider lock and the account's lock. Returns the rebuilt provider."""
    from gaia.connectors.providers import _registry as _provider_registry
    from gaia.connectors.providers import reload as reload_provider
    from gaia.connectors.store import (
        clear_forward_recovery,
        restore_connection_slots,
        save_connection,
        save_provider_credentials,
        snapshot_connection_slots,
        write_forward_recovery,
    )

    previous = snapshot_connection_slots(provider, slot=slot)
    write_forward_recovery(provider, previous, slot=slot)
    try:
        # 4. Persist the forwarded OAuth client → ``provider:<provider>`` slot.
        save_provider_credentials(
            provider, client_id=client_id, client_secret=client_secret
        )

        # 5. Rebuild the cached provider from the forwarded client so
        #    ``client_id_hash`` is recomputed from it.
        prov = reload_provider(provider)

        # 6. Persist the connection (refresh_token + metadata) → the
        #    account's slot, keyed by the forwarded client's hash so the
        #    tripwire passes coherently. ``save_connection`` rewrites the whole
        #    blob, so the account kind must be threaded through explicitly or a
        #    forwarded grant silently downgrades an already-classified mailbox
        #    to unknown (#2466). A caller that knows the kind passes it;
        #    otherwise the stored value carries over.
        resolved_account_type = account_type or (
            (peek_connection(provider, slot=slot) or {}).get("account_type")
        )
        save_connection(
            provider=provider,
            slot=slot,
            account_id=account_id,
            account_email=account,
            refresh_token=refresh_token,
            scopes=list(scopes),
            client_id_hash=prov.client_id_hash,
            connected_at=connected_at,
            account_type=resolved_account_type,
            # #2591 review: mark it so a later disconnect never revokes the
            # host app's own OAuth grant — see save_connection's docstring.
            forwarded=True,
        )
        clear_forward_recovery(provider)
    except BaseException as e:
        _provider_registry.pop(provider, None)
        try:
            restore_connection_slots(previous)
            clear_forward_recovery(provider)
        except Exception as restore_err:
            if not isinstance(e, Exception):
                # Ctrl+C or shutdown: say what was left behind, don't mask it.
                logger.error(
                    "api: forward of %s interrupted and its undo failed (%s); "
                    "the stored client and connection may not match",
                    provider,
                    restore_err,
                )
                raise e from restore_err
            raise ConnectorsError(
                f"Forwarding the {provider} connection failed ({e}), and "
                "putting back the previous client config and connection also "
                f"failed ({restore_err}). They may no longer match: reconnect "
                f"from Settings → Connectors, or run `gaia connectors connect "
                f"{provider}`, or forward the connection again."
            ) from restore_err
        raise
    return prov


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
    for provider_id, slot in (
        (p, a.slot) for p in _store_list() for a in _accounts.list_accounts(p)
    ):
        try:
            load_current_connection(provider_id, slot=slot)
        except ConfigurationError as e:
            logger.warning("tripwire: provider %s misconfigured: %s", provider_id, e)
            continue
        except ConnectionBusyError:
            logger.info(
                "tripwire: provider %s is being saved by another GAIA operation; "
                "left in place",
                provider_id,
            )
        except AuthRequiredError:
            # Tripwire fired (hash OR tenant) — load_connection already
            # cleared the entry; nothing else to do here.
            logger.info("tripwire: provider %s entry cleared by tripwire", provider_id)
        except Exception as e:
            logger.warning("tripwire: provider %s check failed: %s", provider_id, e)


def connected_mailbox_providers() -> list[str]:
    """Return ids of OAuth PKCE connectors with at least one connected account.

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

    result: list[str] = []
    for spec in REGISTRY.all():
        if spec.type != "oauth_pkce":
            continue
        if any(a.status == "ok" for a in _accounts.list_accounts(spec.id)):
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
    "list_accounts",
    "list_agent_grants",
    "list_connections",
    "load_activations",
    "load_grants",
    "poll_device_flow",
    "resolve_declared_scopes",
    "revoke_agent_grant",
    "revoke_connection",
    "revoke_connection_async",
    "set_default_account",
    "start_authorization",
    "start_device_flow",
    "tripwire_check",
]
