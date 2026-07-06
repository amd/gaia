# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lemonade Server tooling resolution and start-command construction.

Modern Lemonade Server (10.7/10.8) removed the ``lemonade-server`` CLI:

* Windows ships ``LemonadeServer.exe`` (server, started with ``--silent``)
  plus ``lemonade.exe`` (client) under
  ``%LOCALAPPDATA%\\lemonade_server\\bin``.
* Linux ships ``/usr/bin/lemonade`` (client) and ``/usr/bin/lemond``
  (daemon) managed by the ``lemond`` systemd unit.
* Context size is passed via the ``LEMONADE_CTX_SIZE`` environment
  variable, NOT a ``serve --ctx-size`` flag.

Legacy Lemonade still uses ``lemonade-server serve --ctx-size N`` (plus
``--no-tray`` on Windows). This module is the single shared primitive for
detecting which tooling is installed and how to launch it; the installer
(`gaia.installer`) and the runtime client (`gaia.llm.lemonade_client`)
both consume it instead of hard-coding ``lemonade-server``.

stdlib-only by design — import direction is installer -> llm, no cycles.
"""

import logging
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Legacy CLI names, in probe order. lemonade-server-dev is the pip/CI variant.
_LEGACY_BINARIES = ("lemonade-server", "lemonade-server-dev")

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass
class LemonadeTooling:
    """Resolved Lemonade tooling on this machine."""

    found: bool
    kind: str  # "modern" | "legacy" | "none"
    client_path: Optional[str] = None
    server_launcher: Optional[str] = None


@dataclass
class StartSpec:
    """How to start the Lemonade server for the resolved tooling.

    ``env`` contains ONLY the additional variables the server needs; the
    caller must merge it into the parent environment at the Popen call
    site — ``env={**os.environ, **spec.env}`` — never replace it (a bare
    ``env=spec.env`` drops PATH/LOCALAPPDATA and breaks LemonadeServer.exe).
    """

    argv: List[str]
    env: Dict[str, str] = field(default_factory=dict)


def _classify_kind_from_name(path_str: str) -> str:
    """Infer modern/legacy from a binary's basename (for env overrides)."""
    name = Path(path_str).name.lower()
    if name.startswith("lemonade-server"):
        return "legacy"
    if name.startswith("lemonadeserver") or name in (
        "lemond",
        "lemonade",
        "lemonade.exe",
    ):
        return "modern"
    return "legacy"


def resolve_lemonade() -> LemonadeTooling:
    """Resolve installed Lemonade tooling.

    Precedence, in this exact order:

    1. ``LEMONADE_SERVER_PATH`` env var (CI override) — used verbatim;
       ``shutil.which`` is never consulted.
    2. Modern tooling by CANONICAL path probe (not PATH order):
       Windows ``%LOCALAPPDATA%\\lemonade_server\\bin\\LemonadeServer.exe``,
       Linux ``/usr/bin/lemonade``. Modern wins even when a stale legacy
       ``lemonade-server`` binary is also on PATH.
    3. Legacy ``shutil.which("lemonade-server")`` (also tolerates the
       pip/CI ``lemonade-server-dev`` variant).
    """
    env_path = os.environ.get("LEMONADE_SERVER_PATH")
    if env_path:
        log.debug("Using LEMONADE_SERVER_PATH override: %s", env_path)
        return LemonadeTooling(
            found=True,
            kind=_classify_kind_from_name(env_path),
            client_path=env_path,
            server_launcher=env_path,
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

    for name in _LEGACY_BINARIES:
        legacy_path = shutil.which(name)
        if legacy_path:
            log.debug("Found legacy Lemonade CLI: %s", legacy_path)
            return LemonadeTooling(
                found=True,
                kind="legacy",
                client_path=legacy_path,
                server_launcher=legacy_path,
            )

    return LemonadeTooling(found=False, kind="none")


def get_installed_version(tooling: LemonadeTooling) -> Optional[str]:
    """Return the installed Lemonade version ("X.Y.Z"), or None.

    Modern: ``lemonade --version`` (real output: ``lemonade version 10.7.0``).
    Legacy: ``lemonade-server --version``.
    """
    if not tooling.found or not tooling.client_path:
        return None

    try:
        result = subprocess.run(
            [tooling.client_path, "--version"],
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


def build_start_command(tooling: LemonadeTooling, ctx_size: Optional[int]) -> StartSpec:
    """Build the argv + extra-env needed to start the resolved server.

    Modern Windows -> ``LemonadeServer.exe --silent`` with
    ``LEMONADE_CTX_SIZE`` in env. Modern Linux -> best-effort
    ``systemctl --user start lemond`` (the daemon is normally already up).
    Legacy -> ``lemonade-server serve --ctx-size N`` (+ ``--no-tray`` on
    Windows), byte-identical to the historical argv.

    Modern-vs-Windows dispatch keys off the tooling's ``server_launcher``
    (an ``.exe`` means the Windows server binary) rather than the host
    platform, so a resolved tooling object is self-describing.
    """
    if not tooling.found:
        raise ValueError(
            "Cannot build a start command: no Lemonade tooling found. "
            "Run `gaia init` to install Lemonade Server, or set "
            "LEMONADE_SERVER_PATH to an existing binary."
        )

    if tooling.kind == "modern":
        env = {"LEMONADE_CTX_SIZE": str(ctx_size)} if ctx_size is not None else {}
        launcher = tooling.server_launcher or ""
        if launcher.lower().endswith(".exe"):
            return StartSpec(argv=[launcher, "--silent"], env=env)
        # Linux daemon — best-effort user-unit start; the server is
        # normally already running under systemd.
        return StartSpec(argv=["systemctl", "--user", "start", "lemond"], env=env)

    if tooling.kind == "legacy":
        argv = [tooling.server_launcher or "lemonade-server", "serve"]
        if platform.system() == "Windows":
            argv.append("--no-tray")
        if ctx_size is not None:
            argv.extend(["--ctx-size", str(ctx_size)])
        return StartSpec(argv=argv, env={})

    raise ValueError(
        f"Unknown Lemonade tooling kind {tooling.kind!r} "
        "(expected 'modern' or 'legacy')."
    )
