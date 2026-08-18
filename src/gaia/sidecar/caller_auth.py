# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Caller authentication for a local agent sidecar's REST API.

A sidecar binds loopback and speaks for the user's data with the user's
privileges. "Loopback only" is not access control: any other local process can
reach it, and so can the user's *browser* — via DNS rebinding (a page resolves
``evil.com`` → 127.0.0.1) or a plain drive-by ``fetch`` at
``http://127.0.0.1:<port>``. The browser vector is the sharp one, because it
needs no local foothold at all.

Three controls, keyed on a :class:`CallerAuthConfig` installed at app build:

1. **Per-session bearer token** — the spawning parent mints a random token and
   passes it as a 0600 file whose path arrives in the *token-file* env var
   (preferred; the secret never sits in the environment, where any local
   process can read it via ``/proc/<pid>/environ`` or ``ps eww``) or directly
   in the *token* env var (legacy). Non-exempt requests must present
   ``Authorization: Bearer <token>``.
2. **Host allowlist** — the ``Host`` header must be loopback, which is what
   defeats DNS rebinding (the rebound request carries ``Host: evil.com``).
3. **Origin rejection** — a request carrying a non-loopback browser ``Origin``
   is refused. Non-browser clients send no ``Origin`` and are unaffected.

Fail-loud: rejections carry an actionable message, never a silent degrade. With
no token configured the token check is skipped and a loud warning is logged, but
Host/Origin still apply — so a hand-run developer sidecar keeps the control that
stops a web page driving it.

Each sidecar binds its own env-var names and exempt paths; everything else is
shared so the two cannot drift apart.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import FrozenSet, Optional
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from gaia.logger import get_logger

logger = get_logger(__name__)

# Hosts a sidecar may be reached as. It only ever binds loopback, so any other
# Host header is a rebinding attempt.
LOOPBACK_HOSTS: FrozenSet[str] = frozenset({"127.0.0.1", "localhost", "::1"})


def generate_session_token() -> str:
    """Mint a fresh, cryptographically-random per-session bearer token.

    URL-safe so it survives an ``Authorization`` header and any env/JSON channel
    verbatim. 32 bytes of entropy (~43 chars) — unguessable.
    """
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class CallerAuthConfig:
    """The active caller-auth policy for one sidecar app.

    ``token`` is the per-session bearer secret; ``None`` disables the token check
    (dev-only, logged loudly) while leaving Host/Origin in force. ``surface`` names
    the sidecar in rejection messages. ``exempt_paths`` skip the token check only —
    Host/Origin still cover them.
    """

    token: Optional[str]
    surface: str = "sidecar"
    allowed_hosts: FrozenSet[str] = LOOPBACK_HOSTS
    allowed_origin_hosts: FrozenSet[str] = LOOPBACK_HOSTS
    exempt_paths: FrozenSet[str] = field(default_factory=frozenset)


# Process-wide active config. One sidecar app per process; left None in any
# process (product server, OpenAPI export) that never calls configure().
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


def config_from_env(
    *,
    token_file_env_var: str,
    token_env_var: str,
    surface: str,
    exempt_paths: FrozenSet[str] = frozenset(),
) -> CallerAuthConfig:
    """Build a policy from the environment.

    A set token-file path whose file is missing/unreadable/empty is a LOUD
    startup error, never a silent auth-off skip. Neither var set → ``token=None``:
    the token check is skipped (dev) but Host/Origin protection still applies.
    """
    token_path = os.environ.get(token_file_env_var) or None
    if token_path:
        if os.environ.get(token_env_var):
            logger.warning(
                "Both %s and %s are set; using the secret file (%s) and "
                "ignoring the bare env token.",
                token_file_env_var,
                token_env_var,
                token_path,
            )
        try:
            token = Path(token_path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise RuntimeError(
                f"{token_file_env_var} points at '{token_path}' but the "
                f"launch-secret file cannot be read: {e}. The spawning parent "
                "creates this file on spawn and removes it on sidecar exit — "
                "do not set the variable by hand unless the file exists. Unset "
                "it to run without caller auth (local development only)."
            ) from e
        if not token:
            raise RuntimeError(
                f"{token_file_env_var} points at '{token_path}' but the file "
                "is empty — refusing to start with an empty caller-auth token. "
                "Unset the variable to run without caller auth (local "
                "development only)."
            )
        return CallerAuthConfig(token=token, surface=surface, exempt_paths=exempt_paths)
    token = os.environ.get(token_env_var) or None
    return CallerAuthConfig(token=token, surface=surface, exempt_paths=exempt_paths)


def is_exempt_path(path: str) -> bool:
    """Whether ``path`` skips the token requirement (probes / HTML pages)."""
    config = get_config()
    return bool(config and path in config.exempt_paths)


def _host_only(header_value: str) -> str:
    """Extract the bare host from a ``Host`` header value, dropping the port.

    Handles the IPv6 literal form ``[::1]:8141`` as well as ``127.0.0.1:8141``.
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

    True only when a token is configured AND the presented bearer matches. Uses
    :func:`hmac.compare_digest` so a wrong token can't be timed out character by
    character.
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

    The token check lives in a route dependency so it can skip exempt probe
    paths; these transport-level controls must cover *every* request, exempt
    ones included.

    Pure ASGI, not ``BaseHTTPMiddleware``: it inspects two headers and hands the
    untouched ``(scope, receive, send)`` on. ``BaseHTTPMiddleware`` wraps the
    response body and can buffer or reorder a ``StreamingResponse``, which would
    break the sidecars' line-by-line SSE.
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
                    f"host. The {config.surface} serves only 127.0.0.1/localhost; "
                    "a non-loopback Host is a DNS-rebinding attempt.",
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
                        f"The {config.surface} refuses browser origins other than "
                        "loopback (drive-by / DNS-rebinding protection).",
                    )
                    return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send, status: int, detail: str) -> None:
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


__all__ = [
    "LOOPBACK_HOSTS",
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
