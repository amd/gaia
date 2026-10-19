# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Private, per-install Lemonade Server for GAIA.

Upstream ships an "embeddable" Lemonade build: a ``lemond`` daemon plus a
``lemonade`` HTTP client, with no installer, no tray app and no machine-wide
state. This module downloads that artifact, unpacks it under ``~/.gaia``, and
runs it on a private port behind a generated API key.

The point is a GAIA that carries its own inference server: ``gaia init``
installs and starts this one, and a system-wide Lemonade install is never
used unless ``LEMONADE_BASE_URL`` points at it.

Layout under ``$GAIA_HOME/lemonade`` (``~/.gaia/lemonade`` by default)::

    dist/<version>/     unpacked artifact (lemond, lemonade, resources/)
    cache/              lemond cache_dir -- downloaded backends live in bin/
    config/config.json  lemond config_dir
    state.json          the running instance: pid, port, API key, version, owner
    last_port.json      the port the last start used, preferred by the next one
    lemond.log          daemon stdout/stderr

``cache/`` and ``config/`` are deliberately *not* versioned: backends run from
141 MB (llama.cpp CPU + Vulkan) to 4.3 GB (ROCm, which also pulls a per-GPU
TheRock runtime), and re-downloading them on every Lemonade bump is not
acceptable. lemond invalidates stale binaries itself via the
``clear_bin_if_lemonade_below`` pin in its ``backend_versions.json``.
"""

import errno
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from gaia.env import child_env
from gaia.logger import get_logger
from gaia.utils.archive import ArchiveError, safe_extract
from gaia.utils.terminal import (
    background_popen_kwargs,
    windows_hidden_popen_kwargs,
)
from gaia.version import LEMONADE_VERSION

log = get_logger(__name__)

GITHUB_RELEASE_BASE = "https://github.com/lemonade-sdk/lemonade/releases/download"

#: Set by GAIA's credentials file: LEMONADE_BASE_URL/LEMONADE_API_KEY in this
#: environment describe GAIA's own server, not one the user runs.
EMBEDDED_ENV_MARKER = "GAIA_LEMONADE_EMBEDDED"
RELEASES_PAGE = "https://github.com/lemonade-sdk/lemonade/releases"

# (platform.system(), normalized machine) -> asset name template.
_ASSET_TEMPLATES: Dict[Tuple[str, str], str] = {
    ("Windows", "x86_64"): "lemonade-embeddable-{version}-windows-x64.zip",
    ("Linux", "x86_64"): "lemonade-embeddable-{version}-ubuntu-x64.tar.gz",
    ("Linux", "aarch64"): "lemonade-embeddable-{version}-ubuntu-arm64.tar.gz",
    ("Darwin", "aarch64"): "lemonade-embeddable-{version}-macos-arm64.tar.gz",
}

_MACHINE_ALIASES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "arm64": "aarch64",
    "aarch64": "aarch64",
}

# SHA-256 of every embeddable asset for LEMONADE_VERSION, as published by the
# GitHub release API. Bumping LEMONADE_VERSION without refreshing these makes
# install() fail loudly; tests/integration/test_lemonade_embeddable_assets.py
# checks them against the live release so CI catches the omission first.
EMBEDDABLE_SHA256: Dict[str, str] = {
    "lemonade-embeddable-2026.40.0-windows-x64.zip": "30acbcc8beea565f0ea69ea92b229c45c7e7c756a6b35573c45840765c599e8f",
    "lemonade-embeddable-2026.40.0-ubuntu-x64.tar.gz": "7b7ff14dfe9aea312f5d88281e6499d98fa52ad09f0272dd7ef32e6b6ada3537",
    "lemonade-embeddable-2026.40.0-ubuntu-arm64.tar.gz": "6ab4a80e51e80f8410b1b31aa7665717e0f78926d57ff55e2bf83995d19dbb32",
    "lemonade-embeddable-2026.40.0-macos-arm64.tar.gz": "5301b1004e95454b4b4b2a0b09206764c52d387fe6fcd6ac5340bffe55779151",
}

# lemond reads these from <config_dir>/config.json.
#
# broadcast=false keeps the private instance off the UDP discovery beacon, so a
# stray `lemonade` CLI on the machine does not attach to GAIA's server.
#
# Deliberately absent: no_fetch_executables. Setting it true collapses the
# advertised catalogue from 204 models to the 4 runnable by built-in backends,
# which would break `gaia download` for everything else. write_config() merges
# over the existing file, so it strips this key rather than merely not adding it.
# auto_evict=false is pinned rather than inherited. Lemonade v2026.39.1 added
# VRAM auto-eviction and defaults it off today, but it is a new knob and GAIA's
# residency model rests on which way it points.
#
# GAIA assumes a chat model and an embedder stay co-resident: RAGSDK scopes its
# embedder unload so a global /unload cannot take the chat model with it (#1544),
# and ModelSlotBroker serialises sidecar loads so they do not race the slot.
# Eviction under VRAM pressure is that same failure arriving from underneath.
# Under pressure the choice is a loud load failure or a silent eviction that
# resurfaces as a cold reload or a truncated answer; fail-loudly takes the loud
# one. Turning it on is a real option for VRAM-constrained machines, but it needs
# measurement on AMD GPU hardware first (#4170).
#
# auto_evict_threshold_pct is inert while this is false, so it is left unset
# rather than stated and ignored.
_LEMOND_CONFIG = {
    "config_version": 2,
    "host": "localhost",
    "broadcast": False,
    "auto_evict": False,
}
_LEMOND_STRIPPED_KEYS = ("no_fetch_executables",)


def _lemond_config(ctx_size: Optional[int] = None) -> Dict[str, object]:
    """The config GAIA writes for its private instance.

    ``global_timeout`` is the per-request budget. Lemonade 11.9.0 made it
    configurable, defaulting to 600s, which is short for the window GAIA pins: a
    long document is one large prefill, and 65536 tokens on a slow machine does
    not finish in ten minutes — the request dies and the answer is truncated.

    The value comes from ``request_budget_seconds`` so the server and the client
    that talks to it cannot be set to disagree.
    """
    from gaia.llm.lemonade_client import request_budget_seconds

    return {**_LEMOND_CONFIG, "global_timeout": request_budget_seconds(ctx_size)}


_HEALTH_PATH = "/api/v1/health"
_START_TIMEOUT = 60.0
_STOP_TIMEOUT = 20.0
_DOWNLOAD_TIMEOUT = 300


#: What :meth:`EmbeddedLemonade._identify` makes of a recorded pid.
OWN = "own"
OTHER = "other"
UNKNOWN = "unknown"


class EmbeddedLemonadeError(RuntimeError):
    """An embedded Lemonade operation failed. Message names the remedy."""


class UnsupportedPlatformError(EmbeddedLemonadeError):
    """No embeddable artifact is published for this OS/architecture."""


@dataclass(frozen=True)
class EmbeddedStatus:
    """Snapshot of the embedded instance.

    Attributes:
        installed: Whether the artifact is unpacked and the daemon present.
        running: Whether the recorded instance answers its health endpoint.
        version: Version of the running instance, else the targeted version.
        port: TCP port of the running instance, if any.
        pid: Process id of the running instance, if any.
        base_url: OpenAI-compatible base URL, if running.
        dist_dir: Where the artifact is unpacked.
        unresponsive_pid: A daemon process that is alive but not answering.
            Set only when ``running`` is False; the caller must stop it rather
            than start a second one.
        owner_pid: The GAIA daemon that owns the instance, if one claimed it.
            None for one ``gaia init`` or ``gaia lemonade embedded start``
            started that no daemon has taken over yet.
        owner_started: That daemon's process start time, so a recycled pid is
            not mistaken for it.
    """

    installed: bool
    running: bool
    version: str
    port: Optional[int] = None
    pid: Optional[int] = None
    base_url: Optional[str] = None
    dist_dir: Optional[Path] = None
    unresponsive_pid: Optional[int] = None
    owner_pid: Optional[int] = None
    owner_started: Optional[float] = None


def pid_exists(pid: int) -> bool:
    """Whether a process with *pid* is running. Standard library only.

    Cheap enough for every URL resolution, unlike the identity check in
    :meth:`EmbeddedLemonade._identify`, so it can be wrong only for a
    recycled pid -- which then fails to connect, as before.
    """
    if pid <= 0:
        return False
    # The real OS, not platform.system(): on Windows signal 0 is CTRL_C_EVENT,
    # so os.kill(pid, 0) Ctrl+Cs the process group or raises WinError 87.
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


#: lemond processes this process spawned, by pid. Holding the handle is what
#: lets an unexpected exit be reported with its exit code (#5261).
_SPAWNED: Dict[int, subprocess.Popen] = {}

#: How much of lemond.log to scan for its last lines; the file grows unbounded.
_LOG_TAIL_BYTES = 64 * 1024

# Seconds between the Windows FILETIME epoch (1601) and the Unix one.
_FILETIME_EPOCH_OFFSET = 11644473600


def _windows_exit_info(pid: int) -> Optional[Tuple[int, float]]:
    """Exit code and exit time of a finished Windows process, by pid.

    Readable only while some process still holds a handle to it -- the daemon
    that spawned it does. None when it cannot be opened or is still running.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    # A private WinDLL, so the restype set here leaves ctypes.windll alone.
    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        if code.value == 259:  # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        exited = times[1]
        ticks = (exited.dwHighDateTime << 32) | exited.dwLowDateTime
        return code.value, ticks / 1e7 - _FILETIME_EPOCH_OFFSET
    finally:
        kernel32.CloseHandle(handle)


def _exit_details(pid: int) -> Tuple[Optional[int], Optional[float]]:
    """``(exit code, exit time)`` of a lemond that is gone; either may be None."""
    info = _windows_exit_info(pid)
    if info is not None:
        return info
    process = _SPAWNED.get(pid)
    if process is not None and process.poll() is not None:
        return process.returncode, None
    return None, None


def _describe_exit_code(code: Optional[int]) -> str:
    if code is None:
        return "exit code unavailable"
    if code < 0:
        return f"killed by signal {-code}"
    if code > 0x7FFFFFFF:
        return f"exit code {code} (0x{code:08X})"
    return f"exit code {code}"


#: Display adapters whose Vulkan cooperative-matrix path fails under the
#: llama.cpp Lemonade bundles (b10825) on an old driver: Qwen3.6 crashed on its
#: first token and Gemma 4 generated at 14 tok/s, not 34. Strix Halo only — the
#: one GPU this was measured on.
_COOPMAT_OFF_ADAPTERS = re.compile(r"Radeon\(TM\) 80[4-6]0S", re.IGNORECASE)

#: The newest driver (registry ``DriverVersion``) measured crashing. Newer ones
#: run Qwen3.6 with coopmat on, and turning it off there halves prompt speed.
_COOPMAT_BROKEN_THROUGH = (32, 0, 12078, 30)

#: Windows' display-adapter class; each adapter's subkey names the adapter.
_DISPLAY_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)


def _windows_display_adapters() -> List[Tuple[str, str]]:
    """``(DriverDesc, DriverVersion)`` of every display adapter Windows records."""
    import winreg  # pylint: disable=import-error

    adapters = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS_KEY)
    except OSError:
        return adapters
    with root:
        index = 0
        while True:
            try:
                subkey = winreg.EnumKey(root, index)
            except OSError:
                return adapters
            index += 1
            try:
                with winreg.OpenKey(root, subkey) as adapter:
                    name = str(winreg.QueryValueEx(adapter, "DriverDesc")[0])
                    try:
                        version = str(winreg.QueryValueEx(adapter, "DriverVersion")[0])
                    except OSError:
                        version = ""
                    adapters.append((name, version))
            except OSError:
                continue  # "Properties" and other non-adapter subkeys


def _driver_version(text: str) -> Optional[Tuple[int, ...]]:
    try:
        return tuple(int(part) for part in text.strip().split("."))
    except ValueError:
        return None


def vulkan_workarounds() -> Dict[str, str]:
    """Environment the embedded Lemonade needs for this machine's GPU.

    A value the user already set is left alone.
    """
    if sys.platform != "win32" or "GGML_VK_DISABLE_COOPMAT" in os.environ:
        return {}
    for name, version_text in _windows_display_adapters():
        if not _COOPMAT_OFF_ADAPTERS.search(name):
            continue
        version = _driver_version(version_text)
        if version is None:
            log.info(
                "%s reports no readable driver version (%r); leaving Vulkan coopmat "
                "on. If Qwen3.6 crashes on its first token, update the AMD driver "
                "or set GGML_VK_DISABLE_COOPMAT=1.",
                name,
                version_text,
            )
        elif version <= _COOPMAT_BROKEN_THROUGH:
            log.info(
                "%s driver %s is old enough to crash Qwen3.6 under Vulkan coopmat; "
                "turning coopmat off. Updating the AMD driver keeps it on and faster.",
                name,
                version_text,
            )
            return {"GGML_VK_DISABLE_COOPMAT": "1"}
    return {}


def gaia_home() -> Path:
    """Return GAIA's state directory.

    Returns:
        ``$GAIA_HOME`` when set, otherwise ``~/.gaia``.
    """
    override = os.environ.get("GAIA_HOME")
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override))).resolve()
    return Path.home() / ".gaia"


def _normalized_machine() -> str:
    """Return this host's architecture in the naming the asset table uses.

    Returns:
        ``x86_64`` or ``aarch64``.

    Raises:
        UnsupportedPlatformError: The architecture is not one GAIA maps.
    """
    raw = platform.machine().lower()
    normalized = _MACHINE_ALIASES.get(raw)
    if normalized is None:
        raise UnsupportedPlatformError(
            f"Unsupported CPU architecture '{platform.machine()}' for embedded "
            f"Lemonade. Run Lemonade Server on a supported machine and set "
            f"LEMONADE_BASE_URL to it, or see {RELEASES_PAGE} for the published "
            f"assets."
        )
    return normalized


def asset_name(version: str = LEMONADE_VERSION) -> str:
    """Return the embeddable asset filename for this platform.

    Args:
        version: Lemonade version to resolve.

    Returns:
        The release asset filename.

    Raises:
        UnsupportedPlatformError: No asset is published for this OS/arch.
    """
    key = (platform.system(), _normalized_machine())
    template = _ASSET_TEMPLATES.get(key)
    if template is None:
        supported = ", ".join(f"{s}/{m}" for s, m in sorted(_ASSET_TEMPLATES))
        raise UnsupportedPlatformError(
            f"Embedded Lemonade is not published for {key[0]}/{key[1]}. "
            f"Supported: {supported}. Run Lemonade Server on a supported "
            f"machine and set LEMONADE_BASE_URL to it, or check {RELEASES_PAGE}."
        )
    return template.format(version=version)


def asset_url(version: str = LEMONADE_VERSION) -> str:
    """Return the download URL for this platform's embeddable asset.

    Args:
        version: Lemonade version to resolve.

    Returns:
        Absolute GitHub release download URL.
    """
    return f"{GITHUB_RELEASE_BASE}/v{version}/{asset_name(version)}"


def _inside(path: str, root: str) -> bool:
    """Whether *path* is under *root* (both compared case-normalized)."""
    try:
        resolved = os.path.normcase(str(Path(path).resolve()))
        return os.path.commonpath([root, resolved]) == root
    except (OSError, ValueError):
        # ValueError: different drives on Windows -- not under root.
        return False


def _anchor_pid(proc, windows: bool = sys.platform == "win32") -> int:
    """The process whose death orphans *proc*.

    Windows never reparents, so it is the recorded parent. POSIX reparents an
    orphan to init or a subreaper, so it is the session leader instead: lemond
    starts its own session, and its backends stay in it.
    """
    if windows:
        return proc.ppid()
    return os.getsid(proc.pid)  # pylint: disable=no-member


def _outlived(anchor: int, born: float) -> bool:
    """Whether the process *anchor* is gone, or its pid now names a process
    younger than one created at *born*. One the OS hides counts as alive."""
    import psutil

    try:
        return psutil.Process(anchor).create_time() > born
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied as e:
        log.debug("Treating pid %s as alive: cannot inspect it (%s)", anchor, e)
        return False


def _free_port() -> int:
    """Return a TCP port the OS reports as free on loopback.

    Returns:
        A port number nothing is currently bound to.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_LOOPBACKS = ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1"))
# The machine has no IPv6 stack: nothing can hold a port on ::1.
_NO_FAMILY = (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT)


def _binds(family: int, host: str, port: int, reuseport: bool) -> bool:
    """Whether a socket of *family* can bind *host*:*port* right now."""
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
    except OSError as e:
        if family == socket.AF_INET6 and e.errno in _NO_FAMILY:
            return True
        raise
    with sock:
        if reuseport:
            # Only the POSIX path passes reuseport; Windows has no SO_REUSEPORT.
            sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_REUSEPORT, 1  # pylint: disable=no-member
            )
        try:
            sock.bind((host, port))
        except OSError as e:
            return family == socket.AF_INET6 and e.errno in _NO_FAMILY
    return True


def _answers(family: int, host: str, port: int) -> bool:
    """Whether something accepts TCP connections on *host*:*port*."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except ConnectionRefusedError:
        return False
    except TimeoutError:
        return True  # a listener whose backlog is full
    except OSError as e:
        if family == socket.AF_INET6 and e.errno in _NO_FAMILY + (errno.ENETUNREACH,):
            return False
        raise


def _listening_on(port: int) -> bool:
    """Whether any local socket, on any address, listens on *port*."""
    import psutil

    return any(
        c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port
        for c in psutil.net_connections(kind="tcp")
    )


def _port_free(port: int) -> bool:
    """Whether lemond could bind *port* on loopback with nothing listening there.

    lemond (cpp-httplib) binds 127.0.0.1 and ::1 with SO_REUSEPORT on POSIX,
    which is what lets it reuse a port its last run left in TIME_WAIT; a bind
    without it fails there for up to a minute. So the POSIX probe binds the
    same way. SO_REUSEPORT also binds through a live same-user listener -- the
    user's own lemond, say -- so a connect must find nobody answering first.

    Windows has no SO_REUSEPORT and TIME_WAIT never blocks a plain bind. But a
    bind there only conflicts with the exact same address (127.0.0.1 ignores
    0.0.0.0 and ::), and a refused connect takes ~2 s, so the OS's listener
    table decides instead.
    """
    if sys.platform == "win32":
        if _listening_on(port):
            return False
        return all(
            _binds(family, host, port, reuseport=False) for family, host in _LOOPBACKS
        )
    # Connect before binding: a connect that lands on its own ephemeral port
    # leaves a TIME_WAIT on *port*, which the bind then reports as taken.
    if any(_answers(family, host, port) for family, host in _LOOPBACKS):
        return False
    return all(
        _binds(family, host, port, reuseport=True) for family, host in _LOOPBACKS
    )


def _extract(archive: Path, dest: Path) -> None:
    """Unpack *archive* into *dest*, validating every member.

    Args:
        archive: ``.zip`` or ``.tar.gz`` file.
        dest: Empty directory to unpack into.

    Raises:
        EmbeddedLemonadeError: Unknown suffix or an unsafe member.
    """
    if archive.name.endswith(".zip"):
        kind = "zip"
    elif archive.name.endswith((".tar.gz", ".tgz")):
        kind = "tar"
    else:
        raise EmbeddedLemonadeError(
            f"Cannot unpack '{archive.name}': expected a .zip or .tar.gz "
            f"embedded Lemonade asset. Delete {archive} and retry."
        )
    try:
        # Links only on tar: the Windows .zip asset has none, and os.symlink
        # needs Developer Mode or admin there.
        safe_extract(archive, dest, allow_links=(kind == "tar"), kind=kind)
    except ArchiveError as e:
        raise EmbeddedLemonadeError(
            f"Refusing to unpack embedded Lemonade: {e}. The download is corrupt "
            f"or tampered with -- delete it and retry, and report it at "
            f"{RELEASES_PAGE}."
        ) from e


def _flatten_single_root(unpacked: Path) -> Path:
    """Return the directory actually holding the payload.

    The published archives wrap their contents in a single
    ``lemonade-embeddable-<version>-<platform>/`` directory. Strip it so the
    on-disk layout does not depend on that packaging choice.

    Args:
        unpacked: Directory the archive was extracted into.

    Returns:
        *unpacked*, or its sole subdirectory when that is all it contains.
    """
    entries = list(unpacked.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return unpacked


class EmbeddedLemonade:
    """Manages GAIA's private Lemonade Server: install, start, stop, uninstall, status.

    Args:
        version: Lemonade version to install and run.
        home: GAIA state directory. Defaults to :func:`gaia_home`.
        progress_callback: Called as ``(bytes_done, total_bytes)`` while
            downloading. ``total_bytes`` is 0 when the server sends no length.
    """

    def __init__(
        self,
        version: str = LEMONADE_VERSION,
        home: Optional[Path] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ):
        self.version = version
        self.root = (home or gaia_home()) / "lemonade"
        self.progress_callback = progress_callback

    # -- paths ------------------------------------------------------------

    @property
    def dist_dir(self) -> Path:
        """Directory holding the unpacked artifact for this version."""
        return self.root / "dist" / self.version

    @property
    def cache_dir(self) -> Path:
        """lemond ``cache_dir`` -- shared across versions, holds ``bin/``."""
        return self.root / "cache"

    @property
    def config_dir(self) -> Path:
        """lemond ``config_dir`` -- shared across versions."""
        return self.root / "config"

    @property
    def env_path(self) -> Path:
        """Shell-sourceable credentials for the running instance.

        The API key is written here instead of printed, so it never reaches
        terminal scrollback, shell history or a CI log.
        """
        name = "env.ps1" if platform.system() == "Windows" else "env.sh"
        return self.root / name

    @property
    def state_path(self) -> Path:
        """JSON file recording the running instance."""
        return self.root / "state.json"

    @property
    def last_port_path(self) -> Path:
        """The port the last start used. Survives ``stop`` so the next start
        lands on the same port."""
        return self.root / "last_port.json"

    @property
    def log_path(self) -> Path:
        """File the daemon's stdout and stderr are appended to."""
        return self.root / "lemond.log"

    @property
    def daemon_path(self) -> Path:
        """Path to the ``lemond`` executable for this version."""
        suffix = ".exe" if platform.system() == "Windows" else ""
        return self.dist_dir / f"lemond{suffix}"

    @property
    def client_path(self) -> Path:
        """Path to the bundled ``lemonade`` HTTP client for this version."""
        suffix = ".exe" if platform.system() == "Windows" else ""
        return self.dist_dir / f"lemonade{suffix}"

    def is_installed(self) -> bool:
        """Whether the artifact is unpacked and the daemon is present.

        Returns:
            True if :attr:`daemon_path` exists.
        """
        return self.daemon_path.is_file()

    # -- install ----------------------------------------------------------

    def install(self, force: bool = False) -> Path:
        """Download, verify and unpack the embeddable artifact.

        Args:
            force: Reinstall even when the version is already unpacked.

        Returns:
            The directory the artifact was unpacked into.

        Raises:
            UnsupportedPlatformError: No asset for this OS/architecture.
            EmbeddedLemonadeError: Download, checksum or unpack failed.
        """
        if self.is_installed() and not force:
            log.debug("Embedded Lemonade %s already installed", self.version)
            return self.dist_dir

        if force and self.is_installed():
            running = self.status()
            if running.running or running.unresponsive_pid:
                raise EmbeddedLemonadeError(
                    f"Refusing to reinstall embedded Lemonade while it is "
                    f"running (pid {running.pid or running.unresponsive_pid}). "
                    f"Run `gaia lemonade embedded stop` first."
                )

        name = asset_name(self.version)
        expected = EMBEDDABLE_SHA256.get(name)
        if expected is None:
            raise EmbeddedLemonadeError(
                f"No pinned SHA-256 for '{name}'. GAIA verifies the embeddable "
                f"download against a checksum pinned in "
                f"gaia/llm/lemonade_embedded.py (EMBEDDABLE_SHA256); the pins "
                f"cover Lemonade {LEMONADE_VERSION}. Add the digest from "
                f"{RELEASES_PAGE}/tag/v{self.version} before installing "
                f"version {self.version}."
            )

        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gaia-lemonade-") as tmp:
            archive = Path(tmp) / name
            self._download(asset_url(self.version), archive)
            self._verify(archive, expected)

            unpacked = Path(tmp) / "unpacked"
            _extract(archive, unpacked)
            payload = _flatten_single_root(unpacked)
            self._install_payload(payload)

        if not self.is_installed():
            raise EmbeddedLemonadeError(
                f"Unpacked embedded Lemonade {self.version} but "
                f"{self.daemon_path.name} is missing from {self.dist_dir}. The "
                f"release layout changed -- check "
                f"{RELEASES_PAGE}/tag/v{self.version}."
            )
        log.info("Installed embedded Lemonade %s to %s", self.version, self.dist_dir)
        return self.dist_dir

    def _download(self, url: str, dest: Path) -> None:
        """Stream *url* to *dest*, reporting progress.

        Args:
            url: Asset download URL.
            dest: File to write.

        Raises:
            EmbeddedLemonadeError: The asset is missing or the transfer failed.
        """
        log.info("Downloading %s", url)
        request = urllib.request.Request(url, headers={"User-Agent": "GAIA/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as response:
                total = int(response.headers.get("content-length", 0))
                done = 0
                with open(dest, "wb") as handle:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        if self.progress_callback:
                            self.progress_callback(done, total)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise EmbeddedLemonadeError(
                    f"Embedded Lemonade asset not found: {url} (HTTP {e.code}). "
                    f"Either LEMONADE_VERSION points at a release without an "
                    f"embeddable build, or the asset was renamed. Check "
                    f"{RELEASES_PAGE}/tag/v{self.version}."
                ) from e
            raise EmbeddedLemonadeError(
                f"Failed to download {url}: HTTP {e.code} {e.reason}. Retry, or "
                f"download it by hand from {RELEASES_PAGE}/tag/v{self.version}."
            ) from e
        except urllib.error.URLError as e:
            raise EmbeddedLemonadeError(
                f"Failed to download {url}: {e.reason}. Check network access to "
                f"github.com (proxy? offline?) and retry."
            ) from e
        except (ConnectionError, TimeoutError) as e:
            # http.client.RemoteDisconnected escapes URLError - see do_open().
            raise EmbeddedLemonadeError(
                f"Download of {url} dropped mid-transfer: {e}. This is usually a "
                f"transient network failure - retry, or download the asset by hand "
                f"from {RELEASES_PAGE}/tag/v{self.version}."
            ) from e

    def _verify(self, archive: Path, expected_sha256: str) -> None:
        """Check *archive* against its pinned digest.

        Args:
            archive: Downloaded file.
            expected_sha256: Hex digest the release publishes.

        Raises:
            EmbeddedLemonadeError: The digest does not match.
        """
        digest = hashlib.sha256()
        with open(archive, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise EmbeddedLemonadeError(
                f"Checksum mismatch for {archive.name}: expected "
                f"{expected_sha256}, got {actual}. The download is corrupt or "
                f"the release asset was replaced. Retry; if it persists, "
                f"compare against {RELEASES_PAGE}/tag/v{self.version}."
            )
        log.debug("Verified %s (sha256 %s)", archive.name, actual)

    def _install_payload(self, payload: Path) -> None:
        """Move an unpacked payload into :attr:`dist_dir`.

        Replaces any previous unpack of the same version, so a half-written
        directory from an interrupted install cannot survive.

        Args:
            payload: Directory holding ``lemond``, ``resources/`` and friends.
        """
        self.dist_dir.parent.mkdir(parents=True, exist_ok=True)
        if self.dist_dir.exists():
            shutil.rmtree(self.dist_dir)
        shutil.move(str(payload), str(self.dist_dir))

        if platform.system() != "Windows":
            for name in ("lemond", "lemonade"):
                binary = self.dist_dir / name
                if binary.is_file():
                    binary.chmod(binary.stat().st_mode | 0o111)

    def write_config(self) -> Path:
        """Write ``config.json`` into the lemond config directory.

        Returns:
            Path to the written file.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.config_dir / "config.json"
        # Lemonade persists its own keys here (e.g. cloud_providers), so only
        # the keys GAIA owns are overwritten; the rest must survive a restart.
        existing: Dict[str, object] = {}
        if path.is_file():
            problem = None
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                problem = str(e)
            else:
                if isinstance(loaded, dict):
                    existing = loaded
                else:
                    problem = f"expected a JSON object, found {type(loaded).__name__}"
            if problem:
                log.warning(
                    "Replacing unusable %s (%s); reinstall any cloud provider "
                    "that was connected to embedded Lemonade",
                    path,
                    problem,
                )
        # Rewritten on every start(), so a device-profile or GAIA_CTX_SIZE change
        # moves the request budget with it rather than leaving a stale number.
        merged = {**existing, **_lemond_config()}
        for key in _LEMOND_STRIPPED_KEYS:
            if key in merged:
                log.warning(
                    "Removing %s=%r from %s: GAIA's embedded Lemonade needs it "
                    "unset to list its full model catalogue and install backends",
                    key,
                    merged.pop(key),
                    path,
                )
        # Atomic: a torn write would lose settings only Lemonade can recreate.
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        self._pin_llamacpp_backends()
        return path

    def _pin_llamacpp_backends(self) -> None:
        """Record per-model load options Lemonade applies even when it auto-loads.

        The embedder is loaded on demand by ``/embeddings`` as well as by GAIA's
        explicit loads, so its ubatch has to live in Lemonade's saved options.
        A saved CPU backend is dropped so each start tries the GPU again; the
        client saves the CPU back only if the GPU load fails.
        """
        from gaia.llm.lemonade_client import (
            DEFAULT_EMBEDDING_MODEL,
            EMBEDDER_LLAMACPP_ARGS,
            manages_embedder_backend,
        )

        path = self.config_dir / "recipe_options.json"
        options: Dict[str, object] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise EmbeddedLemonadeError(
                    f"{path} is not valid JSON ({e}); fix or delete it, then "
                    "start again."
                ) from e
            if not isinstance(loaded, dict):
                raise EmbeddedLemonadeError(
                    f"{path} holds {type(loaded).__name__}, not a JSON object; "
                    "fix or delete it, then start again."
                )
            options = loaded
        # Lemonade keys saved options by the registered (``user.``) name.
        for model in (DEFAULT_EMBEDDING_MODEL,):
            if not manages_embedder_backend(model):
                continue
            entry = options.get(model)
            entry = dict(entry) if isinstance(entry, dict) else {}
            if entry.get("llamacpp_backend") == "cpu":
                log.info(
                    "Clearing the saved CPU backend for %s in %s so it tries the "
                    "GPU again",
                    model,
                    path,
                )
                del entry["llamacpp_backend"]
            args = str(entry.get("llamacpp_args") or "")
            if "--ubatch-size" not in args:
                entry["llamacpp_args"] = f"{args} {EMBEDDER_LLAMACPP_ARGS}".strip()
            options[model] = entry
        path.write_text(json.dumps(options, indent=2), encoding="utf-8")

    # -- state ------------------------------------------------------------

    def _read_state(self) -> Optional[dict]:
        """Return the recorded instance, or None when there is none.

        Returns:
            The parsed state file, or None if absent or unreadable.
        """
        if not self.state_path.is_file():
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("Discarding unreadable %s: %s", self.state_path, e)
            return None

    def _write_state(self, state: dict) -> None:
        """Persist the running instance.

        The file holds the API key that is the server's only protection, so on
        POSIX it is created 0600 rather than chmod-ed afterwards -- a chmod
        leaves the key world-readable in between. The old file is removed first
        because the mode argument only applies when ``open`` creates the file;
        rewriting one left behind by an earlier build would keep its mode.

        Windows ignores the mode bits; there the file is protected by the ACL
        it inherits from the user's profile directory.

        Args:
            state: pid, port, api_key and version of the live daemon.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path.unlink(missing_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(self.state_path, flags, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)

    def _clear_state(self) -> None:
        """Remove the state and credentials files if present.

        Both go together: credentials naming a dead port are worse than none,
        because the next process to grab that port inherits the trust.
        """
        self.state_path.unlink(missing_ok=True)
        self.env_path.unlink(missing_ok=True)

    # -- lifecycle --------------------------------------------------------

    def _health(self, port: int, api_key: str, timeout: float = 3.0) -> Optional[dict]:
        """Probe the health endpoint of a candidate instance.

        The API key doubles as an identity check: only the daemon GAIA started
        accepts the key GAIA generated, so a successful probe proves the port
        is ours and not a recycled pid or an unrelated server.

        Args:
            port: Port to probe.
            api_key: Bearer token the instance was started with.
            timeout: Per-request timeout in seconds.

        Returns:
            The decoded health payload, or None if it did not answer.
        """
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{_HEALTH_PATH}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def _identify(self, pid: int, port: int, api_key: str) -> str:
        """Whether *pid* is the lemond GAIA started for this state record.

        A recycled pid must never be mistaken for GAIA's server, because the
        caller kills what this approves. :data:`OWN` needs all three:

        - the executable is a ``lemond`` under this install's ``dist/`` tree;
        - its command line carries ``--port <port>`` from the record;
        - its ``LEMONADE_API_KEY`` is the recorded key. When the OS will not
          show another process's environment, the first two decide: nothing
          else runs a binary out of GAIA's private directory.

        A user's own lemond, another app's server, or any process that took
        over the pid is :data:`OTHER` and is left alone. A ``lemond`` whose
        command line the OS will not show -- one started from an elevated
        terminal, say -- is :data:`UNKNOWN`: neither killed nor forgotten.

        Args:
            pid: Process id from the state file.
            port: Port from the state file.
            api_key: API key from the state file.

        Returns:
            :data:`OWN`, :data:`OTHER` or :data:`UNKNOWN`.
        """
        import psutil

        try:
            proc = psutil.Process(pid)
            cmdline = proc.cmdline()
        except psutil.NoSuchProcess:
            return OTHER
        except psutil.AccessDenied:
            try:
                name = proc.name()
            except psutil.NoSuchProcess:
                return OTHER
            except psutil.AccessDenied:
                return UNKNOWN
            return UNKNOWN if Path(name).stem.lower() == "lemond" else OTHER
        if not self._is_own_cmdline(cmdline, port):
            return OTHER
        try:
            env = proc.environ()
        except psutil.AccessDenied:
            return OWN
        except psutil.NoSuchProcess:
            return OTHER
        return OWN if env.get("LEMONADE_API_KEY") == api_key else OTHER

    def _is_own_cmdline(self, cmdline: List[str], port: int) -> bool:
        """Whether *cmdline* runs this install's lemond on *port*."""
        if not cmdline:
            return False
        exe = Path(cmdline[0])
        if exe.stem.lower() != "lemond":
            return False
        dist_root = os.path.normcase(str((self.root / "dist").resolve()))
        try:
            exe_dir = os.path.normcase(str(exe.resolve().parent))
            inside = os.path.commonpath([dist_root, exe_dir]) == dist_root
        except (OSError, ValueError):
            # ValueError: different drives on Windows -- not under dist/.
            return False
        if not inside:
            return False
        args = cmdline[1:]
        return any(
            arg == "--port" and i + 1 < len(args) and args[i + 1] == str(port)
            for i, arg in enumerate(args)
        )

    def status(self) -> EmbeddedStatus:
        """Report whether the embedded instance is installed and running.

        The state file is only discarded once the recorded process is gone.
        A daemon that is up but slow to answer -- loading a multi-gigabyte
        backend, say -- is reported through ``unresponsive_pid`` and keeps its
        state, so ``stop`` can still reach it. Finding it gone also stops the
        backends it left (:meth:`_reap_orphaned_backends`).

        Returns:
            The current :class:`EmbeddedStatus`.
        """
        return self._status(reap=True)

    def _status(self, reap: bool) -> EmbeddedStatus:
        installed = self.is_installed()
        stopped = EmbeddedStatus(
            installed=installed,
            running=False,
            version=self.version,
            dist_dir=self.dist_dir if installed else None,
        )

        state = self._read_state()
        if not state:
            return stopped

        port = state.get("port")
        api_key = state.get("api_key")
        pid = state.get("pid")
        if not (port and api_key and pid):
            log.warning("Discarding incomplete state in %s", self.state_path)
            self._clear_state()
            return stopped

        owner_pid = state.get("owner_pid")
        owner_pid = int(owner_pid) if owner_pid else None
        owner_started = state.get("owner_started")
        owner_started = float(owner_started) if owner_started else None
        if self._health(int(port), api_key) is not None:
            return EmbeddedStatus(
                installed=installed,
                running=True,
                version=state.get("version", self.version),
                port=int(port),
                pid=int(pid),
                base_url=self.base_url_for(int(port)),
                dist_dir=self.dist_dir if installed else None,
                owner_pid=owner_pid,
                owner_started=owner_started,
            )

        spawned = _SPAWNED.get(int(pid))
        if spawned is not None and spawned.poll() is not None:
            identity = OTHER
        else:
            identity = self._identify(int(pid), int(port), api_key)
        if identity != OTHER:
            log.warning(
                "Embedded Lemonade (pid %s) is not answering on port %s%s",
                pid,
                port,
                (
                    ""
                    if identity == OWN
                    else ", and GAIA may not inspect it to confirm it is its own"
                ),
            )
            return EmbeddedStatus(
                installed=installed,
                running=False,
                version=state.get("version", self.version),
                port=int(port),
                dist_dir=self.dist_dir if installed else None,
                unresponsive_pid=int(pid),
                owner_pid=owner_pid,
                owner_started=owner_started,
            )

        self._report_gone(state)
        recorded = self._read_state()
        if recorded is not None and recorded.get("pid") == pid:
            self._clear_state()  # unless a start recorded a new server since
        if reap:
            # Safe without the lock: a concurrent start's backends have a live anchor.
            self._reap_orphaned_backends()
        return stopped

    def _reap_orphaned_backends(self) -> List[int]:
        """Kill backends a dead lemond of this install left running.

        lemond's llama-server children each hold gigabytes of memory. Killed
        without its tree, or crashed, lemond leaves them running, and the next
        start loads the same models again beside them. A process is reaped only
        when its executable is under this install's ``cache/bin`` and the
        process that anchored it (:func:`_anchor_pid`) is gone or was replaced
        by a younger one on the same pid. A tool the user runs from that
        directory has a live anchor -- their shell -- and is left alone, as are
        a live lemond's backends and anything outside the install.

        Returns:
            The pids killed.
        """
        import psutil

        bin_dir = self.cache_dir / "bin"
        # Names first: reading every process's path or parent takes seconds.
        names = {name.lower() for _, _, files in os.walk(bin_dir) for name in files}
        if not names:
            return []
        bin_root = os.path.normcase(str(bin_dir.resolve()))
        killed = []
        for proc in psutil.process_iter(["pid", "name"]):
            if (proc.info.get("name") or "").lower() not in names:
                continue
            try:
                exe = proc.exe()
                if not _inside(exe, bin_root):
                    continue
                anchor, born = _anchor_pid(proc), proc.create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as e:
                log.debug("Not reaping pid %s: cannot inspect it (%s)", proc.pid, e)
                continue
            if not _outlived(anchor, born):
                continue
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as e:
                log.warning(
                    "Backend %s (pid %s) outlived the server that started it, "
                    "but GAIA may not stop it (%s). End it by hand: Task "
                    "Manager on Windows, `kill %s` elsewhere.",
                    exe,
                    proc.pid,
                    e,
                    proc.pid,
                )
                continue
            log.warning(
                "Stopped backend %s (pid %s): the server that started it is gone",
                exe,
                proc.pid,
            )
            killed.append(proc)
        # The next lemond loads models into the memory these release.
        _, alive = psutil.wait_procs(killed, timeout=5)
        for proc in alive:
            log.warning("Backend pid %s is still exiting after 5s", proc.pid)
        return [proc.pid for proc in killed]

    def _log_tail(self, lines: int = 3) -> str:
        """The last *lines* non-health lines of lemond's log, `` | ``-joined."""
        try:
            with open(self.log_path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - _LOG_TAIL_BYTES))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError as e:
            return f"<{self.log_path} unreadable: {e}>"
        kept = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and "health" not in line.lower()
        ]
        return " | ".join(kept[-lines:]) or f"<{self.log_path} is empty>"

    def _report_gone(self, state: dict) -> None:
        """Log how a recorded lemond ended, unless GAIA stopped it on purpose."""
        pid = int(state["pid"])
        spawned = _SPAWNED.get(pid)
        if state.get("stop_requested_by"):
            log.debug("Recorded instance (pid %s) is gone; GAIA stopped it", pid)
            _SPAWNED.pop(pid, None)
            return
        code, exited_at = _exit_details(pid)
        started_at = state.get("started_at")
        if started_at:
            end = exited_at if exited_at is not None else time.time()
            when = (
                f"started {time.strftime('%H:%M:%S', time.localtime(started_at))}, "
                f"{'exited' if exited_at is not None else 'found gone'} "
                f"{end - float(started_at):.0f}s later"
            )
        else:
            when = "start time not recorded"
        log.warning(
            "Embedded Lemonade exited without GAIA stopping it: pid %s, port %s, "
            "%s, %s%s. Last lines of %s: %s",
            pid,
            state.get("port"),
            when,
            _describe_exit_code(code),
            "" if spawned is not None else " (not started by this process)",
            self.log_path,
            self._log_tail(),
        )
        _SPAWNED.pop(pid, None)

    def recorded_health(self) -> Tuple[bool, Optional[dict]]:
        """Ask the recorded instance for its ``/health`` payload.

        Returns:
            ``(recorded, health)``: whether an instance is recorded at all, and
            its health payload, or None when it did not answer.
        """
        state = self._read_state()
        port = state.get("port") if state else None
        api_key = state.get("api_key") if state else None
        if not (port and api_key):
            return False, None
        return True, self._health(int(port), api_key)

    def current_api_key(self) -> Optional[str]:
        """Return the API key of the recorded instance, if there is one.

        Every request to the private server needs this as a bearer token.
        Callers that only need to hand it to a shell should point the user at
        :attr:`env_path` rather than printing it.

        Returns:
            The bearer token, or None when no instance is recorded.
        """
        state = self._read_state()
        return state.get("api_key") if state else None

    def claim(self, owner_pid: int, owner_started: Optional[float] = None) -> None:
        """Record the GAIA daemon that owns the running instance.

        Args:
            owner_pid: The daemon's pid.
            owner_started: Its process start time.

        Raises:
            EmbeddedLemonadeError: No instance is recorded to claim.
        """
        with self._instance_lock():
            state = self._read_state()
            if not state:
                raise EmbeddedLemonadeError(
                    f"No embedded Lemonade is recorded in {self.state_path}, so "
                    "there is nothing to take ownership of. Start it with "
                    "`gaia daemon start`."
                )
            owner = {"owner_pid": owner_pid, "owner_started": owner_started}
            if any(state.get(k) != v for k, v in owner.items()):
                self._write_state({**state, **owner})

    @contextmanager
    def _instance_lock(self, timeout: float = _START_TIMEOUT + 30.0):
        """Hold the cross-process lock over starting, stopping or claiming.

        ``gaia init``, ``gaia lemonade embedded start`` and the daemon can each
        start the server. Without this, two that start at once both spawn, and
        the one that loses the port clears the state file the winner wrote,
        leaving a server nothing can find to stop.

        Raises:
            EmbeddedLemonadeError: Another GAIA process held it past *timeout*.
        """
        from gaia.daemon.lock import try_lock, unlock

        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "instance.lock"
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = time.monotonic() + timeout
            while not try_lock(fd):
                if time.monotonic() >= deadline:
                    raise EmbeddedLemonadeError(
                        f"Another GAIA process has been starting or stopping "
                        f"Lemonade Server for {timeout:.0f}s and holds {path}. "
                        f"Wait for it, or check {self.log_path}, then retry."
                    )
                time.sleep(0.1)
            try:
                yield
            finally:
                unlock(fd)
        finally:
            os.close(fd)

    def _read_last_port(self) -> Optional[int]:
        """The port the last start used, or None when there is no usable record."""
        try:
            raw = json.loads(self.last_port_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            log.warning(
                "Ignoring unreadable %s (%s); choosing a new port",
                self.last_port_path,
                e,
            )
            return None
        port = raw.get("port") if isinstance(raw, dict) else None
        if isinstance(port, bool) or not isinstance(port, int):
            log.warning(
                "Ignoring %s: it holds no port number; choosing a new port",
                self.last_port_path,
            )
            return None
        return port if 1024 <= port <= 65535 else None

    def _choose_port(self) -> int:
        """Reuse the last start's port while it is free, else take a new one."""
        last = self._read_last_port()
        if last is not None:
            if _port_free(last):
                return last
            log.info(
                "Port %s from the last start is in use by another program; "
                "starting on a new port",
                last,
            )
        return _free_port()

    def _record_port(self, port: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.last_port_path.write_text(json.dumps({"port": port}), encoding="utf-8")

    def _write_env_file(self, port: int, api_key: str) -> Path:
        """Write the sourceable credentials file for the running instance.

        Created owner-only, and removed first so the mode applies: ``open``
        honours its mode argument only when it creates the file, so rewriting
        one left behind by an earlier build would keep that file's permissions.

        Windows ignores the mode bits; there the file is protected by the ACL
        it inherits from the user's profile directory.

        Args:
            port: Port the instance listens on.
            api_key: Bearer token the instance requires.

        Returns:
            Path to the written file.
        """
        base_url = self.base_url_for(port)
        # The marker lets GAIA recognise these values as its own server's and
        # follow the live state file once this port and key go stale.
        if platform.system() == "Windows":
            body = (
                f'$env:LEMONADE_BASE_URL = "{base_url}"\n'
                f'$env:LEMONADE_API_KEY = "{api_key}"\n'
                f'$env:{EMBEDDED_ENV_MARKER} = "1"\n'
            )
        else:
            body = (
                f'export LEMONADE_BASE_URL="{base_url}"\n'
                f'export LEMONADE_API_KEY="{api_key}"\n'
                f'export {EMBEDDED_ENV_MARKER}="1"\n'
            )

        self.root.mkdir(parents=True, exist_ok=True)
        self.env_path.unlink(missing_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(self.env_path, flags, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
        return self.env_path

    def env_load_command(self) -> str:
        """Return the shell command that loads the credentials file.

        Returns:
            A command the user can paste to set both environment variables.
        """
        if platform.system() == "Windows":
            return f". {self.env_path}"
        return f"source {self.env_path}"

    @staticmethod
    def base_url_for(port: int) -> str:
        """Return the OpenAI-compatible base URL for *port*.

        Args:
            port: Port the instance listens on.

        Returns:
            A URL suitable for ``LEMONADE_BASE_URL``.
        """
        return f"http://localhost:{port}/api/v1"

    def start(
        self,
        port: Optional[int] = None,
        timeout: float = _START_TIMEOUT,
        install_if_missing: bool = True,
        owner_pid: Optional[int] = None,
        owner_started: Optional[float] = None,
    ) -> EmbeddedStatus:
        """Start the private daemon and wait for it to answer.

        Args:
            port: Port to bind. When omitted, the last start's port is reused
                if it is free, else a free one is chosen; either way it is
                recorded for the next start.
            timeout: Seconds to wait for the health endpoint.
            install_if_missing: Download the artifact when it is not unpacked.
            owner_pid: The GAIA daemon starting it, recorded as its owner.
            owner_started: That daemon's process start time.

        Returns:
            Status of the running instance.

        Raises:
            EmbeddedLemonadeError: Not installed, the daemon exited, or it
                never became healthy.
        """
        with self._instance_lock():
            return self._start_locked(
                port, timeout, install_if_missing, owner_pid, owner_started
            )

    def _start_locked(
        self,
        port: Optional[int],
        timeout: float,
        install_if_missing: bool,
        owner_pid: Optional[int],
        owner_started: Optional[float],
    ) -> EmbeddedStatus:
        existing = self._status(reap=False)
        if existing.running:
            if existing.version != self.version:
                raise EmbeddedLemonadeError(
                    f"Embedded Lemonade {existing.version} is already running on "
                    f"port {existing.port}, but GAIA now targets "
                    f"{self.version}. Run `gaia lemonade embedded stop` first, "
                    f"then start again to pick up the new version."
                )
            log.info("Embedded Lemonade already running on port %s", existing.port)
            return existing

        if existing.unresponsive_pid:
            raise EmbeddedLemonadeError(
                f"Embedded Lemonade (pid {existing.unresponsive_pid}) is running "
                f"but not answering on port {existing.port}. Starting another "
                f"would leave two servers behind. Run "
                f"`gaia lemonade embedded stop`, check {self.log_path} for why "
                f"it stalled, then start again."
            )

        if not self.is_installed():
            if not install_if_missing:
                raise EmbeddedLemonadeError(
                    f"Embedded Lemonade {self.version} is not installed at "
                    f"{self.dist_dir}. Run `gaia lemonade embedded start` "
                    f"without --no-install to download it."
                )
            self.install()

        self._reap_orphaned_backends()
        self.write_config()
        port = port or self._choose_port()
        api_key = secrets.token_urlsafe(32)

        env = child_env({"LEMONADE_API_KEY": api_key, **vulkan_workarounds()})

        argv = [
            str(self.daemon_path),
            str(self.cache_dir),
            str(self.config_dir),
            "--port",
            str(port),
        ]
        log.info("Starting embedded Lemonade: %s", " ".join(argv))

        # Own group/session so the server outlives the CLI process that spawned
        # it. A hidden console, not none: lemond's llama/whisper/kokoro children
        # inherit it instead of each opening a blank window.
        kwargs = background_popen_kwargs()

        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "ab") as logfile:
            process = subprocess.Popen(
                argv,
                env=env,
                stdout=logfile,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(self.dist_dir),
                **kwargs,
            )
        _SPAWNED[process.pid] = process
        started_at = time.time()

        # Record the instance before waiting: an interrupt during the wait must
        # still leave `stop` able to find and kill what we just spawned.
        self._write_state(
            {
                "pid": process.pid,
                "port": port,
                "api_key": api_key,
                "version": self.version,
                "owner_pid": owner_pid,
                "owner_started": owner_started,
                "started_at": started_at,
            }
        )

        healthy = False
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise EmbeddedLemonadeError(
                        f"Embedded Lemonade exited immediately (code "
                        f"{process.returncode}) on port {port}. Read "
                        f"{self.log_path} for the daemon's own error, then retry "
                        f"with `gaia lemonade embedded start --port <other>` if "
                        f"the port was taken."
                    )
                if self._health(port, api_key) is not None:
                    healthy = True
                    break
                time.sleep(0.25)
            if not healthy:
                raise EmbeddedLemonadeError(
                    f"Embedded Lemonade did not answer {_HEALTH_PATH} on port "
                    f"{port} within {timeout:.0f}s. Read {self.log_path} for what "
                    f"it was doing, then retry."
                )
        except BaseException:
            _SPAWNED.pop(process.pid, None)
            self._terminate(process)
            recorded = self._read_state()
            if recorded is None or recorded.get("pid") == process.pid:
                self._clear_state()
            raise

        self._write_state(
            {
                "pid": process.pid,
                "port": port,
                "api_key": api_key,
                "version": self.version,
                "owner_pid": owner_pid,
                "owner_started": owner_started,
                "started_at": started_at,
            }
        )
        self._write_env_file(port, api_key)
        self._record_port(port)
        log.info("Embedded Lemonade %s ready on port %s", self.version, port)
        return EmbeddedStatus(
            installed=True,
            running=True,
            version=self.version,
            port=port,
            pid=process.pid,
            base_url=self.base_url_for(port),
            dist_dir=self.dist_dir,
            owner_pid=owner_pid,
            owner_started=owner_started,
        )

    def stop(self, timeout: float = _STOP_TIMEOUT) -> bool:
        """Stop the recorded instance and its inference backends.

        Reaches a daemon that has stopped answering as well as a healthy one,
        so a stalled server can still be cleaned up. The pid is only signalled
        after :meth:`_identify` confirms it is GAIA's lemond, so a recycled
        pid -- or a user's own Lemonade -- is never killed by mistake.

        Args:
            timeout: Seconds to wait for the daemon to exit.

        Returns:
            True if an instance was stopped, False if none was running.

        Raises:
            EmbeddedLemonadeError: The process would not terminate, or the
                recorded pid could not be confirmed as GAIA's server.
        """
        with self._instance_lock():
            return self._stop_locked(timeout)

    def _stop_locked(self, timeout: float) -> bool:
        current = self.status()
        state = self._read_state()
        if state is None or not (current.running or current.unresponsive_pid):
            self._clear_state()
            return False

        pid = int(state["pid"])
        port = int(state["port"])
        api_key = state["api_key"]
        identity = self._identify(pid, port, api_key)
        if identity == UNKNOWN:
            raise EmbeddedLemonadeError(
                f"GAIA may not inspect pid {pid}, the Lemonade Server recorded in "
                f"{self.state_path}, so it did not stop it. It was probably "
                "started from an administrator terminal: run "
                "`gaia lemonade embedded stop` from one."
            )
        if identity == OTHER:
            # Something answers with GAIA's key, but the recorded pid now runs
            # another program: never signal it.
            raise EmbeddedLemonadeError(
                f"GAIA's Lemonade Server answers on port {port}, but pid {pid} "
                f"recorded in {self.state_path} is not it, so GAIA killed "
                f"nothing. Stop the server by hand (`gaia kill --port {port}`), "
                f"then delete {self.state_path}."
            )
        log.info("Stopping embedded Lemonade (pid %s, port %s)", pid, port)

        # Any process that finds it gone now knows the exit was deliberate.
        self._write_state({**state, "stop_requested_by": os.getpid()})
        try:
            self._kill_tree(pid)
        except EmbeddedLemonadeError:
            self._write_state(state)
            raise
        _SPAWNED.pop(pid, None)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            gone = self._health(port, api_key, timeout=1.0) is None
            if gone and self._identify(pid, port, api_key) != OWN:
                self._clear_state()
                log.info("Embedded Lemonade stopped")
                return True
            time.sleep(0.25)

        raise EmbeddedLemonadeError(
            f"Embedded Lemonade (pid {pid}) is still alive {timeout:.0f}s after "
            f"being asked to stop. Kill it with `gaia kill --port {port}`, then "
            f"delete {self.state_path}."
        )

    def uninstall(self) -> bool:
        """Remove the embedded Lemonade runtime and its private state.

        The runtime owns the whole ``<GAIA_HOME>/lemonade`` tree, including
        downloaded backends and credentials. Refuse to remove it while the
        daemon is reachable (or alive but unresponsive), so callers cannot
        leave a running process with deleted files beneath it.

        Returns:
            True when a runtime was removed, or False when none existed.

        Raises:
            EmbeddedLemonadeError: The daemon is still running or the runtime
                could not be removed.
        """
        status = self.status()
        if status.running or status.unresponsive_pid:
            raise EmbeddedLemonadeError(
                "Cannot uninstall embedded Lemonade while it is running. "
                "Run `gaia lemonade embedded stop` first."
            )
        if not self.root.exists():
            return False
        try:
            shutil.rmtree(self.root)
        except OSError as e:
            raise EmbeddedLemonadeError(
                f"Could not remove embedded Lemonade from {self.root}: {e}. "
                "Check that its files are writable, then retry."
            ) from e
        return True

    @staticmethod
    def _kill_tree(pid: int) -> None:
        """Terminate a daemon and the backend processes it spawned.

        llama-server children hold gigabytes of GPU memory, so killing only the
        daemon would leak them.

        Args:
            pid: Process id of the daemon, confirmed to be lemond.

        Raises:
            EmbeddedLemonadeError: The signal could not be delivered.
        """
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=True,
                    capture_output=True,
                    timeout=30,
                    **windows_hidden_popen_kwargs(),
                )
            else:
                # start() makes the daemon a session leader, so its backends
                # share its process group. killpg/getpgid are POSIX-only, so
                # the no-member warning is a false positive on Windows.
                pgid = os.getpgid(pid)  # pylint: disable=no-member
                if pgid == pid:
                    os.killpg(pgid, signal.SIGTERM)  # pylint: disable=no-member
                else:
                    # Not a group leader, so the group is someone else's:
                    # signal lemond and its own descendants only.
                    EmbeddedLemonade._kill_descendants_then(pid)
        except ProcessLookupError:
            log.debug("Process %s already gone", pid)
        except (OSError, subprocess.SubprocessError) as e:
            raise EmbeddedLemonadeError(
                f"Could not terminate embedded Lemonade (pid {pid}): {e}. Kill "
                f"it by hand and delete the state file, then retry."
            ) from e

    @staticmethod
    def _kill_descendants_then(pid: int) -> None:
        """SIGTERM *pid*'s descendants, then *pid* itself."""
        import psutil

        try:
            children = psutil.Process(pid).children(recursive=True)
        except psutil.NoSuchProcess as e:
            raise ProcessLookupError(pid) from e
        for child in children:
            try:
                os.kill(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                log.debug("Backend pid %s already gone", child.pid)
        os.kill(pid, signal.SIGTERM)

    def unload_models(self, timeout: float = 30.0) -> bool:
        """Unload every model from the running instance; the server stays up.

        Returns:
            True if the instance unloaded, False if none is running.

        Raises:
            EmbeddedLemonadeError: The instance refused or did not answer.
        """
        current = self.status()
        api_key = self.current_api_key()
        if not current.running or not api_key:
            return False
        request = urllib.request.Request(
            f"http://127.0.0.1:{current.port}/api/v1/unload",
            method="POST",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
        except (urllib.error.URLError, OSError) as e:
            raise EmbeddedLemonadeError(
                f"GAIA's Lemonade Server on port {current.port} did not unload its "
                f"models: {e}. Read {self.log_path}; `gaia lemonade embedded stop` "
                "frees the memory by stopping it."
            ) from e
        log.info("Unloaded every model from embedded Lemonade (port %s)", current.port)
        return True

    def _terminate(self, process: subprocess.Popen) -> None:
        """Kill a daemon that never became healthy, and anything it spawned.

        Args:
            process: The spawned daemon.
        """
        try:
            self._kill_tree(process.pid)
        except EmbeddedLemonadeError as e:
            log.warning("Could not clean up failed start: %s", e)
            process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            log.warning("Embedded Lemonade pid %s ignored kill", process.pid)

    # -- backends ---------------------------------------------------------

    def install_backend(self, spec: str, timeout: float = 1800.0) -> None:
        """Download an inference backend into the private cache.

        Backends are not part of the embeddable artifact. ``llamacpp:cpu`` and
        ``llamacpp:vulkan`` together are ~141 MB and cover GAIA's default
        models; ``llamacpp:rocm`` pulls a ~4.3 GB per-GPU ROCm runtime.

        Args:
            spec: ``recipe:backend``, e.g. ``llamacpp:vulkan``.
            timeout: Seconds to allow the download.

        Raises:
            EmbeddedLemonadeError: No instance is running, or the install
                failed.
        """
        status = self.status()
        if not status.running:
            raise EmbeddedLemonadeError(
                "Cannot install a backend: no embedded Lemonade is running. "
                "Start it first with `gaia lemonade embedded start`."
            )
        api_key = self.current_api_key()
        if not api_key:
            raise EmbeddedLemonadeError(
                f"The embedded instance answered but {self.state_path} has no "
                f"API key. Run `gaia lemonade embedded stop` then `start` to "
                f"re-record it."
            )

        env = child_env({"LEMONADE_API_KEY": api_key})
        try:
            result = subprocess.run(
                [
                    str(self.client_path),
                    "--port",
                    str(status.port),
                    "backends",
                    "install",
                    spec,
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                **windows_hidden_popen_kwargs(),
            )
        except subprocess.TimeoutExpired as e:
            raise EmbeddedLemonadeError(
                f"Installing backend '{spec}' timed out after {timeout:.0f}s. "
                f"Large backends (ROCm is ~4.3 GB) need a longer timeout or a "
                f"faster link; retry, or check {self.log_path}."
            ) from e

        if result.returncode != 0:
            raise EmbeddedLemonadeError(
                f"Installing backend '{spec}' failed (exit "
                f"{result.returncode}): {(result.stderr or result.stdout).strip()}. "
                f"Run `{self.client_path} --port {status.port} backends` to see "
                f"which specs this machine supports."
            )
        log.info("Installed backend %s", spec)
