# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""On-disk locations for the daemon's single-instance state (design §0.26).

Everything lives under ``~/.gaia/host/``. ``GAIA_DAEMON_HOME`` overrides the
directory (used by tests so a run never clobbers the user's real daemon, and so
concurrent tests stay isolated). The override is read on every call — not cached —
so a subprocess spawned with a different env resolves its own directory.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Union

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


def sidecars_ledger_path() -> Path:
    """The sidecar spawn ledger (pids the daemon must reap after a hard crash)."""
    return host_dir() / "sidecars.json"


def ensure_host_dir() -> Path:
    """Create ``host_dir()`` if missing and return it."""
    d = host_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_write_json(path: Path, payload, mode: int = 0o600) -> None:
    """Atomically persist *payload* as JSON at *path*, default file mode 0600.

    Shared by ``instance.py`` and ``sidecars/ledger.py`` — one copy of a
    secrets-adjacent write routine, not two that drift. Writes a uniquely-named
    temp file in the same directory (so ``os.replace`` is a same-filesystem
    atomic rename), fsyncs it, then renames it over the target. The temp file
    is created ``O_EXCL`` at *mode* so it is never briefly world-readable.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    # O_EXCL: a leftover temp from a prior crashed writer must not be reused.
    if tmp.exists():
        tmp.unlink()
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2))
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(str(tmp), str(path))
    # os.replace preserves the temp's mode; re-assert defensively for platforms
    # where the source mode is not carried across the rename.
    try:
        os.chmod(str(path), mode)
    except OSError:
        pass


def atomic_copy_file(
    src: Union[str, Path], dst: Union[str, Path], mode: int = 0o600
) -> None:
    """Atomically copy *src* to *dst* — same temp-then-rename discipline as
    :func:`atomic_write_json`, but for arbitrary (binary) files.

    Used by the one-time state migration to relocate SQLite stores: the copy is
    written to a uniquely-named temp file in *dst*'s directory, fsynced, then
    ``os.replace``-d over the target, so a crash mid-copy never leaves a
    half-written (and therefore corrupt) database at *dst*. *src* is never
    modified — the migration is non-destructive by construction.
    """
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{dst.name}.{os.getpid()}.tmp"
    if tmp.exists():
        tmp.unlink()
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_TRUNC, mode)
    try:
        with open(src, "rb") as fsrc, os.fdopen(fd, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
            fdst.flush()
            os.fsync(fdst.fileno())
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(str(tmp), str(dst))
    try:
        os.chmod(str(dst), mode)
    except OSError:
        pass
