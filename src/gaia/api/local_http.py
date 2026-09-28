# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""One cross-origin and caller-auth policy for GAIA's local HTTP servers.

Three servers bind loopback on this machine and each answered the same two
questions differently: *which browser origins may read a response*, and *who
may call at all*. ``gaia api`` got CORS right and auth wrong; the generic
``AgentServer`` (``--api``) reflected every requesting Origin back with
credentials; the MCP bridge answered every origin ``*``. Loopback is not a
boundary against any of that -- the attacker is a page in the user's own
browser reaching ``http://localhost:<port>``.

This module is the single copy of the answer, so the next server inherits the
policy instead of inventing a fourth one. It deliberately imports no web
framework at module scope: the MCP bridge runs on ``http.server`` and has to
ask the same questions.

Related: :mod:`gaia.ui.security` enforces the equivalent rules for the Agent
UI backend, which additionally has an ``X-Gaia-UI`` CSRF header that all of
its first-party clients send.
"""

import ipaddress
import os
import re
import secrets
from typing import List, Optional, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)

# -- Origins -----------------------------------------------------------------

#: Browser origins allowed by default: localhost / 127.0.0.1 / [::1] on any
#: port. GAIA's own local pages (the example app's dev server on :3000, a
#: notebook) live here; a page on the open web does not.
LOCAL_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
_LOCAL_ORIGIN_RE = re.compile(LOCAL_ORIGIN_REGEX)

#: Comma-separated extra origins, e.g. ``https://myapp.example.com``.
CORS_ORIGINS_ENV = "GAIA_API_CORS_ORIGINS"


def extra_origins(env_var: str = CORS_ORIGINS_ENV) -> List[str]:
    """Operator-configured origins from *env_var*. Empty by default."""
    raw = os.environ.get(env_var, "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def is_local_origin(origin: str) -> bool:
    """Whether *origin* is a loopback origin on this machine."""
    return bool(origin) and bool(_LOCAL_ORIGIN_RE.match(origin.strip()))


def is_allowed_origin(origin: str, env_var: str = CORS_ORIGINS_ENV) -> bool:
    """Whether a browser at *origin* may read this server's responses.

    A request with no ``Origin`` is not a browser request and is not this
    function's business -- callers decide that separately.
    """
    origin = (origin or "").strip()
    if not origin:
        return False
    allowed = extra_origins(env_var)
    if "*" in allowed:
        return True
    return is_local_origin(origin) or origin in allowed


def cors_config(env_var: str = CORS_ORIGINS_ENV) -> dict:
    """Build a Starlette ``CORSMiddleware`` policy: localhost-only by default.

    *env_var* (comma-separated) adds extra allowed origins. A literal ``*``
    opts into open CORS, which the Fetch spec forbids combining with
    credentials -- so the wildcard also disables credentialed requests.
    Wildcard origins WITH credentials are never configured: Starlette reflects
    any request Origin back in that combination, which is exactly what lets
    any website the user visits read this local, unauthenticated API.
    """
    origins = extra_origins(env_var)
    if "*" in origins:
        logger.warning(
            "%s='*': allowing all origins WITHOUT credentials. To allow "
            "credentialed cross-origin calls, list explicit origins instead "
            "of '*'.",
            env_var,
        )
        return {
            "allow_origins": ["*"],
            "allow_credentials": False,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }
    return {
        "allow_origins": origins,
        "allow_origin_regex": LOCAL_ORIGIN_REGEX,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }


def origin_is_rejected(origin: str, env_var: str = CORS_ORIGINS_ENV) -> bool:
    """Whether a request carrying *origin* must be refused outright.

    A request with no ``Origin`` header is not from a browser (curl, an MCP
    client, the Electron main process) and is left alone -- there is no
    cross-site attack to mount without one. A request that names an origin
    this server does not trust is refused before it reaches a handler, so a
    CORS misconfiguration is never the only thing standing between a random
    web page and the agent loop.
    """
    return bool((origin or "").strip()) and not is_allowed_origin(origin, env_var)


# -- Binds -------------------------------------------------------------------


def is_loopback_bind(host: str) -> bool:
    """Whether binding *host* keeps the server on this machine.

    An empty host, ``0.0.0.0`` and ``::`` all listen on every interface.
    """
    host = (host or "").strip().lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A name we cannot classify. Treat as non-loopback: assuming the safe
        # answer for an unknown name is how an exposed bind slips through.
        return False


# -- Caller auth -------------------------------------------------------------

#: Environment variable holding the API key that gates the agent surfaces.
API_KEY_ENV = "GAIA_API_KEY"
#: Auth scheme for the API key (mirrors the daemon's Bearer contract).
AUTH_SCHEME = "Bearer"

#: Surfaces that have already logged their "running unauthenticated" warning,
#: so a per-request dependency does not repeat it on every call.
_WARNED_SURFACES: set = set()


def api_key_configured() -> bool:
    """Whether an API key is set. An empty value counts as unset."""
    return bool(os.environ.get(API_KEY_ENV))


def warn_if_unauthenticated(surface: str) -> None:
    """Say once, loudly, that *surface* answers anyone who can reach it."""
    if api_key_configured() or surface in _WARNED_SURFACES:
        return
    _WARNED_SURFACES.add(surface)
    logger.warning(
        "%s is running with NO caller authentication: any process on this "
        "machine -- including a web page the user happens to be visiting -- "
        "can drive the agent loop. Set %s to require 'Authorization: %s "
        "<key>'. A future release will make this mandatory.",
        surface,
        API_KEY_ENV,
        AUTH_SCHEME,
    )


class UnauthenticatedBindError(ValueError):
    """A server was asked to listen beyond loopback with no caller auth."""


def assert_bind_is_authenticated(host: str, surface: str) -> None:
    """Refuse a network-facing bind that no caller has to authenticate to.

    Loopback without a key is allowed and warned about once -- making a key
    mandatory there would break every existing ``gaia api`` / ``--api``
    consumer overnight. Binding beyond loopback without one is refused: it
    puts an agent loop that reads and writes the user's files on the network
    for anyone who can route to the port.
    """
    if api_key_configured() or is_loopback_bind(host):
        warn_if_unauthenticated(surface)
        return
    raise UnauthenticatedBindError(
        f"Refusing to serve {surface} on {host!r} with no caller "
        f"authentication. That address is reachable from the network, and the "
        f"agent surface runs tools on this machine. Set {API_KEY_ENV} in the "
        f"server's environment (e.g. "
        f"`export {API_KEY_ENV}=$(openssl rand -hex 32)`) and send it as "
        f"'Authorization: {AUTH_SCHEME} <key>', or bind localhost instead. "
        f"See https://amd-gaia.ai/docs/sdk/infrastructure/api-server."
    )


def check_api_key(
    authorization: Optional[str], *, surface: str, required: bool
) -> Optional[Tuple[int, str]]:
    """Validate an ``Authorization`` header against ``GAIA_API_KEY``.

    Returns ``None`` when the request may proceed, otherwise an
    ``(http_status, detail)`` pair. Framework-free so the FastAPI dependency
    below and any non-FastAPI handler share one implementation.

    *required* distinguishes the two postures. ``True`` (the ``/v1/<agent>/*``
    relay) means an unset key disables the surface entirely. ``False`` (chat
    completions, the ``AgentServer`` REST API) means an unset key leaves the
    surface open for backward compatibility, warned about once -- but a key
    that IS set is enforced on every request.
    """
    expected = os.environ.get(API_KEY_ENV)
    if not expected:
        if not required:
            warn_if_unauthenticated(surface)
            return None
        return (
            503,
            f"{surface} is disabled: no API key is configured. Set "
            f"{API_KEY_ENV} in the server's environment (e.g. "
            f"`export {API_KEY_ENV}=$(openssl rand -hex 32)`), restart it, "
            f"and send the key as 'Authorization: {AUTH_SCHEME} <key>'.",
        )
    if not authorization:
        return (
            401,
            f"Missing API key. Send 'Authorization: {AUTH_SCHEME} <key>' "
            f"matching {API_KEY_ENV} on the server.",
        )
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != AUTH_SCHEME.lower() or not credential:
        return (
            401,
            f"Malformed Authorization header. Expected '{AUTH_SCHEME} <key>'.",
        )
    # Bytes, not str: str compare_digest raises TypeError on non-ASCII input.
    if not secrets.compare_digest(
        credential.encode("utf-8", "surrogateescape"),
        expected.encode("utf-8", "surrogateescape"),
    ):
        return (
            401,
            f"Invalid API key. It must match {API_KEY_ENV} on the server.",
        )
    return None


def build_require_api_key(surface: str, required: bool = True):
    """FastAPI dependency wrapping :func:`check_api_key`.

    No silent fallback in either posture: every rejection is an HTTP error
    naming what failed, what to send, and where the key comes from.
    """
    from fastapi import Header, HTTPException

    def require_api_key(authorization: Optional[str] = Header(default=None)) -> None:
        failure = check_api_key(authorization, surface=surface, required=required)
        if failure is None:
            return
        status, detail = failure
        headers = {"WWW-Authenticate": AUTH_SCHEME} if status == 401 else None
        raise HTTPException(status_code=status, detail=detail, headers=headers)

    setattr(require_api_key, CALLER_GUARD_MARKER, True)
    return require_api_key


#: Methods a page can trigger cross-site that do not change state. OPTIONS
#: never reaches a route -- ``CORSMiddleware`` answers preflights first.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Attribute stamped on every guard :func:`build_caller_guard` returns, so a
#: route-table test can prove a route carries one without importing it.
CALLER_GUARD_MARKER = "__gaia_caller_guard__"


#: Paths :func:`build_caller_guard` leaves open by default: liveness only.
DEFAULT_PUBLIC_PATHS = frozenset({"/health"})


def build_caller_guard(
    surface: str,
    *,
    public_paths: Optional[frozenset] = None,
    origins_env: str = CORS_ORIGINS_ENV,
):
    """FastAPI dependency: refuse foreign browser origins, then check the key.

    Attach it app-wide (``FastAPI(dependencies=[Depends(guard)])``) so a route
    added later is covered without its author doing anything.

    1. A mutating request whose ``Origin`` is not allowed is a 403 -- before
       any handler runs, whatever the CORS headers would have said.
    2. Every non-public path goes through :func:`check_api_key` with
       ``required=False``: enforced when ``GAIA_API_KEY`` is set, warned
       about once when it is not.
    """
    from fastapi import HTTPException, Request

    if public_paths is None:
        public_paths = DEFAULT_PUBLIC_PATHS

    def caller_guard(request: Request) -> None:
        origin = request.headers.get("origin", "")
        if request.method not in SAFE_METHODS and origin_is_rejected(
            origin, origins_env
        ):
            logger.warning(
                "Rejecting %s %s on %s: cross-origin request from %r",
                request.method,
                request.url.path,
                surface,
                origin,
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Cross-origin request from {origin!r} rejected. Only "
                    f"loopback origins may call {surface}; add yours to "
                    f"{origins_env} (comma-separated) to allow it."
                ),
            )
        if request.url.path in public_paths:
            return
        failure = check_api_key(
            request.headers.get("authorization"), surface=surface, required=False
        )
        if failure is None:
            return
        status, detail = failure
        headers = {"WWW-Authenticate": AUTH_SCHEME} if status == 401 else None
        raise HTTPException(status_code=status, detail=detail, headers=headers)

    setattr(caller_guard, CALLER_GUARD_MARKER, True)
    return caller_guard
