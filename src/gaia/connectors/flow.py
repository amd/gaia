# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OAuth flow lifecycle and loopback callback server.

Built on ``aiohttp.web`` (already in base ``install_requires``) — never
``asyncio.start_server`` (which is raw TCP and would silently lose the
auth code), never ``http.server`` (which would re-open the threading-to-
async bridge we explicitly avoid).

The runner runs in whichever event loop calls ``start_authorization``.
SDK / CLI / AgentUI callers all drive the same primitive; only the
surrounding presentation layer differs.

Plan amendment A8 hardenings:
- Explicit ``None`` guard before ``hmac.compare_digest`` (the runtime
  raises ``TypeError`` otherwise — a malformed redirect would surface
  as an unstructured 500).
- Static success HTML literal — no f-string interpolation of any
  request-supplied data — XSS-proof by construction.
- ``webbrowser.open`` dispatched via ``run_in_executor`` so a slow
  browser launch on Linux does not block concurrent SSE streams.

Single-flow scope: ``_pending`` is a ``dict[flow_id, _PendingFlow]``,
but only one flow can be active at a time per process — a second
``start_authorization`` call while one is pending raises
``FlowInProgressError``.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import secrets
import uuid
import webbrowser
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import httpx
from aiohttp import web

from gaia.connectors._refresh_lock import locked_write
from gaia.connectors.errors import (
    ConnectorsError,
    ConsentDeniedError,
    FlowTimeoutError,
    GrantAfterConnectError,
    OAuthProviderError,
    ProfileRefusedError,
    ScopeNotAllowedError,
)
from gaia.connectors.events import emit
from gaia.connectors.pkce import compute_code_challenge, generate_code_verifier
from gaia.connectors.prior_state import resolve_or_reject_empty_scopes
from gaia.connectors.providers import get as get_provider
from gaia.connectors.store import (
    DEFAULT_ACCOUNT,
    connection_slot_in_use,
    peek_connection,
)

if TYPE_CHECKING:
    from gaia.connectors.accounts import Account

logger = logging.getLogger(__name__)


# Static success page (A8) — a literal string, no interpolation. The user
# closes the browser tab when they see this. window.close() works for
# popup-style auth flows; for tab-style flows the user closes manually.
_SUCCESS_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'><title>"
    "Connected to GAIA</title></head>"
    "<body style='font-family: system-ui, sans-serif; padding: 2rem; "
    "max-width: 480px; margin: 0 auto; color: #1a1a1a;'>"
    "<h1>Connected.</h1>"
    "<p>You may close this tab and return to GAIA.</p>"
    "<script>setTimeout(function(){ try { window.close(); } catch(e){} }, 800);</script>"
    "</body></html>"
)

# Static error page used for invalid callback shapes (no state, mismatched
# state, etc.). Also a literal — never interpolates query-string data.
_ERROR_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'><title>"
    "GAIA — request rejected</title></head>"
    "<body style='font-family: system-ui, sans-serif; padding: 2rem; "
    "max-width: 480px; margin: 0 auto; color: #1a1a1a;'>"
    "<h1>Request rejected.</h1>"
    "<p>Return to GAIA and start the connection again.</p>"
    "</body></html>"
)


# Long enough for a first-time user to read an unverified-app consent screen
# and click through Google's interstitial.
_FLOW_TIMEOUT_SECONDS = 900

# An overall cap on the profile lookup (httpx's timeout is per phase).
_USERINFO_DEADLINE_SECONDS = 10.0


@dataclass(frozen=True)
class SignInIntent:
    """Why a sign-in was started.

    ``add_account`` shows the provider's account picker. ``reconnect`` is the
    account id of the stored account being signed in again; its address goes
    to the provider as a hint. Either way the account that actually signs in
    decides where the token is saved (see ``accounts.save_signed_in_account``).
    """

    add_account: bool = False
    reconnect: Optional[str] = None


@dataclass
class _PendingFlow:
    flow_id: str
    provider_id: str
    scopes: list[str]
    code_verifier: str
    state: str
    redirect_uri: str
    runner: web.AppRunner
    future: "asyncio.Future[Dict[str, Any]]"
    # Per-agent grants to commit atomically with a successful token exchange
    # (#2117). Maps a namespaced agent id → the scopes to grant for this
    # connector. Empty means "connect only" — the legacy behaviour.
    grant_agents: Dict[str, list[str]] = field(default_factory=dict)
    # Ends a flow nobody awaits (the apps) when the user just closes the tab.
    expiry: Optional[asyncio.TimerHandle] = None
    # What the user asked for: add another account, or reconnect one.
    intent: SignInIntent = field(default_factory=SignInIntent)


@dataclass(frozen=True)
class AccountIdentity:
    """Who signed in: the provider's stable account id and the address."""

    account_id: str
    email: str


# v1 single-flow constraint per the plan: only one flow can be pending at
# a time. The dict shape is forward-compat for v2 multi-flow.
_pending: dict[str, _PendingFlow] = {}
# Strong references, so a running expiry is not garbage-collected.
_expiring: set[asyncio.Future] = set()


def _decode_id_token_claims(id_token: str) -> Dict[str, Any]:
    """
    Base64url-decode an id_token's payload segment and return its claims.

    Best-effort and unvalidated — the signature is not checked, so callers may
    only use the result for display labels and local classification, never for
    authorization. Returns ``{}`` for a malformed or absent token.
    """
    try:
        _, payload_b64, _ = id_token.split(".")
    except (AttributeError, ValueError):
        return {}
    # base64url, no padding — pad up to a multiple of 4.
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded).decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _decode_email_from_id_token(id_token: str) -> Optional[str]:
    """
    Extract the user's email from an id_token payload.

    Checks claims in priority order:
      1. ``email`` — present in Google id_tokens and most OIDC providers.
      2. ``preferred_username`` — Microsoft identity platform (personal
         Outlook.com accounts and Azure AD work/school accounts).
      3. ``upn`` — legacy Microsoft on-premises claim.

    Production validation is deferred to the userinfo endpoint; this is a
    quick path for display on the OAuth success page.
    """
    claims = _decode_id_token_claims(id_token)
    email = claims.get("email") or claims.get("preferred_username") or claims.get("upn")
    return email if isinstance(email, str) else None


def _resolve_account_type(provider, id_token: str) -> Optional[str]:
    """Classify the signed-in account from the id_token, if the provider can.

    Duck-typed on ``provider.classify_account_type(claims)`` (Microsoft derives
    ``personal`` vs ``work`` from the ``tid`` claim, #2466). The claims may be
    empty — a provider that does not request ``openid`` gets no id_token at all,
    and decides from what it does know — so the provider, not this function,
    owns what is conclusive. Returns ``None`` when the provider has no notion of
    account type or nothing was conclusive; an unknown kind is recorded as
    unknown, never guessed. Never raises: the account kind is metadata, and
    failing to derive it must not fail a connect that otherwise succeeded.
    """
    classify = getattr(provider, "classify_account_type", None)
    if not callable(classify):
        return None
    claims = _decode_id_token_claims(id_token or "")
    try:
        account_type = classify(claims)
    except Exception as e:  # noqa: BLE001 — metadata only, must not fail connect
        logger.warning(
            "flow: account-type classification for %s failed (%s); recording "
            "it as unknown",
            getattr(provider, "provider_id", "?"),
            e,
        )
        return None
    return account_type if isinstance(account_type, str) and account_type else None


async def identify_account(
    provider, id_token: str, access_token: str
) -> AccountIdentity:
    """Which account signed in, from the token response.

    Google: the id_token's ``sub`` and ``email`` (GAIA requests ``openid`` and
    the email scope). Microsoft, which GAIA signs in without ``openid``: the
    Graph ``/me`` profile's ``id`` and address (``userinfo_url``). Raises
    ``ConnectorsError`` when either is missing: saving a sign-in GAIA can't
    tell apart from the others would mix accounts together.
    """
    claims = _decode_id_token_claims(id_token or "")
    email = _decode_email_from_id_token(id_token or "")
    provider_id = getattr(provider, "provider_id", "?")
    userinfo_url = getattr(provider, "userinfo_url", None)
    if userinfo_url:
        info = await _fetch_userinfo(provider_id, userinfo_url, access_token)
        account_id = provider.parse_account_id(info)
        email = email or provider.parse_account_email(info)
    else:
        sub = claims.get("sub")
        account_id = sub if isinstance(sub, str) else None
    if not account_id or not email:
        missing = " and ".join(
            name
            for name, value in (("account id", account_id), ("address", email))
            if not value
        )
        raise ConnectorsError(
            f"{provider_id} signed in, but its reply did not say which account "
            f"it was (no {missing}), so GAIA did not save it: it could not tell "
            "this account from the others. Connect again with the connector's "
            "default scopes; a hand-typed --scopes list must keep "
            f"{', '.join(getattr(provider, 'default_scopes', ()))}."
        )
    return AccountIdentity(account_id=account_id, email=email)


async def _fetch_userinfo(provider_id: str, url: str, access_token: str) -> dict:
    if not access_token:
        raise ConnectorsError(
            f"{provider_id} returned no access token to look up the signed-in "
            "account with. Connect again."
        )

    async def _get() -> httpx.Response:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(
                url, headers={"Authorization": f"Bearer {access_token}"}
            )

    try:
        resp = await asyncio.wait_for(_get(), _USERINFO_DEADLINE_SECONDS)
    except asyncio.TimeoutError as e:
        raise ConnectorsError(
            f"{provider_id}'s profile lookup at {url} did not answer within "
            f"{_USERINFO_DEADLINE_SECONDS:.0f}s, so GAIA could not tell which "
            "account signed in. Check the network and connect again."
        ) from e
    except httpx.HTTPError as e:
        raise ConnectorsError(
            f"Could not look up which {provider_id} account signed in "
            f"({type(e).__name__}) at {url}. Check the network and connect again."
        ) from e
    if resp.status_code != 200:
        refused = resp.status_code in (401, 403)
        raise (ProfileRefusedError if refused else ConnectorsError)(
            f"Looking up which {provider_id} account signed in failed with HTTP "
            f"{resp.status_code} at {url}. Connect again with the connector's "
            "default scopes (they include the profile permission)."
        )
    try:
        info = resp.json()
    except ValueError as e:
        raise ConnectorsError(
            f"{provider_id}'s profile reply at {url} was not JSON. Connect again."
        ) from e
    if not isinstance(info, dict):
        raise ConnectorsError(
            f"{provider_id}'s profile reply at {url} had the wrong shape. "
            "Connect again."
        )
    return info


async def _save_signed_in(
    provider_id: str,
    provider,
    payload: Dict[str, Any],
    granted_scopes: list[str],
    account_type: Optional[str],
    *,
    add_account: bool = False,
) -> Dict[str, Any]:
    """Identify the account, store it, and return the result fields."""
    from gaia.connectors.accounts import save_signed_in_account

    identity = await identify_account(
        provider, payload.get("id_token", ""), payload.get("access_token", "")
    )
    account, added = await locked_write(
        provider_id,
        save_signed_in_account,
        provider_id,
        account_id=identity.account_id,
        email=identity.email,
        refresh_token=payload["refresh_token"],
        scopes=granted_scopes,
        client_id_hash=provider.client_id_hash,
        # D8: record the minting authority — None for providers with no
        # concept of a tenant (e.g. Google), which save_connection omits
        # from the blob entirely (A7-style contract).
        tenant=getattr(provider, "tenant", None),
        account_type=account_type,
        add_account=add_account,
    )
    return {
        "account_email": account.email,
        "account_id": account.account_id,
        "added": added,
        "is_default": account.is_default,
    }


def _sign_in_intent(
    provider_id: str, add_account: bool, account: Optional[str]
) -> Tuple[SignInIntent, Optional["Account"]]:
    """The intent of a sign-in, and the stored account it reconnects if any.
    Raises before any sign-in starts when ``account`` is not stored."""
    if add_account and account is not None:
        raise ConnectorsError(
            f"Signing in to {provider_id}: pass add_account=True to add an "
            "account, or account=<id or address> to reconnect one, not both."
        )
    target = None
    if account is not None:
        from gaia.connectors.accounts import resolve_account

        target = resolve_account(provider_id, account)
    intent = SignInIntent(
        add_account=add_account,
        reconnect=target.account_id if target else None,
    )
    return intent, target


def _reject_scopes_outside_catalog(provider_id: str, scopes_list: list[str]) -> None:
    """Reject OAuth scopes outside the connector catalog ceiling (#2736).

    The imports stay local because the catalog loads OAuth providers which
    eventually import this flow module.
    """
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.registry import REGISTRY

    connector_spec = REGISTRY.get(provider_id)
    disallowed = sorted(set(scopes_list) - set(connector_spec.available_scopes))
    if disallowed:
        raise ScopeNotAllowedError(None, provider_id, disallowed)


async def start_authorization(
    provider_id: str,
    scopes: Iterable[str],
    grant_agents: Optional[Mapping[str, Iterable[str]]] = None,
    *,
    add_account: bool = False,
    account: Optional[str] = None,
    callback_port: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Begin the OAuth flow for ``provider_id`` with the requested scopes.

    Returns ``{flow_id, authorization_url, callback_port}``. Spins up a
    loopback aiohttp runner on an ephemeral port (or explicit ``callback_port``),
    stores the pending flow, fires a background callback to ``webbrowser.open(...)``
    (in an executor to keep the event loop responsive), and returns immediately.

    The caller is expected to await ``complete_authorization(flow_id)``
    to wait for the redirect. A flow nobody awaits (the apps) still ends
    after ``_FLOW_TIMEOUT_SECONDS``: its port closes and
    ``connector.oauth.error`` goes out with ``reason: timeout``.

    ``grant_agents`` (#2117) maps a namespaced agent id → the scopes to
    grant that agent for ``provider_id`` once the token exchange succeeds.
    This makes the grant part of the same connect flow: connecting a
    mailbox from the email surface hands the email agent access without a
    separate CLI step. The grants are committed in
    ``_exchange_code_for_tokens`` — a grant that cannot be written fails
    the whole flow loudly rather than leaving a connected-but-ungranted
    dead end.

    ``add_account`` shows the provider's account picker so the user can sign
    in to another account. ``account`` (an account id or address) reconnects
    that stored account: its address is sent as a sign-in hint. The account
    that actually signs in decides where the token is saved: a stored account
    with the same id is updated in place, any other is added, and no account
    is ever overwritten by a different one.
    """
    if callback_port is not None:
        if (
            not isinstance(callback_port, int)
            or isinstance(callback_port, bool)
            or callback_port < 1
            or callback_port > 65535
        ):
            raise ConnectorsError(
                f"Invalid callback port {callback_port!r}: "
                "must be an integer between 1 and 65535."
            )

    intent, target = _sign_in_intent(provider_id, add_account, account)
    if _pending:
        # User re-clicking Connect signals the previous flow is dead.
        # Common case: Google blocks the auth (wrong account / consent
        # denied / closed tab) and never redirects to the loopback
        # callback, so complete_authorization is never awaited and
        # _teardown_flow never runs. Evict any stale entries and proceed
        # — single-active-flow semantics are preserved because we tear
        # down before starting fresh. FlowInProgressError remains in the
        # public API for explicit-cancel callers (cancel_flow).
        stale_ids = list(_pending.keys())
        logger.info(
            "flow: evicting %d stale pending flow(s) on new start_authorization: %s",
            len(stale_ids),
            stale_ids,
        )
        for stale_id in stale_ids:
            await _teardown_flow(stale_id)

    provider = get_provider(provider_id)
    scopes_list = resolve_or_reject_empty_scopes(
        provider_id, scopes, provider.default_scopes
    )
    # Both CLI and Agent UI browser flows converge here.
    _reject_scopes_outside_catalog(provider_id, scopes_list)
    extra_params: Dict[str, str] = {}
    if add_account or target is not None:
        extra_params = provider.account_prompt_params(
            select_account=add_account,
            login_hint=target.email if target else None,
            account_type=target.account_type if target else None,
        )

    code_verifier = generate_code_verifier()
    challenge = compute_code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)
    flow_id = uuid.uuid4().hex

    loop = asyncio.get_event_loop()
    future: "asyncio.Future[Dict[str, Any]]" = loop.create_future()
    # The apps never await it; each failure also goes out as an event.
    future.add_done_callback(lambda f: f.cancelled() or f.exception())

    app = web.Application()

    async def callback(request: web.Request) -> web.Response:
        return await _handle_callback(request, flow_id)

    app.router.add_get("/callback", callback)
    # aiohttp's access log would write the code and state in the query string.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", callback_port or 0)
    try:
        await site.start()
    except OSError as e:
        await runner.cleanup()
        if callback_port is not None:
            cause = (
                "permission denied (ports below 1024 need elevated rights)"
                if isinstance(e, PermissionError)
                else f"port is already in use ({type(e).__name__})"
            )
            raise ConnectorsError(
                f"Could not bind OAuth callback to port {callback_port}: {cause}. "
                "Choose a free port above 1024 with --callback-port, or omit it for an ephemeral port."
            ) from e
        raise

    # Read back the actual port the kernel assigned. aiohttp keeps the
    # bound sockets on the runner.sites list.
    port = site._server.sockets[0].getsockname()[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    authorization_url = provider.authorization_url(
        redirect_uri=redirect_uri,
        challenge=challenge,
        state=state,
        scopes=scopes_list,
        extra_params=extra_params,
    )

    _pending[flow_id] = _PendingFlow(
        flow_id=flow_id,
        provider_id=provider_id,
        scopes=scopes_list,
        code_verifier=code_verifier,
        state=state,
        redirect_uri=redirect_uri,
        runner=runner,
        future=future,
        grant_agents={
            agent_id: list(agent_scopes)
            for agent_id, agent_scopes in (grant_agents or {}).items()
        },
        expiry=loop.call_later(_FLOW_TIMEOUT_SECONDS, _start_expiry, flow_id),
        intent=intent,
    )

    # Fire-and-forget the browser launch — A8: do not block the event
    # loop on a slow browser-launch (5s on some Linux setups freezes
    # all concurrent SSE streams).
    async def _open_browser():
        try:
            # Returns False rather than raising when no browser is registered —
            # the normal case on headless Ubuntu, WSL and SSH sessions. Ignoring
            # it leaves the user watching a "check your browser" message forever.
            opened = await loop.run_in_executor(
                None, webbrowser.open, authorization_url
            )
        except Exception as e:
            # Best-effort — the authorization_url is also returned to
            # the caller for a copy-paste fallback. Type only: an OSError
            # carries the URL, and with it the state.
            logger.warning(
                "flow: webbrowser.open failed (%s); fall back "
                "to copy-paste of authorization_url",
                type(e).__name__,
            )
            return
        if not opened:
            logger.warning(
                "flow: no browser could be launched on this host; the user must "
                "open authorization_url themselves"
            )

    asyncio.ensure_future(_open_browser())

    logger.info(
        "flow: started scopes=%d grant_agents=%d flow_id=%s",
        len(scopes_list),
        len(_pending[flow_id].grant_agents),
        flow_id,
    )
    return {
        "flow_id": flow_id,
        "authorization_url": authorization_url,
        "callback_port": port,
    }


async def complete_authorization(flow_id: str) -> Dict[str, Any]:
    """
    Wait up to 15 minutes for the loopback callback to fulfil the flow.

    Returns a ``ConnectorState`` dict
    ``{provider, account_email, scopes, connected_at}`` once the token
    exchange succeeds and the connection is persisted via
    ``store.save_connection``.

    Raises ``FlowTimeoutError``, ``ConsentDeniedError``, or
    ``ConnectorsError`` on the unhappy paths.
    """
    flow = _pending.get(flow_id)
    if flow is None:
        raise ConnectorsError(
            f"Unknown flow_id {flow_id!r}. Either it was never started, "
            "already completed, or was cancelled."
        )

    try:
        try:
            return await asyncio.wait_for(flow.future, timeout=_FLOW_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as e:
            raise _timeout_error(flow.provider_id) from e
    finally:
        await _teardown_flow(flow_id)


def _timeout_error(provider_id: str) -> FlowTimeoutError:
    return FlowTimeoutError(
        f"Sign-in to {provider_id} was not finished within "
        f"{_FLOW_TIMEOUT_SECONDS // 60} minutes, so the sign-in link you "
        "were given no longer works. Run `gaia connectors connect "
        f"{provider_id}` (or click Connect again) for a fresh link."
    )


def _start_expiry(flow_id: str) -> None:
    task = asyncio.ensure_future(_expire_flow(flow_id))
    _expiring.add(task)
    task.add_done_callback(_expiring.discard)


async def _expire_flow(flow_id: str) -> None:
    """End a flow still pending at its deadline: close its port, tell the app."""
    flow = _pending.get(flow_id)
    if flow is None:
        return
    unfinished = not flow.future.done()
    if unfinished:
        flow.future.set_exception(_timeout_error(flow.provider_id))
    await _teardown_flow(flow_id)
    if unfinished:
        await emit(
            "connector.oauth.error",
            {
                "connector_id": flow.provider_id,
                "reason": "timeout",
                "error": "Sign-in did not finish in time.",
            },
        )


async def cancel_flow(flow_id: str) -> None:
    """Tear down a pending flow without waiting (used by tests / UI)."""
    await _teardown_flow(flow_id)


async def _teardown_flow(flow_id: str) -> None:
    flow = _pending.pop(flow_id, None)
    if flow is None:
        return
    if flow.expiry is not None:
        flow.expiry.cancel()
    try:
        await flow.runner.cleanup()
    except Exception as e:
        # Cleanup is best-effort — log and move on.
        logger.warning("flow: runner.cleanup failed for %s: %s", flow_id, e)


async def revoke_provider_token(
    provider_id: str,
    *,
    slot: str = DEFAULT_ACCOUNT,
    account_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Attempt a provider-side OAuth revoke of the stored refresh token (#2591).

    This is the honest half of "disconnect": callers (``oauth_pkce.disconnect``,
    ``api.revoke_connection``) MUST NOT report a full revoke just because the
    local keyring entry was deleted — that was the literal #2591 bug (GAIA
    told the user it disconnected when it had only forgotten locally, leaving
    the app's Google grant live). This function never raises for a revoke
    failure and never deletes local state itself; it only reports what really
    happened so the caller can act on the local delete regardless and report
    the remote outcome truthfully:

    - ``revoke_supported=False, revoke_error=None`` — the provider has no
      public revoke endpoint (Microsoft's identity platform today; see
      ``MicrosoftOAuthProvider.revoke_url``). The caller must say so, not
      imply a revoke happened.
    - ``revoke_supported=False, revoke_error=<reason>`` — no revoke was even
      attempted, but *not* because the provider lacks one: either the stored
      connection was forwarded by a host app (see below) or the provider
      could not be resolved. ``revoke_error`` names which, so callers don't
      conflate "can't be revoked" with "wasn't tried".
    - ``revoke_supported=True, revoked_remotely=True`` — the provider's
      revoke endpoint accepted the request (``revoke_attempted=True``).
    - ``revoke_supported=True, revoked_remotely=False,
      nothing_stored=True``: the slot was empty, so there was no refresh
      token to revoke and no provider call was made. This is distinct from a
      revoke that failed.
    - ``revoke_supported=<provider's>, revoked_remotely=False,
      nothing_stored=False, revoke_error=<why>`` — the slot holds a sign-in
      GAIA cannot read (#8713), so there was no token to send. The local copy
      is still removed and ``revoke_error`` says where to revoke by hand. The
      slot is not empty, so this must never be reported as nothing stored.
    - ``revoke_supported=True, revoked_remotely=False,
      revoke_attempted=True`` — the endpoint call failed; ``revoke_error``
      carries why. The provider-side grant is still live and the caller must
      say so.

    ``revoke_attempted`` is True only when a revoke request was actually sent.
    It is False for an empty slot, an unreadable sign-in, a forwarded
    connection, an unresolvable provider, and a provider with no revoke
    endpoint, so callers can say "not attempted" instead of "failed".

    **Forwarded connections are never revoked remotely (#2591 review).** A
    connection imported via ``api.import_forwarded_connection`` stores a
    refresh token minted under the *host app's* OAuth client, not GAIA's own
    (``forwarded=True`` in the keyring blob, set by ``save_connection``).
    Google's revoke endpoint takes no client auth and kills the whole grant
    for whoever the token belongs to — so revoking it here would silently
    sign the host app out of the user's account, recoverable only by
    re-consenting through that other app. Local removal still proceeds
    (the caller deletes the keyring entry regardless); only the remote call
    is skipped.
    """
    blob = peek_connection(provider_id, slot=slot)
    if blob and blob.get("forwarded"):
        return {
            "revoke_supported": False,
            "revoked_remotely": False,
            "revoke_attempted": False,
            "nothing_stored": False,
            "revoke_error": (
                "this connection was forwarded to GAIA by another application "
                "— the token belongs to that app, not GAIA, so revoking it "
                "would also sign the host app out of the account. Disconnect "
                "it from the app that shared it, or from your account's "
                "connected-apps page, to fully revoke access."
            ),
        }

    # None means empty OR unreadable; only an empty slot is "nothing stored".
    if blob is None and provider_id in HAS_REVOKE_ENDPOINT:
        if not connection_slot_in_use(provider_id, slot=slot):
            return nothing_to_revoke(provider_id)
        return unreadable_sign_in(provider_id, account_type=account_type)

    try:
        provider = get_provider(provider_id)
    except (ConnectorsError, KeyError) as exc:
        # Unresolvable/unconfigured provider (e.g. a test double id, or a
        # connector whose client credentials were never set up) — this is
        # NOT the same as "this provider has no revoke endpoint": we simply
        # couldn't determine whether one exists, and any live grant is
        # untouched.
        return {
            "revoke_supported": False,
            "revoked_remotely": False,
            "revoke_attempted": False,
            "nothing_stored": False,
            "revoke_error": f"provider {provider_id!r} could not be resolved: {exc}",
        }
    # Read the attribute directly, never via getattr-with-default: a provider
    # that forgets to declare it must fail loudly, not silently report
    # "revoke not supported" — the exact dishonesty #2591 exists to remove.
    revoke_url = provider.revoke_url
    result: Dict[str, Any] = {
        "revoke_supported": bool(revoke_url),
        "revoked_remotely": False,
        "revoke_attempted": False,
        "nothing_stored": False,
        "revoke_error": None,
    }
    refresh_token = (blob or {}).get("refresh_token")
    if not refresh_token:
        result["nothing_stored"] = True
        return result
    if not revoke_url:
        return result

    # Logs name the endpoint, not ``provider_id``: the id is derived from
    # ``spec.oauth_provider_ref``, which every credential-name heuristic reads
    # as secret material, and the host identifies the provider just as exactly.
    revoke_host = urlsplit(revoke_url).netloc
    result["revoke_attempted"] = True

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(revoke_url, data={"token": refresh_token})
    except httpx.HTTPError as exc:
        # Only the HTTP call itself (timeout, connection refused, DNS, TLS,
        # protocol errors) is an expected failure mode worth reporting
        # honestly and swallowing here. httpx exception strings are safe:
        # they describe the failure (e.g. "ConnectTimeout"), never echo the
        # request body we just posted the refresh token in. Anything else
        # (a bug in this function) is NOT caught — it should crash loudly
        # rather than be reported as a routine revoke failure.
        result["revoke_error"] = (
            f"{provider_id} revoke request failed: {type(exc).__name__}"
        )
        logger.warning(
            "flow: provider-side revoke request failed endpoint=%s error_type=%s",
            revoke_host,
            type(exc).__name__,
        )
        return result

    if response.status_code in (200, 204):
        result["revoked_remotely"] = True
        return result

    # Never interpolate the response body into a log line or a
    # caller-visible field: this request just posted the refresh token, and
    # some providers echo request context (e.g. `error_description`) back
    # into error bodies. The status code is enough for a user to act on and
    # carries no risk of leaking the token.
    result["revoke_error"] = (
        f"{provider_id} revoke endpoint rejected the request "
        f"(status {response.status_code})"
    )
    logger.warning(
        "flow: provider-side revoke rejected endpoint=%s status=%s",
        revoke_host,
        response.status_code,
    )
    return result


#: Whether each OAuth provider has a revoke endpoint, mirroring its ``revoke_url``.
#: Static so a disconnect can answer without the provider's client configured.
HAS_REVOKE_ENDPOINT: Dict[str, bool] = {
    "google": True,
    "microsoft": False,
    "microsoft_work": False,
}


def nothing_to_revoke(provider_id: str) -> Dict[str, Any]:
    """The revoke outcome for an account with no token stored: no live grant.

    Matches what :func:`revoke_provider_token` reports for the same case, so a
    provider with no revoke endpoint still says so. Reads nothing from the
    keyring, so it answers on one that refuses reads.
    """
    if provider_id not in HAS_REVOKE_ENDPOINT:
        return {
            "revoke_supported": False,
            "revoked_remotely": False,
            "revoke_attempted": False,
            "nothing_stored": False,
            "revoke_error": f"provider {provider_id!r} could not be resolved",
        }
    return {
        "revoke_supported": HAS_REVOKE_ENDPOINT[provider_id],
        "revoked_remotely": False,
        "revoke_attempted": False,
        "nothing_stored": True,
        "revoke_error": None,
    }


#: Where a user removes GAIA's access by hand, per OAuth provider id.
REVOKE_PAGES: Dict[str, Tuple[str, str]] = {
    "google": ("Google", "https://myaccount.google.com/permissions"),
    "microsoft": ("Microsoft", "https://account.live.com/consent/Manage"),
    "microsoft_work": ("Microsoft", "https://myapps.microsoft.com"),
}


def revoke_page_for(
    provider_id: str, *, account_type: Optional[str] = None
) -> Tuple[str, str]:
    """The provider label and connected-apps URL where the user revokes GAIA.

    ``account_type`` sends a work account saved under ``microsoft`` to the
    work page. Shared by every path that has to tell the user to revoke by
    hand (weak keyring, unreadable sign-in).
    """
    from gaia.connectors.providers.microsoft import ACCOUNT_TYPE_WORK

    page_id = provider_id
    if provider_id == "microsoft" and account_type == ACCOUNT_TYPE_WORK:
        page_id = "microsoft_work"
    return REVOKE_PAGES.get(
        page_id, (provider_id, "your account's connected-apps page")
    )


def unreadable_sign_in(
    provider_id: str, *, account_type: Optional[str] = None
) -> Dict[str, Any]:
    """The revoke outcome when the slot holds a sign-in GAIA cannot read.

    ``peek_connection`` returns ``None`` for a corrupt blob while the entry
    stays in place, so this is not an empty slot: the local copy is removed,
    but there is no token to revoke, and the user must revoke GAIA's access
    at the provider by hand (#8713).
    """
    label, page = revoke_page_for(provider_id, account_type=account_type)
    return {
        "revoke_supported": HAS_REVOKE_ENDPOINT[provider_id],
        "revoked_remotely": False,
        "revoke_attempted": False,
        "nothing_stored": False,
        "revoke_error": (
            "GAIA could not read the stored sign-in, so it removed the local "
            f"copy without revoking GAIA's access at {label}. To revoke it, "
            f"remove GAIA at {page}"
        ),
    }


def not_revoked_on_weak_keyring(
    provider_id: str,
    *,
    uninspectable: bool = False,
    account_type: Optional[str] = None,
) -> Dict[str, Any]:
    """The revoke outcome when the keyring refuses reads (weak or uninspectable).

    Revoking needs the stored refresh token, which GAIA will not read from
    such a keyring, so only the local copy is removed. ``revoke_error`` tells
    the user where to revoke GAIA's access by hand. ``uninspectable`` is a
    chain GAIA could not check, which may well be secure; ``account_type``
    sends a work account saved under ``microsoft`` to the work page.
    """
    label, page = revoke_page_for(provider_id, account_type=account_type)
    if uninspectable:
        why = "GAIA could not confirm the keyring on this PC is secure"
        fix = "use a single system credential store"
    else:
        why = "The keyring on this PC is not secure"
        fix = "switch to a secure credential store"
    return {
        "revoke_supported": False,
        "revoked_remotely": False,
        "revoke_attempted": False,
        "nothing_stored": False,
        "revoke_error": (
            f"{why}, so GAIA removed the sign-in without reading it and did "
            f"not revoke GAIA's access at {label}. To revoke it, remove GAIA "
            f"at {page}. To keep using connectors, {fix}: "
            "https://amd-gaia.ai/docs/security/connections"
        ),
    }


async def _handle_callback(request: web.Request, flow_id: str) -> web.Response:
    """Loopback handler for ``GET /callback``."""
    flow = _pending.get(flow_id)
    if flow is None:
        # Stale callback for a flow that was already cleaned up.
        return web.Response(text=_ERROR_HTML, content_type="text/html", status=400)

    received_state = request.query.get("state")
    error = request.query.get("error")
    code = request.query.get("code")
    logger.info(
        "flow: callback flow_id=%s params=%s",
        flow_id,
        ",".join(sorted(request.query.keys())) or "none",
    )

    # A8: explicit None guard. ``hmac.compare_digest(None, str)`` raises
    # ``TypeError`` and aiohttp would surface that as an unstructured 500.
    if received_state is None or not hmac.compare_digest(received_state, flow.state):
        # Static error page; no echoed input.
        return web.Response(text=_ERROR_HTML, content_type="text/html", status=400)

    if error is not None:
        # Common case: ?error=access_denied — the user clicked "deny" on
        # the consent screen. Resolve the future with the typed exception
        # and serve the rejection page (NOT the success page — telling a
        # user who just clicked "Deny" that they're connected is wrong).
        if not flow.future.done():
            flow.future.set_exception(
                ConsentDeniedError(f"OAuth flow rejected by user: {error}")
            )
        # The apps never await the flow, so they learn of the cancel this way.
        await emit(
            "connector.oauth.error",
            {
                "connector_id": flow.provider_id,
                "reason": "cancelled" if error == "access_denied" else "rejected",
                "error": (
                    "Sign-in was cancelled."
                    if error == "access_denied"
                    else f"The provider refused the sign-in ({error})."
                ),
            },
        )
        return web.Response(text=_ERROR_HTML, content_type="text/html", status=400)

    if code is None:
        # State matched but no code — malformed redirect.
        return web.Response(text=_ERROR_HTML, content_type="text/html", status=400)

    # Exchange the code for tokens.
    try:
        result = await _exchange_code_for_tokens(flow, code)
    except Exception as e:
        logger.warning("flow: sign-in to %s failed: %s", flow.provider_id, e)
        if not flow.future.done():
            flow.future.set_exception(e)
        await emit(
            "connector.oauth.error",
            {"connector_id": flow.provider_id, "reason": "failed", "error": str(e)},
        )
        return web.Response(text=_ERROR_HTML, content_type="text/html", status=502)

    if not flow.future.done():
        flow.future.set_result(result)
    return web.Response(text=_SUCCESS_HTML, content_type="text/html")


async def _commit_grants(flow: _PendingFlow, granted_scopes: Iterable[str]) -> None:
    """Write per-agent grants limited to the scopes the token exchange granted.

    Called only after the connection is persisted. Each grant is written
    through the same ledger the CLI/SDK/Settings panel use, so it is
    immediately visible and revocable there. Emits ``connector.grant.changed``
    per agent so subscribed UIs refresh the grants panel without a reload.

    Fails loudly: a grant that cannot be written raises ``ConnectorsError``,
    which propagates to ``_handle_callback`` and surfaces on the flow future.
    Connecting-without-granting is the bug this flow exists to prevent, so a
    grant failure must not be swallowed.
    """
    await _commit_grants_for_provider(
        flow.provider_id, flow.grant_agents, granted_scopes
    )


async def _commit_grants_for_provider(
    provider_id: str,
    grant_agents: Optional[Mapping[str, Iterable[str]]],
    granted_scopes: Iterable[str],
) -> None:
    """Commit effective per-agent grants for either OAuth flow entry point."""
    if not grant_agents:
        return

    # Local import mirrors the lazy-keyring contract in connectors/__init__.py
    # and keeps flow.py's module-load dependency graph unchanged.
    from gaia.connectors.grants import grant_agent

    granted_scope_set = set(granted_scopes)
    for agent_id, agent_scopes in grant_agents.items():
        requested_scopes = list(agent_scopes)
        effective_scopes = [
            scope for scope in requested_scopes if scope in granted_scope_set
        ]
        if not effective_scopes:
            raise GrantAfterConnectError(
                provider_id,
                agent_id,
                reason=(
                    "the provider granted none of the scopes this agent asked "
                    f"for ({' '.join(requested_scopes)}). The connection was "
                    "saved; re-run connect and approve them on the consent "
                    "screen."
                ),
            )
        if len(effective_scopes) != len(requested_scopes):
            logger.warning(
                "flow: narrowed grant connector_id=%s agent_id=%s requested=%d "
                "granted=%d — the user declined some scopes at consent",
                provider_id,
                agent_id,
                len(requested_scopes),
                len(effective_scopes),
            )
        try:
            grant_agent(provider_id, agent_id, effective_scopes)
        except Exception as e:
            raise GrantAfterConnectError(
                provider_id,
                agent_id,
                reason=(
                    f"{e}. The connection was saved; grant the agent manually "
                    f"from Settings → Connectors, or via `gaia connectors "
                    f"grants grant {provider_id} {agent_id} --scopes "
                    f"{' '.join(effective_scopes)}`"
                ),
            ) from e
        await emit(
            "connector.grant.changed",
            {
                "connector_id": provider_id,
                "agent_id": agent_id,
                "scopes": effective_scopes,
            },
        )
        logger.info(
            "flow: granted connector_id=%s agent_id=%s scopes=%d on connect",
            provider_id,
            agent_id,
            len(effective_scopes),
        )


def _resolve_granted_scopes(
    payload: Dict[str, Any], requested: "list[str]"
) -> "list[str]":
    """The scopes a token-exchange response actually granted (#2730 D6).

    Per RFC 6749 §5.1 the token endpoint returns ``scope`` only when the
    granted set differs from what was requested; its absence means "as
    requested." An explicitly empty ``scope`` therefore means that none of the
    requested scopes were granted. Google's granular-consent screen lets a user
    untick Calendar while approving Gmail, so trusting the request
    unconditionally (what this code did before) records a connection that lies
    about carrying scopes the user declined — every downstream coverage check
    then passes against a fabricated record instead of catching the shortfall
    here, loudly, with an actionable message.

    The returned set is persisted as-is, including scopes beyond the request:
    Google's ``include_granted_scopes=true`` folds earlier grants into every
    reconnect, and the refresh token really does carry them. Intersecting with
    the request would understate the connection's authority and keep
    ``check_scopes`` demanding a reconnect for a scope the token already holds
    (#2605). Per-agent grants narrow separately, in ``_commit_grants``.
    """
    if "scope" not in payload:
        return list(requested)
    raw = payload.get("scope")
    if not isinstance(raw, str):
        logger.warning(
            "flow: token response contained a non-string scope; treating it "
            "as no granted scopes"
        )
        return []
    granted: "list[str]" = []
    for scope in raw.split():
        if scope not in granted:
            granted.append(scope)
    return granted


#: Bound on each provider-supplied field, matching OAuthProviderError's own.
_MAX_PROVIDER_FIELD_LEN = 300


#: Real codes and verifiers are far longer; a short value would garble words.
_MIN_SCRUB_LEN = 8


def _scrub(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= _MIN_SCRUB_LEN:
            text = text.replace(secret, "[redacted]")
    return text


def _structured_oauth_error(
    resp: Any, sent_secrets: Iterable[str] = ()
) -> "tuple[str, str]":
    """The provider's RFC 6749 ``(error, error_description)``, bounded.

    Never falls back to the raw body (#3875): every request these responses
    answer carries a credential — an authorization code, a device code, a
    refresh token — and providers echo request context back into error
    bodies, so the body must not reach a log line or a user-visible error.
    Non-string fields are dropped rather than coerced, so a provider that
    nests an object under ``error`` yields no detail instead of a stringified
    fragment of its body. ``sent_secrets`` (the code, verifier or device code
    the request carried) are replaced if a provider echoes them in either field.
    """
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001 — body may be empty/non-JSON
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error")
    description = payload.get("error_description")
    secrets = list(sent_secrets)
    return (
        (
            _scrub(error, secrets)[:_MAX_PROVIDER_FIELD_LEN]
            if isinstance(error, str)
            else ""
        ),
        (
            _scrub(description, secrets)[:_MAX_PROVIDER_FIELD_LEN]
            if isinstance(description, str)
            else ""
        ),
    )


async def _exchange_code_for_tokens(flow: _PendingFlow, code: str) -> Dict[str, Any]:
    """Run the token-exchange step and persist the connection."""
    provider = get_provider(flow.provider_id)
    body = provider.token_request_body(
        code=code, verifier=flow.code_verifier, redirect_uri=flow.redirect_uri
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(provider.token_url, data=body)

    if response.status_code != 200:
        # Structured, bounded fields only (#2590) — the request this answers
        # carried the authorization code and PKCE verifier, so the raw body
        # never reaches the message (#3875).
        error, description = _structured_oauth_error(
            response, (code, flow.code_verifier)
        )
        raise OAuthProviderError(
            flow.provider_id,
            error=error,
            error_description=description,
            status_code=response.status_code,
        )
    payload = response.json()
    if not payload.get("refresh_token"):
        raise ConnectorsError(
            f"Token endpoint for {flow.provider_id} returned no "
            "refresh_token. Make sure the provider's "
            "authorization_params() includes the offline-access flags "
            "(Google requires access_type=offline + prompt=consent). See "
            "docs/security/connections.mdx."
        )

    account_type = _resolve_account_type(provider, payload.get("id_token", ""))
    granted_scopes = _resolve_granted_scopes(payload, flow.scopes)
    saved = await _save_signed_in(
        flow.provider_id,
        provider,
        payload,
        granted_scopes,
        account_type,
        add_account=flow.intent.add_account,
    )
    if flow.intent.reconnect:
        # Lets the caller say so when a different account came back.
        saved["requested_account_id"] = flow.intent.reconnect

    # No separate state-cache write needed — the keyring blob written
    # above is the source of truth for "configured / account / scopes",
    # and the router reads it via ``store.peek_connection`` for the UI.

    # #2117 — commit the per-agent grants requested at connect time, in the
    # same flow as the connection. This is what closes the consumer-breaking
    # dead end: a mailbox connected from the email surface now hands the
    # email agent access without a follow-up CLI grant. Fail loudly — a
    # connection that persisted but whose grant could not be written is the
    # exact silent half-success the connect flow must not produce.
    await _commit_grants(flow, granted_scopes)

    # Google's token endpoint does not return a ``connected_at`` field
    # (RFC 6749 has no such concept) — record the local wall-clock at
    # exchange time. ``save_connection`` does the same for the keyring blob.
    import time as _time

    state_dict = {
        "provider": flow.provider_id,
        **saved,
        "scopes": granted_scopes,
        "connected_at": _time.time(),
    }
    await _emit_connected(flow.provider_id, saved)
    return state_dict


async def _emit_connected(provider_id: str, saved: Dict[str, Any]) -> None:
    """Emit the framework event (what the Agent UI listens for) and the legacy
    one. ``account_id`` names the account; ``added`` says it is new;
    ``is_default`` says whether it is the default account (an added account
    is not, unless it is the first); ``requested_account_id`` is the account
    a reconnect asked for, if any."""
    await emit(
        "connector.oauth.completed",
        {
            "connector_id": provider_id,
            "account_email": saved["account_email"],
            "account_id": saved["account_id"],
            "added": saved["added"],
            "is_default": saved["is_default"],
            "requested_account_id": saved.get("requested_account_id"),
        },
    )
    await emit(
        "connection.connected",
        {
            "provider": provider_id,
            "account_email": saved["account_email"],
            "account_id": saved["account_id"],
        },
    )


# ---------------------------------------------------------------------------
# Device-code flow (RFC 8628) — #1275
# ---------------------------------------------------------------------------
# Zero-setup sign-in for providers that expose a ``device_code_url`` (Microsoft
# today). No loopback redirect and no per-user app registration: the user opens
# a short URL, types a code, and approves. The resulting refresh token persists
# through the SAME ``store.save_connection`` as the loopback flow, so every
# downstream consumer (tokens.get_or_refresh, the email agent's
# _get_outlook_token) is identical regardless of how the user connected.

_DEVICE_POLL_DEFAULT_INTERVAL = 5


async def start_device_flow(
    provider_id: str,
    scopes: Iterable[str],
    *,
    add_account: bool = False,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Request a device + user code from the provider's device-code endpoint.

    Returns ``{provider_id, scopes, device_code, user_code, verification_uri,
    expires_in, interval, message, intent}``. The caller shows ``user_code`` +
    ``verification_uri`` (or the provider-supplied ``message``) to the user,
    then awaits :func:`poll_device_flow` with the returned ``device_code`` and
    ``intent``.

    ``add_account`` and ``account`` mean what they mean for
    :func:`start_authorization`. The device-code page has no account picker
    or sign-in hint, so they only decide where the sign-in is saved.
    """
    intent, _target = _sign_in_intent(provider_id, add_account, account)
    provider = get_provider(provider_id)
    device_code_url = getattr(provider, "device_code_url", None)
    if not device_code_url:
        raise ConnectorsError(
            f"Provider {provider_id!r} does not support the device-code flow. "
            "Use start_authorization (browser loopback) instead. See "
            "docs/security/connections.mdx."
        )
    scopes_list = resolve_or_reject_empty_scopes(
        provider_id, scopes, provider.default_scopes
    )
    _reject_scopes_outside_catalog(provider_id, scopes_list)
    body = provider.device_code_request_body(scopes_list)

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(device_code_url, data=body)
    if resp.status_code != 200:
        # AADSTS9002346 (D11, #2628): the app registration is scoped to
        # personal Microsoft accounts only, so a non-consumers authority
        # rejects it — under the split, that means it was registered for
        # "microsoft" (consumers) but connected via "microsoft_work"
        # (organizations, or a pinned Directory tenant id). Name the
        # connector to use instead, never an env var. Membership test only —
        # the body is matched against, never surfaced.
        if "AADSTS9002346" in resp.text:
            other = "microsoft" if provider_id != "microsoft" else "microsoft_work"
            raise ConnectorsError(
                f"Device-code request for {provider_id} was rejected: this app "
                "registration is configured for personal Microsoft accounts only "
                "(Outlook.com / Hotmail / Live). Reconnect using the "
                f"{other!r} connector instead, e.g.:\n"
                f"  gaia connectors connect {other} --device\n"
                "See docs/connectors/microsoft."
            )
        # D9: the client-id env var name is CONNECTOR-SPECIFIC
        # (GAIA_MICROSOFT_CLIENT_ID for "microsoft",
        # GAIA_MICROSOFT_WORK_CLIENT_ID for "microsoft_work") — never
        # hard-coded to the personal one. Tenant is no longer an env var at
        # all (D6); the only tenant knob left is microsoft_work's optional
        # Directory (tenant) ID setup field.
        client_id_env = f"GAIA_{provider_id.upper()}_CLIENT_ID"
        # Structured, bounded fields only — never the raw body (#3875).
        error, description = _structured_oauth_error(resp)
        detail = description or error
        reason = f" ({detail})" if detail else ""
        raise ConnectorsError(
            f"Device-code request for {provider_id} failed with status "
            f"{resp.status_code}{reason}. Check the client id "
            f"({client_id_env}), or the Directory (tenant) ID setup field if "
            f"you set one. See docs/connectors/microsoft.mdx."
        )
    d = resp.json()
    logger.info(
        "device-flow: started provider=%s scopes=%d", provider_id, len(scopes_list)
    )
    return {
        "provider_id": provider_id,
        "scopes": scopes_list,
        "device_code": d["device_code"],
        "user_code": d["user_code"],
        "verification_uri": (
            d.get("verification_uri") or d.get("verification_url") or ""
        ),
        "expires_in": int(d.get("expires_in", 900)),
        "interval": int(d.get("interval", _DEVICE_POLL_DEFAULT_INTERVAL)),
        "message": d.get("message", ""),
        "intent": intent,
    }


async def poll_device_flow(
    provider_id: str,
    device_code: str,
    *,
    scopes: Iterable[str],
    interval: int = _DEVICE_POLL_DEFAULT_INTERVAL,
    expires_in: int = 900,
    grant_agents: Optional[Mapping[str, Iterable[str]]] = None,
    intent: Optional[SignInIntent] = None,
) -> Dict[str, Any]:
    """Poll the token endpoint until the user approves the device code.

    ``intent`` is the one :func:`start_device_flow` returned; without it the
    sign-in is saved as a plain connect.

    Honors the RFC 8628 poll cadence: ``authorization_pending`` waits one
    ``interval``; ``slow_down`` widens it by 5s. Persists the connection on
    success and commits any ``grant_agents`` (namespaced agent id → scopes) in
    the same step, so a device-code connect can grant an agent atomically the
    way the loopback flow does. Raises ``FlowTimeoutError`` /
    ``ConsentDeniedError`` / ``ConnectorsError`` on the unhappy paths — never a
    silent empty result.
    """
    import time as _time

    provider = get_provider(provider_id)
    scopes_list = resolve_or_reject_empty_scopes(
        provider_id, scopes, provider.default_scopes
    )
    # An SDK caller can reach poll_ without going through start_, and these
    # scopes are what save_connection records.
    _reject_scopes_outside_catalog(provider_id, scopes_list)
    body = provider.device_token_request_body(device_code)
    poll_interval = max(int(interval), 1)
    deadline = _time.monotonic() + max(int(expires_in), poll_interval)

    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            resp = await client.post(provider.token_url, data=body)
            if resp.status_code == 200:
                payload = resp.json()
                break
            err, err_description = _structured_oauth_error(resp, (device_code,))
            if err == "authorization_pending":
                pass
            elif err == "slow_down":
                poll_interval += 5
            elif err == "expired_token":
                raise FlowTimeoutError(
                    f"Device-code for {provider_id} expired before sign-in. "
                    "Run the connect command again."
                )
            elif err in ("authorization_declined", "access_denied"):
                raise ConsentDeniedError(
                    f"Device-code sign-in for {provider_id} was declined."
                )
            else:
                # Structured, bounded fields only (#2590) — see
                # OAuthProviderError. This is where an admin-consent-required
                # rejection (AADSTS65001) surfaces during polling; the raw
                # body is never used as a fallback, because this request just
                # posted the device code (#3875).
                raise OAuthProviderError(
                    provider_id,
                    error=err,
                    error_description=err_description,
                    status_code=resp.status_code,
                )
            if _time.monotonic() >= deadline:
                raise FlowTimeoutError(
                    f"Device-code for {provider_id} timed out after "
                    f"{expires_in}s waiting for sign-in. Run connect again."
                )
            await asyncio.sleep(poll_interval)

    if not payload.get("refresh_token"):
        raise ConnectorsError(
            f"Device-code token response for {provider_id} returned no "
            "refresh_token. The 'offline_access' scope must be requested (it is "
            "in the Microsoft default_scopes). See docs/security/connections.mdx."
        )
    account_type = _resolve_account_type(provider, payload.get("id_token", ""))
    granted_scopes = _resolve_granted_scopes(payload, scopes_list)
    intent = intent or SignInIntent()
    saved = await _save_signed_in(
        provider_id,
        provider,
        payload,
        granted_scopes,
        account_type,
        add_account=intent.add_account,
    )
    if intent.reconnect:
        saved["requested_account_id"] = intent.reconnect

    await _commit_grants_for_provider(provider_id, grant_agents, granted_scopes)

    await _emit_connected(provider_id, saved)
    logger.info(
        "device-flow: connected provider=%s added=%s scopes=%d",
        provider_id,
        saved["added"],
        len(granted_scopes),
    )
    return {
        "provider": provider_id,
        **saved,
        "scopes": granted_scopes,
        "connected_at": _time.time(),
    }


# ---------------------------------------------------------------------------
# Classification (#2590) — turn a raised OAuth failure into "which setup step
# to redo", never into raw provider text reaching model context.
# ---------------------------------------------------------------------------

#: Marker Microsoft uses for "this account's tenant requires an admin to
#: approve the app" — the one AADSTS code this route can actually produce
#: (see setup_routes.MS_PERSONAL's public_client_flows / permissions steps).
_ADMIN_CONSENT_MARKER = "AADSTS65001"


def classify_oauth_exception(exc: Exception) -> tuple[str, str]:
    """Classify a raised OAuth failure into ``(category, guidance)``.

    ``category`` is one of ``authorization_declined``, ``expired_token``,
    ``access_denied``, ``admin_consent_required``, or ``unrecognized``.
    ``guidance`` names the setup step to redo where one is known.

    Classifies ONLY what this route can actually produce (#2590 design §6) —
    anything else is reported from ``OAuthProviderError``'s own BOUNDED
    ``error`` / ``error_description`` fields, never ``str(exc)``: a provider
    error body is not something GAIA generates, so nothing here may assume
    it is safe to echo unbounded.
    """
    if isinstance(exc, ConsentDeniedError):
        return "authorization_declined", (
            "Sign-in was declined at the consent screen. Redo the sign-in "
            "step and approve the requested permissions."
        )
    if isinstance(exc, FlowTimeoutError):
        return "expired_token", (
            "The sign-in code expired before it was approved. Redo the "
            "sign-in step and enter the code promptly."
        )
    if isinstance(exc, OAuthProviderError):
        error = exc.error
        description = exc.error_description
        if error == "access_denied":
            return "access_denied", (
                "Access was denied. Redo the sign-in step and approve the "
                "requested permissions."
            )
        if error == "admin_consent_required" or _ADMIN_CONSENT_MARKER in description:
            return "admin_consent_required", (
                "This account's organization requires an administrator to "
                "approve the app before it can sign in. Redo the "
                "permissions step with an account that can grant consent, "
                "or ask an administrator to approve it."
            )
        bounded = description or error or "no further detail was returned"
        return "unrecognized", f"The provider rejected the request: {bounded}"
    return "unrecognized", "The sign-in failed for an unrecognized reason."
