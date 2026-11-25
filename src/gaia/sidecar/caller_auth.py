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
2. **Host allowlist** — the ``Host`` header must be present AND loopback, which
   is what defeats DNS rebinding (the rebound request carries ``Host: evil.com``).
   An absent or empty ``Host`` is refused too, so a raw-socket client cannot skip
   the control by omitting the header.
3. **Origin rejection** — a request carrying a non-loopback browser ``Origin``
   is refused. Non-browser clients send no ``Origin`` and are unaffected.

Fail-loud: rejections carry an actionable message, never a silent degrade. A
sidecar with no token refuses to start (:func:`check_startup`) unless the
developer sets ``GAIA_SIDECAR_ALLOW_NO_TOKEN=1``; only then is the token check
skipped, with a loud warning, and Host/Origin still apply — so a hand-run
developer sidecar keeps the control that stops a web page driving it.

A sidecar binds its own env-var names and exempt paths and inherits everything
else from here. Only the flagship (``gaiapy.caller_auth``) does so today —
the email sidecar still carries its own copy of this logic, so a change here
does NOT reach it.
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

# The explicit developer opt-in to serve with no caller-auth token. Only "1"
# opts in; GAIA's launchers never set it.
ALLOW_NO_TOKEN_ENV_VAR = "GAIA_SIDECAR_ALLOW_NO_TOKEN"

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

    ``token`` is the per-session bearer secret. ``None`` fails every token check
    and refuses startup, unless ``allow_no_token`` records the developer opt-in
    (:data:`ALLOW_NO_TOKEN_ENV_VAR`) — then the token check is skipped while
    Host/Origin stay in force. ``surface`` names the sidecar in rejection
    messages; the two env-var names let a refusal say what to set.
    ``exempt_paths`` skip the token check only — Host/Origin still cover them.
    """

    token: Optional[str]
    surface: str = "sidecar"
    allowed_hosts: FrozenSet[str] = LOOPBACK_HOSTS
    allowed_origin_hosts: FrozenSet[str] = LOOPBACK_HOSTS
    exempt_paths: FrozenSet[str] = field(default_factory=frozenset)
    allow_no_token: bool = False
    token_file_env_var: str = ""
    token_env_var: str = ""


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
    error, never a silent auth-off skip. Neither var set → ``token=None``, and
    :func:`check_startup` then refuses to serve unless the developer opted in
    with :data:`ALLOW_NO_TOKEN_ENV_VAR`. A missing token never raises here, so
    importing a sidecar module (stdio transport, OpenAPI export) needs none.
    """
    names = {
        "surface": surface,
        "exempt_paths": exempt_paths,
        "token_file_env_var": token_file_env_var,
        "token_env_var": token_env_var,
    }
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
                "do not set the variable by hand unless the file exists."
            ) from e
        if not token:
            raise RuntimeError(
                f"{token_file_env_var} points at '{token_path}' but the file "
                "is empty — refusing to start with an empty caller-auth token. "
                "Write the token into the file, or start the sidecar through "
                "a GAIA launcher, which creates the file for you."
            )
        return CallerAuthConfig(token=token, **names)
    token = os.environ.get(token_env_var) or None
    return CallerAuthConfig(
        token=token,
        allow_no_token=token is None and _no_token_opted_in(),
        **names,
    )


def _no_token_opted_in() -> bool:
    return os.environ.get(ALLOW_NO_TOKEN_ENV_VAR, "").strip() == "1"


def check_startup(config: CallerAuthConfig) -> None:
    """Refuse to serve without a caller-auth token unless a developer opted in.

    Call it before the sidecar accepts its first request. With a token it does
    nothing; with no token and :data:`ALLOW_NO_TOKEN_ENV_VAR` set it logs a loud
    warning; otherwise it raises :class:`RuntimeError` naming the fix.
    """
    if config.token:
        return
    file_var = config.token_file_env_var or "the token-file variable"
    env_var = config.token_env_var or "the token variable"
    if config.allow_no_token:
        logger.warning(
            "%s: caller authentication DISABLED — neither %s nor %s is set and "
            "%s=1 opts out of the token check. Any local process can drive this "
            "sidecar; use this only for a sidecar you run by hand while "
            "developing. Host/Origin protection is still enforced.",
            config.surface,
            file_var,
            env_var,
            ALLOW_NO_TOKEN_ENV_VAR,
        )
        return
    raw_flag = os.environ.get(ALLOW_NO_TOKEN_ENV_VAR, "").strip()
    flag_note = (
        f" ({ALLOW_NO_TOKEN_ENV_VAR} is '{raw_flag}'; only '1' opts in.)"
        if raw_flag
        else ""
    )
    raise RuntimeError(
        f"The {config.surface} refuses to start without a caller-auth token: "
        f"neither {file_var} nor {env_var} is set. Start it through a GAIA "
        "launcher (the GAIA daemon, or the npm package's startSidecar / serve), "
        f"which mints a per-session token and passes it as {file_var}, a 0600 "
        "file. For a sidecar you run by hand while developing, set "
        f"{ALLOW_NO_TOKEN_ENV_VAR}=1 to run without the token check.{flag_note} "
        "See 'Caller authentication' in the agent's SPEC.md."
    )


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

    True when a token is configured AND the presented bearer matches, or when no
    token is configured and the developer opted out of the check. Uses
    :func:`hmac.compare_digest` so a wrong token can't be timed out character by
    character.
    """
    if config.token is None:
        return config.allow_no_token
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

            # Fail closed on an absent/empty Host: HTTP/1.1 requires one and
            # every real client sends it, so "no Host" is a raw-socket caller
            # skipping the rebinding control, not a case to wave through.
            raw_host = headers.get("host", "")
            host = _host_only(raw_host)
            if host not in config.allowed_hosts:
                # Keyed on the RAW header: _host_only also returns "" for a
                # malformed value (`:8141`, an unbracketed `::1`), and calling
                # that "no Host header" misdirects whoever is debugging it.
                named = (
                    f"Host header '{raw_host}'"
                    if raw_host.strip()
                    else "A request with no Host header"
                )
                await self._reject(
                    scope,
                    receive,
                    send,
                    400,
                    f"Rejected: {named} is not an allowed loopback host. The "
                    f"{config.surface} serves only 127.0.0.1/localhost; send "
                    "'Host: 127.0.0.1:<port>'. A non-loopback or absent Host is "
                    "a DNS-rebinding attempt.",
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


# HTTP methods FastAPI/OpenAPI ever attach an operation to under a path item.
_OPENAPI_METHODS: FrozenSet[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
)


def openapi_security_extension(
    spec: dict, *, exempt_paths: FrozenSet[str] = frozenset()
) -> dict:
    """Overlay the bearer-token gate onto a generated OpenAPI document (#4605).

    The token check is a plain ``Request``-typed dependency, by design — it
    must not risk behaviour drift in the live auth path — so FastAPI's schema
    generator never sees it and emits no ``securitySchemes``/``security`` at
    all. This documents the real posture instead: a sidecar refuses to start
    without a token, so every gated operation declares
    ``security: [{"bearerAuth": []}]``. ``exempt_paths`` routes get an explicit
    empty requirement (``security: []``) so they read as deliberately public,
    not merely undocumented. Mutates and returns ``spec``.
    """
    schemes = spec.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes["bearerAuth"] = {"type": "http", "scheme": "bearer"}
    for path, operations in spec.get("paths", {}).items():
        requirement = [] if path in exempt_paths else [{"bearerAuth": []}]
        for method, operation in operations.items():
            if method in _OPENAPI_METHODS:
                operation["security"] = requirement
    return spec


def install_openapi_security(
    app, *, exempt_paths: FrozenSet[str] = frozenset()
) -> None:
    """Wire :func:`openapi_security_extension` onto ``app.openapi()`` (#4605).

    Shared by a sidecar's real server app and its OpenAPI export app, so the
    bearer-gate overlay is defined once and the two documents can never drift
    apart. Delegates to the app's own ``app.openapi`` (FastAPI's default
    schema builder) rather than calling ``fastapi.openapi.utils.get_openapi``
    directly — the default already forwards everything ``FastAPI.__init__``
    was given (``servers``, ``openapi_tags``, etc.), so nothing set on the app
    can silently drop out of the published spec.
    """
    base_openapi = app.openapi

    def _openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        app.openapi_schema = openapi_security_extension(
            base_openapi(), exempt_paths=exempt_paths
        )
        return app.openapi_schema

    app.openapi = _openapi


__all__ = [
    "ALLOW_NO_TOKEN_ENV_VAR",
    "LOOPBACK_HOSTS",
    "CallerAuthConfig",
    "HostOriginMiddleware",
    "generate_session_token",
    "configure",
    "reset",
    "get_config",
    "config_from_env",
    "check_startup",
    "is_exempt_path",
    "token_ok",
    "openapi_security_extension",
    "install_openapi_security",
]
