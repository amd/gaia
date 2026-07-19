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
app (``gaia_agent_email.server``, which the frozen binary freezes) — the product
server (``gaia.api.openai_server``) and the OpenAPI export app mount the same
router unchanged, so their posture is untouched.

Three controls, all keyed on a single :class:`CallerAuthConfig` set at app build:

1. **Per-session bearer token** — the spawning parent (npm ``lifecycle.ts`` or
   the daemon's ``AgentSidecarManager``) generates a cryptographically-random
   token and hands it to the sidecar either as a 0600 file whose path arrives in
   ``GAIA_EMAIL_SIDECAR_TOKEN_FILE`` (preferred, #2149 — the secret never sits
   in the environment) or directly in ``GAIA_EMAIL_SIDECAR_TOKEN`` (legacy).
   Every non-exempt request must present ``Authorization: Bearer <token>`` or it
   is rejected with 401. This authenticates the *caller*.
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
from pathlib import Path
from typing import FrozenSet, Optional
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from gaia.logger import get_logger

logger = get_logger(__name__)

# Preferred channel (#2149): the spawning parent writes the token to a 0600,
# owner-only file and passes its PATH here — the secret itself never sits in the
# process environment (which any local process can read via /proc/<pid>/environ
# or `ps eww`). MUST equal the daemon's mirrored literal in
# gaia.daemon.sidecars.spec (kept as plain strings so core never imports this
# wheel).
TOKEN_FILE_ENV_VAR = "GAIA_EMAIL_SIDECAR_TOKEN_FILE"

# Legacy channel: the token directly in the environment. Still honored for
# older spawning parents and bare integrators; deprecated for daemon spawns
# (#2149). An env var (not argv) so the secret at least never shows up in a
# `ps`/Task Manager process listing the way command-line arguments do.
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
    """Build a policy from the environment.

    Preferred (#2149): ``GAIA_EMAIL_SIDECAR_TOKEN_FILE`` names a 0600 file the
    spawning parent wrote the token into — the secret never sits in the
    environment. A set path var whose file is missing/unreadable/empty is a
    LOUD startup error, never a silent auth-off skip. Legacy:
    ``GAIA_EMAIL_SIDECAR_TOKEN`` carries the token directly. Neither set →
    ``token=None`` — the token check is skipped (dev mode) but Host/Origin
    protection still applies.
    """
    token_path = os.environ.get(TOKEN_FILE_ENV_VAR) or None
    if token_path:
        if os.environ.get(TOKEN_ENV_VAR):
            logger.warning(
                "Both %s and %s are set; using the secret file (%s) and "
                "ignoring the bare env token.",
                TOKEN_FILE_ENV_VAR,
                TOKEN_ENV_VAR,
                token_path,
            )
        try:
            token = Path(token_path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise RuntimeError(
                f"{TOKEN_FILE_ENV_VAR} points at '{token_path}' but the "
                f"launch-secret file cannot be read: {e}. The spawning parent "
                "creates this file on spawn and removes it on sidecar exit — "
                "do not set the variable by hand unless the file exists. Unset "
                "it to run without caller auth (local development only)."
            ) from e
        if not token:
            raise RuntimeError(
                f"{TOKEN_FILE_ENV_VAR} points at '{token_path}' but the file "
                "is empty — refusing to start with an empty caller-auth token. "
                "Unset the variable to run without caller auth (local "
                "development only)."
            )
        return CallerAuthConfig(token=token)
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


class HostOriginMiddleware:
    """Reject non-loopback ``Host`` (400) and non-loopback ``Origin`` (403).

    The token check lives in the ``require_caller_token`` route dependency (so it
    can be scoped to the routers and skip the exempt probe/HTML paths); this
    middleware enforces the transport-level controls that must cover *every*
    request, including the exempt paths. No-ops when auth was never configured.

    Implemented as **pure ASGI** (not ``BaseHTTPMiddleware``) so it only inspects
    two request headers and then hands the untouched ``(scope, receive, send)``
    to the app — ``BaseHTTPMiddleware`` wraps the response body and can buffer /
    reorder streaming responses, which would break the line-by-line
    ``StreamingResponse`` from ``POST /v1/email/init``.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        config = get_config()
        if config is not None:
            headers = Headers(scope=scope)

            host = _host_only(headers.get("host", ""))
            if host and host not in config.allowed_hosts:
                await self._reject(
                    scope,
                    receive,
                    send,
                    400,
                    f"Rejected: Host header '{host}' is not an allowed loopback "
                    "host. The email sidecar serves only 127.0.0.1/localhost; a "
                    "non-loopback Host is a DNS-rebinding attempt.",
                )
                return

            origin = headers.get("origin")
            if origin is not None:
                origin_host = (urlsplit(origin).hostname or "").lower()
                if origin_host not in config.allowed_origin_hosts:
                    await self._reject(
                        scope,
                        receive,
                        send,
                        403,
                        f"Rejected: cross-origin request from Origin '{origin}'. "
                        "The email sidecar refuses browser origins other than "
                        "loopback (drive-by / DNS-rebinding protection).",
                    )
                    return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send, status: int, detail: str) -> None:
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


__all__ = [
    "TOKEN_ENV_VAR",
    "TOKEN_FILE_ENV_VAR",
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
