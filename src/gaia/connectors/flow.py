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

v1 single-flow scope: ``_pending`` is a ``dict[flow_id, _PendingFlow]``,
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
from typing import Any, Dict, Iterable, Mapping, Optional

import httpx
from aiohttp import web

from gaia.connectors.errors import (
    ConnectorsError,
    ConsentDeniedError,
    FlowTimeoutError,
)
from gaia.connectors.events import emit
from gaia.connectors.pkce import compute_code_challenge, generate_code_verifier
from gaia.connectors.providers import get as get_provider
from gaia.connectors.store import save_connection

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


_FLOW_TIMEOUT_SECONDS = 120


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


# v1 single-flow constraint per the plan: only one flow can be pending at
# a time. The dict shape is forward-compat for v2 multi-flow.
_pending: dict[str, _PendingFlow] = {}


def _decode_email_from_id_token(id_token: str) -> Optional[str]:
    """
    Extract the user's email from an id_token payload.

    Best-effort — base64url-decode the middle segment, parse JSON, check
    claims in priority order:
      1. ``email`` — present in Google id_tokens and most OIDC providers.
      2. ``preferred_username`` — Microsoft identity platform (personal
         Outlook.com accounts and Azure AD work/school accounts).
      3. ``upn`` — legacy Microsoft on-premises claim.

    Production validation is deferred to the userinfo endpoint; this is a
    quick path for display on the OAuth success page.
    """
    try:
        _, payload_b64, _ = id_token.split(".")
    except ValueError:
        return None
    # base64url, no padding — pad up to a multiple of 4.
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return None
    email = (
        payload.get("email") or payload.get("preferred_username") or payload.get("upn")
    )
    return email if isinstance(email, str) else None


async def _resolve_account_email(provider, id_token: str, access_token: str) -> str:
    """Best-effort account email for display: prefer the id_token claim; if it
    yields nothing, fall back to the provider's ``userinfo_url`` (e.g. Graph
    ``/me``). Returns ``"default"`` when neither works — the connection is keyed
    by DEFAULT_ACCOUNT internally, so this only affects the display label and
    must never fail the connect. The userinfo call is skipped entirely for
    providers whose id_token already carries the email (e.g. Google)."""
    email = _decode_email_from_id_token(id_token or "")
    if email:
        return email
    userinfo_url = getattr(provider, "userinfo_url", None)
    parse = getattr(provider, "parse_account_email", None)
    if userinfo_url and access_token and callable(parse):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    userinfo_url, headers={"Authorization": f"Bearer {access_token}"}
                )
            if resp.status_code == 200:
                return parse(resp.json()) or "default"
            logger.warning(
                "flow: userinfo lookup for %s returned %s (label only)",
                getattr(provider, "provider_id", "?"),
                resp.status_code,
            )
        except Exception as e:  # noqa: BLE001 — label-only, never fail connect
            logger.warning(
                "flow: userinfo lookup for %s failed (%s); label falls back to "
                "'default'",
                getattr(provider, "provider_id", "?"),
                e,
            )
    return "default"


async def start_authorization(
    provider_id: str,
    scopes: Iterable[str],
    grant_agents: Optional[Mapping[str, Iterable[str]]] = None,
) -> Dict[str, Any]:
    """
    Begin the OAuth flow for ``provider_id`` with the requested scopes.

    Returns ``{flow_id, authorization_url}``. Spins up a loopback aiohttp
    runner on an ephemeral port, stores the pending flow, fires a
    background callback to ``webbrowser.open(...)`` (in an executor to
    keep the event loop responsive), and returns immediately.

    The caller is expected to await ``complete_authorization(flow_id)``
    to wait for the redirect.

    ``grant_agents`` (#2117) maps a namespaced agent id → the scopes to
    grant that agent for ``provider_id`` once the token exchange succeeds.
    This makes the grant part of the same connect flow: connecting a
    mailbox from the email surface hands the email agent access without a
    separate CLI step. The grants are committed in
    ``_exchange_code_for_tokens`` — a grant that cannot be written fails
    the whole flow loudly rather than leaving a connected-but-ungranted
    dead end.
    """
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
    scopes_list = list(scopes) or list(provider.default_scopes)

    code_verifier = generate_code_verifier()
    challenge = compute_code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)
    flow_id = uuid.uuid4().hex

    loop = asyncio.get_event_loop()
    future: "asyncio.Future[Dict[str, Any]]" = loop.create_future()

    app = web.Application()

    async def callback(request: web.Request) -> web.Response:
        return await _handle_callback(request, flow_id)

    app.router.add_get("/callback", callback)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    # Read back the actual port the kernel assigned. aiohttp keeps the
    # bound sockets on the runner.sites list.
    port = site._server.sockets[0].getsockname()[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    authorization_url = provider.authorization_url(
        redirect_uri=redirect_uri,
        challenge=challenge,
        state=state,
        scopes=scopes_list,
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
    )

    # Fire-and-forget the browser launch — A8: do not block the event
    # loop on a slow browser-launch (5s on some Linux setups freezes
    # all concurrent SSE streams).
    async def _open_browser():
        try:
            await loop.run_in_executor(None, webbrowser.open, authorization_url)
        except Exception as e:
            # Best-effort — the authorization_url is also returned to
            # the caller for a copy-paste fallback.
            logger.warning(
                "flow: webbrowser.open failed (%s); fall back "
                "to copy-paste of authorization_url",
                e,
            )

    asyncio.ensure_future(_open_browser())

    logger.info(
        "flow: started scopes=%d grant_agents=%d flow_id=%s",
        len(scopes_list),
        len(_pending[flow_id].grant_agents),
        flow_id,
    )
    return {"flow_id": flow_id, "authorization_url": authorization_url}


async def complete_authorization(flow_id: str) -> Dict[str, Any]:
    """
    Wait up to 120 seconds for the loopback callback to fulfil the flow.

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
            raise FlowTimeoutError(
                f"OAuth flow {flow_id!r} timed out after "
                f"{_FLOW_TIMEOUT_SECONDS}s. Restart the flow."
            ) from e
    finally:
        await _teardown_flow(flow_id)


async def cancel_flow(flow_id: str) -> None:
    """Tear down a pending flow without waiting (used by tests / UI)."""
    await _teardown_flow(flow_id)


async def _teardown_flow(flow_id: str) -> None:
    flow = _pending.pop(flow_id, None)
    if flow is None:
        return
    try:
        await flow.runner.cleanup()
    except Exception as e:
        # Cleanup is best-effort — log and move on.
        logger.warning("flow: runner.cleanup failed for %s: %s", flow_id, e)


async def _handle_callback(request: web.Request, flow_id: str) -> web.Response:
    """Loopback handler for ``GET /callback``."""
    flow = _pending.get(flow_id)
    if flow is None:
        # Stale callback for a flow that was already cleaned up.
        return web.Response(text=_ERROR_HTML, content_type="text/html", status=400)

    received_state = request.query.get("state")
    error = request.query.get("error")
    code = request.query.get("code")

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
        return web.Response(text=_ERROR_HTML, content_type="text/html", status=400)

    if code is None:
        # State matched but no code — malformed redirect.
        return web.Response(text=_ERROR_HTML, content_type="text/html", status=400)

    # Exchange the code for tokens.
    try:
        result = await _exchange_code_for_tokens(flow, code)
    except Exception as e:
        if not flow.future.done():
            flow.future.set_exception(e)
        return web.Response(text=_ERROR_HTML, content_type="text/html", status=502)

    if not flow.future.done():
        flow.future.set_result(result)
    return web.Response(text=_SUCCESS_HTML, content_type="text/html")


async def _commit_grants(flow: _PendingFlow) -> None:
    """Write the per-agent grants requested at ``start_authorization`` time.

    Called only after the connection is persisted. Each grant is written
    through the same ledger the CLI/SDK/Settings panel use, so it is
    immediately visible and revocable there. Emits ``connector.grant.changed``
    per agent so subscribed UIs refresh the grants panel without a reload.

    Fails loudly: a grant that cannot be written raises ``ConnectorsError``,
    which propagates to ``_handle_callback`` and surfaces on the flow future.
    Connecting-without-granting is the bug this flow exists to prevent, so a
    grant failure must not be swallowed.
    """
    if not flow.grant_agents:
        return

    # Local import mirrors the lazy-keyring contract in connectors/__init__.py
    # and keeps flow.py's module-load dependency graph unchanged.
    from gaia.connectors.grants import grant_agent

    for agent_id, agent_scopes in flow.grant_agents.items():
        try:
            grant_agent(flow.provider_id, agent_id, list(agent_scopes))
        except Exception as e:
            raise ConnectorsError(
                f"Connected {flow.provider_id!r} but failed to grant it to "
                f"agent {agent_id!r}: {e}. The connection was saved; grant the "
                f"agent manually from Settings → Connectors, or via "
                f"`gaia connectors grants grant {flow.provider_id} {agent_id} "
                f"--scopes {' '.join(agent_scopes)}`."
            ) from e
        await emit(
            "connector.grant.changed",
            {
                "connector_id": flow.provider_id,
                "agent_id": agent_id,
                "scopes": list(agent_scopes),
            },
        )
        logger.info(
            "flow: granted connector_id=%s agent_id=%s scopes=%d on connect",
            flow.provider_id,
            agent_id,
            len(agent_scopes),
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
        raise ConnectorsError(
            f"Token exchange for {flow.provider_id} failed with status "
            f"{response.status_code}: {response.text}. See docs/security/connections.mdx."
        )
    payload = response.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise ConnectorsError(
            f"Token endpoint for {flow.provider_id} returned no "
            "refresh_token. Make sure the provider's "
            "authorization_params() includes the offline-access flags "
            "(Google requires access_type=offline + prompt=consent). See "
            "docs/security/connections.mdx."
        )

    account_email = await _resolve_account_email(
        provider, payload.get("id_token", ""), payload.get("access_token", "")
    )

    save_connection(
        provider=flow.provider_id,
        account_email=account_email or "default",
        refresh_token=refresh_token,
        scopes=flow.scopes,
        client_id_hash=provider.client_id_hash,
    )

    # No separate state-cache write needed — the keyring blob written
    # above is the source of truth for "configured / account / scopes",
    # and the router reads it via ``store.peek_connection`` for the UI.

    # #2117 — commit the per-agent grants requested at connect time, in the
    # same flow as the connection. This is what closes the consumer-breaking
    # dead end: a mailbox connected from the email surface now hands the
    # email agent access without a follow-up CLI grant. Fail loudly — a
    # connection that persisted but whose grant could not be written is the
    # exact silent half-success the connect flow must not produce.
    await _commit_grants(flow)

    # Google's token endpoint does not return a ``connected_at`` field
    # (RFC 6749 has no such concept) — record the local wall-clock at
    # exchange time. ``save_connection`` does the same for the keyring blob.
    import time as _time

    state_dict = {
        "provider": flow.provider_id,
        "account_email": account_email or "default",
        "scopes": flow.scopes,
        "connected_at": _time.time(),
    }
    # Emit both the new framework event-name (matches the SSE router
    # docstring and what the AgentUI listens for) and the legacy name
    # for any older subscribers. The keys ``connector_id`` /
    # ``account_email`` match the router-documented payload.
    await emit(
        "connector.oauth.completed",
        {
            "connector_id": flow.provider_id,
            "account_email": state_dict["account_email"],
        },
    )
    await emit(
        "connection.connected",
        {"provider": flow.provider_id, "account_email": state_dict["account_email"]},
    )
    return state_dict


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


async def start_device_flow(provider_id: str, scopes: Iterable[str]) -> Dict[str, Any]:
    """Request a device + user code from the provider's device-code endpoint.

    Returns ``{provider_id, scopes, device_code, user_code, verification_uri,
    expires_in, interval, message}``. The caller shows ``user_code`` +
    ``verification_uri`` (or the provider-supplied ``message``) to the user,
    then awaits :func:`poll_device_flow` with the returned ``device_code``.
    """
    provider = get_provider(provider_id)
    device_code_url = getattr(provider, "device_code_url", None)
    if not device_code_url:
        raise ConnectorsError(
            f"Provider {provider_id!r} does not support the device-code flow. "
            "Use start_authorization (browser loopback) instead. See "
            "docs/security/connections.mdx."
        )
    scopes_list = list(scopes) or list(provider.default_scopes)
    body = provider.device_code_request_body(scopes_list)

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(device_code_url, data=body)
    if resp.status_code != 200:
        raise ConnectorsError(
            f"Device-code request for {provider_id} failed with status "
            f"{resp.status_code}: {resp.text[:300]}. Check the client id / "
            "tenant (GAIA_MICROSOFT_CLIENT_ID / GAIA_MICROSOFT_TENANT). See "
            "docs/security/connections.mdx."
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
    }


async def poll_device_flow(
    provider_id: str,
    device_code: str,
    *,
    scopes: Iterable[str],
    interval: int = _DEVICE_POLL_DEFAULT_INTERVAL,
    expires_in: int = 900,
    grant_agents: Optional[Mapping[str, Iterable[str]]] = None,
) -> Dict[str, Any]:
    """Poll the token endpoint until the user approves the device code.

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
    scopes_list = list(scopes) or list(provider.default_scopes)
    body = provider.device_token_request_body(device_code)
    poll_interval = max(int(interval), 1)
    deadline = _time.monotonic() + max(int(expires_in), poll_interval)

    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            resp = await client.post(provider.token_url, data=body)
            if resp.status_code == 200:
                payload = resp.json()
                break
            try:
                err_payload = resp.json()
            except Exception:  # noqa: BLE001 — body may be empty/non-JSON
                err_payload = {}
            err = err_payload.get("error", "")
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
                raise ConnectorsError(
                    f"Device-code polling for {provider_id} failed: "
                    f"{err or resp.status_code}: "
                    f"{err_payload.get('error_description', resp.text[:200])}. "
                    "See docs/security/connections.mdx."
                )
            if _time.monotonic() >= deadline:
                raise FlowTimeoutError(
                    f"Device-code for {provider_id} timed out after "
                    f"{expires_in}s waiting for sign-in. Run connect again."
                )
            await asyncio.sleep(poll_interval)

    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise ConnectorsError(
            f"Device-code token response for {provider_id} returned no "
            "refresh_token. The 'offline_access' scope must be requested (it is "
            "in the Microsoft default_scopes). See docs/security/connections.mdx."
        )
    account_email = await _resolve_account_email(
        provider, payload.get("id_token", ""), payload.get("access_token", "")
    )

    save_connection(
        provider=provider_id,
        account_email=account_email,
        refresh_token=refresh_token,
        scopes=scopes_list,
        client_id_hash=provider.client_id_hash,
    )

    if grant_agents:
        from gaia.connectors.grants import grant_agent

        for agent_id, agent_scopes in grant_agents.items():
            try:
                grant_agent(provider_id, agent_id, list(agent_scopes))
            except Exception as e:
                raise ConnectorsError(
                    f"Connected {provider_id!r} via device code but failed to "
                    f"grant it to agent {agent_id!r}: {e}. Grant it manually "
                    f"with `gaia connectors grants grant {provider_id} "
                    f"{agent_id} --scopes {' '.join(agent_scopes)}`."
                ) from e

    await emit(
        "connector.oauth.completed",
        {"connector_id": provider_id, "account_email": account_email},
    )
    await emit(
        "connection.connected",
        {"provider": provider_id, "account_email": account_email},
    )
    logger.info(
        "device-flow: connected provider=%s account=%s scopes=%d",
        provider_id,
        account_email,
        len(scopes_list),
    )
    return {
        "provider": provider_id,
        "account_email": account_email,
        "scopes": scopes_list,
        "connected_at": _time.time(),
    }
