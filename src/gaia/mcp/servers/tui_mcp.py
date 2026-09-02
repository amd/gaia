# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""MCP server that drives the GAIA terminal UI through its local control API.

Lets an MCP client (Claude Code, the eval harness, a script) read what the TUI
is currently showing, send keystrokes and text to it, and wait for the screen to
reach a given state.

The TUI boots straight into one agent -- splash, readiness gate, chat -- so
there is nothing to browse and no agent to pick. The tools here are the generic
drive-any-screen set; a launch is `gaia tui` itself, and a different agent is
`gaia tui chat --agent <id>`.

The TUI must be started with its control server enabled (``gaia tui --control``).
That server binds loopback on an ephemeral port and advertises itself in
``~/.gaia/tui/control.json`` (mode 0600), so every tool here starts by
discovering and validating that file rather than assuming a fixed port.

Usage:
    uv run python -m gaia.mcp.servers.tui_mcp --stdio
    uv run python -m gaia.mcp.servers.tui_mcp --port 8767
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

import requests

from gaia.logger import get_logger

if TYPE_CHECKING:  # import only for type checking; runtime import is lazy (#1750)
    from mcp.server import MCPServer

logger = get_logger(__name__)

# ── Control-server contract ──────────────────────────────────────────

#: Directory holding ``control.json``. ``GAIA_TUI_HOME`` overrides it (tests, and
#: side-by-side TUIs); the override is read on every call, never cached.
ENV_TUI_HOME = "GAIA_TUI_HOME"
CONTROL_FILE_NAME = "control.json"

#: Stable identity the control server stamps into the file and returns from
#: ``/control/v1/status``. A foreign process that grabbed the recycled port
#: cannot answer with this id, so the probe below rejects it.
SERVICE_ID = "gaia-tui-control"
API_PREFIX = "/control/v1"
AUTH_SCHEME = "Bearer"

#: Loopback answers in milliseconds; a longer wait only delays the "no TUI" verdict.
PROBE_TIMEOUT = 1.5
REQUEST_TIMEOUT = 15.0

#: Extra seconds added to a ``/wait`` request's HTTP timeout on top of the
#: caller's ``timeout_ms``, so the server's own 408 always wins the race.
WAIT_TIMEOUT_SLACK = 5.0

#: How much screen text an error detail carries before it is truncated.
SCREEN_TRUNCATE = 2000

#: Server-side limits (``tui/internal/control/server.go``). Enforced here too so
#: an out-of-range argument gets a useful message instead of a bare 400.
MIN_COLS, MAX_COLS = 20, 500
MIN_ROWS, MAX_ROWS = 5, 200
MAX_KEYS_PER_CALL = 100
MAX_DELAY_MS = 2000
MAX_WAIT_MS = 10 * 60 * 1000

#: A batch's total pause budget (``delay_ms × (count - 1)``), so a request can
#: never outlive the client's own HTTP timeout.
MAX_INJECTION_DELAY_MS = 10_000

#: The TUI ships as its own binary; ``gaia tui`` is the packaged entry point and
#: ``tui/bin/gaia`` the source build. Both spellings are given because a remedy
#: the reader cannot actually run is not a remedy.
_START_CMD = "gaia tui --control (source build: ./tui/bin/gaia --control)"
START_HINT = f"Start one with: {_START_CMD}"
RESTART_HINT = f"Restart it with: {_START_CMD}"

#: Only loopback is ever legitimate: the control server binds 127.0.0.1 and the
#: bearer token must never be sent anywhere else.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: requests honours HTTP_PROXY/ALL_PROXY by default. Loopback must never be
#: proxied — it would hand the bearer token and the screen text to the proxy.
NO_PROXY: Dict[str, Any] = {"http": None, "https": None}

MCP_DEFAULT_PORT = 8767  # 8765 is agent_ui_mcp, 8766 is the MCP bridge (cli.py)
MCP_DEFAULT_HOST = "localhost"


# ── Discovery ────────────────────────────────────────────────────────


class ControlHomeError(RuntimeError):
    """Raised when neither ``GAIA_TUI_HOME`` nor a home directory is resolvable."""


def control_dir() -> Path:
    """Directory that contains ``control.json`` (``GAIA_TUI_HOME`` overrides)."""
    override = os.environ.get(ENV_TUI_HOME)
    if override:
        return Path(override)
    try:
        return Path.home() / ".gaia" / "tui"
    except RuntimeError as e:
        raise ControlHomeError(
            f"Cannot locate your home directory ({e}), so the GAIA TUI control "
            f"file cannot be found. Set {ENV_TUI_HOME} to the directory holding "
            f"{CONTROL_FILE_NAME}."
        ) from e


def control_path() -> Path:
    """Full path to the TUI control file."""
    return control_dir() / CONTROL_FILE_NAME


def _display_path() -> str:
    """``control_path()`` with the user's home collapsed to ``~`` for messages."""
    path = control_path()
    try:
        return f"~/{path.relative_to(Path.home())}"
    except (ValueError, RuntimeError):
        return str(path)


def read_control_file() -> Optional[Dict[str, Any]]:
    """Load ``control.json``.

    Returns ``None`` when the file is absent, unreadable, or malformed — a
    half-written or garbage file is "no trustworthy TUI", never an exception the
    caller has to catch. Malformed content is logged so it is not silent.
    """
    path = control_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning("tui: cannot read %s: %s", path, e)
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError(f"expected a JSON object, got {type(data).__name__}")
        return {
            "pid": int(data["pid"]),
            "port": int(data["port"]),
            "token": str(data["token"]),
            "host": str(data.get("host", "127.0.0.1")),
            "service": str(data.get("service", SERVICE_ID)),
            "api_version": str(data.get("api_version", "v1")),
            "started_at": float(data.get("started_at", 0.0)),
            "version": str(data.get("version", "")),
        }
    except (ValueError, KeyError, TypeError) as e:
        logger.warning(
            "tui: %s is present but malformed (%s); treating as stale", path, e
        )
        return None


def pid_alive(pid: int) -> bool:
    """True if *pid* refers to a running (non-zombie) process."""
    import psutil

    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).status() not in (
            psutil.STATUS_ZOMBIE,
            psutil.STATUS_DEAD,
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        # AccessDenied means the pid exists but belongs to another user; the
        # token-authed probe below is the real check either way.
        return bool(psutil.pid_exists(pid))


def base_url_of(info: Dict[str, Any]) -> str:
    """Loopback base URL for a discovered control server."""
    return f"http://{info['host']}:{info['port']}"


def _err(detail: str) -> Dict[str, Any]:
    return {"status": "error", "detail": detail}


def _probe(info: Dict[str, Any], timeout: float) -> Tuple[Optional[dict], str]:
    """Token-authed ``/status`` probe.

    Returns ``(body, "")`` when the server is genuinely our TUI, otherwise
    ``(None, kind)`` where *kind* is ``"unreachable"`` (no answer / not JSON)
    or ``"foreign"`` (answered, but a different service or pid).
    """
    url = f"{base_url_of(info)}{API_PREFIX}/status"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {info['token']}"},
            timeout=timeout,
            proxies=NO_PROXY,
        )
    except requests.exceptions.RequestException:
        return None, "unreachable"
    if r.status_code != 200:
        return None, "unreachable"
    try:
        body = r.json()
    except ValueError:
        return None, "unreachable"
    if not isinstance(body, dict) or body.get("service") != SERVICE_ID:
        return None, "foreign"
    try:
        if int(body.get("pid", -1)) != info["pid"]:
            return None, "foreign"
    except (TypeError, ValueError):
        return None, "foreign"
    return body, ""


def discover(
    timeout: float = PROBE_TIMEOUT,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Find the running TUI's control server.

    Returns ``(info, None)`` on success or ``(None, error_dict)`` — exactly one
    is non-None. Trust requires two checks, because a crashed TUI leaves a file
    behind and the OS happily recycles both its pid and its port: the recorded
    pid must be alive AND the recorded port must answer a token-authed status
    probe with our service id and that same pid.
    """
    try:
        disp = _display_path()
        # Read first, then classify — checking existence first would call a file
        # deleted in between "malformed" and tell the user to delete it again.
        info = read_control_file()
        missing = info is None and not control_path().exists()
    except ControlHomeError as e:
        return None, _err(str(e))

    if info is None:
        if missing:
            return None, _err(
                f"No GAIA TUI is running (no control file at {disp}). {START_HINT}"
            )
        return None, _err(
            f"The GAIA TUI control file at {disp} is malformed or unreadable, so it "
            f"cannot be trusted. Delete it and start a fresh TUI with: "
            f"gaia tui --control"
        )

    if info["host"] not in LOOPBACK_HOSTS:
        # The token travels on every request; a control file naming a remote
        # host would hand it — and the screen contents — to that host.
        return None, _err(
            f"The GAIA TUI control file at {disp} names a non-loopback host "
            f"({info['host']!r}). The control server only ever binds 127.0.0.1, so "
            f"this file has been tampered with or corrupted. Delete it and "
            f"{RESTART_HINT.lower()}"
        )

    if not pid_alive(info["pid"]):
        return None, _err(
            f"No GAIA TUI is running: the control file at {disp} records pid "
            f"{info['pid']}, which is not running — a stale control file left by a "
            f"crashed TUI. {START_HINT}"
        )

    body, kind = _probe(info, timeout=timeout)
    if kind == "unreachable":
        return None, _err(
            f"The GAIA TUI recorded in {disp} (pid {info['pid']}) did not answer the "
            f"control status probe on its recorded port. The TUI may be hung, or the "
            f"port may have been recycled by another process. {RESTART_HINT}"
        )
    if kind == "foreign":
        return None, _err(
            f"Another process now owns the port recorded in {disp} — it answered the "
            f"probe but is not this GAIA TUI (service or pid mismatch), so the control "
            f"file is stale. {RESTART_HINT}"
        )

    resolved = dict(info)
    resolved["status"] = body
    return resolved, None


def _discovered() -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """:func:`discover` with the info slot always a dict, for call sites.

    The dict is empty exactly when the error is set, so ``if error: return error``
    remains the only branch a tool needs.
    """
    info, error = discover()
    return (info or {}), error


# ── HTTP plumbing ────────────────────────────────────────────────────


def _scrub(text: str, base_url: str, token: str = "") -> str:
    """Strip the control server's URL and auth token out of a message."""
    out = (text or "").replace(base_url, "")
    # urllib3 spells the endpoint host:port in some messages, without a scheme.
    out = out.replace(base_url.replace("http://", ""), "")
    if token:
        out = out.replace(token, "<redacted>")
    return out.strip()


def _normalize_error(
    e: Exception,
    base_url: str,
    status_code: Optional[int] = None,
    token: str = "",
) -> Dict[str, Any]:
    """Normalize an exception into a structured error dict without leaking URLs.

    Returns ``{"status": "error", "detail": "<clean message>"}``. Control-server
    errors arrive as ``{"error": {"code", "message", "hint"}}``; the message
    (plus hint) is what the caller can act on, so unwrap it. The base URL and the
    auth token are scrubbed — an error body is a message to a user, not a place
    to echo credentials.
    """
    if isinstance(e, requests.exceptions.ConnectionError):
        result = _err(
            f"Cannot reach the GAIA TUI control server — the TUI is no longer "
            f"listening. {RESTART_HINT}"
        )
        # Flagged, not sniffed out of the prose: callers that need to know the
        # server went away must not depend on the wording of this sentence.
        result["unreachable"] = True
        return result

    if isinstance(e, requests.exceptions.Timeout):
        return _err("The GAIA TUI control server did not respond in time.")

    if isinstance(e, requests.exceptions.HTTPError):
        resp = e.response
        code = resp.status_code if resp is not None else (status_code or 0)
        detail = ""
        if resp is not None:
            try:
                body = resp.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                envelope = body["error"]
                detail = str(envelope.get("message", "") or "")
                hint = str(envelope.get("hint", "") or "")
                if hint:
                    detail = f"{detail} Hint: {hint}".strip()
            elif resp.text:
                detail = resp.text[:200]
        if not detail:
            detail = f"HTTP {code}"
        return _err(_scrub(detail, base_url, token))

    return _err(_scrub(str(e)[:400], base_url, token))


def _raw_request(
    info: Dict[str, Any],
    method: str,
    path: str,
    *,
    timeout: float = REQUEST_TIMEOUT,
    **kwargs,
) -> requests.Response:
    """Issue a token-authed request to the control server (may raise)."""
    url = f"{base_url_of(info)}{API_PREFIX}{path}"
    return requests.request(
        method.upper(),
        url,
        headers={"Authorization": f"{AUTH_SCHEME} {info['token']}"},
        timeout=timeout,
        proxies=NO_PROXY,
        **kwargs,
    )


def _request(
    info: Dict[str, Any],
    method: str,
    path: str,
    *,
    timeout: float = REQUEST_TIMEOUT,
    **kwargs,
) -> Dict[str, Any]:
    """Token-authed request returning the JSON body or a structured error dict."""
    base_url = base_url_of(info)
    # Transport and HTTP errors are handled separately from decoding: several
    # requests exceptions (MissingSchema, InvalidURL, InvalidJSONError) subclass
    # ValueError, so a shared handler would report a malformed control file as
    # "the server returned a non-JSON response" and point the user at the TUI.
    try:
        r = _raw_request(info, method, path, timeout=timeout, **kwargs)
        r.raise_for_status()
    except Exception as e:  # pylint: disable=broad-except
        return _normalize_error(e, base_url, token=info.get("token", ""))
    try:
        return _json_object(r.json())
    except ValueError:
        return _err(
            "The GAIA TUI control server returned a response that was not JSON. "
            f"{RESTART_HINT}"
        )


def _json_object(body: Any) -> Dict[str, Any]:
    """Return a decoded JSON body, rejecting anything that is not an object."""
    if not isinstance(body, dict):
        raise ValueError(f"expected a JSON object, got {type(body).__name__}")
    return body


def _is_error(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "error"


def _truncate(text: str, limit: int = SCREEN_TRUNCATE) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text)} chars total]"


# ── State helpers ────────────────────────────────────────────────────


def _state_of(status: Dict[str, Any]) -> Dict[str, Any]:
    state = status.get("state")
    return state if isinstance(state, dict) else {}


def _view_of(status: Dict[str, Any]) -> str:
    return _state_of(status).get("view") or "unknown"


def _summarize(status: Dict[str, Any]) -> str:
    """One-line human summary of what the TUI is showing."""
    state = _state_of(status)
    view = _view_of(status)

    # A TUI whose event loop is not consuming messages reads fine but silently
    # discards every keystroke — lead with that, it explains everything after it.
    if status.get("running") is False:
        return (
            "NOT ACCEPTING INPUT — the TUI's event loop is not running (still "
            "starting up, or the user quit it). Reads still work; keys and text "
            "are refused. Start a fresh one with: gaia tui --control"
        )

    overlay = state.get("overlay")

    if view == "splash":
        # One render while the readiness gate starts behind it. Nothing here
        # takes input, so a caller that lands on it should wait, not press.
        return "starting up — the readiness gate opens on its own, no key needed"

    if view == "preflight":
        parts = ["readiness gate"]
        agent = state.get("agent")
        if agent:
            parts.append(f"for {agent!r}")
        blocker = state.get("blocker")
        if blocker:
            # The row key, not the rendered remedy: the wording is allowed to
            # change, the key is not.
            parts.append(f"blocked on {blocker!r} — read the screen for the fix")
        else:
            parts.append("nothing is refusing the launch yet")
        if overlay:
            parts.append(f"overlay {overlay!r}")
        return ", ".join(parts)

    if view == "chat":
        agent = state.get("agent") or "?"
        summary = f"chat with {agent!r}"
        if state.get("streaming"):
            summary += ", streaming"
        if overlay:
            summary += f", overlay {overlay!r}"
        return summary

    return (
        "TUI is running but does not report its view state "
        "(read the screen with tui_screen)"
    )


def _normalize_keys(keys: Any) -> Tuple[List[str], Optional[str]]:
    """Accept a list of key names or one comma/space separated string."""
    if isinstance(keys, str):
        items: List[Any] = [keys]
    elif isinstance(keys, (list, tuple)):
        items = list(keys)
    else:
        return [], (
            "keys must be a list of key names (e.g. ['tab', 'down', 'enter']) or a "
            f"comma/space separated string, got {type(keys).__name__}"
        )

    out: List[str] = []
    for item in items:
        if not isinstance(item, str):
            return [], (
                f"key names must be strings, got {type(item).__name__} in the keys list"
            )
        if len(item) == 1:
            # A one-character item is the key itself — "," and " " are typeable.
            out.append(item)
            continue
        out.extend(part for part in re.split(r"[,\s]+", item.strip()) if part)

    if not out:
        return [], "No keys given — pass at least one key name, e.g. ['enter']."
    return out, None


def _status() -> Dict[str, Any]:
    info, error = _discovered()
    if error:
        return error
    body = _request(info, "get", "/status")
    if _is_error(body):
        return body
    body["summary"] = _summarize(body)
    return body


def _screen(fmt: str = "plain") -> Dict[str, Any]:
    if fmt not in ("plain", "ansi"):
        return _err(f"Unknown format {fmt!r} — use 'plain' (default) or 'ansi'.")
    info, error = _discovered()
    if error:
        return error
    return _request(info, "get", "/screen", params={"format": fmt})


def _send_keys(keys: Any, delay_ms: int = 0) -> Dict[str, Any]:
    names, key_error = _normalize_keys(keys)
    if key_error:
        return _err(key_error)
    if len(names) > MAX_KEYS_PER_CALL:
        return _err(
            f"{len(names)} keys is more than the control server accepts in one "
            f"call (max {MAX_KEYS_PER_CALL}) — split it across calls."
        )
    try:
        delay_ms = int(delay_ms)
    except (TypeError, ValueError):
        return _err(f"delay_ms must be a whole number of ms, got {delay_ms!r}.")
    if not 0 <= delay_ms <= MAX_DELAY_MS:
        return _err(f"delay_ms must be between 0 and {MAX_DELAY_MS}, got {delay_ms}.")
    budget = delay_ms * max(len(names) - 1, 0)
    if budget > MAX_INJECTION_DELAY_MS:
        return _err(
            f"delay_ms {delay_ms} across {len(names)} keys would pause for "
            f"{budget} ms, over the {MAX_INJECTION_DELAY_MS} ms cap — lower "
            f"delay_ms or split the batch."
        )
    info, error = _discovered()
    if error:
        return error
    return _request(info, "post", "/keys", json={"keys": names, "delay_ms": delay_ms})


def _send_text(text: str) -> Dict[str, Any]:
    if not isinstance(text, str) or not text:
        return _err("No text given — pass the text to type into the TUI.")
    info, error = _discovered()
    if error:
        return error
    return _request(info, "post", "/text", json={"text": text})


def _wait(
    info: Dict[str, Any],
    *,
    contains: str = "",
    absent: str = "",
    state: Optional[Dict[str, Any]] = None,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    """POST /wait against an already-discovered control server.

    A wait timeout comes back as HTTP 408 carrying the screen the TUI actually
    had — surfaced as a structured error including that screen, because "timed
    out" on its own tells the caller nothing about what went wrong.
    """
    try:
        timeout_ms = int(timeout_ms)
    except (TypeError, ValueError):
        return _err(f"timeout_ms must be a whole number of ms, got {timeout_ms!r}.")
    if not 0 < timeout_ms <= MAX_WAIT_MS:
        return _err(
            f"timeout_ms must be between 1 and {MAX_WAIT_MS} (10 minutes), "
            f"got {timeout_ms}."
        )

    payload: Dict[str, Any] = {"timeout_ms": timeout_ms}
    if contains:
        payload["contains"] = contains
    if absent:
        payload["absent"] = absent
    if state:
        payload["state"] = state
    if len(payload) == 1:
        return _err(
            "Nothing to wait for — pass at least one of contains, absent, or a state "
            "matcher."
        )

    base_url = base_url_of(info)
    http_timeout = int(timeout_ms) / 1000.0 + WAIT_TIMEOUT_SLACK
    try:
        r = _raw_request(info, "post", "/wait", timeout=http_timeout, json=payload)
    except Exception as e:  # pylint: disable=broad-except
        return _normalize_error(e, base_url, token=info.get("token", ""))

    if r.status_code == 408:
        try:
            body = r.json()
        except ValueError:
            body = {}
        envelope = body.get("error") if isinstance(body, dict) else {}
        envelope = envelope if isinstance(envelope, dict) else {}
        screen = str(envelope.get("screen", "") or "")
        elapsed = envelope.get("elapsed_ms", timeout_ms)
        wanted = ", ".join(
            filter(
                None,
                [
                    f"contains {contains!r}" if contains else "",
                    f"absent {absent!r}" if absent else "",
                    f"state {state!r}" if state else "",
                ],
            )
        )
        result = _err(
            f"Timed out after {elapsed} ms waiting for {wanted}. "
            f"The screen actually contained:\n{_truncate(screen)}"
        )
        result["matched"] = False
        result["timed_out"] = True
        result["elapsed_ms"] = elapsed
        result["screen"] = screen
        if envelope.get("state"):
            result["state"] = envelope["state"]
        return result

    try:
        r.raise_for_status()
    except Exception as e:  # pylint: disable=broad-except
        return _normalize_error(e, base_url, token=info.get("token", ""))
    try:
        return _json_object(r.json())
    except ValueError:
        return _err(
            "The GAIA TUI control server returned a response that was not JSON. "
            f"{RESTART_HINT}"
        )


def _wait_for(
    contains: str = "", absent: str = "", timeout_ms: int = 30000
) -> Dict[str, Any]:
    info, error = _discovered()
    if error:
        return error
    return _wait(info, contains=contains, absent=absent, timeout_ms=timeout_ms)


def _resize(cols: int, rows: int) -> Dict[str, Any]:
    try:
        cols, rows = int(cols), int(rows)
    except (TypeError, ValueError):
        return _err("cols and rows must be integers.")
    if not MIN_COLS <= cols <= MAX_COLS or not MIN_ROWS <= rows <= MAX_ROWS:
        return _err(
            f"Terminal size out of range: got {cols}x{rows}, but cols must be "
            f"{MIN_COLS}-{MAX_COLS} and rows {MIN_ROWS}-{MAX_ROWS}."
        )
    info, error = _discovered()
    if error:
        return error
    return _request(info, "post", "/resize", json={"cols": cols, "rows": rows})


def route_logging_to_stderr() -> None:
    """Move stdout log handlers to stderr — required before stdio transport.

    Importing ``gaia`` installs a root log handler on **stdout**
    (:mod:`gaia.logger`), and the MCP server itself logs one INFO line per
    request. On stdio transport stdout carries JSON-RPC only, so those lines
    land mid-stream and the client rejects every message that follows.
    """
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
            handler.setStream(sys.stderr)


def create_tui_mcp() -> "MCPServer":
    """Create the MCP server exposing the GAIA TUI control tools."""
    # Imported lazily so the helpers above stay importable without the optional
    # ``mcp`` dependency, which the unit-test job does not install (issue #1750).
    from mcp.server import MCPServer

    mcp = MCPServer(name="GAIA TUI")

    @mcp.tool()
    def tui_status() -> Dict[str, Any]:
        """Check whether a GAIA TUI is running and what it is currently showing.

        Start here: it verifies the TUI's control server is reachable and returns
        the terminal size, the frame sequence number, and a ``summary`` line such
        as "readiness gate for 'gaia', blocked on 'lemonade'" or
        "chat with 'gaia', streaming".

        ``running: false`` means the TUI answers reads but is not consuming
        input — every keystroke would be refused. The summary leads with that
        when it happens, so check it before blaming a key that "did nothing".

        If no TUI is running at all, the error explains how to start one.
        """
        return _status()

    @mcp.tool()
    def tui_screen(format: str = "plain") -> Dict[str, Any]:
        """Read what the GAIA TUI is showing right now.

        This is the workhorse — read the screen after every tui_send_keys or
        tui_send_text call to see what actually happened, instead of assuming.

        Args:
            format: "plain" (default) strips ANSI escapes and is what you want
                for reading text; "ansi" keeps colors and cursor codes.
        """
        return _screen(format)

    @mcp.tool()
    def tui_send_keys(keys: Union[List[str], str], delay_ms: int = 0) -> Dict[str, Any]:
        """Send one or more keystrokes to the GAIA TUI.

        Keys are named: "enter", "esc", "tab", "up", "down", "left", "right",
        "backspace", "space", "ctrl+c", or a single character like "y".
        For convenience you may also pass one comma/space separated string
        (``"tab,down,enter"``) instead of a list.

        Read the result with tui_screen — key delivery says nothing about what
        the TUI did with it.

        The reply carries ``settled``: true means the batch was handled AND
        redrawn, so reading the screen straight after is race-free; false means
        the TUI was still busy — the keys are queued, not dropped, so re-read
        rather than resend. If the TUI's event loop is not running (starting up,
        or the user quit), this fails instead of pretending the keys landed.

        Args:
            keys: Key names, e.g. ["tab", "down", "enter"].
            delay_ms: Milliseconds to pause between keys (default 0, max 2000;
                the total pause across a batch may not exceed 10s).
        """
        return _send_keys(keys, delay_ms)

    @mcp.tool()
    def tui_send_text(text: str) -> Dict[str, Any]:
        """Type text into the GAIA TUI's focused input (e.g. the chat prompt).

        This types only — it does not submit. Follow with
        tui_send_keys(["enter"]) to send the message.

        Like tui_send_keys, the reply carries ``settled``: false means the TUI
        was still busy and the runes are queued, so re-read the screen instead of
        typing again.
        """
        return _send_text(text)

    @mcp.tool()
    def tui_wait_for(
        contains: str = "", absent: str = "", timeout_ms: int = 30000
    ) -> Dict[str, Any]:
        """Block until the GAIA TUI's screen matches, instead of polling it.

        Pass at least one matcher; both are ANDed and matched against the plain
        (ANSI-stripped) screen. On timeout this returns an error containing the
        text the screen actually had, so you can see why the match never landed.

        Args:
            contains: Text that must appear on screen.
            absent: Text that must have disappeared from the screen.
            timeout_ms: How long to wait before giving up (default 30000).
        """
        return _wait_for(contains=contains, absent=absent, timeout_ms=timeout_ms)

    @mcp.tool()
    def tui_resize(cols: int, rows: int) -> Dict[str, Any]:
        """Resize the GAIA TUI's virtual terminal.

        Useful for reproducing narrow-terminal layout bugs, or for widening the
        screen so long lines are not wrapped before you read them.

        The reply carries ``settled`` (see tui_send_keys). If the TUI ends up
        laid out for a different size — a real terminal resize can override this
        one — that comes back as an error naming both sizes, not as a success
        echoing the numbers you asked for.

        Args:
            cols: Terminal width, 20-500.
            rows: Terminal height, 5-200.
        """
        return _resize(cols, rows)

    return mcp


def main():
    parser = argparse.ArgumentParser(description="GAIA TUI control MCP Server")
    parser.add_argument(
        "--port",
        type=int,
        default=MCP_DEFAULT_PORT,
        help=f"MCP server port (default: {MCP_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        default=MCP_DEFAULT_HOST,
        help=f"MCP server host (default: {MCP_DEFAULT_HOST})",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Use stdio transport instead of HTTP (for Claude Code integration)",
    )
    args = parser.parse_args()

    mcp = create_tui_mcp()

    if args.stdio:
        route_logging_to_stderr()
        print("Starting GAIA TUI MCP Server (stdio mode)...", file=sys.stderr)
        mcp.run(transport="stdio")
    else:
        print("\n🚀 GAIA TUI MCP Server")
        print(f"   Control file: {_display_path()}")
        print(f"   MCP: http://{args.host}:{args.port}/mcp")
        try:
            tool_count = len(
                mcp._tool_manager._tools
            )  # pylint: disable=protected-access
            print(f"   Tools: {tool_count} registered\n")
        except AttributeError:
            logger.debug("MCPServer tool registry layout changed; skipping tool count")
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
