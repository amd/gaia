# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Private, per-install Lemonade Server for GAIA.

Upstream ships an "embeddable" Lemonade build: a ``lemond`` daemon plus a
``lemonade`` HTTP client, with no installer, no tray app and no machine-wide
state. This module downloads that artifact, unpacks it under ``~/.gaia``, and
runs it on a private port behind a generated API key.

The point is a GAIA that carries its own inference server. The system-wide
Lemonade install keeps working and stays the fallback -- see
``gaia.llm.lemonade_launcher`` for that path.

Layout under ``$GAIA_HOME/lemonade`` (``~/.gaia/lemonade`` by default)::

    dist/<version>/     unpacked artifact (lemond, lemonade, resources/)
    cache/              lemond cache_dir -- downloaded backends live in bin/
    config/config.json  lemond config_dir
    state.json          the running instance: pid, port, API key, version
    lemond.log          daemon stdout/stderr

``cache/`` and ``config/`` are deliberately *not* versioned: backends run from
141 MB (llama.cpp CPU + Vulkan) to 4.3 GB (ROCm, which also pulls a per-GPU
TheRock runtime), and re-downloading them on every Lemonade bump is not
acceptable. lemond invalidates stale binaries itself via the
``clear_bin_if_lemonade_below`` pin in its ``backend_versions.json``.
"""

import hashlib
import json
import os
import platform
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from gaia.logger import get_logger
from gaia.version import LEMONADE_VERSION

log = get_logger(__name__)

GITHUB_RELEASE_BASE = "https://github.com/lemonade-sdk/lemonade/releases/download"
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
    "lemonade-embeddable-11.8.1-windows-x64.zip": "9f76aeacec1ec9e3fce2460929229c02ae637bcdf73e70467b6a1aedc8739921",
    "lemonade-embeddable-11.8.1-ubuntu-x64.tar.gz": "ed7809b66e325ee99c9fe3e241cfec7c6fc4b43ae794dcb70fc3627bfa3e4865",
    "lemonade-embeddable-11.8.1-ubuntu-arm64.tar.gz": "0bbd7435a0a6a7d876de4a92f00118802775d2b5c268570acd55651c836a07f5",
    "lemonade-embeddable-11.8.1-macos-arm64.tar.gz": "472aa96b290ddb3b4950b028151020aac1f838e59f32b347cfa0a15e2b573cb5",
}

# lemond reads these from <config_dir>/config.json.
#
# broadcast=false keeps the private instance off the UDP discovery beacon, so a
# stray `lemonade` CLI on the machine does not attach to GAIA's server.
#
# Deliberately absent: no_fetch_executables. Setting it true collapses the
# advertised catalogue from 204 models to the 4 runnable by built-in backends,
# which would break `gaia download` for everything else.
_LEMOND_CONFIG = {
    "config_version": 2,
    "host": "localhost",
    "broadcast": False,
}

_HEALTH_PATH = "/api/v1/health"
_START_TIMEOUT = 60.0
_STOP_TIMEOUT = 20.0
_DOWNLOAD_TIMEOUT = 300


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
    """

    installed: bool
    running: bool
    version: str
    port: Optional[int] = None
    pid: Optional[int] = None
    base_url: Optional[str] = None
    dist_dir: Optional[Path] = None
    unresponsive_pid: Optional[int] = None


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
            f"Lemonade. Install Lemonade Server system-wide instead ("
            f"`gaia init`), or see {RELEASES_PAGE} for the published assets."
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
            f"Supported: {supported}. Install Lemonade Server system-wide "
            f"instead (`gaia init`), or check {RELEASES_PAGE}."
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


def _free_port() -> int:
    """Return a TCP port the OS reports as free on loopback.

    Returns:
        A port number nothing is currently bound to.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _reject(entry: str, detail: str) -> "EmbeddedLemonadeError":
    """Build the refusal raised for an archive member GAIA will not unpack.

    Args:
        entry: Name of the offending member.
        detail: What is wrong with it.

    Returns:
        The error to raise.
    """
    return EmbeddedLemonadeError(
        f"Refusing to unpack embedded Lemonade: archive entry '{entry}' "
        f"{detail}. The download is corrupt or tampered with -- delete it and "
        f"retry, and report it at {RELEASES_PAGE}."
    )


def _destination_for(name: str, dest: Path) -> Path:
    """Resolve where *name* may be written under *dest*.

    Absolute names, drive letters and ``..`` segments all resolve to somewhere
    outside *dest* and are refused by the containment check.

    Args:
        name: Archive member name.
        dest: Already-resolved directory the archive unpacks into.

    Returns:
        The absolute path the member is allowed to occupy.

    Raises:
        EmbeddedLemonadeError: The member escapes *dest*.
    """
    target = (dest / name).resolve()
    if target != dest and dest not in target.parents:
        raise _reject(name, f"escapes {dest}")
    return target


def _write_link(member: tarfile.TarInfo, target: Path, dest: Path) -> None:
    """Recreate a symlink or hard link, refusing one that points out of *dest*.

    Args:
        member: The link member.
        target: Validated path the link itself occupies.
        dest: Already-resolved directory the archive unpacks into.

    Raises:
        EmbeddedLemonadeError: The link points outside *dest*.
    """
    # A symlink's target is read relative to the directory holding it; a hard
    # link names another member, relative to the archive root.
    anchor = target.parent if member.issym() else dest
    pointee = (anchor / member.linkname).resolve()
    if pointee != dest and dest not in pointee.parents:
        raise _reject(member.name, f"links to '{member.linkname}', outside {dest}")

    target.parent.mkdir(parents=True, exist_ok=True)
    if member.issym():
        os.symlink(member.linkname, target)
    else:
        os.link(pointee, target)


def _extract_tar(archive: Path, dest: Path) -> None:
    """Unpack a ``.tar.gz`` into *dest*, one validated member at a time.

    Every member is materialised explicitly rather than through
    ``extractall``: tar carries symlinks, hard links and device nodes, and the
    bulk API's safety depends on a ``filter`` argument that only exists from
    Python 3.10.12 on. Writing each member ourselves is the same guarantee on
    every interpreter GAIA supports.

    Args:
        archive: The ``.tar.gz`` file.
        dest: Already-resolved directory to unpack into.

    Raises:
        EmbeddedLemonadeError: A member escapes *dest* or is a special file.
    """
    with tarfile.open(archive, "r:gz") as handle:
        links = []
        for member in handle.getmembers():
            target = _destination_for(member.name, dest)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                source = handle.extractfile(member)
                if source is None:
                    raise _reject(member.name, "is an unreadable file entry")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, open(target, "wb") as sink:
                    shutil.copyfileobj(source, sink)
                # & 0o777 drops setuid, setgid and sticky; nothing in the
                # published artifact needs them.
                target.chmod(member.mode & 0o777)
            elif member.issym() or member.islnk():
                links.append((member, target))
            else:
                raise _reject(member.name, "is a device or special file")

        # Links go last. Created inline, one could become a path component a
        # later member is written through -- the write would follow it out of
        # dest even though the member's own name resolved inside.
        for member, target in links:
            _write_link(member, target, dest)


def _extract_zip(archive: Path, dest: Path) -> None:
    """Unpack a ``.zip`` into *dest*, one validated member at a time.

    ``zipfile`` silently rewrites a traversing member name to a harmless one.
    Checking first turns that into the loud failure a tampered artifact
    deserves.

    Args:
        archive: The ``.zip`` file.
        dest: Already-resolved directory to unpack into.

    Raises:
        EmbeddedLemonadeError: A member escapes *dest*.
    """
    with zipfile.ZipFile(archive) as handle:
        for name in handle.namelist():
            _destination_for(name, dest)
            handle.extract(name, dest)


def _extract(archive: Path, dest: Path) -> None:
    """Unpack *archive* into *dest*, validating every member path.

    Args:
        archive: ``.zip`` or ``.tar.gz`` file.
        dest: Empty directory to unpack into.

    Raises:
        EmbeddedLemonadeError: Unknown suffix or an unsafe member path.
    """
    dest.mkdir(parents=True, exist_ok=True)
    resolved = dest.resolve()
    if archive.name.endswith(".zip"):
        _extract_zip(archive, resolved)
    elif archive.name.endswith((".tar.gz", ".tgz")):
        _extract_tar(archive, resolved)
    else:
        raise EmbeddedLemonadeError(
            f"Cannot unpack '{archive.name}': expected a .zip or .tar.gz "
            f"embedded Lemonade asset. Delete {archive} and retry."
        )


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
        path.write_text(json.dumps(_LEMOND_CONFIG, indent=2), encoding="utf-8")
        return path

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

    @staticmethod
    def _daemon_alive(pid: int) -> bool:
        """Whether *pid* is a live ``lemond`` process.

        Checked by image name, not just existence, so a recycled pid belonging
        to some unrelated program is never mistaken for GAIA's daemon.

        Args:
            pid: Process id from the state file.

        Returns:
            True if a running process with that id is a lemond binary.
        """
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                return "lemond" in result.stdout.lower()
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            return "lemond" in result.stdout.lower()
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Could not check whether pid %s is alive: %s", pid, e)
            return False

    def status(self) -> EmbeddedStatus:
        """Report whether the embedded instance is installed and running.

        The state file is only discarded once the recorded process is gone.
        A daemon that is up but slow to answer -- loading a multi-gigabyte
        backend, say -- is reported through ``unresponsive_pid`` and keeps its
        state, so ``stop`` can still reach it.

        Returns:
            The current :class:`EmbeddedStatus`.
        """
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

        if self._health(int(port), api_key) is not None:
            return EmbeddedStatus(
                installed=installed,
                running=True,
                version=state.get("version", self.version),
                port=int(port),
                pid=int(pid),
                base_url=self.base_url_for(int(port)),
                dist_dir=self.dist_dir if installed else None,
            )

        if self._daemon_alive(int(pid)):
            log.warning(
                "Embedded Lemonade (pid %s) is running but not answering on port %s",
                pid,
                port,
            )
            return EmbeddedStatus(
                installed=installed,
                running=False,
                version=state.get("version", self.version),
                port=int(port),
                dist_dir=self.dist_dir if installed else None,
                unresponsive_pid=int(pid),
            )

        log.debug("Recorded instance (pid %s) is gone; clearing state", pid)
        self._clear_state()
        return stopped

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
        if platform.system() == "Windows":
            body = (
                f'$env:LEMONADE_BASE_URL = "{base_url}"\n'
                f'$env:LEMONADE_API_KEY = "{api_key}"\n'
            )
        else:
            body = (
                f'export LEMONADE_BASE_URL="{base_url}"\n'
                f'export LEMONADE_API_KEY="{api_key}"\n'
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
    ) -> EmbeddedStatus:
        """Start the private daemon and wait for it to answer.

        Args:
            port: Port to bind. A free one is chosen when omitted.
            timeout: Seconds to wait for the health endpoint.
            install_if_missing: Download the artifact when it is not unpacked.

        Returns:
            Status of the running instance.

        Raises:
            EmbeddedLemonadeError: Not installed, the daemon exited, or it
                never became healthy.
        """
        existing = self.status()
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

        self.write_config()
        port = port or _free_port()
        api_key = secrets.token_urlsafe(32)

        env = dict(os.environ)
        env["LEMONADE_API_KEY"] = api_key

        argv = [
            str(self.daemon_path),
            str(self.cache_dir),
            str(self.config_dir),
            "--port",
            str(port),
        ]
        log.info("Starting embedded Lemonade: %s", " ".join(argv))

        # Detach so the daemon outlives the CLI process that spawned it.
        kwargs = {}
        if platform.system() == "Windows":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            kwargs["start_new_session"] = True

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

        # Record the instance before waiting: an interrupt during the wait must
        # still leave `stop` able to find and kill what we just spawned.
        self._write_state(
            {
                "pid": process.pid,
                "port": port,
                "api_key": api_key,
                "version": self.version,
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
            self._terminate(process)
            self._clear_state()
            raise

        self._write_state(
            {
                "pid": process.pid,
                "port": port,
                "api_key": api_key,
                "version": self.version,
            }
        )
        self._write_env_file(port, api_key)
        log.info("Embedded Lemonade %s ready on port %s", self.version, port)
        return EmbeddedStatus(
            installed=True,
            running=True,
            version=self.version,
            port=port,
            pid=process.pid,
            base_url=self.base_url_for(port),
            dist_dir=self.dist_dir,
        )

    def stop(self, timeout: float = _STOP_TIMEOUT) -> bool:
        """Stop the recorded instance and its inference backends.

        Reaches a daemon that has stopped answering as well as a healthy one,
        so a stalled server can still be cleaned up. The pid is only signalled
        after it is confirmed to be a lemond process, so a recycled pid is
        never killed by mistake.

        Args:
            timeout: Seconds to wait for the daemon to exit.

        Returns:
            True if an instance was stopped, False if none was running.

        Raises:
            EmbeddedLemonadeError: The process would not terminate.
        """
        current = self.status()
        state = self._read_state()
        if state is None or not (current.running or current.unresponsive_pid):
            self._clear_state()
            return False

        pid = int(state["pid"])
        port = int(state["port"])
        api_key = state["api_key"]
        log.info("Stopping embedded Lemonade (pid %s, port %s)", pid, port)

        self._kill_tree(pid)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            gone = self._health(port, api_key, timeout=1.0) is None
            if gone and not self._daemon_alive(pid):
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
                )
            else:
                # start() makes the daemon a session leader, so its backends
                # share its process group. killpg/getpgid are POSIX-only, so
                # the no-member warning is a false positive on Windows.
                os.killpg(  # pylint: disable=no-member
                    os.getpgid(pid), signal.SIGTERM  # pylint: disable=no-member
                )
        except ProcessLookupError:
            log.debug("Process %s already gone", pid)
        except (OSError, subprocess.SubprocessError) as e:
            raise EmbeddedLemonadeError(
                f"Could not terminate embedded Lemonade (pid {pid}): {e}. Kill "
                f"it by hand and delete the state file, then retry."
            ) from e

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

        env = dict(os.environ)
        env["LEMONADE_API_KEY"] = api_key
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
