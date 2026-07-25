# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""MCP server that drives the GAIA terminal UI through its local control API.

Lets an MCP client (Claude Code, the eval harness, a script) read what the TUI
is currently showing, send keystrokes and text to it, wait for the screen to
reach a given state, and drive high-level flows like launching an agent from
the hub.

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
    from mcp.server.fastmcp import FastMCP

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

#: Tab presses allowed when the build reports no ``hub_tab_index`` and the wrap
#: back to the starting tab therefore cannot be detected.
MAX_TAB_PROBES = 4

#: Runaway guard for the tab cycle; the wrap-detection is what normally ends it.
MAX_HUB_TABS = 32

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

    if view == "chat":
        agent = state.get("agent") or "?"
        summary = f"chat with {agent!r}"
        if state.get("streaming"):
            summary += ", streaming"
        if state.get("can_return_to_hub") is False:
            summary += " (standalone session — esc quits, there is no hub)"
        return summary

    if view == "hub":
        parts = ["hub view"]
        tab = state.get("hub_tab")
        if tab:
            parts.append(f"tab {tab!r}")
        visible = state.get("visible_agent_ids") or []
        parts.append(f"{len(visible)} agents visible")
        selected = state.get("selected_agent_id")
        if selected:
            parts.append(f"selected {selected!r}")
        if state.get("filtering"):
            parts.append("search filter active")
        overlay = state.get("overlay")
        if overlay:
            parts.append(f"overlay {overlay!r}")
        return ", ".join(parts)

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


#: Phrases the hub uses when it refuses to launch something. Matched only on a
#: line that also names the agent.
_REFUSAL_PHRASES = (
    "not installed",
    "coming soon",
    "not available",
    "unavailable",
    "failed",
)

#: Verb forms for the fallback scan, where no agent name anchors the match. The
#: hub's tab bar reads "Installed (1)  Available (9)  Coming Soon (3)", so a bare
#: "coming soon" would quote the tab bar back as if it were an explanation.
_REFUSAL_SENTENCES = (
    "is not installed",
    "is coming soon",
    "is not available",
    "is unavailable",
    "failed to",
)


def _hub_status_line(screen: str, agent_id: str) -> str:
    """Pull the hub's own explanation out of a rendered screen, or ``""``.

    Never guesses: the last line of a hub screen is the keybinding footer, and
    quoting that back as "the hub says" would be an invented explanation.
    """
    lines = [ln.strip() for ln in (screen or "").splitlines() if ln.strip()]
    lowered_id = agent_id.lower()
    for ln in reversed(lines):
        low = ln.lower()
        if lowered_id in low and any(p in low for p in _REFUSAL_PHRASES):
            return ln
    for ln in reversed(lines):
        if any(p in ln.lower() for p in _REFUSAL_SENTENCES):
            return ln
    return ""


_FOOTER_SEP_RE = re.compile(r"\s{2,}|\s*[·|•]\s*")


def _parse_footer_bindings(screen: str) -> Dict[str, str]:
    """Parse the hub footer (``Enter=launch  /=search  d=delete``) into key→action.

    Returns the bindings of the last line that advertises at least two of them,
    or ``{}`` when the screen has no recognizable footer.
    """
    for line in reversed([ln for ln in (screen or "").splitlines() if ln.strip()]):
        bindings: Dict[str, str] = {}
        for token in _FOOTER_SEP_RE.split(line.strip()):
            if "=" not in token:
                continue
            key, _, action = token.partition("=")
            key, action = key.strip(), action.strip()
            if key and action:
                bindings[key] = action
        if len(bindings) >= 2:
            return bindings
    return {}


_UNINSTALL_ACTION_RE = re.compile(r"uninstall|delete|remove", re.IGNORECASE)
_INSTALL_ACTION_RE = re.compile(r"(?<!un)install", re.IGNORECASE)


def _find_binding(bindings: Dict[str, str], kind: str) -> Optional[str]:
    """Return the key bound to install/uninstall, or ``None`` if the hub offers none."""
    pattern = _UNINSTALL_ACTION_RE if kind == "uninstall" else _INSTALL_ACTION_RE
    for key, action in bindings.items():
        if pattern.search(action):
            return key
    return None


def _format_bindings(bindings: Dict[str, str]) -> str:
    if not bindings:
        return "none (no footer bindings were visible on screen)"
    return "  ".join(f"{k}={v}" for k, v in bindings.items())


# ── Tool implementations (plain functions — importable without ``mcp``) ──


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


def _press(info: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Send one navigation key and require the TUI to have redrawn for it.

    ``settled: false`` means the model was still busy, so the status read that
    follows would describe the screen *before* the key — and every navigation
    step here is a decision made from that status. Acting on it is the race the
    whole state-driven design exists to avoid, so stop instead.
    """
    result = _request(info, "post", "/keys", json={"keys": [key], "delay_ms": 0})
    if _is_error(result):
        return result
    if result.get("settled") is False:
        return _err(
            f"The TUI did not finish handling {key!r} in time (settled=false), so "
            f"its reported state is not current and navigating on it would act on "
            f"a stale screen. The key is queued and may still land — read "
            f"tui_screen before retrying."
        )
    return result


def _leave_chat(
    info: Dict[str, Any], status: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Return to the hub if a chat is open, so hub-only reads are of the hub.

    Returns ``(status, None)`` with a re-read status, or ``({}, error)``.
    """
    if _view_of(status) != "chat":
        return status, None

    # Whether esc goes back or quits is the model's to answer, not ours to guess:
    # in a standalone `gaia chat --subprocess` session esc IS quit, so pressing it
    # would kill the very session we were asked to drive.
    can_return = _state_of(status).get("can_return_to_hub")
    if can_return is False:
        return {}, _err(
            "This is a standalone chat session, not a chat opened from the hub — "
            "esc there quits the TUI instead of returning to a hub, so there is no "
            "hub to navigate. Drive this session directly with tui_send_text and "
            "tui_send_keys, and read it with tui_screen."
        )
    if can_return is None:
        return {}, _err(
            "The running TUI does not report can_return_to_hub, so whether esc "
            "returns to the hub or quits the program cannot be known — and "
            "guessing wrong ends the session. Drive it directly with "
            f"tui_send_keys, or run a current build: {START_HINT}"
        )

    # Esc cancels an in-flight response. Throwing away the user's running turn to
    # satisfy a navigation step is not this tool's call to make.
    if _state_of(status).get("streaming"):
        return {}, _err(
            "The chat is streaming a response right now, and leaving it would "
            "cancel that turn. Wait for it to finish (tui_wait_for) or cancel it "
            "deliberately with tui_send_keys(['esc'])."
        )

    pressed = _press(info, "esc")
    if _is_error(pressed):
        return {}, pressed
    waited = _wait(info, state={"view": "hub"}, timeout_ms=5000)
    if _is_error(waited):
        # A standalone `gaia chat --control` session has no hub to go back to —
        # there esc quits, so the control server dies with it.
        if waited.get("unreachable"):
            return {}, _err(
                "The TUI exited when leaving the chat: a standalone chat session "
                "quits on esc instead of returning to a hub. Agent navigation by "
                f"id needs the hub TUI — {START_HINT}"
            )
        return {}, _err(
            f"Could not return to the hub from the chat view: {waited['detail']}"
        )
    status = _request(info, "get", "/status")
    if _is_error(status):
        return {}, status
    return status, None


def _navigate_to_agent(
    info: Dict[str, Any], agent_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Put the hub's selection on *agent_id*.

    Returns ``(status, None)`` with the status read after the last move, or
    ``(None, error)``. Every step is driven off the TUI's reported state rather
    than a blind key-count, so it either converges or says why it could not.
    """
    status = _request(info, "get", "/status")
    if _is_error(status):
        return None, status

    if _view_of(status) == "unknown":
        return None, _err(
            "The running GAIA TUI does not report its view state, so high-level "
            "navigation is unavailable in this build. Drive it manually with "
            "tui_send_keys and read the result with tui_screen."
        )

    status, leave_error = _leave_chat(info, status)
    if leave_error:
        return None, leave_error

    # An overlay eats the next keypress (any key dismisses help; the confirm
    # dialog answers it), so navigating under one moves nothing and misreports
    # why. Say what is in the way instead of pressing into it.
    overlay = str(_state_of(status).get("overlay") or "")
    if overlay:
        return None, _err(
            f"The hub is covered by the {overlay!r} overlay, which would swallow "
            f"the navigation keys. Dismiss it first with tui_send_keys(['esc']), "
            f"then retry."
        )

    if _state_of(status).get("filtering"):
        pressed = _press(info, "esc")
        if _is_error(pressed):
            return None, pressed
        status = _request(info, "get", "/status")
        if _is_error(status):
            return None, status

    # Cycle tabs until the agent is among the visible ids. ``POST /keys`` applies
    # the key before it answers, so the status read after each press is current.
    seen: List[str] = []
    found = False
    first_tab_index = _state_of(status).get("hub_tab_index")
    # The wrap back to the first tab is the real bound — the cap only stops a
    # build that reports no tab index, so adding a hub tab cannot silently make
    # the last one unreachable.
    for _ in range(MAX_TAB_PROBES if first_tab_index is None else MAX_HUB_TABS):
        state = _state_of(status)
        visible = list(state.get("visible_agent_ids") or [])
        if agent_id in visible:
            found = True
            break
        tab = state.get("hub_tab") or "?"
        seen.append(f"{tab}: {', '.join(visible) if visible else '(empty)'}")
        pressed = _press(info, "tab")
        if _is_error(pressed):
            return None, pressed
        status = _request(info, "get", "/status")
        if _is_error(status):
            return None, status
        if first_tab_index is not None:
            if _state_of(status).get("hub_tab_index") == first_tab_index:
                break

    if not found:
        return None, _err(
            f"Agent {agent_id!r} is not in any hub tab. Agents seen per tab — "
            + " | ".join(seen)
        )

    # Move the selection onto the agent. The hub list does not wrap and keeps its
    # cursor across a tab switch, so the direction has to be computed — pressing
    # "down" alone never reaches a row above the cursor.
    visible = list(_state_of(status).get("visible_agent_ids") or [])
    selected = _state_of(status).get("selected_agent_id") or ""
    if selected not in visible:
        return None, _err(
            f"The hub does not report which row is selected (it says "
            f"{selected!r}), so the selection cannot be moved onto {agent_id!r}. "
            f"Move it yourself with tui_send_keys(['down']) and read tui_screen."
        )

    delta = visible.index(agent_id) - visible.index(selected)
    key = "down" if delta > 0 else "up"
    for _ in range(abs(delta)):
        pressed = _press(info, key)
        if _is_error(pressed):
            return None, pressed
        status = _request(info, "get", "/status")
        if _is_error(status):
            return None, status

    if _state_of(status).get("selected_agent_id") == agent_id:
        return status, None

    return None, _err(
        f"Could not move the hub selection onto {agent_id!r} — after "
        f"{abs(delta)} {key!r} presses the selection is "
        f"{_state_of(status).get('selected_agent_id')!r}. The visible agents are: "
        f"{', '.join(visible) if visible else '(none)'}."
    )


def _with_screen(info: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the current plain screen to a success result.

    A failed read reports itself in ``screen_error`` rather than passing an empty
    string off as the screen — the caller would read that as a blank TUI.
    """
    screen_result = _request(info, "get", "/screen", params={"format": "plain"})
    if _is_error(screen_result):
        result["screen"] = ""
        result["screen_error"] = screen_result["detail"]
    else:
        result["screen"] = screen_result.get("screen", "")
    return result


def _launch_agent(agent_id: str, timeout_ms: int = 20000) -> Dict[str, Any]:
    if not agent_id:
        return _err("No agent_id given — pass the agent to launch, e.g. 'email'.")

    info, error = _discovered()
    if error:
        return error

    # Already there: re-launching would close the chat and throw the conversation
    # away to arrive back where we started.
    opening = _request(info, "get", "/status")
    if _is_error(opening):
        return opening
    if _view_of(opening) == "chat" and _state_of(opening).get("agent") == agent_id:
        result = {"launched": True, "already_open": True, "agent": agent_id}
        return _with_screen(info, result)

    _, nav_error = _navigate_to_agent(info, agent_id)
    if nav_error:
        return nav_error

    pressed = _press(info, "enter")
    if _is_error(pressed):
        return pressed

    waited = _wait(
        info, state={"view": "chat", "agent": agent_id}, timeout_ms=timeout_ms
    )
    if _is_error(waited):
        # Only a genuine wait timeout means "the TUI refused"; a transport error
        # is its own failure and must not be reported as a hub refusal.
        status = _request(info, "get", "/status") if waited.get("timed_out") else {}
        if not _is_error(status) and _view_of(status) == "hub":
            screen_result = _request(info, "get", "/screen", params={"format": "plain"})
            screen = (
                screen_result.get("screen", "")
                if not _is_error(screen_result)
                else waited.get("screen", "")
            )
            line = _hub_status_line(screen, agent_id)
            detail = f"The TUI stayed on the hub instead of launching {agent_id!r}."
            if line:
                detail += f" The hub says: {line!r}"
            result = _err(detail)
            result["screen"] = _truncate(screen)
            return result
        return waited

    return _with_screen(info, {"launched": True, "agent": agent_id})


def _hub_capability(
    info: Dict[str, Any], kind: str, status: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Find the hub's install/uninstall key, or explain that it has none.

    The key is read off the rendered footer rather than guessed, so this stays
    correct as the hub's bindings change (and fails loudly when they are absent).
    """
    # An overlay replaces the whole screen, footer included — reading it there
    # would report "no such keybinding" for a hub that has one.
    overlay = str(_state_of(status or {}).get("overlay") or "")
    if overlay:
        return None, _err(
            f"The hub is covered by the {overlay!r} overlay, so its keybindings are "
            f"not readable. Dismiss it first with tui_send_keys(['esc'])."
        )

    screen_result = _request(info, "get", "/screen", params={"format": "plain"})
    if _is_error(screen_result):
        return None, screen_result
    bindings = _parse_footer_bindings(screen_result.get("screen", ""))
    key = _find_binding(bindings, kind)
    if not key:
        verb = "Uninstall" if kind == "uninstall" else "Install"
        return None, _err(
            f"{verb} from the TUI hub is not yet available — the hub screen has no "
            f"{kind} keybinding in this build. Bindings currently offered: "
            f"{_format_bindings(bindings)}."
        )
    return key, None


_YES_RE = re.compile(r"\bYes\b")
_NO_RE = re.compile(r"\bNo\b")


def _confirm_marker(screen: str) -> str:
    """Marker text for an open confirmation dialog, or ``""`` if none is showing.

    Word-bounded on purpose: a substring test would read an agent named ``Notion``
    as a "No" button and send a stray ``y`` into whatever is actually focused.
    """
    screen = screen or ""
    if 'Uninstall "' in screen:
        return 'Uninstall "'
    if _YES_RE.search(screen) and _NO_RE.search(screen):
        return "Yes"
    return ""


def _hub_action(agent_id: str, kind: str) -> Dict[str, Any]:
    if not agent_id:
        return _err(f"No agent_id given — pass the agent to {kind}.")

    info, error = _discovered()
    if error:
        return error

    status = _request(info, "get", "/status")
    if _is_error(status):
        return status
    if _view_of(status) == "unknown":
        return _err(
            "The running GAIA TUI does not report its view state, so high-level "
            "navigation is unavailable in this build. Drive it manually with "
            "tui_send_keys and read the result with tui_screen."
        )

    # The capability is read off the hub's footer, so a chat must be closed
    # first — otherwise the chat's status bar is mistaken for "no binding".
    status, leave_error = _leave_chat(info, status)
    if leave_error:
        return leave_error

    key, cap_error = _hub_capability(info, kind, status)
    if cap_error or not key:
        return cap_error or _err(f"The hub offers no {kind} keybinding in this build.")

    _, nav_error = _navigate_to_agent(info, agent_id)
    if nav_error:
        return nav_error

    # Snapshot what the hub looked like before the key, so "did anything happen?"
    # is answerable afterwards without assuming which way the row should move.
    before = _request(info, "get", "/screen", params={"format": "plain"})
    if _is_error(before):
        return before
    before_screen = before.get("screen", "")

    pressed = _press(info, key)
    if _is_error(pressed):
        return pressed

    screen_result = _request(info, "get", "/screen", params={"format": "plain"})
    if _is_error(screen_result):
        return screen_result
    screen = screen_result.get("screen", "")

    # A reported overlay is authoritative; the screen text is the fallback for a
    # build whose state does not carry one.
    status = _request(info, "get", "/status")
    if _is_error(status):
        return status
    overlay = str(_state_of(status).get("overlay") or "")
    marker = _confirm_marker(screen)

    confirmed_dialog = bool(overlay or marker)
    if confirmed_dialog:
        pressed = _press(info, "y")
        if _is_error(pressed):
            return pressed
        if overlay:
            waited = _wait(info, state={"overlay": ""}, timeout_ms=15000)
        else:
            waited = _wait(info, absent=marker, timeout_ms=15000)
        if _is_error(waited):
            return waited
        screen_result = _request(info, "get", "/screen", params={"format": "plain"})
        if _is_error(screen_result):
            return screen_result
        screen = screen_result.get("screen", "")

    verify_error = _verify_hub_action(info, agent_id, kind, screen, before_screen)
    if verify_error:
        return verify_error

    done_key = "uninstalled" if kind == "uninstall" else "installed"
    return {
        done_key: True,
        "agent": agent_id,
        "confirmed": confirmed_dialog,
        "screen": screen,
    }


def _verify_hub_action(
    info: Dict[str, Any], agent_id: str, kind: str, screen: str, before_screen: str
) -> Optional[Dict[str, Any]]:
    """Confirm the hub actually did it. Returns an error dict, or None if it did.

    Pressing a key and seeing a dialog close is not evidence: the hub ignores its
    uninstall key unless the agent is installed, so reporting "uninstalled" for a
    row still sitting on screen is exactly the quiet wrong answer the
    No-Silent-Fallbacks rule forbids.
    """
    status = _request(info, "get", "/status")
    if _is_error(status):
        return status
    visible = list(_state_of(status).get("visible_agent_ids") or [])

    if kind == "uninstall":
        if agent_id not in visible:
            return None
        detail = (
            f"The hub still lists {agent_id!r} on the "
            f"{_state_of(status).get('hub_tab') or 'current'} tab after the "
            f"uninstall key, so nothing was removed — the hub only uninstalls an "
            f"agent that is currently installed."
        )
    else:
        # An install promotes the agent to another tab, so it may legitimately
        # vanish from this one. The only claim that holds either way is that
        # SOMETHING changed; an unchanged screen means the key was ignored.
        if screen != before_screen:
            return None
        detail = (
            f"The hub ignored the install key for {agent_id!r} — the screen is "
            f"unchanged, so nothing was installed."
        )

    line = _hub_status_line(screen, agent_id)
    if line:
        detail += f" The hub says: {line!r}"
    result = _err(detail)
    result["screen"] = _truncate(screen)
    return result


def _uninstall_agent(agent_id: str) -> Dict[str, Any]:
    return _hub_action(agent_id, "uninstall")


def _install_agent(agent_id: str) -> Dict[str, Any]:
    return _hub_action(agent_id, "install")


# ── MCP server ───────────────────────────────────────────────────────


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


def create_tui_mcp() -> "FastMCP":
    """Create the MCP server exposing the GAIA TUI control tools."""
    # Imported lazily so the helpers above stay importable without the optional
    # ``mcp`` dependency, which the unit-test job does not install (issue #1750).
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(name="GAIA TUI")

    @mcp.tool()
    def tui_status() -> Dict[str, Any]:
        """Check whether a GAIA TUI is running and what it is currently showing.

        Start here: it verifies the TUI's control server is reachable and returns
        the terminal size, the frame sequence number, and a ``summary`` line such
        as "hub view, tab 'Installed', 5 agents visible, selected 'bash'" or
        "chat with 'email', streaming".

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

    @mcp.tool()
    def tui_launch_agent(agent_id: str, timeout_ms: int = 20000) -> Dict[str, Any]:
        """Open an agent's chat from the GAIA TUI hub, by id.

        Handles the whole navigation: leaves any open chat, clears an active
        search filter, cycles hub tabs until the agent is visible, moves the
        selection onto it, presses enter, and waits for the chat view to appear.
        Each step is verified against the TUI's reported state.

        Returns ``{"launched": true, "agent": ..., "screen": ...}``. If that chat
        is already open it returns ``already_open: true`` without touching the
        keyboard, so an open conversation is never thrown away.

        It refuses rather than press a key when doing so would destroy something:
        while the chat is streaming a response, or while a help/confirm overlay
        is covering the hub. If the agent cannot be launched (not installed,
        coming soon), the error quotes the hub's own status line.

        Args:
            agent_id: The hub's agent id, e.g. "email" or "bash".
            timeout_ms: How long to wait for the chat view (default 20000).
        """
        return _launch_agent(agent_id, timeout_ms)

    @mcp.tool()
    def tui_uninstall_agent(agent_id: str) -> Dict[str, Any]:
        """Uninstall an agent from the GAIA TUI hub.

        Navigates to the agent, presses the hub's uninstall key, and confirms the
        dialog if one appears. The key is read off the hub footer rather than
        guessed — if this build's hub has no uninstall binding, the tool says so
        instead of pressing something arbitrary.

        The outcome is verified, not assumed: the hub ignores its uninstall key
        for an agent that is not installed, and that comes back as an error
        rather than a cheerful ``uninstalled: true``.
        """
        return _uninstall_agent(agent_id)

    @mcp.tool()
    def tui_install_agent(agent_id: str) -> Dict[str, Any]:
        """Install an agent from the GAIA TUI hub.

        Navigates to the agent, presses the hub's install key, and confirms the
        dialog if one appears. The key is read off the hub footer rather than
        guessed.

        Note: the hub has no install binding today, so this reports that it is
        not yet available and lists the bindings the hub does offer. Installing
        an agent still goes through `gaia agent install` on the CLI.
        """
        return _install_agent(agent_id)

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
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print("\n🚀 GAIA TUI MCP Server")
        print(f"   Control file: {_display_path()}")
        print(f"   MCP: http://{args.host}:{args.port}/mcp")
        tool_count = len(mcp._tool_manager._tools)  # pylint: disable=protected-access
        print(f"   Tools: {tool_count} registered\n")
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
