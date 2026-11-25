# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Request defenses for the Agent UI backend.

Every ``/api`` and ``/v1`` request must present the Agent UI API token
(:class:`ApiTokenMiddleware`, :mod:`gaia.ui.api_token`), local callers
included: binding loopback is not access control. Three more layers stop a
web page the user visits from driving the backend through the user's own
browser, all applied in middleware so a route added tomorrow is covered
without its author remembering anything:

1. **CSRF header** -- every mutating (non-GET/HEAD/OPTIONS) request to
   ``/api/*`` or ``/v1/*`` must carry ``X-Gaia-UI: 1``. A page cannot set
   a custom header on a cross-origin request without a CORS preflight,
   and the app's CORS policy approves preflights only from its own
   first-party dev origins.
2. **Origin allowlist** -- a mutating request whose ``Origin`` names
   somewhere other than this server, loopback, or the live tunnel is
   refused outright, so a CORS misconfiguration alone is not enough.
3. **Host allowlist** -- DNS rebinding needs a *hostname* that resolves
   to loopback, so a ``Host`` that is neither an IP literal, nor
   loopback, nor the live tunnel is refused.

``Origin: null`` and a missing ``Origin`` are accepted: the packaged
Electron shell serves the SPA from ``file://`` (an opaque origin) and
non-browser callers send no ``Origin`` at all. Neither can be produced by
a cross-site page that *also* carries ``X-Gaia-UI`` -- layer 1 stops
those, and a sandboxed iframe's ``null``-origin preflight is refused by
CORS before the real request is ever sent.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Receive, Scope, Send

from gaia.ui import api_token

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

#: Header every first-party Agent UI client sends on mutating requests.
UI_HEADER_NAME = "x-gaia-ui"
UI_HEADER_VALUE = "1"

#: Sent by the Agent UI frontend itself, which talks to the flagship only.
#: Other clients (eval harness, MCP bridge) may still pick any registered agent.
CLIENT_HEADER_NAME = "x-gaia-client"
AGENT_UI_CLIENT = "agent-ui"
FLAGSHIP_AGENT_ID = "gaia"

#: Path prefixes the guards apply to -- everything a client calls.
GUARDED_PREFIXES = ("/api/", "/v1/")

#: Methods a cross-site page can trigger without a preflight but that do
#: not change state. ``OPTIONS`` must pass through so ``CORSMiddleware``
#: can answer preflights.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Mutating paths exempt from the CSRF header.
#:
#: Deliberately empty. The OAuth loopback callback -- the one flow that
#: cannot send a custom header, because the provider redirects the
#: browser to it -- is a ``GET`` served by its own aiohttp server in
#: ``gaia.connectors.flow``, not by this app. An entry here is a hole, so
#: ``tests/unit/chat/ui/test_ui_request_guard.py`` asserts it stays empty.
CSRF_EXEMPT_PATHS: frozenset = frozenset()

#: Paths that need no token: a liveness probe that returns no user data.
TOKEN_EXEMPT_PATHS: frozenset = frozenset({"/api/health"})

#: Cookie a phone carries after opening the mobile-access QR link.
TUNNEL_COOKIE_NAME = "gaia_tunnel_token"

#: Hostnames that always denote this machine.
_LOOPBACK_NAMES = frozenset({"localhost", "::1"})

#: Explicit opt-in for deployments behind a proxy or a named host.
#: Comma-separated. Never populated by default.
ENV_ALLOWED_ORIGINS = "GAIA_UI_ALLOWED_ORIGINS"
ENV_ALLOWED_HOSTS = "GAIA_UI_ALLOWED_HOSTS"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _env_values(name: str) -> tuple:
    """Comma-separated, lowercased values of an allowlist env var."""
    return tuple(
        v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip()
    )


def _hostname(authority: str) -> str:
    """Lowercased hostname of a ``host[:port]`` authority.

    IPv6 authorities keep their brackets in ``Host``/``Origin``; strip
    them so the result compares equal to ``ipaddress`` output.
    """
    host = authority.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return host[1:end]
    if host.count(":") > 1:
        # Bare IPv6 with no brackets. RFC 3986 requires them, so nothing
        # correct sends this -- but splitting on the last colon would turn
        # "::1" into ":" and quietly fail the loopback check.
        return host
    return host.rsplit(":", 1)[0] if ":" in host else host


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _is_loopback_name(hostname: str) -> bool:
    if hostname in _LOOPBACK_NAMES or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _tunnel_host(app) -> Optional[str]:
    """Hostname of the live tunnel, or ``None`` when no tunnel is up."""
    tunnel = getattr(getattr(app, "state", None), "tunnel", None)
    if tunnel is None or not getattr(tunnel, "active", False):
        return None
    url = (tunnel.get_status() or {}).get("url")
    # A tunnel can be flagged active a beat before its URL is minted.
    if not isinstance(url, str) or not url:
        return None
    return _hostname(urlsplit(url).netloc)


def is_allowed_host(host_header: str, app) -> bool:
    """Whether ``Host`` may address this server.

    Any IP literal passes: DNS rebinding needs a *name* to re-point, so a
    literal cannot be used for it, and rejecting literals would break
    ``--host 0.0.0.0`` LAN access. Names must be loopback, the live
    tunnel, or listed in ``GAIA_UI_ALLOWED_HOSTS``.
    """
    if not host_header:
        # HTTP/1.0 and some local probes omit Host entirely; with no name
        # there is nothing to rebind.
        return True
    hostname = _hostname(host_header)
    if _is_ip_literal(hostname) or _is_loopback_name(hostname):
        return True
    if hostname == _tunnel_host(app):
        return True
    allowed = _env_values(ENV_ALLOWED_HOSTS)
    return hostname in allowed or host_header.strip().lower() in allowed


def is_allowed_origin(origin: str, host_header: str, app) -> bool:
    """Whether a mutating request's ``Origin`` is first-party.

    ``null`` and absent origins pass -- see the module docstring. Anything
    else must be same-origin with the request's own ``Host``, loopback,
    the live tunnel, or listed in ``GAIA_UI_ALLOWED_ORIGINS``.
    """
    if not origin or origin.strip().lower() == "null":
        return True
    if origin.strip().lower() in _env_values(ENV_ALLOWED_ORIGINS):
        return True
    hostname = _hostname(urlsplit(origin).netloc)
    if not hostname:
        return False
    if host_header and hostname == _hostname(host_header):
        return True
    if _is_loopback_name(hostname):
        return True
    return hostname == _tunnel_host(app)


def require_ui_header(request: Request) -> None:
    """Require ``X-Gaia-UI: 1`` -- the single copy of the route-level guard.

    Kept as a dependency so a *read* route can opt into it explicitly.
    Every mutating route is covered by :class:`UIRequestGuardMiddleware`
    whether or not its decorator names this.
    """
    if request.headers.get(UI_HEADER_NAME) != UI_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="missing X-Gaia-UI header")


_FORWARDED_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto")


def via_tunnel(request: Request) -> bool:
    """The request came over mobile access: ngrok adds ``X-Forwarded-*``."""
    return any(h in request.headers for h in _FORWARDED_HEADERS)


def require_local_pc(request: Request) -> None:
    """Refuse a request that did not come from the PC running GAIA.

    The tunnel connects from loopback, so the peer address alone can't tell a
    paired phone from the desktop; the forwarded headers ngrok adds can.
    """
    host = request.client.host if request.client else ""
    if not _is_loopback_name(host) or via_tunnel(request):
        raise HTTPException(
            status_code=403,
            detail="Change this on the PC running GAIA. It can't be changed "
            "over mobile access or from another computer.",
        )


def flagship_only(
    request: Request, agent_type: Optional[str], stored: Optional[str] = None
) -> Optional[str]:
    """Hold Agent UI requests to the flagship; return the agent type to use.

    Requests from other clients pass through unchanged. From the Agent UI, a
    requested non-flagship agent is a 422, and a chat stored under a retired
    agent is a 409 that says to start a new chat -- never a silent reroute.
    """
    if request.headers.get(CLIENT_HEADER_NAME) != AGENT_UI_CLIENT:
        return agent_type
    if agent_type and agent_type != FLAGSHIP_AGENT_ID:
        raise HTTPException(
            status_code=422,
            detail=(
                f"The Agent UI runs the GAIA agent only; agent_type {agent_type!r} "
                "is not available here. Use `gaia eval agent --agent-type` or the "
                "CLI to run another agent."
            ),
        )
    if stored is not None and stored != FLAGSHIP_AGENT_ID:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This chat was made with the retired {stored!r} agent, so it is "
                "read-only. Start a new chat; GAIA covers what that agent did."
            ),
        )
    return FLAGSHIP_AGENT_ID


# ── Middleware ──────────────────────────────────────────────────────────────


async def _send_rejection(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class UIRequestGuardMiddleware:
    """Enforce the Host, Origin, and ``X-Gaia-UI`` rules for the whole app.

    Pure ASGI rather than ``BaseHTTPMiddleware`` so it adds no task-group
    hop around the SSE streams it sits in front of.

    Registered last in ``create_app`` so it is the *outermost* middleware:
    a rejection then carries no CORS headers, and no route or auth
    shortcut downstream can skip it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        app = scope.get("app")
        host_header = headers.get("host", "")
        method = scope.get("method", "")
        path = scope.get("path", "")

        if not is_allowed_host(host_header, app):
            logger.warning(
                "Rejecting %s %s: untrusted Host %r (possible DNS rebinding)",
                method,
                path,
                host_header,
            )
            await _send_rejection(
                send,
                400,
                f"Invalid Host header: {host_header!r} is not a name this "
                f"server answers to. Reach the Agent UI on localhost or its "
                f"IP address, or set {ENV_ALLOWED_HOSTS} to a comma-separated "
                f"list of hostnames to allow.",
            )
            return

        if (
            method not in SAFE_METHODS
            and path.startswith(GUARDED_PREFIXES)
            and path not in CSRF_EXEMPT_PATHS
        ):
            origin = headers.get("origin", "")
            if not is_allowed_origin(origin, host_header, app):
                logger.warning(
                    "Rejecting %s %s: cross-origin request from %r",
                    method,
                    path,
                    origin,
                )
                await _send_rejection(send, 403, "Cross-origin request rejected")
                return
            if headers.get(UI_HEADER_NAME) != UI_HEADER_VALUE:
                logger.warning(
                    "Rejecting %s %s: missing %s header (origin=%r)",
                    method,
                    path,
                    UI_HEADER_NAME,
                    origin,
                )
                await _send_rejection(send, 403, "missing X-Gaia-UI header")
                return

        await self.app(scope, receive, send)


class ApiTokenMiddleware:
    """Require the Agent UI API token on every ``/api`` and ``/v1`` request.

    Accepted from ``Authorization: Bearer`` or the ``gaia_ui_token`` cookie.
    While mobile access is on, a request that came through the tunnel may
    present the tunnel's own token instead, so a paired phone works without
    the install's token; :func:`require_local_pc` still refuses such requests.
    ``OPTIONS`` passes so CORS can answer preflights, which carry no
    credentials.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "")
        if (
            not path.startswith(GUARDED_PREFIXES)
            or path in TOKEN_EXEMPT_PATHS
            or method == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return

        app = scope.get("app")
        expected = getattr(getattr(app, "state", None), "ui_api_token", None)
        if not expected:
            raise RuntimeError(
                "ApiTokenMiddleware is installed but app.state.ui_api_token is "
                "unset; build the app with gaia.ui.server.create_app()."
            )
        headers = Headers(scope=scope)
        cookies = cookie_parser(headers.get("cookie", ""))
        presented = api_token.bearer(headers.get("authorization", ""))
        if api_token.token_matches(expected, presented) or api_token.token_matches(
            expected, cookies.get(api_token.COOKIE_NAME)
        ):
            await self.app(scope, receive, send)
            return

        tunnel = getattr(app.state, "tunnel", None)
        forwarded = any(h in headers for h in _FORWARDED_HEADERS)
        if tunnel is not None and tunnel.active and forwarded:
            tunnel_token = presented or cookies.get(TUNNEL_COOKIE_NAME)
            if tunnel_token and tunnel.validate_token(tunnel_token):
                tunnel.note_device(headers.get("user-agent", ""))
                await self.app(scope, receive, send)
                return

        client = scope.get("client")
        logger.warning(
            "Rejecting %s %s from %s: no valid Agent UI API token",
            method,
            path,
            client[0] if client else "unknown",
        )
        body = json.dumps(
            {
                "detail": (
                    "This request has no valid GAIA Agent UI token. Open the "
                    "Agent UI from the GAIA desktop app, or from the sign-in "
                    "link printed when it started. Scripts send 'Authorization: "
                    f"Bearer <token>' with the token in the "
                    f"{api_token.TOKEN_FILE_NAME} file in GAIA's home folder."
                ),
                "code": api_token.REJECTION_CODE,
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
