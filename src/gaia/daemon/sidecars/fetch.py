# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Verified binary fetch (port of fetch.ts) — the security boundary.

Three verified sources, checked in order; there is NO 'use it anyway' path:

1. **Agent Hub install** — a binary the Hub installer already verified against
   the hub manifest's server-computed SHA-256 and recorded in its ``.installed``
   sentinel (#2086). Re-checked against that sentinel here before every spawn.
2. **Installer-placed flagship** — the ``gaia-agent`` install.sh/install.ps1
   verified and recorded beside itself in ``gaia-agent.install.json``.
   Re-checked against that record before every spawn, like source 1.
3. **Lock fetch** — resolve the host platform -> look up the artifact in
   binaries.lock.json -> download -> **verify its SHA-256 against the lock and
   raise loudly on any mismatch** -> write atomically into the cache -> chmod +x
   on POSIX. Source 3 is a repo-checkout/npm-package convenience: the lock is
   NOT wheel package data, so on a pip install the Hub install (source 1, which
   verifies against the hub manifest) is the only path — an absent lock reports
   "not installed" with the `gaia hub install <id>` remedy, never a
   broken-install error.

A tampered/truncated binary is rejected before it can ever be spawned.

Relocated from ``gaia.ui.email_sidecar.fetch`` (issue #2142, T1). Generalization
capped to relocation: ``default_cache_dir`` now takes the agent's cache dir
name (from its spec) instead of hardcoding "email" — everything else is
unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gaia.config import gaia_agents_dir
from gaia.daemon.sidecars import platform as plat
from gaia.daemon.sidecars.errors import (
    BinaryNotFoundError,
    IntegrityError,
    PlatformError,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> Optional[str]:
    # Streamed: `gaia init --check` hashes a ~160 MB agent on every call.
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def verify_sha256(data: bytes, expected: str, source_label: str) -> str:
    """Raise ``IntegrityError`` loudly on mismatch — the no-silent-fallback gate."""
    actual = sha256_hex(data)
    if actual.lower() != expected.lower():
        raise IntegrityError(
            f"SHA-256 mismatch for {source_label}:\n"
            f"  expected {expected}\n  actual   {actual}\n"
            "Refusing to use a binary that does not match binaries.lock.json. The "
            "download may be corrupt, truncated, or tampered with. Re-run the fetch; "
            "if it persists, the lock may be stale relative to the published artifact."
        )
    return actual


def default_cache_dir(agent_dir_name: str = "email") -> Path:
    return gaia_agents_dir() / agent_dir_name


@dataclass(frozen=True)
class FetchResult:
    binary_path: Path
    platform_key: str
    sha256: str
    url: str
    cached: bool
    # Installed binary version (hub sentinel / lock agentVersion). The manager
    # negotiates secret delivery off this BEFORE spawn (#2149).
    version: Optional[str] = None


def _join_url(base: str, name: str) -> str:
    return f"{base.rstrip('/')}/{name.lstrip('/')}"


FLAGSHIP_AGENT_ID = "gaia"


def install_command(agent_id: str) -> str:
    """The command that installs *agent_id* when it is missing.

    `gaia init` installs the flagship whenever no install sentinel says it is
    there; every other agent installs by id.
    """
    if agent_id == FLAGSHIP_AGENT_ID:
        return "gaia init"
    return f"gaia hub install {agent_id}"


def _install_remedy(agent_dir_name: str) -> str:
    return (
        f"Install it with `{install_command(agent_dir_name)}` (or Install in the "
        "Agent UI), which downloads the published binary and verifies its SHA-256 "
        "against the published manifest — independent of this lock. The background "
        "service reads the install from disk on the agent's next start. To run "
        "from source instead, use dev mode."
    )


def _load_lock_or_install_hint(
    lock_path: Optional[Path], agent_dir_name: str
) -> "plat.BinaryLock":
    """Load the binary lock, mapping "no lock at all" to an install remedy.

    binaries.lock.json is a repo-checkout/npm-package asset, not wheel package
    data — a pip-installed ``amd-gaia`` has no such file, and the hub manifest
    (which the Agent Hub installer verifies against) is the real source of
    truth for published artifacts. So an absent default lock means "this agent
    is not installed yet", not "your install is broken": say so.
    """
    try:
        return plat.load_lock(lock_path, agent_dir_name)
    except PlatformError as e:
        if lock_path is not None:
            # An explicitly-supplied lock that cannot be read is a caller
            # contract violation, not a missing install — surface it as-is.
            raise
        # The lock path means nothing to a user of an installed GAIA.
        logger.debug("sidecar fetch: no binary lock: %s", e)
        raise BinaryNotFoundError(
            f"no verified '{agent_dir_name}' agent binary is installed. "
            f"{_install_remedy(agent_dir_name)}"
        ) from e


def _hub_installed_binary(cache: Path, platform_key: str) -> Optional[FetchResult]:
    """Return the Hub-installed binary in *cache* if its sentinel verifies it.

    The Agent Hub installer verifies every artifact against the hub manifest's
    server-computed SHA-256 and records it in a ``.installed`` sentinel next to
    the binary (#2086) — an equivalent integrity guarantee to the lock's, so it
    must not be re-gated on binaries.lock.json (whose SHAs may still be
    placeholders, #2095). Returns ``None`` when no verified install exists;
    raises :class:`IntegrityError` if the installed file no longer matches its
    recorded SHA.
    """
    from gaia.hub import installer

    sentinel = installer.read_sentinel(cache.name, cache.parent)
    if (
        sentinel is None
        or sentinel.artifact_kind != installer.ARTIFACT_KIND_BINARY
        or not sentinel.executable
        or not sentinel.artifact_sha256
    ):
        return None
    binary_path = cache / sentinel.executable
    actual = file_sha256(binary_path)
    if actual is None:
        return None
    if actual.lower() != sentinel.artifact_sha256.lower():
        raise IntegrityError(
            f"hub-installed agent binary at {binary_path} does not match the "
            f"SHA-256 its install sentinel recorded:\n"
            f"  expected {sentinel.artifact_sha256}\n  actual   {actual}\n"
            "The install is corrupt or was tampered with — reinstall the agent."
        )
    logger.info(
        "sidecar fetch: using hub-installed verified binary %s (v%s)",
        binary_path,
        sentinel.version,
    )
    return FetchResult(
        binary_path,
        platform_key,
        actual,
        url="",
        cached=True,
        version=sentinel.version or None,
    )


INSTALL_RECORD_NAME = "gaia-agent.install.json"


def flagship_executable_name() -> str:
    return "gaia-agent.exe" if sys.platform == "win32" else "gaia-agent"


def installer_placed_flagship() -> Optional[Path]:
    """The flagship an installer put outside the hub, else ``None``.

    install.sh / install.ps1 put it in GAIA's bin dir; the terminal packages
    ship it beside the frozen gaia (Windows setup) or on PATH (.deb, .rpm,
    .pkg). A pip install's PATH is not trusted for it. Says nothing about
    whether the copy is verified -- see :func:`installer_verified_binary`.
    """
    from gaia.frozen import is_frozen
    from gaia.home import gaia_home

    name = flagship_executable_name()
    candidates = [gaia_home() / "bin" / name]
    if is_frozen():
        candidates.append(Path(sys.executable).parent / name)
        on_path = shutil.which("gaia-agent")
        if on_path:
            candidates.append(Path(on_path))
    for path in candidates:
        if not path.is_file():
            continue
        if sys.platform != "win32" and not os.access(path, os.X_OK):
            continue
        return path
    return None


def _read_install_record(record: Path) -> "tuple[str, str]":
    """``(version, sha256)`` from an installer's record; raises when unusable."""
    reinstall = (
        "Re-run the GAIA installer, or install the agent with "
        f"`{install_command(FLAGSHIP_AGENT_ID)}`."
    )
    try:
        data = json.loads(record.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise IntegrityError(
            f"cannot read the GAIA agent's install record at {record}: {e}. "
            f"{reinstall}"
        ) from e
    version = data.get("version") if isinstance(data, dict) else None
    sha = data.get("sha256") if isinstance(data, dict) else None
    if (
        not isinstance(version, str)
        or not version
        or not isinstance(sha, str)
        or not _SHA256_RE.match(sha)
    ):
        raise IntegrityError(
            f"the GAIA agent's install record at {record} names no version and "
            f"SHA-256. {reinstall}"
        )
    return version, sha.lower()


def installer_verified_binary(
    agent_dir_name: str, platform_key: Optional[str] = None
) -> Optional[FetchResult]:
    """The installer-placed flagship, verified against its install record.

    The installers check the download against the published SHA-256, then
    record that digest and the version beside the binary
    (``gaia-agent.install.json``). Returns ``None`` when there is no copy or
    no record; raises :class:`IntegrityError` when the copy no longer matches
    its record. The daemon starts this copy and `gaia init --check` counts
    it, so the two agree on it.
    """
    if agent_dir_name != FLAGSHIP_AGENT_ID:
        return None
    path = installer_placed_flagship()
    if path is None:
        return None
    record = path.parent / INSTALL_RECORD_NAME
    if not record.is_file():
        return None
    version, expected = _read_install_record(record)
    actual = file_sha256(path)
    if actual is None or actual != expected:
        raise IntegrityError(
            f"the GAIA agent at {path} does not match the SHA-256 its installer "
            f"recorded:\n  expected {expected}\n  actual   {actual}\n"
            "It was replaced or corrupted after install. Re-run the GAIA "
            "installer, or install the agent with "
            f"`{install_command(FLAGSHIP_AGENT_ID)}`."
        )
    logger.info(
        "sidecar fetch: using installer-placed verified binary %s (v%s)",
        path,
        version,
    )
    return FetchResult(
        path,
        platform_key or plat.current_platform_key(),
        actual,
        url="",
        cached=True,
        version=version,
    )


def hub_installed_binary(agent_dir_name: str) -> Optional[Path]:
    """The verified Hub-installed binary for *agent_dir_name*, or ``None``.

    Never downloads. Raises :class:`IntegrityError` when an install exists but
    no longer matches its sentinel.
    """
    installed = _hub_installed_binary(
        default_cache_dir(agent_dir_name), plat.current_platform_key()
    )
    return installed.binary_path if installed is not None else None


def fetch_binary(
    *,
    out_dir: Optional[Path] = None,
    base_url: Optional[str] = None,
    platform_key: Optional[str] = None,
    lock_path: Optional[Path] = None,
    force: bool = False,
    timeout: float = 120.0,
    session=None,
    agent_dir_name: str = "email",
) -> FetchResult:
    """Fetch + verify + cache a sidecar agent's binary for the current platform.

    A Hub-installed, sentinel-verified binary short-circuits BEFORE the lock is
    consulted (#2095) — unless ``force`` explicitly asks for a lock re-fetch.

    Raises:
        PlatformError: unsupported platform / incomplete or placeholder lock entry.
        IntegrityError: SHA-256 mismatch (tampered install or download).
        RuntimeError: download/network failure (HTTP status surfaced).
    """
    key = platform_key or plat.current_platform_key()
    cache = Path(out_dir) if out_dir is not None else default_cache_dir(agent_dir_name)

    if not force:
        installed = _hub_installed_binary(cache, key)
        if installed is not None:
            return installed
        installed = installer_verified_binary(agent_dir_name, key)
        if installed is not None:
            return installed

    lock = _load_lock_or_install_hint(lock_path, agent_dir_name)
    entry = plat.resolve_entry(lock, key)
    resolved_base = base_url or os.environ.get("ASSETS_BASE_URL") or lock.base_url
    if not resolved_base:
        raise PlatformError(
            "no download base URL: binaries.lock.json has no baseUrl, ASSETS_BASE_URL "
            "is unset, and none was passed. Set ASSETS_BASE_URL or pass base_url."
        )
    if plat.is_placeholder_sha(entry.sha256):
        raise PlatformError(
            f"the binary lock has a placeholder sha256 for '{key}' "
            f"({entry.sha256}); this lock publishes no verifiable binary for it. "
            "Fetch is blocked so an unverifiable binary can never be trusted. "
            f"{_install_remedy(agent_dir_name)}"
        )

    cache.mkdir(parents=True, exist_ok=True)
    binary_path = cache / entry.executable
    url = _join_url(resolved_base, entry.filename)

    if not force:
        existing = file_sha256(binary_path)
        if existing and existing.lower() == entry.sha256.lower():
            logger.info("sidecar fetch: cache hit %s matches lock sha256", binary_path)
            return FetchResult(
                binary_path,
                key,
                existing,
                url,
                cached=True,
                version=lock.agent_version or None,
            )

    if session is None:
        import requests

        session = requests.Session()
    logger.info("sidecar fetch: downloading %s binary from %s", key, url)
    resp = session.get(
        url, timeout=timeout, headers={"accept": "application/octet-stream"}
    )
    if not getattr(resp, "ok", resp.status_code == 200):
        raise RuntimeError(
            f"download failed: HTTP {resp.status_code} {getattr(resp, 'reason', '')} "
            f"for {url}. Check ASSETS_BASE_URL and that the artifact is published "
            f"for {key}."
        )
    data = resp.content
    sha = verify_sha256(data, entry.sha256, f"{key} ({url})")

    # Write to a temp then rename so a crash mid-write never leaves a
    # half-written "verified" binary. Clean up the temp on any failure.
    tmp = binary_path.with_suffix(binary_path.suffix + f".download.{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, binary_path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    if os.name != "nt":
        binary_path.chmod(
            binary_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
    logger.info("sidecar fetch: installed verified binary -> %s", binary_path)
    return FetchResult(
        binary_path,
        key,
        sha,
        url,
        cached=False,
        version=lock.agent_version or None,
    )
