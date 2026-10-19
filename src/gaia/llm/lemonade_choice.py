# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which Lemonade Server GAIA uses: its own private one, or one the user runs.

GAIA installs and starts a private Lemonade by default. A user who already runs
Lemonade can point GAIA at it instead, so one machine never holds two servers
and two copies of every model. The choice lives in ``~/.gaia/config.json``:

* ``lemonade_mode``: ``"gaia"`` (GAIA's own) or ``"external"`` (the user's).
  Unset means nobody has chosen yet, which behaves as ``"gaia"`` and lets
  first-run setup offer a Lemonade it finds running.
* ``lemonade_base_url``: the user's server, used only in ``external`` mode.
* ``lemonade_api_key_stored``: the server's API key is in the OS credential
  store (never in the config file).

``LEMONADE_BASE_URL`` / ``LEMONADE_API_KEY`` still win over all of this.

In ``external`` mode GAIA never starts a Lemonade of any kind. A server that is
down, too old, needs a key it does not have, or is not Lemonade at all is an
error that names the next step, never a reason to start GAIA's own.

Every call reads the config file afresh, so a long-lived process (the daemon,
the Agent UI server) follows a changed setting without a restart.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlparse

from gaia.logger import get_logger

log = get_logger(__name__)

MODE_GAIA = "gaia"
MODE_EXTERNAL = "external"
MODES = (MODE_GAIA, MODE_EXTERNAL)

#: Credential-store slot for the user's Lemonade API key.
API_KEY_SECRET_NAME = "lemonade_api_key"

#: Ports a locally installed Lemonade listens on: the current default, then
#: the pre-10.1 one. Same list the TUI's preflight probes.
DETECT_PORTS = (13305, 8000)

_DETECT_TIMEOUT = (0.5, 3.0)
_PROBE_TIMEOUT = (3.0, 10.0)

#: Where a user who wants GAIA's own server back is pointed, in one line.
SWITCH_BACK_HINT = "run `gaia config set lemonade_mode gaia` to use GAIA's own instead"


class LemonadeChoiceError(ValueError):
    """The Lemonade choice is unusable; the message names the fix.

    ``status`` is the probe outcome when a server answered badly (for
    example ``needs_key``), so a UI can ask for what is missing.
    """

    def __init__(self, message: str, status: Optional[str] = None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# The saved choice
# ---------------------------------------------------------------------------


def normalize_external_url(raw: Optional[str]) -> str:
    """Validate a user-typed Lemonade URL and return it in canonical form.

    Bare origins gain ``/api/v1``, matching ``resolve_lemonade_base_url``.

    Raises:
        LemonadeChoiceError: Not an http(s) URL with a host, or it carries
            credentials, a query, or a fragment that GAIA would mishandle.
    """
    value = (raw or "").strip()
    if not value:
        raise LemonadeChoiceError(
            "No Lemonade URL given. Use the address your Lemonade Server "
            "listens on, e.g. http://localhost:13305."
        )
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as e:
        raise LemonadeChoiceError(f"{value!r} is not a valid URL: {e}.") from e
    if parsed.scheme not in ("http", "https"):
        raise LemonadeChoiceError(
            f"{value!r} is not an http:// or https:// address. Use the address "
            "your Lemonade Server listens on, e.g. http://localhost:13305."
        )
    if not parsed.hostname:
        raise LemonadeChoiceError(f"{value!r} has no host name.")
    if parsed.username is not None or parsed.password is not None:
        raise LemonadeChoiceError(
            "Don't put credentials in the Lemonade URL. Give the API key "
            "separately; GAIA keeps it in the OS credential store."
        )
    if parsed.query or parsed.fragment:
        raise LemonadeChoiceError(
            f"{value!r} has a query or fragment; give just the server address."
        )
    if port == 0:
        raise LemonadeChoiceError(f"{value!r} has port 0.")
    trimmed = value.rstrip("/")
    if not parsed.path.strip("/"):
        return urlparse(trimmed)._replace(path="/api/v1").geturl()
    return trimmed


def _config():
    from gaia.config import GaiaConfig

    return GaiaConfig.load()


def saved_mode() -> Optional[str]:
    """The saved ``lemonade_mode``, or None when nobody has chosen."""
    return _config().lemonade_mode


def external_lemonade_url() -> Optional[str]:
    """The user's server when ``lemonade_mode`` is ``external``, else None.

    Raises:
        LemonadeChoiceError: ``external`` is saved without a usable URL.
    """
    cfg = _config()
    if cfg.lemonade_mode != MODE_EXTERNAL:
        return None
    if not cfg.lemonade_base_url:
        raise LemonadeChoiceError(
            "lemonade_mode is 'external' but lemonade_base_url is not set. Run "
            "`gaia lemonade connect <url>`, or " + SWITCH_BACK_HINT + "."
        )
    return normalize_external_url(cfg.lemonade_base_url)


def external_mode_active() -> bool:
    """True when the saved choice, not ``LEMONADE_BASE_URL``, picks the server.

    In that mode GAIA starts nothing: the server is the user's to run.
    """
    if _env_url():
        return False
    return _config().lemonade_mode == MODE_EXTERNAL


def _env_url() -> Optional[str]:
    """``LEMONADE_BASE_URL`` when it is the user's choice (not GAIA's export)."""
    if os.getenv("GAIA_LEMONADE_EMBEDDED", "").strip() == "1":
        return None
    return os.getenv("LEMONADE_BASE_URL", "").strip() or None


def configured_source() -> Optional[str]:
    """Who chose the server: ``"env"``, ``"config"``, or None for GAIA's own."""
    if _env_url():
        return "env"
    if external_lemonade_url():
        return "config"
    return None


def switch_back_hint() -> str:
    """How to stop using the configured server, for the source that set it."""
    if configured_source() == "env":
        return "unset LEMONADE_BASE_URL to use GAIA's own instead"
    return SWITCH_BACK_HINT


def external_api_key(base_url: Optional[str]) -> Optional[str]:
    """The stored key for the saved external server, when *base_url* is it.

    Scoped like the embedded key: it is only ever sent to the server it was
    saved for.

    Raises:
        LemonadeChoiceError: A key was saved but the credential store cannot
            return it.
    """
    cfg = _config()
    if cfg.lemonade_mode != MODE_EXTERNAL or not cfg.lemonade_api_key_stored:
        return None
    if not cfg.lemonade_base_url or not base_url:
        return None
    try:
        saved = normalize_external_url(cfg.lemonade_base_url)
        target = normalize_external_url(base_url)
    except LemonadeChoiceError:
        return None
    if saved != target:
        return None
    from gaia.connectors.errors import ConnectorsError

    try:
        from gaia.connectors.store import peek_secret

        key = peek_secret(API_KEY_SECRET_NAME)
    except ConnectorsError as e:
        raise LemonadeChoiceError(
            f"GAIA saved an API key for the Lemonade Server at {saved}, but the "
            f"OS credential store could not return it: {e}. Re-enter it with "
            "`gaia lemonade connect <url>`."
        ) from e
    if not key:
        raise LemonadeChoiceError(
            f"GAIA expected an API key for the Lemonade Server at {saved} in the "
            "OS credential store, but none is there. Re-enter it with "
            "`gaia lemonade connect <url>`."
        )
    return key


# ---------------------------------------------------------------------------
# Probing a server
# ---------------------------------------------------------------------------

#: Probe outcomes.
OK = "ok"
NEEDS_KEY = "needs_key"
BAD_KEY = "bad_key"
TOO_OLD = "too_old"
NOT_LEMONADE = "not_lemonade"
UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class ProbeResult:
    """What answered at a URL, and whether GAIA can use it."""

    url: str
    status: str
    version: Optional[str] = None
    minimum: Optional[str] = None
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status == OK

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "status": self.status,
            "version": self.version,
            "minimum": self.minimum,
            "message": self.message(),
        }

    def message(self) -> str:
        """One plain sentence and the next step."""
        if self.status == OK:
            return f"Lemonade Server v{self.version} is running at {self.url}."
        if self.status == NEEDS_KEY:
            return (
                f"The Lemonade Server at {self.url} needs an API key. Enter the "
                "key it was started with (its LEMONADE_API_KEY)."
            )
        if self.status == BAD_KEY:
            return (
                f"The Lemonade Server at {self.url} rejected the API key. Check "
                "the key it was started with (its LEMONADE_API_KEY)."
            )
        if self.status == TOO_OLD:
            return (
                f"The Lemonade Server at {self.url} is v{self.version}; GAIA needs "
                f"v{self.minimum} or newer. Upgrade it from "
                "https://lemonade-server.ai, or " + SWITCH_BACK_HINT + "."
            )
        if self.status == NOT_LEMONADE:
            return (
                f"Something answers at {self.url}, but it is not a Lemonade "
                f"Server ({self.detail}). Check the address and port."
            )
        return (
            f"No Lemonade Server is answering at {self.url} ({self.detail}). "
            "Start it, or " + SWITCH_BACK_HINT + "."
        )


def minimum_version() -> str:
    """The oldest Lemonade the flagship profile runs against."""
    from gaia.installer.init_command import DEFAULT_INIT_PROFILE, INIT_PROFILES

    return INIT_PROFILES[DEFAULT_INIT_PROFILE]["min_lemonade_version"]


def probe(
    url: str,
    api_key: Optional[str] = None,
    *,
    timeout: Tuple[float, float] = _PROBE_TIMEOUT,
) -> ProbeResult:
    """Ask ``<url>/health`` what is there. Never raises for a bad answer.

    Only a JSON body with ``status: ok`` and a parseable ``version`` counts as
    Lemonade: a web server returning HTML, or any other JSON, does not.
    """
    import requests

    from gaia.version import parse_version

    base = normalize_external_url(url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    minimum = minimum_version()
    try:
        response = requests.get(
            f"{base}/health", headers=headers, timeout=timeout, allow_redirects=False
        )
    except requests.exceptions.RequestException as e:
        return ProbeResult(base, UNREACHABLE, minimum=minimum, detail=_short(e))
    if response.status_code in (401, 403):
        return ProbeResult(base, BAD_KEY if api_key else NEEDS_KEY, minimum=minimum)
    if response.status_code != 200:
        return ProbeResult(
            base, NOT_LEMONADE, minimum=minimum, detail=f"HTTP {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError:
        kind = response.headers.get("content-type", "").split(";")[0] or "not JSON"
        return ProbeResult(base, NOT_LEMONADE, minimum=minimum, detail=kind)
    version = body.get("version") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or body.get("status") != "ok"
        or not isinstance(version, str)
        or parse_version(version) is None
    ):
        return ProbeResult(
            base, NOT_LEMONADE, minimum=minimum, detail="no Lemonade health answer"
        )
    if parse_version(version) < parse_version(minimum):
        return ProbeResult(base, TOO_OLD, version=version, minimum=minimum)
    return ProbeResult(base, OK, version=version, minimum=minimum)


def _short(error: Exception) -> str:
    text = str(error).splitlines()[0] if str(error) else type(error).__name__
    return text[:160]


def detect_running_lemonade() -> Optional[ProbeResult]:
    """A Lemonade the user runs on this machine's default ports, if any.

    Only offered when it really is Lemonade: a ``/health`` answer that says so,
    or a 401 on a port where this machine has a Lemonade install (found the way
    :func:`~gaia.llm.lemonade_launcher.resolve_lemonade` finds one). GAIA's own
    private server is never offered back to it.

    Returns the first usable or key-protected server, else a too-old one so
    the caller can say why it was not offered, else None.
    """
    from gaia.llm.lemonade_client import running_embedded_lemonade_url
    from gaia.llm.lemonade_launcher import resolve_lemonade

    own = running_embedded_lemonade_url()
    too_old: Optional[ProbeResult] = None
    installed: Optional[bool] = None
    for port in DETECT_PORTS:
        url = f"http://localhost:{port}/api/v1"
        if own and urlparse(own).port == port:
            continue
        result = probe(url, timeout=_DETECT_TIMEOUT)
        if result.status == OK:
            return result
        if result.status == NEEDS_KEY:
            if installed is None:
                installed = resolve_lemonade().found
            if installed:
                return result
        if result.status == TOO_OLD and too_old is None:
            too_old = result
    return too_old


# ---------------------------------------------------------------------------
# Changing the choice
# ---------------------------------------------------------------------------


def use_external(url: str, api_key: Optional[str] = None) -> ProbeResult:
    """Check the user's server and save it as GAIA's Lemonade.

    Nothing is saved unless the server answers as a new-enough Lemonade with
    the key given (or needs none). Without a key, one already saved for this
    same address is reused.

    Raises:
        LemonadeChoiceError: The URL is malformed, or the server is not usable;
            the message is the probe's one-sentence answer.
    """
    from gaia.config import GaiaConfig

    base = normalize_external_url(url)
    key = (api_key or "").strip() or None
    if key is None:
        cfg = GaiaConfig.load()
        if cfg.lemonade_base_url == base:
            key = _stored_key(cfg)
    result = probe(base, key)
    if not result.usable:
        raise LemonadeChoiceError(result.message(), result.status)
    if key:
        _store_key(key)
    else:
        _forget_key()
    cfg = GaiaConfig.load()
    cfg.lemonade_mode = MODE_EXTERNAL
    cfg.lemonade_base_url = base
    cfg.lemonade_api_key_stored = bool(key)
    cfg.save()
    return result


def use_gaia() -> None:
    """Go back to GAIA's own server. The saved URL is kept for a later switch."""
    from gaia.config import GaiaConfig

    cfg = GaiaConfig.load()
    cfg.lemonade_mode = MODE_GAIA
    cfg.save()


#: Probe status for a switch to GAIA's own server when it is not installed.
NOT_INSTALLED = "not_installed"


def require_gaia_lemonade_installed() -> None:
    """Refuse to leave the user's server for a GAIA server that isn't there.

    Without it GAIA would fall back to the default port, which is usually the
    user's own server, while saying it had switched.

    Raises:
        LemonadeChoiceError: GAIA's own server is missing or only partly
            unpacked; status ``not_installed``.
    """
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    if EmbeddedLemonade().is_installed():
        return
    raise LemonadeChoiceError(
        "GAIA's own Lemonade Server is not installed, so GAIA is still using "
        "yours. Install it and switch in one step with `gaia init "
        "--gaia-lemonade`. Nothing was changed.",
        NOT_INSTALLED,
    )


def restore_mode(previous: Optional[str]) -> None:
    """Put back the saved mode after a switch the daemon could not apply."""
    from gaia.config import GaiaConfig

    GaiaConfig.save_key("lemonade_mode", previous)


def _store_key(key: str) -> None:
    from gaia.connectors.errors import ConnectorsError

    try:
        from gaia.connectors.store import save_secret

        save_secret(API_KEY_SECRET_NAME, key)
    except ConnectorsError as e:
        raise LemonadeChoiceError(
            f"Could not keep the Lemonade API key in the OS credential store: "
            f"{e}. GAIA does not write keys to its config file. Set "
            "LEMONADE_API_KEY in the environment instead."
        ) from e


def _forget_key() -> None:
    from gaia.connectors.errors import ConnectorsError

    try:
        from gaia.connectors.store import delete_secret

        delete_secret(API_KEY_SECRET_NAME)
    except ConnectorsError as e:
        # lemonade_api_key_stored goes false with this, so no reader asks for it.
        log.warning("Could not remove the old Lemonade API key: %s", e)


# ---------------------------------------------------------------------------
# Never changing the user's server
# ---------------------------------------------------------------------------

#: Lemonade routes that change a server's setup instead of using it, and what
#: each one would have done, for the refusal.
_SETUP_ROUTES = {
    "install": "register a cloud provider or install a backend",
    "uninstall": "remove a cloud provider or backend",
    "delete": "delete a model",
    "unload": "unload a model",
    "cloud/auth": "set a cloud provider key",
}

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _server_identity(url: str) -> Optional[Tuple[str, str, int, str]]:
    try:
        parsed = urlparse(url.strip())
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if host in _LOOPBACK_HOSTS:
        host = "localhost"
    scheme = parsed.scheme.lower()
    port = port or (443 if scheme == "https" else 80)
    return scheme, host, port, parsed.path.rstrip("/")


def users_server_route(url: str) -> Optional[Tuple[str, str]]:
    """``(saved_url, route)`` when *url* is on the user's own server, else None.

    Only the saved ``external`` choice counts: ``LEMONADE_BASE_URL`` is an
    override, not a server GAIA promised to leave alone.
    """
    if not external_mode_active():
        return None
    saved = external_lemonade_url()
    mine = _server_identity(saved or "")
    theirs = _server_identity(url)
    if mine is None or theirs is None or mine[:3] != theirs[:3]:
        return None
    path = theirs[3]
    if path != mine[3] and not path.startswith(mine[3] + "/"):
        return None
    return saved, path[len(mine[3]) :].strip("/")


def users_server_unchanged_message(saved_url: str, action: str) -> str:
    """The refusal: GAIA won't change the user's server, and what to do."""
    return (
        f"GAIA is set to use your Lemonade Server at {saved_url}, and it does not "
        f"change that server's setup, so it did not {action}. Do it on your server "
        f"yourself, or {SWITCH_BACK_HINT}."
    )


def users_server_repair_message(url: str, model: str, cause: Exception) -> str:
    """A broken model on the user's server: theirs to repair, never GAIA's."""
    match = users_server_route(url)
    server = match[0] if match else url
    return (
        f"The model '{model}' on your Lemonade Server at {server} is incomplete "
        f"or corrupt, and resuming its download did not fix it ({cause}). GAIA "
        "does not change models on your server. Repair it there by downloading "
        f"it again with your server's own tools, or {SWITCH_BACK_HINT}."
    )


def setup_change_refusal(
    url: str, method: str, payload: Optional[dict] = None
) -> Optional[str]:
    """Why a request must not go to the user's server, or None when it may.

    Using the server is fine: chat, embeddings, loading a model, downloading
    one. Changing how it is set up is not: registering or removing providers,
    handing it keys, installing backends, deleting or unloading models, or
    saving load options into its config.
    """
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return None
    match = users_server_route(url)
    if match is None:
        return None
    saved, route = match
    route = route.split("?", 1)[0]
    if route.startswith("cloud/auth"):
        return users_server_unchanged_message(saved, _SETUP_ROUTES["cloud/auth"])
    if route in _SETUP_ROUTES:
        return users_server_unchanged_message(saved, _SETUP_ROUTES[route])
    if method.upper() == "DELETE":
        return users_server_unchanged_message(saved, f"delete {route}")
    if route == "load" and isinstance(payload, dict) and payload.get("save_options"):
        return users_server_unchanged_message(saved, "save model load options")
    return None


def is_users_server(url: str) -> bool:
    """True when *url* is on the user's own server (saved ``external`` mode)."""
    return users_server_route(url) is not None


def external_down_message(url: str) -> str:
    """The error for the user's server not answering: GAIA starts nothing."""
    from gaia.llm.lemonade_launcher import describe_start_hint

    return (
        f"Your Lemonade Server at {url} is not answering, and GAIA does not "
        "start it or its own while set to use yours. Start it "
        f"({describe_start_hint().instruction}), or {SWITCH_BACK_HINT}."
    )


def apply_to_daemon() -> Optional[dict]:
    """Make a running daemon follow the saved choice; None when none runs.

    Raises:
        DaemonError: The daemon could not apply it or restart.
    """
    from gaia.daemon.client import reconfigure_lemonade

    return reconfigure_lemonade()


def apply_gaia_switch(previous_mode: Optional[str]) -> Optional[dict]:
    """Tell the daemon about a switch to GAIA's own, and wait until it answers.

    The daemon starts GAIA's server in the background, so a start that fails
    would otherwise leave ``gaia`` saved while nothing serves it. Leaving the
    user's server is checked end to end; a first-run choice is not, because
    setup installs the server afterwards.

    Raises:
        DaemonError: The daemon did not switch, or GAIA's server did not start.
            The previous mode is saved back first.
    """
    from gaia.daemon.client import ensure_lemonade
    from gaia.daemon.errors import DaemonError

    try:
        applied = apply_to_daemon()
        if applied is not None and previous_mode == MODE_EXTERNAL:
            applied = {**applied, "lemonade": ensure_lemonade()}
    except DaemonError:
        # The daemon reads the choice live, so it follows this too.
        restore_mode(previous_mode)
        raise
    return applied


def saved_target() -> dict:
    """The server the saved setting picks, ignoring ``LEMONADE_BASE_URL``.

    What the machine-wide daemon should use: one shell's environment
    variable is that shell's override, not the machine's choice.
    """
    url = external_lemonade_url()
    return {"mode": MODE_EXTERNAL if url else MODE_GAIA, "base_url": url}


def env_override_note() -> Optional[str]:
    """A warning when this process's ``LEMONADE_BASE_URL`` overrides the setting."""
    env = _env_url()
    if not env:
        return None
    return (
        f"LEMONADE_BASE_URL is set to {env} here, so commands run from this "
        "shell still use that server. Unset it to follow the setting."
    )


def describe() -> dict:
    """The effective choice, for status screens and the daemon."""
    from gaia.llm.lemonade_client import (
        configured_lemonade_url,
        resolve_lemonade_base_url,
    )

    cfg = _config()
    source = configured_source()
    configured = configured_lemonade_url()
    return {
        "mode": MODE_EXTERNAL if source else MODE_GAIA,
        "saved_mode": cfg.lemonade_mode,
        "source": source or "gaia",
        "base_url": resolve_lemonade_base_url(configured) if configured else None,
        "saved_base_url": cfg.lemonade_base_url,
        "api_key_stored": bool(cfg.lemonade_api_key_stored),
    }


def _stored_key(cfg) -> Optional[str]:
    """The key saved for ``cfg.lemonade_base_url``, regardless of mode."""
    if not cfg.lemonade_api_key_stored:
        return None
    from gaia.connectors.errors import ConnectorsError

    try:
        from gaia.connectors.store import peek_secret

        return peek_secret(API_KEY_SECRET_NAME)
    except ConnectorsError as e:
        raise LemonadeChoiceError(
            f"The OS credential store could not return the saved Lemonade API "
            f"key: {e}. Re-enter it with `gaia lemonade connect <url>`."
        ) from e


def set_mode(mode: str) -> Optional[ProbeResult]:
    """Switch ``lemonade_mode``, checking the user's server before using it.

    Raises:
        LemonadeChoiceError: Unknown mode, no saved server, or the saved
            server is not usable right now (nothing is changed).
    """
    from gaia.config import GaiaConfig

    if mode == MODE_GAIA:
        if saved_mode() == MODE_EXTERNAL:
            require_gaia_lemonade_installed()
        use_gaia()
        return None
    if mode != MODE_EXTERNAL:
        raise LemonadeChoiceError(
            f"lemonade_mode must be one of {', '.join(MODES)}, got {mode!r}."
        )
    cfg = GaiaConfig.load()
    if not cfg.lemonade_base_url:
        raise LemonadeChoiceError(
            "No Lemonade Server saved yet. Run `gaia lemonade connect <url>` "
            "with the address yours listens on."
        )
    result = probe(cfg.lemonade_base_url, _stored_key(cfg))
    if not result.usable:
        raise LemonadeChoiceError(result.message(), result.status)
    cfg.lemonade_mode = MODE_EXTERNAL
    cfg.save()
    return result


def set_url(url: Optional[str]) -> Optional[ProbeResult]:
    """Change the saved server. In ``external`` mode it must work first.

    A new address drops the saved key: it belonged to the old server.

    Raises:
        LemonadeChoiceError: Malformed URL, or (in external mode) the server
            is not usable without a key -- use ``gaia lemonade connect``.
    """
    from gaia.config import GaiaConfig

    cfg = GaiaConfig.load()
    base = normalize_external_url(url) if url and url.strip() else None
    if base is None and cfg.lemonade_mode == MODE_EXTERNAL:
        raise LemonadeChoiceError(
            "GAIA is using your Lemonade Server, so it needs its address. "
            + SWITCH_BACK_HINT[0].upper()
            + SWITCH_BACK_HINT[1:]
            + " first."
        )
    result = None
    if base and cfg.lemonade_mode == MODE_EXTERNAL:
        result = probe(base)
        if result.status == NEEDS_KEY:
            raise LemonadeChoiceError(
                f"The Lemonade Server at {base} needs an API key. Run `gaia "
                f"lemonade connect {base}` to enter it.",
                NEEDS_KEY,
            )
        if not result.usable:
            raise LemonadeChoiceError(result.message(), result.status)
    if base != cfg.lemonade_base_url and cfg.lemonade_api_key_stored:
        _forget_key()
        cfg.lemonade_api_key_stored = False
    cfg.lemonade_base_url = base
    cfg.save()
    return result
