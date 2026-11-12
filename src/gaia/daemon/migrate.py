# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Versioned schema stamp for the daemon's ``host/custody/`` state.

:func:`run_migrations` runs at every daemon start (wired in ``server.run``). It
reads the on-disk version from ``host/custody/schema.json`` (absent = version 0),
runs any pending steps, and stamps the result. An already-current install is a
no-op; a version newer than this build understands is refused loudly, so a
downgraded binary never touches state written by a newer one.

Version 1 once copied ``gaia_chat.db`` and ``memory.db`` into ``host/custody/``,
but nothing reads those copies (custody lives in ``host/custody.db``), so the
step now only stamps. Copies already written are left on disk.
"""

from __future__ import annotations

import json
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


class MigrationState:
    """String constants for the ``status`` field of :func:`run_migrations`."""

    CURRENT = "current"  # on-disk == SCHEMA_VERSION; nothing to do
    MIGRATED = "migrated"  # ran one or more steps and stamped the new version


def custody_dir() -> Path:
    """The v2 custody root: ``host/custody/`` (under ``paths.host_dir()``)."""
    return paths.host_dir() / "custody"


def schema_stamp_path() -> Path:
    """The on-disk schema-version stamp (also the migration journal)."""
    return custody_dir() / "schema.json"


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
# Migration steps — each keyed by the version it PRODUCES
# ---------------------------------------------------------------------------


def _migrate_v0_to_v1() -> List[str]:
    """Stamp-only: version 1's database copy was retired because nothing read it."""
    return []


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
