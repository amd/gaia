# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Cross-origin request defenses for the Agent UI backend.

The backend binds loopback and has no authentication while the tunnel is
off, so any web page the user visits could drive it. Three layers close
that off, all applied in middleware so a route added tomorrow is covered
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
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

#: Header every first-party Agent UI client sends on mutating requests.
UI_HEADER_NAME = "x-gaia-ui"
UI_HEADER_VALUE = "1"

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

    Kept as a dependency for the few *read* routes that opt into it
    explicitly (``GET /api/agents/disk`` and friends). Every mutating
    route is covered by :class:`UIRequestGuardMiddleware` whether or not
    its decorator names this.
    """
    if request.headers.get(UI_HEADER_NAME) != UI_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="missing X-Gaia-UI header")


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
            await _send_rejection(send, 400, "Invalid Host header")
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
