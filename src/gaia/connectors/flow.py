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
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

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


# v1 single-flow constraint per the plan: only one flow can be pending at
# a time. The dict shape is forward-compat for v2 multi-flow.
_pending: dict[str, _PendingFlow] = {}


def _decode_email_from_id_token(id_token: str) -> Optional[str]:
    """
    Extract the ``email`` claim from a Google id_token payload.

    Best-effort — base64url-decode the middle segment, parse JSON, return
    the ``email`` field. Production validation is deferred to the
    userinfo endpoint; this is a quick path for the success page.
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
    email = payload.get("email")
    return email if isinstance(email, str) else None


async def start_authorization(
    provider_id: str,
    scopes: Iterable[str],
) -> Dict[str, Any]:
    """
    Begin the OAuth flow for ``provider_id`` with the requested scopes.

    Returns ``{flow_id, authorization_url}``. Spins up a loopback aiohttp
    runner on an ephemeral port, stores the pending flow, fires a
    background callback to ``webbrowser.open(...)`` (in an executor to
    keep the event loop responsive), and returns immediately.

    The caller is expected to await ``complete_authorization(flow_id)``
    to wait for the redirect.
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
        "flow: started scopes=%d flow_id=%s",
        len(scopes_list),
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

    account_email = _decode_email_from_id_token(payload.get("id_token", "")) or ""

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
