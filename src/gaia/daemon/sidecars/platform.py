# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Platform/arch resolution and binary-lock loading (port of platform.ts).

``binaries.lock.json`` is the single source of truth for which artifact to fetch
for the current host and what its SHA-256 must be. Platform keys are
``{os}-{arch}`` normalized to the npm package's key space
(``win32``/``darwin``/``linux`` + ``x64``/``arm64``).

Relocated from ``gaia.ui.email_sidecar.platform`` (issue #2142, T1).
"""

from __future__ import annotations

import json
import platform as _platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gaia.daemon.sidecars.errors import PlatformError

SUPPORTED_PLATFORMS = ("win32-x64", "darwin-arm64", "darwin-x64", "linux-x64")


@dataclass(frozen=True)
class LockEntry:
    filename: str
    sha256: str
    executable: str
    size: Optional[int] = None


@dataclass(frozen=True)
class BinaryLock:
    schema_version: str
    agent_version: str
    base_url: str
    binaries: "dict[str, LockEntry]"


def current_platform_key(plat: Optional[str] = None, arch: Optional[str] = None) -> str:
    """Resolve the host's platform key in the npm package's namespace.

    Maps Python's ``sys.platform``/``platform.machine()`` to the ``{os}-{arch}``
    keys used in binaries.lock.json.
    """
    raw_os = plat if plat is not None else sys.platform
    if raw_os.startswith("win"):
        os_key = "win32"
    elif raw_os == "darwin":
        os_key = "darwin"
    elif raw_os.startswith("linux"):
        os_key = "linux"
    else:
        os_key = raw_os
    raw_arch = (arch if arch is not None else _platform.machine()).lower()
    if raw_arch in ("x86_64", "amd64", "x64"):
        arch_key = "x64"
    elif raw_arch in ("arm64", "aarch64"):
        arch_key = "arm64"
    else:
        arch_key = raw_arch
    return f"{os_key}-{arch_key}"


def default_lock_path() -> Path:
    """Locate the repo's binaries.lock.json (npm package ships the canonical one)."""
    # src/gaia/daemon/sidecars/platform.py -> repo root is parents[4].
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "hub" / "agents" / "email" / "npm" / "binaries.lock.json"


def load_lock(lock_path: Optional[Path] = None) -> BinaryLock:
    path = Path(lock_path) if lock_path is not None else default_lock_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PlatformError(
            f"cannot read the sidecar binary lock at {path}: {e}. This file is "
            "only present in a source checkout (it ships in the agent's npm "
            "package, not the installed Python wheel), so an installed GAIA "
            "resolves the binary from an Agent Hub install instead — reinstalling "
            "the Python wheel will not create this file."
        ) from e
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PlatformError(
            f"binaries.lock.json at {path} is not valid JSON: {e}"
        ) from e
    binaries = parsed.get("binaries")
    if not isinstance(binaries, dict):
        raise PlatformError(f'binaries.lock.json at {path} is missing a "binaries" map')
    entries = {
        k: LockEntry(
            filename=v.get("filename", ""),
            sha256=v.get("sha256", ""),
            executable=v.get("executable", ""),
            size=v.get("size"),
        )
        for k, v in binaries.items()
    }
    return BinaryLock(
        schema_version=parsed.get("schemaVersion", ""),
        agent_version=parsed.get("agentVersion", ""),
        base_url=parsed.get("baseUrl", ""),
        binaries=entries,
    )


def resolve_entry(lock: BinaryLock, platform_key: str) -> LockEntry:
    entry = lock.binaries.get(platform_key)
    if entry is None:
        available = ", ".join(lock.binaries) or "(none)"
        raise PlatformError(
            f"no sidecar binary for platform '{platform_key}'. Available in "
            f"binaries.lock.json: {available}. Supported targets: "
            + ", ".join(SUPPORTED_PLATFORMS)
        )
    if not entry.sha256 or not entry.filename or not entry.executable:
        raise PlatformError(
            f"binaries.lock.json entry for '{platform_key}' is incomplete "
            "(needs filename, sha256, executable) — likely a placeholder with no "
            "published binary for this platform."
        )
    return entry


def is_placeholder_sha(sha256: str) -> bool:
    """True for the not-yet-published sentinel (all-zeros or a PENDING marker)."""
    s = sha256 or ""
    if not s:
        return False
    return s.strip("0") == "" or "PENDING" in s.upper()
