# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-provider access-token cache with double-checked locking and refresh.

Critical invariants (T-5b, plan amendments A6, A7):

- One ``asyncio.Lock`` per ``(provider, slot)`` cache entry, where the slot
  names one stored account (``accounts.py``). The refresh path acquires it
  with the same bounded wait as the connection lock and releases it in a
  ``finally``, so a refresh that raises never leaves it held.

- The daemon, the Agent UI and the CLI each refresh in their own process,
  so a refresh also holds that account's connection lock
  (``_refresh_lock.held_account``, shared with connect and disconnect) and
  re-reads the stored connection after taking it. Without it two processes
  refresh with the same refresh token, and the loser's ``invalid_grant``
  deleted the connection the winner had just rotated. The lock is per
  account, so a slow refresh of one account never stalls another. The one
  exception is a forwarded import: it changes the client config every
  account shares, so while one is in flight (or was interrupted and not yet
  undone) every account of that provider reports busy.

- A refresh records the account's state in the account list: an
  ``invalid_grant`` or a cleared connection marks it ``reauth_required``,
  and the next success clears that. A connection saved before GAIA recorded
  stable account ids gets its id (and a missing address) filled in on its
  next refresh; if that id is already stored in another slot, the two merge.

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
from typing import TYPE_CHECKING, Optional, Tuple

import httpx

from gaia.connectors import _loop, _refresh_lock
from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionBusyError,
    ConnectionRevokedError,
    ConnectorsError,
    ProfileRefusedError,
)
from gaia.connectors.providers import OAuthProvider
from gaia.connectors.providers import get as get_provider
from gaia.connectors.providers import reload as reload_provider
from gaia.connectors.store import (
    DEFAULT_ACCOUNT,
    delete_connection,
    forward_recovery_slot,
    load_connection,
    peek_connection,
    recover_forward_recovery,
    save_connection,
)

if TYPE_CHECKING:
    from gaia.connectors.flow import AccountIdentity

logger = logging.getLogger(__name__)


# 60s buffer per AC4: refresh proactively when the access token is within
# this many seconds of expiring. Prevents a tool from receiving a token
# that expires mid-API-call.
_EXPIRY_BUFFER_SECONDS = 60

# An overall cap on the token POST (httpx's timeout is per phase), so a peer
# holding the connection lock always finishes inside its 15 s wait.
_TOKEN_POST_DEADLINE_SECONDS = 10.0

# How long get_or_refresh_sync waits for a whole refresh.
_REFRESH_BUDGET_SECONDS = _loop._DEFAULT_TIMEOUT
# Kept back from the budget for saving the refreshed token.
_SAVE_RESERVE_SECONDS = 3.0
# The account lookup is skipped when less than this is left of the budget.
_MIN_LOOKUP_SECONDS = 1.0


@dataclass
class _AccessTokenCache:
    """Per-(provider, slot) cache entry. Lock guards the refresh path."""

    access_token: Optional[str] = None
    expires_at: float = 0.0  # ``time.monotonic()``-based
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Module-level cache. Tests reset this between cases via the autouse
# fixture in ``tests/unit/connectors/conftest.py``.
_cache: dict[Tuple[str, str], _AccessTokenCache] = {}


def _cache_key(provider_id: str, slot: str) -> Tuple[str, str]:
    return (provider_id, slot)


def _is_expired(entry: _AccessTokenCache) -> bool:
    return entry.access_token is None or (
        entry.expires_at - time.monotonic() < _EXPIRY_BUFFER_SECONDS
    )


def load_current_connection(
    provider_id: str,
    *,
    slot: str = DEFAULT_ACCOUNT,
    lock_held: bool = False,
) -> Tuple[OAuthProvider, Optional[dict]]:
    """Return ``(provider, stored connection or None)`` for one account,
    checked against the client config in the credential store, not just this
    process's cache.

    Another GAIA process may have forwarded or reconnected the connection under
    a new client or tenant, leaving the cached provider stale. On a mismatch
    the provider is rebuilt from the stored client config and checked again.

    A connection that still mismatches is cleared (the store's tripwire raises
    ``AuthRequiredError``) only under the account's connection lock, so a
    forward landing meanwhile is never the one deleted. ``lock_held`` says the
    caller holds it; otherwise it is taken without waiting, and when anything
    else holds it (another process, or another thread here) the connection is
    left in place and ``ConnectionBusyError`` is raised. An unreadable blob
    (absent, torn or corrupt) is handed to the store under the lock too, and
    raises ``ConnectionBusyError`` while the lock is busy, so "busy" can also
    mean "unreadable and not yet cleared". Blocking keyring I/O.
    """
    # A forwarded save spans the client and connection slots. If its process
    # died between those writes, restore the pre-save pair under the provider
    # and account locks before constructing a provider from the new client.
    marked = forward_recovery_slot(provider_id)
    if marked is not None:
        # An account lock is held: the provider lock can't be taken after it.
        if lock_held or not _try_recover_forward(provider_id, marked):
            raise ConnectionBusyError(provider_id)

    provider = get_provider(provider_id)
    current = peek_connection(provider_id, slot=slot)
    if current is None:
        # Absent, torn or corrupt: the store's own read decides, and may delete,
        # so it runs under the lock. Busy means a writer is mid-save.
        if lock_held:
            return provider, _load(provider_id, provider, slot)
        with _refresh_lock.try_held_account_sync(provider_id, slot) as acquired:
            if acquired:
                return load_current_connection(provider_id, slot=slot, lock_held=True)
        raise ConnectionBusyError(provider_id)
    if _disagrees(provider, current):
        logger.info(
            "connectors: %s client config changed outside this process "
            "(cached hash=%s tenant=%s, stored hash=%s tenant=%s); reloading",
            provider_id,
            provider.client_id_hash,
            getattr(provider, "tenant", None),
            current.get("client_id_hash"),
            current.get("tenant"),
        )
        provider = reload_provider(provider_id)
    if not _disagrees(provider, current):
        return provider, current
    if lock_held:
        return provider, _load(provider_id, provider, slot)
    with _refresh_lock.try_held_account_sync(provider_id, slot) as acquired:
        if acquired:
            return load_current_connection(provider_id, slot=slot, lock_held=True)
    raise ConnectionBusyError(provider_id, connection=_summary(current))


def _try_recover_forward(provider_id: str, marked_slot: str) -> bool:
    """Undo an interrupted forward if both of its locks are free now."""
    with _refresh_lock.try_held_sync(provider_id) as got_provider:
        if not got_provider:
            return False
        with _refresh_lock.try_held_account_sync(provider_id, marked_slot) as got:
            if not got:
                return False
            recover_forward_recovery(provider_id)
            return True


def recover_interrupted_forward(provider_id: str) -> None:
    """Undo an interrupted forward, waiting for its locks. Blocking."""
    marked = forward_recovery_slot(provider_id)
    if marked is None:
        return
    with _refresh_lock.held_sync(provider_id):
        with _refresh_lock.held_account_sync(provider_id, marked):
            recover_forward_recovery(provider_id)


_SUMMARY_FIELDS = ("account_email", "connected_at", "account_type")


def _summary(stored: dict) -> dict:
    """Display metadata of a stored connection: no tokens, and no scopes —
    a connection mid-save may be about to change, so its scopes can't be
    trusted for a decision."""
    return {k: stored.get(k) for k in _SUMMARY_FIELDS}


def _load(provider_id: str, provider: OAuthProvider, slot: str) -> Optional[dict]:
    # ``current_tenant`` (A18) is the LIVE provider's resolved tenant —
    # ``getattr`` because Google has no ``.tenant`` attribute at all.
    return load_connection(
        provider_id,
        current_client_id_hash=provider.client_id_hash,
        current_tenant=getattr(provider, "tenant", None),
        slot=slot,
    )


def _disagrees(provider: OAuthProvider, stored: dict) -> bool:
    """Whether ``stored`` would trip the store's client or tenant check."""
    if stored.get("client_id_hash") != provider.client_id_hash:
        return True
    stored_tenant = stored.get("tenant")
    live_tenant = getattr(provider, "tenant", None)
    return (
        stored_tenant is not None
        and live_tenant is not None
        and stored_tenant != live_tenant
    )


async def get_or_refresh(provider_id: str, *, slot: str = DEFAULT_ACCOUNT) -> str:
    """
    Return a fresh access token for one stored account of ``provider_id``.

    Uses double-checked locking: the unlocked re-check inside the cache hit
    path keeps concurrent callers off the lock when the token is fresh; the
    second check inside the locked block prevents N+1 refreshes when 10
    callers race.
    """
    get_provider(provider_id)  # an unknown or unconfigured provider fails now
    budget_ends = time.monotonic() + _REFRESH_BUDGET_SECONDS

    key = _cache_key(provider_id, slot)
    entry = _cache.get(key)
    if entry is None:
        entry = _cache.setdefault(key, _AccessTokenCache())

    if not _is_expired(entry):
        return entry.access_token  # type: ignore[return-value]

    # The refresh holds only the account lock, so an interrupted forward,
    # which needs the provider lock first, is undone before taking it.
    if await _refresh_lock.run_to_completion(forward_recovery_slot, provider_id):
        await _refresh_lock.run_to_completion(recover_interrupted_forward, provider_id)

    # One wait budget for the slot lock and the file lock together, so the
    # wait plus the token POST stays inside get_or_refresh_sync's 30 s.
    deadline = time.monotonic() + _refresh_lock.LOCK_TIMEOUT_SECONDS
    try:
        await asyncio.wait_for(entry.lock.acquire(), _refresh_lock.LOCK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise _refresh_lock.timeout_error(provider_id) from exc
    try:
        # Re-check inside the lock — a peer task may have refreshed
        # while we were waiting.
        if not _is_expired(entry):
            return entry.access_token  # type: ignore[return-value]

        remaining = max(0.0, deadline - time.monotonic())
        async with _refresh_lock.held_account(provider_id, slot, timeout=remaining):
            return await _refresh_locked(provider_id, slot, entry, budget_ends)
    finally:
        entry.lock.release()


async def _refresh_locked(
    provider_id: str, slot: str, entry: _AccessTokenCache, budget_ends: float
) -> str:
    """Refresh ``entry``; the caller holds its slot lock and the account lock.

    ``budget_ends`` is when the caller's wait runs out (``time.monotonic()``).
    """
    from gaia.connectors import accounts

    # Read first so a connection the tripwire is about to clear can still be
    # listed as needing a reconnect.
    before = await _refresh_lock.run_to_completion(
        peek_connection, provider_id, slot=slot
    )
    # Loaded under the lock: a forward or reconnect that ran while we waited,
    # in this process or another, may have replaced the client.
    # The store raises AuthRequiredError(REAUTH_REQUIRED / TENANT_MISMATCH)
    # directly when the client_id_hash / tenant tripwire fires. ``None`` means
    # the user never connected.
    try:
        provider, stored = await _refresh_lock.run_to_completion(
            load_current_connection, provider_id, slot=slot, lock_held=True
        )
    except AuthRequiredError:
        if before is not None:
            await _refresh_lock.run_to_completion(
                accounts.mark_state,
                provider_id,
                slot,
                "reauth_required",
                stored=before,
            )
        raise
    if stored is None:
        raise AuthRequiredError(
            AuthRequiredError.Reason.NOT_CONNECTED, provider=provider_id
        )

    try:
        refreshed = await _refresh_token(provider, stored["refresh_token"])
    except ConnectorsError as e:
        if isinstance(e, ConnectionRevokedError):
            if not await _refresh_lock.run_to_completion(
                _delete_if_unchanged, provider_id, slot, stored["refresh_token"]
            ):
                raise _replaced_error(provider_id) from e
            await _refresh_lock.run_to_completion(
                accounts.mark_state,
                provider_id,
                slot,
                "reauth_required",
                stored=stored,
            )
        # A5: enrich with split-related guidance ONLY for a genuine
        # OAuth-protocol rejection — ConnectionRevokedError (400
        # invalid_grant) or the generic-400/non-200 bare ConnectorsError
        # branch — on a LEGACY blob (no recorded tenant: "we don't know"
        # is the only case where the split is a plausible cause, not a
        # confirmed one). ConfigurationError and AuthRequiredError are
        # ALSO ConnectorsError subclasses but must propagate unchanged —
        # they name a different, already-precise cause (missing secret /
        # rejected client) that split-language would only muddy.
        # httpx.* exceptions are not ConnectorsError at all and are
        # never caught here — they propagate raw (no try/except wraps
        # the token POST itself).
        if (
            not isinstance(e, (ConfigurationError, AuthRequiredError))
            and stored.get("tenant") is None
            and provider_id in ("microsoft", "microsoft_work")
        ):
            other = "microsoft_work" if provider_id == "microsoft" else "microsoft"
            account_kind = "work/school" if provider_id == "microsoft" else "personal"
            enriched = (
                f"{e} This looks like it could be the Microsoft connector "
                f"split (#2628): if this account is a {account_kind} "
                f"account, reconnect via the {other!r} connector instead."
            )
            if isinstance(e, ConnectionRevokedError):
                raise ConnectionRevokedError(provider_id, message=enriched) from e
            raise ConnectorsError(enriched) from e
        raise

    new_refresh = refreshed.refresh_token
    identity = (
        None
        if stored.get("account_id")
        else await _identify_for_backfill(provider, refreshed, slot, budget_ends)
    )

    # Refresh-token rotation: if the provider returned a new refresh
    # token, persist it before exposing the access token to callers.
    # A4: prefer the RECORDED tenant — it is a historical fact about
    # which authority minted THIS refresh token, and Microsoft rotates
    # the token on every refresh, so dropping it here loses the
    # diagnostic within one daemon tick. A legacy blob (no recorded
    # tenant) has no such fact to preserve, and a successful refresh IS
    # proof the live provider's tenant just minted the token we are
    # about to persist — record it then, upgrading the blob out of
    # "legacy" exactly once, on proof, rather than leaving it legacy
    # forever (which would keep re-triggering the A5 split-language
    # guess on every future unrelated failure).
    rotated = bool(new_refresh) and new_refresh != stored["refresh_token"]
    if rotated or identity is not None:
        # A reconnect or disconnect during the POST must not be overwritten.
        if not await _refresh_lock.run_to_completion(
            _save_refreshed,
            provider_id,
            slot,
            stored,
            identity,
            provider=provider_id,
            slot=slot,
            account_email=stored.get("account_email", DEFAULT_ACCOUNT),
            account_id=stored.get("account_id"),
            refresh_token=new_refresh if rotated else stored["refresh_token"],
            scopes=stored.get("scopes", []),
            client_id_hash=provider.client_id_hash,
            connected_at=stored.get("connected_at"),
            tenant=stored.get("tenant") or getattr(provider, "tenant", None),
            # Carry the derived account kind across rotation — it comes from
            # the connect-time id_token, which a refresh does not re-issue.
            account_type=stored.get("account_type"),
            # #2591 review: a forwarded connection must stay marked
            # forwarded across rotation, or the next disconnect would
            # revoke the host app's own grant.
            forwarded=bool(stored.get("forwarded")),
        ):
            raise _replaced_error(provider_id)

    if await _refresh_lock.run_to_completion(
        accounts.entry_state, provider_id, slot
    ) not in (None, "ok"):
        await _refresh_lock.run_to_completion(
            accounts.mark_state, provider_id, slot, "ok"
        )

    entry.access_token = refreshed.access_token
    entry.expires_at = time.monotonic() + refreshed.expires_in
    return entry.access_token


# Accounts whose token can't read its own profile (a forwarded Microsoft
# token without User.Read): asked once per process, not on every refresh.
_identity_unavailable: set[Tuple[str, str]] = set()


async def _identify_for_backfill(
    provider: OAuthProvider, refreshed: "_Refreshed", slot: str, budget_ends: float
) -> Optional["AccountIdentity"]:
    """Who a refresh says the account is, or None. Only metadata: a failure
    or a slow lookup here never fails the refresh, so the lookup gets only
    the time left before ``budget_ends`` beyond what saving the token needs."""
    from gaia.connectors.flow import identify_account

    if not refreshed.id_token and not getattr(provider, "userinfo_url", None):
        return None  # This refresh carried no identity; a later one may.
    key = (provider.provider_id, slot)
    if key in _identity_unavailable:
        return None
    left = budget_ends - time.monotonic() - _SAVE_RESERVE_SECONDS
    if left < _MIN_LOOKUP_SECONDS:
        logger.info(
            "connectors: no time left in this %s refresh to learn its account "
            "id; will retry on the next refresh",
            provider.provider_id,
        )
        return None
    try:
        return await asyncio.wait_for(
            identify_account(provider, refreshed.id_token, refreshed.access_token),
            left,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "connectors: looking up the account id of a %s connection took "
            "over %.0fs on refresh; will retry on the next refresh",
            provider.provider_id,
            left,
        )
    except ProfileRefusedError as e:
        _identity_unavailable.add(key)
        logger.warning(
            "connectors: could not learn the account id of a %s connection "
            "on refresh (%s); not asking again until GAIA restarts. Reconnect "
            "the account to record it.",
            provider.provider_id,
            e,
        )
    except ConnectorsError as e:
        logger.warning(
            "connectors: could not learn the account id of a %s connection "
            "on refresh (%s); will retry on the next refresh",
            provider.provider_id,
            e,
        )
    return None


async def get_token_with_expiry(
    provider_id: str, *, slot: str = DEFAULT_ACCOUNT
) -> Tuple[str, float]:
    """Return ``(access_token, wall_clock_expires_at)`` for a provider.

    Delegates to :func:`get_or_refresh` for the actual token refresh, then
    reads the cache entry's monotonic ``expires_at`` and converts it to a
    ``time.time()``-based wall-clock timestamp suitable for external
    consumers (HTTP headers, JSON payloads).

    This function exists because ``get_or_refresh`` returns a plain ``str``
    (the access token) and multiple callers depend on that signature.
    ``OAuthPkceHandler.get_credential`` needs the expiry as well, so it
    calls this wrapper instead.
    """
    token = await get_or_refresh(provider_id, slot=slot)
    key = _cache_key(provider_id, slot)
    entry = _cache.get(key)
    if entry and entry.expires_at:
        remaining = entry.expires_at - time.monotonic()
        wall_expires = time.time() + max(remaining, 0)
    else:
        wall_expires = 0.0
    return token, wall_expires


@dataclass(frozen=True)
class _Refreshed:
    access_token: str
    refresh_token: Optional[str]
    expires_in: int
    id_token: str


async def _refresh_token(provider, refresh_token: str) -> _Refreshed:
    """
    Exchange a refresh token for a fresh access token.

    ``refresh_token`` in the result is the rotated token, or None when the
    provider kept the old one. Raises ``ConnectionRevokedError`` on
    ``invalid_grant``.
    """
    body = provider.refresh_request_body(refresh_token)

    async def _post():
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(provider.token_url, data=body)

    try:
        response = await asyncio.wait_for(_post(), _TOKEN_POST_DEADLINE_SECONDS)
    except asyncio.TimeoutError as exc:
        raise httpx.TimeoutException(
            f"Token endpoint for {provider.provider_id} did not answer within "
            f"{_TOKEN_POST_DEADLINE_SECONDS:.0f}s. Check your connection and retry."
        ) from exc

    if response.status_code == 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if payload.get("error") == "invalid_grant":
            # The caller clears the stored entry (only if it still holds this
            # refresh token — see _delete_if_unchanged).
            raise ConnectionRevokedError(provider.provider_id)
        # Other 400s — actionable but not invalid_grant.
        raise ConnectorsError(
            f"Token endpoint refused refresh for {provider.provider_id}: "
            f"{payload.get('error', 'unknown')} (status 400). See "
            "docs/security/connections.mdx."
        )

    if response.status_code == 401:
        # Google returns 401 invalid_client when the client_secret is
        # absent or wrong in the refresh POST.  Distinguish the two cases
        # so the error tells the user what to do rather than just "try again":
        #   - No client_secret configured → ConfigurationError (fix: re-enter
        #     credentials in Settings → Connectors).
        #   - Secret is present but token rejected → AuthRequiredError (fix:
        #     reconnect from Settings → Connectors).
        # Public PKCE clients (Microsoft/Entra) never have a secret — that's
        # the correct, expected state, not a misconfiguration — so only
        # providers in PROVIDERS_REQUIRING_CLIENT_SECRET take the
        # config-missing branch (#1638).
        try:
            err_payload = response.json()
        except ValueError:
            err_payload = {}
        from gaia.connectors.oauth_pkce import PROVIDERS_REQUIRING_CLIENT_SECRET

        client_secret = getattr(provider, "client_secret", None)
        if (
            not client_secret
            and provider.provider_id in PROVIDERS_REQUIRING_CLIENT_SECRET
        ):
            raise ConfigurationError(
                f"Token endpoint returned 401 for {provider.provider_id}: "
                "client_secret is not configured. Open Settings → Connectors "
                f"→ {provider.provider_id} and re-enter the Client Secret, or "
                "set the GAIA_GOOGLE_CLIENT_SECRET environment variable. "
                "See docs/runbooks/google-oauth-client.md."
            )
        raise AuthRequiredError(
            AuthRequiredError.Reason.REAUTH_REQUIRED,
            provider=provider.provider_id,
            message=(
                f"Token endpoint returned 401 for {provider.provider_id} "
                f"({err_payload.get('error', 'invalid_client')}). "
                "Reconnect from Settings → Connectors → "
                f"{provider.provider_id}. "
                "See docs/runbooks/google-oauth-client.md."
            ),
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

    return _Refreshed(
        access_token=access,
        refresh_token=payload.get("refresh_token"),
        expires_in=int(expires_in),
        id_token=payload.get("id_token") or "",
    )


def _save_refreshed(
    provider_id: str,
    slot_name: str,
    stored: dict,
    identity: Optional["AccountIdentity"],
    **fields,
) -> bool:
    """Save a refresh's result, filling in the account's id and address when
    the refresh revealed them. Returns False when the stored connection was
    replaced meanwhile. Blocking; the caller holds this slot's account lock.

    The account list is written before the token, so a crash between them
    leaves the token without its id and the next refresh finishes the job.
    """
    current = peek_connection(provider_id, slot=slot_name)
    if current is None or current.get("refresh_token") != stored["refresh_token"]:
        return False
    if identity is not None:
        if stored.get("account_email") in (None, "", DEFAULT_ACCOUNT):
            fields["account_email"] = identity.email
        try:
            holder = _claim_identity(
                provider_id,
                slot_name,
                identity.account_id,
                fields["account_email"],
                fields["scopes"],
            )
        except (ConnectorsError, OSError) as e:
            logger.warning(
                "connectors: could not record the account id of a %s "
                "connection (%s); will retry on the next refresh",
                provider_id,
                e,
            )
            holder = None
        if holder == slot_name:
            fields["account_id"] = identity.account_id
        elif holder is not None:
            return True  # Merged into the account's later sign-in.
    save_connection(**fields)
    return True


def _claim_identity(
    provider_id: str, slot: str, account_id: str, email: str, scopes: list
) -> Optional[str]:
    """Record ``account_id`` for ``slot`` and return the slot that holds it,
    or None when that needs a lock someone holds now; a later refresh tries
    again. When an old build stored the same account in a second slot, the
    two merge: ``slot``, whose token was just proven, is kept unless the
    other slot was granted scopes it lacks. The survivor takes the default
    flag."""
    from gaia.connectors import accounts

    twin = accounts.slot_holding(provider_id, account_id, besides=slot)
    if twin is None:
        if accounts.record_identity(provider_id, slot, account_id, email):
            return slot
        return None
    # Never waits: this thread already holds an account lock.
    with (
        _refresh_lock.try_held_sync(provider_id) as provider_free,
        _refresh_lock.try_held_account_sync(provider_id, twin) as twin_free,
    ):
        if not (provider_free and twin_free):
            return None
        if accounts.slot_holding(provider_id, account_id, besides=slot) != twin:
            return None
        twin_blob = peek_connection(provider_id, slot=twin) or {}
        mine = peek_connection(provider_id, slot=slot) or {}
        twin_wins = set(scopes) < set(twin_blob.get("scopes") or []) and (
            twin_blob.get("client_id_hash") == mine.get("client_id_hash")
        )
        if twin_wins:
            delete_connection(provider_id, slot=slot)
            accounts.drop_duplicate(provider_id, keep=twin, drop=slot)
            return twin
        delete_connection(provider_id, slot=twin)
        accounts.drop_duplicate(provider_id, keep=slot, drop=twin)
        if accounts.record_identity(provider_id, slot, account_id, email):
            return slot
        return None


def _delete_if_unchanged(provider_id: str, slot: str, sent_refresh_token: str) -> bool:
    """Delete the connection only if it still holds the rejected refresh token.

    ``None`` from the store can mean a torn read of a chunked blob that a
    reconnect is writing, so it is never taken as permission to delete.
    """
    current = peek_connection(provider_id, slot=slot)
    if current is None or current.get("refresh_token") != sent_refresh_token:
        return False
    delete_connection(provider_id, slot=slot)
    return True


def _replaced_error(provider_id: str) -> ConnectorsError:
    return ConnectorsError(
        f"The {provider_id} connection was replaced (reconnected, disconnected, "
        "or refreshed by another GAIA process) while this refresh was in "
        "flight; the stored connection was left as is. Retry the request."
    )


def get_or_refresh_sync(provider_id: str, *, slot: str = DEFAULT_ACCOUNT) -> str:
    """
    Synchronous wrapper around ``get_or_refresh`` for sync agent contexts.

    Must NOT be called from a thread that already has a running asyncio
    event loop. Use ``await get_or_refresh(...)`` directly from async code
    instead. This guard makes the failure surface as an actionable error
    rather than a confusing crash deep inside the runtime.

    Submits the coroutine to the persistent connector event loop (see
    ``_loop.py``) and blocks with a bounded wait. The persistent loop avoids
    two bugs that ``asyncio.run`` caused (#1579): the cross-loop Lock error
    (Python ≤ 3.11) and the Windows ProactorEventLoop teardown hang on
    repeated create/destroy cycles.

    Contextvar propagation: the persistent-loop bridge captures
    ``copy_context()`` at submit time, so the agent-id contextvar set by
    the agent runtime is visible inside the async refresh code — the same
    guarantee the previous ``asyncio.run`` path provided. See
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
    from gaia.connectors._loop import run_sync

    return run_sync(get_or_refresh(provider_id, slot=slot))
