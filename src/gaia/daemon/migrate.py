# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One-time, versioned migration of pre-v2 ``~/.gaia`` state into host custody
(design §0.10 step 0; breakdown item V2-13).

Before Agent UI v2 the single Agent-UI process owned everything in-process and
wrote its stores flat under ``~/.gaia`` — sessions at ``~/.gaia/chat/gaia_chat.db``
and memory at ``~/.gaia/memory.db`` — with no agent tag and no on-disk schema
version. Under v2 the always-on daemon is the custody owner (§0.9): stores live
under ``~/.gaia/host/custody/`` and every row is scoped to an agent. Without a
migration, an upgrader either loses past chats or leaks memory cross-agent.

This module supplies that migration and the schema-version stamp that gates it:

* :func:`run_migrations` runs at every daemon start (wired in ``server.run``).
* The on-disk schema version is read from ``host/custody/schema.json``. Absent =
  version 0 = a pre-v2 install that has never been migrated.
* Older-than-current → ordered migration steps run, then the version is stamped.
* Current → no-op (this is what makes a second run idempotent).
* Newer-than-current → **loud refusal** (a downgraded binary must not touch state
  written by a newer one), never a silent proceed.

Invariants (CLAUDE.md — critical for a migration):

* **Non-destructive.** Legacy stores are *copied*, never moved or deleted, so the
  original data is always recoverable even if the daemon is later rolled back.
* **WAL-aware.** The legacy stores run in WAL mode, so committed-but-not-yet-
  checkpointed transactions live in the ``-wal`` sidecar, not the main ``.db``.
  A raw byte copy of the main file would silently drop them — losing the newest
  sessions/memory of a user whose old process crashed or is still writing. The
  copy therefore goes through ``sqlite3``'s online-backup API
  (:func:`_atomic_sqlite_snapshot`), which reads through the engine and captures
  a transaction-consistent snapshot including the WAL.
* **Atomic.** Each snapshot is written to a temp file then ``os.replace``-d into
  place, so a crash mid-copy never leaves a half-written database in custody.
* **Fail loud.** Any step that cannot complete raises :class:`MigrationError`
  (originals intact, version left un-stamped so the next start retries) — never a
  partial success that swallows the error.

The custody target paths below are what a pre-#2153 daemon writes; #2153 (the
``/host/v1`` custody API, V2-12) reads from the same layout. They are centralized
here so a rebase onto #2153's final scoping repoints one place, not the logic.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable, List

from gaia.daemon import paths
from gaia.daemon.errors import MigrationError
from gaia.logger import get_logger

logger = get_logger(__name__)

# Current on-disk custody schema version. Bump when a new migration step is added
# and register the step in ``_MIGRATIONS`` keyed by the version it produces.
SCHEMA_VERSION = 1

# Pre-v2 state predates per-agent scoping, so it all belongs to one agent: the
# host chat agent that owned the single Agent-UI process. #2153 may repoint this.
DEFAULT_HOST_AGENT_ID = "chat"

# Legacy ``~/.gaia`` root override. Mirrors ``gaia.config.GAIA_CONFIG_DIR`` but is
# resolved on every call (not import-time) so tests can monkeypatch the env and so
# a subprocess with a different env resolves its own root — the same discipline
# ``paths.host_dir`` uses for ``GAIA_DAEMON_HOME``.
_ENV_LEGACY_HOME = "GAIA_CONFIG_DIR"


class MigrationState:
    """String constants for the ``status`` field of :func:`run_migrations`."""

    CURRENT = "current"  # on-disk == SCHEMA_VERSION; nothing to do
    MIGRATED = "migrated"  # ran one or more steps and stamped the new version


# ---------------------------------------------------------------------------
# Path helpers (centralized so a #2153 rebase repoints here, not the logic)
# ---------------------------------------------------------------------------


def legacy_gaia_dir() -> Path:
    """The pre-v2 ``~/.gaia`` root holding the flat legacy stores."""
    override = os.environ.get(_ENV_LEGACY_HOME)
    if override:
        return Path(override)
    return Path.home() / ".gaia"


def custody_dir() -> Path:
    """The v2 custody root: ``host/custody/`` (under ``paths.host_dir()``)."""
    return paths.host_dir() / "custody"


def schema_stamp_path() -> Path:
    """The on-disk schema-version stamp (also the migration journal)."""
    return custody_dir() / "schema.json"


def legacy_sessions_db() -> Path:
    """Pre-v2 Agent-UI sessions DB (``~/.gaia/chat/gaia_chat.db``)."""
    return legacy_gaia_dir() / "chat" / "gaia_chat.db"


def legacy_memory_db() -> Path:
    """Pre-v2 agent memory DB (``~/.gaia/memory.db``)."""
    return legacy_gaia_dir() / "memory.db"


def custody_sessions_db() -> Path:
    """Sessions in custody, tagged to the default host agent (host index)."""
    return custody_dir() / "agents" / DEFAULT_HOST_AGENT_ID / "sessions.db"


def custody_memory_db() -> Path:
    """Memory in custody, user-scope (single owner across the host's agents)."""
    return custody_dir() / "memory" / "user" / "memory.db"


# ---------------------------------------------------------------------------
# Schema-version stamp
# ---------------------------------------------------------------------------


def read_schema_version() -> int:
    """Return the on-disk schema version, or ``0`` if never stamped (pre-v2).

    A present-but-corrupt stamp is a :class:`MigrationError`, not a silent reset —
    treating an unreadable stamp as "version 0" would re-run migrations over
    already-migrated state.
    """
    path = schema_stamp_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except OSError as e:
        raise MigrationError(
            f"cannot read the custody schema stamp at {path}: {e}. Fix the file "
            "permissions (it must be readable by the daemon user) and restart, or "
            "inspect it via `gaia daemon logs`."
        ) from e
    try:
        version = int(json.loads(raw)["schema_version"])
    except (ValueError, KeyError, TypeError) as e:
        raise MigrationError(
            f"the custody schema stamp at {path} is present but malformed ({e}); "
            "refusing to guess the on-disk version. Restore it from a backup or "
            "remove it only if you are certain the custody store is empty, then "
            "restart the daemon."
        ) from e
    if version < 0:
        raise MigrationError(
            f"the custody schema stamp at {path} records a negative version "
            f"({version}); refusing to proceed over corrupt state."
        )
    return version


def _stamp_schema_version(version: int, applied: List[str]) -> None:
    """Atomically write the schema stamp / journal at *version*."""
    payload = {
        "schema_version": version,
        "stamped_at": time.time(),
        "applied": applied,
    }
    paths.atomic_write_json(schema_stamp_path(), payload)


# ---------------------------------------------------------------------------
# WAL-aware, atomic SQLite copy
# ---------------------------------------------------------------------------


def _atomic_sqlite_snapshot(src: Path, dst: Path, mode: int = 0o600) -> None:
    """Copy the SQLite database *src* to *dst* as a consistent, WAL-aware snapshot.

    The legacy stores run in WAL mode (``PRAGMA journal_mode = WAL``), so a
    committed transaction that has not yet been checkpointed lives in the ``-wal``
    sidecar file, not the main ``.db``. A raw byte copy of the main file would
    silently omit it — losing the newest sessions/memory of any user whose old
    process is still running or crashed before a checkpoint. ``sqlite3``'s
    online-backup API reads *through* the engine, so the snapshot is
    transaction-consistent and includes the WAL, even against a live writer.

    The source is opened for reading only — ``backup`` never writes to it, so the
    migration stays non-destructive. (A literal read-only handle,
    ``file:...?mode=ro``, cannot be used: SQLite cannot open a WAL database
    read-only once its writer has exited, because it may not create the ``-shm``
    wal-index — which is exactly the common upgrade case.) The snapshot is written
    to a temp file and atomically ``os.replace``-d over *dst*, so a crash
    mid-backup never leaves a half-written database in custody.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{dst.name}.{os.getpid()}.tmp"
    if tmp.exists():
        tmp.unlink()
    # A generous busy_timeout so a brief lock held by a live legacy writer is
    # waited out rather than surfaced as a spurious "database is locked" failure.
    src_conn = sqlite3.connect(str(src), timeout=30.0)
    try:
        src_conn.execute("PRAGMA busy_timeout=30000")
        dst_conn = sqlite3.connect(str(tmp))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    finally:
        src_conn.close()
    os.replace(str(tmp), str(dst))
    try:
        os.chmod(str(dst), mode)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Migration steps — each keyed by the version it PRODUCES
# ---------------------------------------------------------------------------


def _migrate_v0_to_v1() -> List[str]:
    """Relocate flat pre-v2 stores into the v2 custody layout.

    Sessions → the host index tagged with the default agent; memory → user-scope.
    Each store is copied atomically and only if the legacy file exists (a fresh
    install has neither — the cold-state path — and simply gets stamped at v1).
    Re-copying is safe: the source is preserved, so a retry after a partial
    failure overwrites any half-migrated target with the authoritative original.
    """
    applied: List[str] = []

    relocations = (
        ("sessions", legacy_sessions_db(), custody_sessions_db()),
        ("memory", legacy_memory_db(), custody_memory_db()),
    )
    for name, src, dst in relocations:
        if not src.exists():
            logger.info("migrate: no legacy %s at %s; skipping", name, src)
            continue
        try:
            _atomic_sqlite_snapshot(src, dst)
        except (OSError, sqlite3.Error) as e:
            raise MigrationError(
                f"failed to migrate legacy {name} from {src} to {dst}: {e}. The "
                f"original at {src} is untouched; free up disk space / fix "
                "permissions on the custody directory and restart the daemon to "
                "retry. No data was lost."
            ) from e
        logger.info("migrate: snapshotted legacy %s %s -> %s", name, src, dst)
        applied.append(f"{name}:{src.name}")

    return applied


# Ordered registry: index i upgrades version i -> i+1. Extend, don't rewrite.
_MIGRATIONS: List[Callable[[], List[str]]] = [
    _migrate_v0_to_v1,
]

# The registry must always be able to reach SCHEMA_VERSION from 0 — a mismatch is
# a programming error (a bumped SCHEMA_VERSION without a registered step), caught
# here at import rather than as a confusing runtime gap. A raise (not assert) so
# it survives ``python -O``.
if len(_MIGRATIONS) != SCHEMA_VERSION:
    raise RuntimeError(
        "migration registry out of sync with SCHEMA_VERSION: "
        f"{len(_MIGRATIONS)} steps for version {SCHEMA_VERSION}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_migrations() -> dict:
    """Bring ``~/.gaia`` custody state to :data:`SCHEMA_VERSION`. Idempotent.

    Called on every daemon start. Returns a small summary dict
    (``status``/``from_version``/``to_version``/``applied``). Raises
    :class:`MigrationError` on a corrupt stamp, an unknown-newer version, or a
    step that fails — the daemon must not serve over ambiguous state.
    """
    current = read_schema_version()

    if current == SCHEMA_VERSION:
        return {
            "status": MigrationState.CURRENT,
            "from_version": current,
            "to_version": SCHEMA_VERSION,
            "applied": [],
        }

    if current > SCHEMA_VERSION:
        raise MigrationError(
            f"the on-disk custody schema is version {current} but this GAIA build "
            f"only understands up to version {SCHEMA_VERSION}. This state was "
            "written by a NEWER GAIA; refusing to run to avoid corrupting it. "
            "Upgrade GAIA to a build that supports this schema, or point it at a "
            "different custody home."
        )

    paths.ensure_host_dir()
    custody_dir().mkdir(parents=True, exist_ok=True)

    applied: List[str] = []
    version = current
    # Run each pending step in order, stamping after EACH so a failure part-way
    # through a multi-version upgrade leaves a consistent, resumable checkpoint.
    for step in _MIGRATIONS[current:SCHEMA_VERSION]:
        step_applied = step()  # MigrationError propagates: version stays un-bumped
        version += 1
        applied.extend(step_applied)
        _stamp_schema_version(version, applied)
        logger.info("migrate: stamped custody schema version %s", version)

    return {
        "status": MigrationState.MIGRATED,
        "from_version": current,
        "to_version": version,
        "applied": applied,
    }
