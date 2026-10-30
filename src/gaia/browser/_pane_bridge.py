# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Client for GAIA desktop's browser-pane bridge. Private to ``gaia.browser``.

The desktop app's main process listens on a random loopback port and writes
where to reach it, with a bearer token, to a discovery file. Every call is one
plain-urllib HTTP request, so the per-call daemon threads the agent runs tools
on need no worker thread of their own (unlike Playwright).

The TypeScript half is ``services/browser-pane/bridge-server.cjs``; both sides
read ``tests/fixtures/browser_pane/bridge/*.json`` in their tests so the wire
cannot drift silently.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from gaia.browser.errors import (
    BrowserError,
    PageDialogOpen,
    PageOffScope,
    PaneNavigationBlocked,
    PaneProtocolMismatch,
    PaneStepFailed,
    PaneUnreachable,
)
from gaia.browser.untrusted import fence

#: Bridge version this package speaks. Bumped only on an incompatible change.
PROTOCOL = 1

#: Set by GAIA desktop on the backend it starts, so a dev build's backend finds
#: that build's bridge and never an installed app's.
DISCOVERY_ENV = "GAIA_BROWSER_PANE_DISCOVERY"

#: Per-call ceiling. A page load inside a call is bounded by main first.
DEFAULT_TIMEOUT_S = 45.0

# No proxy, whatever the environment says: the token must never leave this machine.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def discovery_path() -> Path:
    """The env var GAIA desktop sets on its backend, else ``<GAIA home>/run/browser-pane.json``.

    A daemon sidecar started outside that backend has no env var and reads the
    default, which is where an installed GAIA desktop writes it.
    """
    override = os.environ.get(DISCOVERY_ENV, "").strip()
    if override:
        return Path(override)
    from gaia.config import gaia_home

    return gaia_home() / "run" / "browser-pane.json"


@dataclass(frozen=True)
class Discovery:
    port: int
    token: str
    pid: int
    protocol: int


def discover() -> Optional[Discovery]:
    """The running desktop app's bridge, or None when no desktop app runs.

    None for no file, or for a file whose pid is gone (left by a crash). A
    file that exists but does not parse raises: something wrote it, and
    guessing would hide that.
    """
    path = discovery_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise PaneUnreachable(f"cannot read {path}: {e}") from e
    try:
        data = json.loads(raw)
        disc = Discovery(
            port=int(data["port"]),
            token=str(data["token"]),
            pid=int(data["pid"]),
            protocol=int(data["protocol"]),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise PaneUnreachable(f"{path} is not a valid discovery file ({e})") from e

    from gaia.daemon.instance import pid_alive

    return disc if pid_alive(disc.pid) else None


def is_loopback_origin(origin_or_url: str) -> bool:
    """True for localhost, ``*.localhost``, 127.0.0.0/8 and ``[::1]``.

    browser_eval runs only on these. ``navigation-policy.cjs`` applies the
    same rule, and both test it against ``bridge/loopback.json``.
    """
    host = (urlsplit(origin_or_url).hostname or "").lower().rstrip(".")
    if host in ("localhost", "::1") or host.endswith(".localhost"):
        return True
    parts = host.split(".")
    return (
        len(parts) == 4
        and parts[0] == "127"
        and all(p.isdigit() and int(p) <= 255 for p in parts)
    )


def dev_server_origins(port: int) -> Tuple[str, ...]:
    """Where a dev server GAIA started answers: the agent reads and acts there with no ask.

    ``bridge-server.cjs`` devServerOrigins is the same rule, and both test it
    against ``bridge/dev_server_origins.json``.
    """
    return tuple(
        f"http://{host}:{port}" for host in ("localhost", "127.0.0.1", "[::1]")
    )


def origin_of(url: str) -> Optional[str]:
    """``scheme://host[:port]`` of an http(s) URL, else None."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1].lower()}"


def _error_for(code: str, message: str, data: Dict[str, Any]) -> BrowserError:
    """The Python exception for a bridge error code (``BridgeErrorCode`` in contract.d.ts)."""
    if code == "blocked_navigation":
        return PaneNavigationBlocked(
            str(data.get("url", "")), str(data.get("origin", ""))
        )
    if code == "dialog_open" and isinstance(data.get("dialog"), dict):
        return PageDialogOpen(data["dialog"], done=False)
    if code == "off_scope":
        return PageOffScope(str(data.get("origin", "")))
    if code in ("unauthorized", "forbidden", "bad_request", "unknown_op", "internal"):
        return PaneUnreachable(
            f"the bridge refused the call ({code}). Its report:\n"
            + fence("browser pane", "What the pane reported.", message)
        )
    # Main's messages can quote a page, an address or a program's output, so
    # the model reads GAIA's own summary and gets the report fenced as data.
    return PaneStepFailed(_STEP_SUMMARIES.get(code, _STEP_SUMMARY), message)


_STEP_SUMMARY = "The browser pane could not do that step."

#: What GAIA says for each bridge code, never quoting the page.
_STEP_SUMMARIES = {
    "no_such_tab": "That browser pane tab is gone. Call browser_tabs to see the open ones.",
    "no_page": "The browser pane tab has no web page in it. Open one with browser_open(url).",
    "unsupported": "The browser pane can't do that on this page.",
    "navigation_failed": "The page in the browser pane did not load.",
    "blocked_url": "The browser pane does not open that address.",
    "element_not_found": (
        "That element is not on the page any more. Call browser_snapshot() to get "
        "current refs, then retry."
    ),
    "not_in_desktop": (
        "This chat can't use the browser pane now: the user switched GAIA desktop "
        "to another chat. Ask the user to switch back to this chat."
    ),
    "timeout": "The browser pane step took too long and was stopped.",
    "user_active": (
        "FAILED: this step was not done. The user is using this browser tab, so "
        "GAIA is paused there. Stop using the browser now, tell the user which "
        "steps are not done, and ask how to continue."
    ),
    "private_tab": (
        "The user marked that tab private, so GAIA can't see or use it. Open your "
        "own tab with browser_open(url), or ask the user to switch tabs."
    ),
    "not_actionable": (
        "Nothing was done: that element can't take this action now (it is covered, "
        "hidden, disabled, or not the right kind of element). Call browser_snapshot() "
        "and try again, or ask the user to do this step."
    ),
    "dialog_open": "Nothing was done: the page is showing a box. Answer it with browser_dialog first.",
    "dev_server_failed": "The dev server did not start. Its report and last log lines follow.",
    "port_in_use": "The dev server's port is already taken.",
    "no_such_server": (
        'GAIA has not started that dev server. Call dev_server(action="list") to '
        "see the ones it has."
    ),
}


#: Every code bridge-server.cjs can send. A test checks this against contract.d.ts.
ERROR_CODES = frozenset(
    {
        "unauthorized",
        "forbidden",
        "bad_request",
        "unknown_op",
        "internal",
        "no_such_tab",
        "no_page",
        "unsupported",
        "blocked_navigation",
        "off_scope",
        "navigation_failed",
        "blocked_url",
        "element_not_found",
        "not_in_desktop",
        "timeout",
        "user_active",
        "private_tab",
        "not_actionable",
        "dialog_open",
        "dev_server_failed",
        "port_in_use",
        "no_such_server",
    }
)


class PaneBridge:
    """One desktop app's bridge. ``hello`` must succeed before any other call."""

    def __init__(
        self, disc: Discovery, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        self._disc = disc
        self._timeout_s = timeout_s
        #: The ops ``hello`` said this app serves; empty until it answers.
        self.ops: frozenset = frozenset()

    def hello(self) -> Dict[str, Any]:
        """Raises PaneProtocolMismatch unless the app speaks :data:`PROTOCOL`."""
        res = self.call("hello", {})
        theirs = int(res.get("protocol", -1))
        if theirs != PROTOCOL:
            raise PaneProtocolMismatch(theirs, PROTOCOL)
        self.ops = frozenset(res.get("ops") or ())
        return res

    def attach(self, session_id: str) -> Dict[str, Any]:
        return self.call("attach", {"sessionId": session_id})

    def call(
        self, op: str, body: Dict[str, Any], *, timeout_s: Optional[float] = None
    ) -> Any:
        """POST /v1/<op> with the bearer token, and return the reply.

        A refused connection, a reset or a timeout raises PaneUnreachable and
        is never retried here. A bridge error reply raises its mapped error.
        """
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._disc.port}/v1/{op}",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._disc.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with _OPENER.open(req, timeout=timeout_s or self._timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise self._reply_error(e) from e
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise PaneUnreachable(f"{op} failed: {reason}") from e

    @staticmethod
    def _reply_error(e: urllib.error.HTTPError) -> BrowserError:
        try:
            payload = json.loads(e.read().decode("utf-8"))
            err = payload["error"]
            return _error_for(
                str(err["code"]), str(err["message"]), err.get("data") or {}
            )
        except (ValueError, KeyError, TypeError):
            return PaneUnreachable(
                f"the bridge answered HTTP {e.code} with no error body"
            )


__all__: List[str] = [
    "DISCOVERY_ENV",
    "ERROR_CODES",
    "PROTOCOL",
    "Discovery",
    "PaneBridge",
    "discover",
    "dev_server_origins",
    "discovery_path",
    "is_loopback_origin",
    "origin_of",
]
