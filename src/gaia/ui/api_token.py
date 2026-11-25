# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI API token: the secret every ``/api`` and ``/v1`` caller presents.

Binding loopback is not access control, so the backend requires this token
from every caller, local ones included. It is per-install: created once, 0600,
in GAIA's home folder, and stable across restarts so a signed-in browser stays
signed in. Clients on this PC read the same file:

- the desktop app adds ``Authorization: Bearer <token>`` to its own requests;
- a browser gets it as an HttpOnly, SameSite=Strict cookie by opening a
  single-use launch link (``/?launch=<code>``);
- scripts, the MCP server and the eval harness send the bearer header via
  :func:`auth_headers`.

``GAIA_UI_TOKEN_FILE`` points server and clients at another file; when set, the
file must already exist. Deleting the default file and restarting rotates it.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from gaia.home import gaia_home

ENV_TOKEN_FILE = "GAIA_UI_TOKEN_FILE"
TOKEN_FILE_NAME = "agent-ui-token"

#: Cookie a browser carries after opening a launch link.
COOKIE_NAME = "gaia_ui_token"
#: A year: the token is per-install, so the cookie outlives restarts.
COOKIE_MAX_AGE = 365 * 24 * 3600

#: Query parameter of the launch link.
LAUNCH_PARAM = "launch"
LAUNCH_CODE_TTL_S = 600

#: ``code`` of the 401 body, so the frontend can tell this refusal apart.
REJECTION_CODE = "ui_token_required"


class UITokenError(RuntimeError):
    """The token file is missing, unreadable or empty."""


def token_path() -> Path:
    """Where the token lives: ``GAIA_UI_TOKEN_FILE``, else GAIA's home."""
    override = os.environ.get(ENV_TOKEN_FILE, "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    return gaia_home() / TOKEN_FILE_NAME


def _read(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise UITokenError(
            f"Cannot read the Agent UI API token at {path}: {e}. Open the "
            "GAIA desktop app or the Agent UI once so it creates the token "
            f"in GAIA's home folder, or point {ENV_TOKEN_FILE} "
            "at an existing token file."
        ) from e
    if not token:
        raise UITokenError(
            f"The Agent UI API token file {path} is empty. Delete it and "
            "restart the Agent UI to make a new one."
        )
    return token


def load_or_create_token() -> str:
    """The server's token: read it, or create it owner-only on first run."""
    path = token_path()
    if os.environ.get(ENV_TOKEN_FILE, "").strip():
        return _read(path)
    if path.exists():
        return _read(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lazy: clients import this module on installs without the [ui] extras.
    from gaia.sidecar.caller_auth import generate_session_token

    token = generate_session_token()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    # A hard link appears complete or not at all, and refuses to replace a
    # token another backend created first.
    try:
        os.link(tmp, path)
    except FileExistsError:
        return _read(path)
    finally:
        tmp.unlink()
    return token


def read_token() -> str:
    """A client's token; raises :class:`UITokenError` with the fix if absent."""
    return _read(token_path())


def auth_headers() -> Dict[str, str]:
    """Headers a first-party script sends on every Agent UI request."""
    return {"X-Gaia-UI": "1", "Authorization": f"Bearer {read_token()}"}


def token_matches(expected: str, presented: Optional[str]) -> bool:
    """Constant-time comparison; an absent value never matches."""
    if not presented:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def bearer(authorization_header: str) -> Optional[str]:
    """The token of an ``Authorization: Bearer <token>`` header, if any."""
    scheme, _, value = (authorization_header or "").strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


class LaunchCodes:
    """Single-use, short-lived codes that sign a browser in.

    The code, not the token, goes in the URL, so browser history and terminal
    scrollback only ever hold a code that has been spent or has expired.
    """

    def __init__(self, ttl_s: float = LAUNCH_CODE_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._codes: Dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def ttl_s(self) -> float:
        return self._ttl_s

    def mint(self) -> str:
        code = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._codes = {c: t for c, t in self._codes.items() if t > now}
            self._codes[code] = now + self._ttl_s
        return code

    def redeem(self, code: Optional[str]) -> bool:
        if not code:
            return False
        with self._lock:
            expires = self._codes.pop(code, None)
        return expires is not None and expires > time.monotonic()
