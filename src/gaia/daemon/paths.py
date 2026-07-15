# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""On-disk locations for the daemon's single-instance state (design §0.26).

Everything lives under ``~/.gaia/host/``. ``GAIA_DAEMON_HOME`` overrides the
directory (used by tests so a run never clobbers the user's real daemon, and so
concurrent tests stay isolated). The override is read on every call — not cached —
so a subprocess spawned with a different env resolves its own directory.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_HOME = "GAIA_DAEMON_HOME"


def host_dir() -> Path:
    """Directory holding instance.json, the start lock, and the daemon log."""
    override = os.environ.get(_ENV_HOME)
    if override:
        return Path(override)
    return Path.home() / ".gaia" / "host"


def instance_path() -> Path:
    """The single-instance registry file (pid + port + client token)."""
    return host_dir() / "instance.json"


def lock_path() -> Path:
    """Advisory lock serializing daemon start (so two callers spawn one daemon)."""
    return host_dir() / "instance.lock"


def log_path() -> Path:
    """Daemon stdout/stderr log (what ``gaia daemon logs`` tails)."""
    return host_dir() / "daemon.log"


def ensure_host_dir() -> Path:
    """Create ``host_dir()`` if missing and return it."""
    d = host_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d
