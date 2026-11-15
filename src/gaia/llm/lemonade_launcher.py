# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lemonade Server tooling resolution and start-command construction.

How Lemonade Server installs on each platform:

* Windows: ``LemonadeServer.exe`` (server, started with ``--silent``)
  plus ``lemonade.exe`` (client) under
  ``%LOCALAPPDATA%\\lemonade_server\\bin``.
* Linux: ``/usr/bin/lemonade`` (client) and ``/usr/bin/lemond``
  (daemon) managed by the ``lemond`` systemd unit.
* macOS: ``lemond`` (daemon) + ``lemonade`` (client) under
  ``/usr/local/bin``, installed by the Lemonade app. There is no systemd
  unit, so the daemon is started directly (or from the app).
* Lemonade takes no context size at launch: there is no ``--ctx-size``
  flag and no environment variable for it. GAIA sends the window with every
  ``/load`` instead.

This module is the single shared primitive for detecting which tooling is
installed and how to launch it; the installer
(`gaia.installer`) and the runtime client (`gaia.llm.lemonade_client`)
both consume it instead of hard-coding ``lemonade-server``.

stdlib-only by design — import direction is installer -> llm, no cycles.
"""

import logging
import os
import platform
import posixpath
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from gaia.env import child_env

log = logging.getLogger(__name__)

# macOS: the Lemonade app installs the daemon + client into a standard bin
# dir. /usr/local/bin is where the official installer puts them; the
# Apple-Silicon Homebrew prefix is probed too.
_MACOS_BIN_DIRS = ("/usr/local/bin", "/opt/homebrew/bin")
_MACOS_DAEMON_NAME = "lemond"
_MACOS_CLIENT_NAME = "lemonade"

_MACOS_APP_CANDIDATES = (
    "/Applications/lemonade-app.app",
    "~/Applications/lemonade-app.app",
)

_DOWNLOAD_URL = "https://lemonade-server.ai"

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass
class LemonadeTooling:
    """Resolved Lemonade tooling on this machine."""

    found: bool
    kind: str  # "modern" | "none"
    client_path: Optional[str] = None
    server_launcher: Optional[str] = None
    # "env" when resolved from LEMONADE_SERVER_PATH — an explicit override
    # is launched verbatim, never rerouted to systemctl.
    source: str = "probe"  # "env" | "probe"


@dataclass
class StartSpec:
    """How to start the Lemonade server for the resolved tooling.

    ``env`` contains ONLY the additional variables the server needs; the
    caller must merge it into the parent environment at the Popen call
    site — ``env=child_env(spec.env)`` — never replace it (a bare
    ``env=spec.env`` drops PATH/LOCALAPPDATA and breaks LemonadeServer.exe).
    """

    argv: List[str]
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class StartHint:
    """A remedy a user can actually act on to get Lemonade Server running.

    ``instruction`` is always safe to print verbatim and embeds ``command``
    when one exists. ``command`` is None whenever the platform has no start
    command a user would run (Windows tray, macOS app), or when no single
    line runs in both Command Prompt and PowerShell — ``instruction`` then
    names each form. Call sites must not invent one.
    """

    instruction: str
    command: Optional[str] = None
    # True when ``command`` occupies the terminal until the server stops, so
    # a caller that needs the shell back can append " &".
    foreground: bool = False


def resolve_lemonade() -> LemonadeTooling:
    """Resolve installed Lemonade tooling.

    Precedence, in this exact order:

    1. ``LEMONADE_SERVER_PATH`` env var (CI override) — used verbatim;
       ``shutil.which`` is never consulted.
    2. Tooling by CANONICAL path probe (not PATH order):
       Windows ``%LOCALAPPDATA%\\lemonade_server\\bin\\LemonadeServer.exe``,
       Linux ``/usr/bin/lemonade``, macOS ``/usr/local/bin/lemond``
       (falling back to ``lemond`` on PATH for a non-standard prefix).
    """
    env_path = os.environ.get("LEMONADE_SERVER_PATH")
    if env_path:
        log.debug("Using LEMONADE_SERVER_PATH override: %s", env_path)
        return LemonadeTooling(
            found=True,
            kind="modern",
            client_path=env_path,
            server_launcher=env_path,
            source="env",
        )

    system = platform.system()

    if system == "Windows":
        bin_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "lemonade_server" / "bin"
        server = bin_dir / "LemonadeServer.exe"
        client = bin_dir / "lemonade.exe"
        if server.exists():
            log.debug("Found modern Lemonade at canonical path: %s", server)
            return LemonadeTooling(
                found=True,
                kind="modern",
                client_path=str(client),
                server_launcher=str(server),
            )
    elif system == "Linux":
        client = Path("/usr/bin/lemonade")
        if client.exists():
            log.debug("Found modern Lemonade at canonical path: %s", client)
            return LemonadeTooling(
                found=True,
                kind="modern",
                client_path=str(client),
                server_launcher="/usr/bin/lemond",
            )
    elif system == "Darwin":
        # Probe the daemon (what we start), not the client — the client is
        # only needed for the version query and may be absent.
        for bin_dir in _MACOS_BIN_DIRS:
            # posixpath: the answer is a macOS path whatever host computes it.
            daemon = posixpath.join(bin_dir, _MACOS_DAEMON_NAME)
            if not Path(daemon).exists():
                continue
            client = posixpath.join(bin_dir, _MACOS_CLIENT_NAME)
            log.debug("Found modern Lemonade at canonical path: %s", daemon)
            return LemonadeTooling(
                found=True,
                kind="modern",
                client_path=client if Path(client).exists() else None,
                server_launcher=daemon,
            )
        # Installed under a non-standard prefix but still on PATH.
        daemon_on_path = shutil.which(_MACOS_DAEMON_NAME)
        if daemon_on_path:
            log.debug("Found modern Lemonade daemon on PATH: %s", daemon_on_path)
            return LemonadeTooling(
                found=True,
                kind="modern",
                client_path=shutil.which(_MACOS_CLIENT_NAME),
                server_launcher=daemon_on_path,
            )

    return LemonadeTooling(found=False, kind="none")


def get_installed_version(tooling: LemonadeTooling) -> Optional[str]:
    """Return the installed Lemonade version ("X.Y.Z"), or None.

    Runs ``lemonade --version`` (real output: ``lemonade version 2026.44.1``).
    """
    if not tooling.found or not tooling.client_path:
        return None

    try:
        result = subprocess.run(
            [tooling.client_path, "--version"],
            env=child_env(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.debug("Version probe failed for %s: %s", tooling.client_path, e)
        return None

    match = _VERSION_RE.search(result.stdout + result.stderr)
    if not match:
        log.debug(
            "Could not parse version from %r output: %r",
            tooling.client_path,
            (result.stdout + result.stderr).strip()[:200],
        )
        return None
    return match.group(1)


def build_start_command(tooling: LemonadeTooling) -> StartSpec:
    """Build the argv + extra-env needed to start the resolved server.

    Windows -> ``LemonadeServer.exe --silent``. Linux -> best-effort
    ``systemctl --user start lemond`` (the daemon is normally already up).
    macOS -> the ``lemond`` daemon directly; macOS has no systemd, so the
    Linux form must never leak there.

    Lemonade reads no context-size flag or environment variable, so the
    window goes with every ``/load`` instead and the spec's ``env`` is empty.

    Windows dispatch keys off the tooling's ``server_launcher``
    (an ``.exe`` means the Windows server binary) rather than the host
    platform, so a resolved tooling object is self-describing.
    """
    if not tooling.found:
        raise ValueError(
            "Cannot build a start command: no Lemonade tooling found. "
            "Run `gaia init` to install Lemonade Server, or set "
            "LEMONADE_SERVER_PATH to an existing binary."
        )

    launcher = tooling.server_launcher or ""
    if launcher.lower().endswith(".exe"):
        return StartSpec(argv=[launcher, "--silent"])
    if tooling.source == "env":
        # Explicit LEMONADE_SERVER_PATH override — run the named binary
        # verbatim rather than silently rerouting to systemctl.
        return StartSpec(argv=[launcher])
    if platform.system() == "Darwin":
        # No systemd on macOS — start the daemon directly.
        if not launcher:
            raise ValueError(
                "macOS Lemonade tooling has no server_launcher; "
                f"expected the {_MACOS_DAEMON_NAME!r} daemon path."
            )
        return StartSpec(argv=[launcher])
    # Linux daemon — best-effort user-unit start; the server is
    # normally already running under systemd.
    return StartSpec(argv=["systemctl", "--user", "start", "lemond"])


def _macos_app_installed() -> bool:
    """True if the macOS Lemonade app bundle is present."""
    return any(Path(p).expanduser().exists() for p in _MACOS_APP_CANDIDATES)


def _render_command(argv: List[str], env: Dict[str, str]) -> str:
    """Render argv+env as a copy-pasteable command line for this shell.

    ``shlex.join`` would wrap a Windows path in POSIX single quotes, which
    cmd.exe cannot run — so join (and prefix env) the Windows way there.
    """
    items = sorted(env.items())
    if platform.system() == "Windows":
        rendered = subprocess.list2cmdline(argv)
        if items:
            # Quoted: `set K=V && cmd` would assign "V " (trailing space).
            prefix = " && ".join(f'set "{k}={v}"' for k, v in items)
            rendered = f"{prefix} && {rendered}"
        return rendered

    rendered = shlex.join(argv)
    if items:
        rendered = " ".join(f"{k}={v}" for k, v in items) + f" {rendered}"
    return rendered


_PS_BARE_TOKEN = re.compile(r"[A-Za-z0-9_\-.:\\/=+]+")


def _ps_quote(token: str) -> str:
    """Quote *token* for PowerShell; single quotes keep ``$`` and backtick literal."""
    if _PS_BARE_TOKEN.fullmatch(token):
        return token
    return "'" + token.replace("'", "''") + "'"


def _render_powershell(argv: List[str], env: Dict[str, str]) -> str:
    """Render argv+env for PowerShell, which rejects cmd.exe's ``set ... &&``."""
    program = _ps_quote(argv[0])
    if program != argv[0]:
        # A quoted string is an expression in PowerShell; `&` makes it a call.
        program = f"& {program}"
    rendered = " ".join([program, *(_ps_quote(a) for a in argv[1:])])
    for k, v in reversed(sorted(env.items())):
        rendered = f"$env:{k}={_ps_quote(v)}; {rendered}"
    return rendered


def render_command(spec: StartSpec) -> str:
    """Render a :class:`StartSpec` as the line a user would actually have to run.

    The env is rendered with the argv, so a spec that needs a variable never
    loses it to a bare ``" ".join(argv)``. Windows quoting is handled too, so
    the line is copy-pasteable in the shell it names.
    """
    return _render_command(spec.argv, spec.env)


def gaia_runs_lemonade(base_url: Optional[str] = None) -> bool:
    """Whether the server to start for *base_url* is GAIA's own, not a system install.

    The one decision every start path makes before launching anything: True
    when ``gaia init`` installed GAIA's embedded server (this version, or a
    mismatched one the daemon repairs when it starts it), ``LEMONADE_BASE_URL``
    names no other, and *base_url* is unset or is the address GAIA resolves on
    its own. That server is started through the daemon, never by
    :func:`resolve_lemonade` / :func:`build_start_command`, and binds a port
    chosen at start time -- so a caller holding its stopped-state address
    (the port it last ran on) must re-resolve after starting it.
    """
    from gaia.llm.lemonade_client import (
        configured_lemonade_url,
        resolve_lemonade_base_url,
    )
    from gaia.llm.lemonade_embedded import EmbeddedLemonade, is_gaias_to_start

    if configured_lemonade_url():
        return False
    if not is_gaias_to_start(EmbeddedLemonade()):
        return False
    if base_url is None:
        return True
    return resolve_lemonade_base_url(base_url) == resolve_lemonade_base_url()


def server_down_advice(base_url: Optional[str] = None) -> str:
    """What an app user does about a model server that isn't answering.

    GAIA's own server is started by the daemon on the next message; one the
    user runs needs this machine's start instruction.
    """
    from gaia.llm.surface_hints import APP_SERVER_DOWN_HINT

    if gaia_runs_lemonade(base_url):
        return APP_SERVER_DOWN_HINT
    return describe_start_hint().instruction


def describe_start_hint(ctx_size: Optional[int] = None) -> StartHint:
    """Describe how to start Lemonade Server on THIS machine.

    The single source of user-facing "here's how to start it" advice. It
    never names a command that does not exist on the host: on platforms
    started from a GUI (Windows tray, macOS app) it returns prose with
    ``command=None`` rather than guessing a shell command. Service-managed
    context changes also return manual configuration steps.
    """
    if gaia_runs_lemonade():
        return StartHint(
            instruction=(
                "GAIA starts its Lemonade Server when it needs it. To start it "
                "now, run: gaia lemonade embedded start"
            ),
            command="gaia lemonade embedded start",
        )

    tooling = resolve_lemonade()
    system = platform.system()

    if tooling.found:
        launcher = (tooling.server_launcher or "").lower()
        if launcher.endswith(".exe"):
            # LemonadeServer.exe --silent is what GAIA's auto-start runs; a
            # user starts it from the tray instead.
            return StartHint(
                instruction=(
                    "Start Lemonade Server from the Lemonade tray icon, or "
                    "search for 'Lemonade' in the Start menu."
                )
            )
        spec = build_start_command(tooling)
        if spec.argv[0] == "systemctl" and ctx_size is not None:
            return StartHint(
                instruction=(
                    "If Lemonade is stopped, run `systemctl --user start lemond`. "
                    f"In Lemonade's model settings, set the context size to {ctx_size} "
                    "and reload the model. Starting an already running service "
                    "does not change its context size."
                )
            )
        command = _render_command(spec.argv, spec.env)
        if system == "Windows":
            powershell = _render_powershell(spec.argv, spec.env)
            if powershell != command:
                # No one line runs in both shells, so name each one.
                return StartHint(
                    instruction=(
                        f"Run in Command Prompt: {command} — or in PowerShell: "
                        f"{powershell}"
                    )
                )
        if system == "Darwin":
            # The app is the normal macOS path; the daemon is the CLI way.
            instruction = f"Start the Lemonade app from Applications, or run: {command}"
        else:
            instruction = f"Run: {command}"
        return StartHint(
            instruction=instruction,
            command=command,
            foreground=spec.argv[0] != "systemctl",
        )

    if system == "Darwin" and _macos_app_installed():
        return StartHint(
            instruction="Start the Lemonade app from Applications, then retry."
        )

    return StartHint(
        instruction=(
            "Lemonade Server is not installed. Run `gaia init` to install it, "
            "or set LEMONADE_SERVER_PATH to an existing install."
        )
    )


def describe_upgrade_hint(base_url: Optional[str] = None) -> str:
    """How the Lemonade Server at *base_url* gets to ``LEMONADE_MIN_VERSION``.

    GAIA upgrades its own server when it next starts it, so that case asks
    nothing of the user. A server the user runs is theirs to update; the
    remedy points at Lemonade's download page rather than a command, since how
    it updates depends on how it was installed.
    """
    if gaia_runs_lemonade(base_url):
        return (
            "GAIA updates its own Lemonade Server the next time its daemon "
            "starts it; run `gaia daemon restart` to do that now."
        )
    return f"Update it from {_DOWNLOAD_URL}, then retry."


# Lemonade client sub-commands GAIA points users at, mapped to the verb used
# in the prose. `gaia download` is NOT an alternative: it takes no positional
# model argument, so `gaia download <model>` is rejected by argparse.
_CLIENT_ACTIONS = {"pull": "downloaded", "load": "loaded"}


def describe_client_hint(action: str, model: str) -> StartHint:
    """Describe how to pull/load *model* with the resolved Lemonade client.

    Lemonade ships a ``lemonade`` client that takes
    ``lemonade pull|load <model>``. Returns prose with ``command=None`` when
    no client binary resolved, rather than naming one that isn't there.
    """
    if action not in _CLIENT_ACTIONS:
        raise ValueError(
            f"Unsupported Lemonade client action {action!r} "
            f"(expected one of {sorted(_CLIENT_ACTIONS)})."
        )

    tooling = resolve_lemonade()
    # An explicit LEMONADE_SERVER_PATH names a *server*; using it as the
    # client would be a guess, so only a probed install yields a command.
    if tooling.found and tooling.client_path and tooling.source == "probe":
        command = _render_command([tooling.client_path, action, model], {})
        return StartHint(instruction=f"Run: {command}", command=command)

    return StartHint(
        instruction=(
            f"{model} is not {_CLIENT_ACTIONS[action]}, and no Lemonade client "
            f"CLI was found to do it — see {_DOWNLOAD_URL}."
        )
    )
