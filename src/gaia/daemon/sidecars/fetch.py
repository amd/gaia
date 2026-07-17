# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Verified binary fetch (port of fetch.ts) — the security boundary.

Two verified sources, checked in order; there is NO 'use it anyway' path:

1. **Agent Hub install** — a binary the Hub installer already verified against
   the hub manifest's server-computed SHA-256 and recorded in its ``.installed``
   sentinel (#2086). Re-checked against that sentinel here before every spawn.
2. **Lock fetch** — resolve the host platform -> look up the artifact in
   binaries.lock.json -> download -> **verify its SHA-256 against the lock and
   raise loudly on any mismatch** -> write atomically into the cache -> chmod +x
   on POSIX.

A tampered/truncated binary is rejected before it can ever be spawned.

Relocated from ``gaia.ui.email_sidecar.fetch`` (issue #2142, T1). Generalization
capped to relocation: ``default_cache_dir`` now takes the agent's cache dir
name (from its spec) instead of hardcoding "email" — everything else is
unchanged.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gaia.daemon.sidecars import platform as plat
from gaia.daemon.sidecars.errors import IntegrityError, PlatformError
from gaia.logger import get_logger

logger = get_logger(__name__)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> Optional[str]:
    try:
        return sha256_hex(Path(path).read_bytes())
    except OSError:
        return None


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
    return Path.home() / ".gaia" / "agents" / agent_dir_name


@dataclass(frozen=True)
class FetchResult:
    binary_path: Path
    platform_key: str
    sha256: str
    url: str
    cached: bool


def _join_url(base: str, name: str) -> str:
    return f"{base.rstrip('/')}/{name.lstrip('/')}"


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
            "The install is corrupt or was tampered with — reinstall the agent "
            "from the Agent Hub."
        )
    logger.info(
        "sidecar fetch: using hub-installed verified binary %s (v%s)",
        binary_path,
        sentinel.version,
    )
    return FetchResult(binary_path, platform_key, actual, url="", cached=True)


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

    lock = plat.load_lock(lock_path)
    entry = plat.resolve_entry(lock, key)
    resolved_base = base_url or os.environ.get("ASSETS_BASE_URL") or lock.base_url
    if not resolved_base:
        raise PlatformError(
            "no download base URL: binaries.lock.json has no baseUrl, ASSETS_BASE_URL "
            "is unset, and none was passed. Set ASSETS_BASE_URL or pass base_url."
        )
    if plat.is_placeholder_sha(entry.sha256):
        raise PlatformError(
            f"binaries.lock.json has a placeholder sha256 for '{key}' "
            f"({entry.sha256}); no binary is published for it in this build. Fetch is "
            "blocked so a bad binary can never be trusted. Install the email agent "
            "from the Agent Hub, publish it (release_agent_email.yml) to populate "
            "the real SHA, or run dev mode (GAIA_EMAIL_AGENT_MODE=dev)."
        )

    cache.mkdir(parents=True, exist_ok=True)
    binary_path = cache / entry.executable
    url = _join_url(resolved_base, entry.filename)

    if not force:
        existing = file_sha256(binary_path)
        if existing and existing.lower() == entry.sha256.lower():
            logger.info("sidecar fetch: cache hit %s matches lock sha256", binary_path)
            return FetchResult(binary_path, key, existing, url, cached=True)

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
    return FetchResult(binary_path, key, sha, url, cached=False)
