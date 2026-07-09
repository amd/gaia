# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Caller authentication for the email sidecar's local REST API (#1706).

The frozen sidecar binds ``127.0.0.1`` and exposes draft/send against the user's
connected mailbox. A no-auth localhost API is reachable by any *other* local
process and — via DNS-rebinding / a drive-by web page — by the user's browser,
which is not an acceptable posture for a surface that can send mail as the user.
The existing draft→send *confirmation token* is a payload-integrity check (it
binds a send to one exact message); it is NOT caller authentication.

This module adds the missing caller-auth layer. It is wired ONLY onto the sidecar
app (``packaging/server.py``) — the product server (``gaia.api.openai_server``)
and the OpenAPI export app mount the same router unchanged, so their posture is
untouched.

Three controls, all keyed on a single :class:`CallerAuthConfig` set at app build:

1. **Per-session bearer token** — the spawning parent (npm ``lifecycle.ts`` or the
   Python ``EmailSidecarManager``) generates a cryptographically-random token and
   hands it to the sidecar over the private ``GAIA_EMAIL_SIDECAR_TOKEN`` env
   channel. Every non-exempt request must present ``Authorization: Bearer
   <token>`` or it is rejected with 401. This authenticates the *caller*.
2. **Host allowlist** — the ``Host`` header must be a loopback host
   (127.0.0.1 / localhost / ::1). This closes DNS-rebinding, where a victim's
   browser is tricked into resolving ``evil.com`` → 127.0.0.1 and posting to the
   sidecar (the browser then sends ``Host: evil.com``).
3. **Origin rejection** — a request carrying a browser ``Origin`` that is not a
   loopback origin is rejected with 403. This closes a drive-by web page that
   fetches ``http://127.0.0.1:<port>`` directly. Non-browser clients (the npm /
   Python clients, curl) send no ``Origin`` and are unaffected.

Fail-loud: rejections return an actionable status + message, never a silent
degrade. When no token is configured (a developer running the sidecar by hand
without the env var), the token check is skipped and a loud warning is logged —
the Host/Origin controls still apply. Production always spawns via a parent that
sets the token, so the shipped product is always authenticated.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from typing import FrozenSet, Optional
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from gaia.logger import get_logger

logger = get_logger(__name__)

# The private channel the spawning parent uses to hand the sidecar its token.
# An env var (not argv) so the secret never shows up in a `ps`/Task Manager
# process listing the way command-line arguments do.
TOKEN_ENV_VAR = "GAIA_EMAIL_SIDECAR_TOKEN"

# Hosts the sidecar is allowed to be reached as. The sidecar only ever binds a
# loopback interface, so any other Host header is a rebinding attempt.
LOOPBACK_HOSTS: FrozenSet[str] = frozenset({"127.0.0.1", "localhost", "::1"})

# Router/app paths that never require a token: liveness/version probes (the
# readiness handshake polls these before mail is ever in play) and the
# human-facing HTML pages. None expose mailbox data. Host/Origin controls still
# apply to them.
EXEMPT_PATHS: FrozenSet[str] = frozenset(
    {
        "/health",
        "/version",
        "/v1/email/health",
        "/v1/email/version",
        "/v1/email/spec",
        "/v1/email/playground",
    }
)


def generate_session_token() -> str:
    """Mint a fresh, cryptographically-random per-session bearer token.

    URL-safe so it survives an ``Authorization`` header and any env/JSON channel
    verbatim. 32 bytes of entropy (~43 chars) — unguessable.
    """
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class CallerAuthConfig:
    """The active caller-auth policy for the sidecar app.

    ``token`` is the per-session bearer secret; ``None`` disables the token check
    (dev-only, logged loudly) while leaving the Host/Origin controls in force.
    ``allowed_hosts`` / ``allowed_origin_hosts`` are lowercase host names (no
    port) — defaults to the loopback set.
    """

    token: Optional[str]
    allowed_hosts: FrozenSet[str] = LOOPBACK_HOSTS
    allowed_origin_hosts: FrozenSet[str] = LOOPBACK_HOSTS


# Process-wide active config. Set by the sidecar app at build time; left None in
# any process (product server, OpenAPI export) that never calls configure().
_active: Optional[CallerAuthConfig] = None


def configure(config: CallerAuthConfig) -> None:
    """Install the active caller-auth policy for this process."""
    global _active
    _active = config


def reset() -> None:
    """Clear the active policy (test-isolation seam)."""
    global _active
    _active = None


def get_config() -> Optional[CallerAuthConfig]:
    """Return the active policy, or ``None`` when auth was never configured."""
    return _active


def config_from_env() -> CallerAuthConfig:
    """Build a policy from the environment (``GAIA_EMAIL_SIDECAR_TOKEN``).

    A missing/empty env var yields ``token=None`` — the token check is then
    skipped (dev mode) but Host/Origin protection still applies.
    """
    token = os.environ.get(TOKEN_ENV_VAR) or None
    return CallerAuthConfig(token=token)


def is_exempt_path(path: str) -> bool:
    """Whether ``path`` is exempt from the token requirement (probes / HTML)."""
    return path in EXEMPT_PATHS


def _host_only(header_value: str) -> str:
    """Extract the bare host from a ``Host`` header value, dropping the port.

    Handles the IPv6 literal form ``[::1]:8131`` as well as ``127.0.0.1:8131``.
    Returns a lowercased host (``""`` when the header is empty).
    """
    value = (header_value or "").strip()
    if not value:
        return ""
    if value.startswith("["):  # IPv6 literal: [::1]:port
        end = value.find("]")
        return value[1:end].lower() if end != -1 else value.lower()
    return value.split(":", 1)[0].strip().lower()


def token_ok(config: CallerAuthConfig, authorization_header: str) -> bool:
    """Constant-time check of an ``Authorization: Bearer <token>`` header.

    Returns True only when a token is configured AND the presented bearer token
    matches it. Uses :func:`hmac.compare_digest` so a wrong token can't be
    timed out character by character.
    """
    if config.token is None:
        return True  # token check disabled (dev)
    header = (authorization_header or "").strip()
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented.strip():
        return False
    return hmac.compare_digest(presented.strip(), config.token)


class HostOriginMiddleware(BaseHTTPMiddleware):
    """Reject non-loopback ``Host`` (400) and non-loopback ``Origin`` (403).

    The token check lives in the ``require_caller_token`` route dependency (so it
    can be scoped to the email router and skip the exempt probe/HTML paths); this
    middleware enforces the transport-level controls that must cover *every*
    request, including the exempt paths. No-ops when auth was never configured.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        config = get_config()
        if config is None:
            return await call_next(request)

        host = _host_only(request.headers.get("host", ""))
        if host and host not in config.allowed_hosts:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        f"Rejected: Host header '{host}' is not an allowed "
                        "loopback host. The email sidecar serves only "
                        "127.0.0.1/localhost; a non-loopback Host is a "
                        "DNS-rebinding attempt."
                    )
                },
            )

        origin = request.headers.get("origin")
        if origin is not None:
            origin_host = (urlsplit(origin).hostname or "").lower()
            if origin_host not in config.allowed_origin_hosts:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": (
                            f"Rejected: cross-origin request from Origin "
                            f"'{origin}'. The email sidecar refuses browser "
                            "origins other than loopback (drive-by / "
                            "DNS-rebinding protection)."
                        )
                    },
                )

        return await call_next(request)


__all__ = [
    "TOKEN_ENV_VAR",
    "LOOPBACK_HOSTS",
    "EXEMPT_PATHS",
    "CallerAuthConfig",
    "HostOriginMiddleware",
    "generate_session_token",
    "configure",
    "reset",
    "get_config",
    "config_from_env",
    "is_exempt_path",
    "token_ok",
]
