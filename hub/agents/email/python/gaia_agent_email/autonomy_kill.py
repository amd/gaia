# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Persisted autonomy kill flag — the cross-instance signal a REST/CLI kill
needs to reach the scheduler (#2649).

``set_autonomy_level(LEVEL_OFF)`` (``agent.py``) only ever mutates the config
of the one ``EmailTriageAgent`` object the caller holds. That is sufficient
for the REST/CLI session surface (``agent_routes.py``), which keeps one
live agent per session, but the scheduler (``autonomy_scheduler.py``) builds
a **brand-new** agent from env vars on every fire and never touches that
object — so a kill issued against a session never reached an in-flight or
future scheduled cycle.

Every ``EmailTriageAgent`` — session-built or scheduler-built — opens the
same ``state.db`` (see ``config.resolved_db_path()``), the same way the
trust ledger and session preferences already share it across instances.
This module makes the kill flag one more row in that shared file: a kill
issued through *any* agent instance is visible to *every other* instance's
next read, in-process or across a sidecar restart.

All public helpers are pure functions taking a ``DatabaseMixin``-typed first
argument, mirroring ``schedule_store``/``action_store``. They never reach
into the agent class.
"""

from __future__ import annotations

import time

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# Single-row table (id is always 1) — a kill flag has no history to query,
# just a current state. ``init_schema`` seeds the row so callers never have
# to distinguish "never killed" from "no row yet".
EMAIL_AUTONOMY_KILL_DDL = """
CREATE TABLE IF NOT EXISTS email_autonomy_kill (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    killed    INTEGER NOT NULL DEFAULT 0,
    killed_at REAL
);
"""


def init_schema(db) -> None:
    """Create the kill-flag table and seed its single row. Idempotent."""
    db.execute(EMAIL_AUTONOMY_KILL_DDL)
    db.execute(
        "INSERT OR IGNORE INTO email_autonomy_kill (id, killed, killed_at) "
        "VALUES (1, 0, NULL);"
    )


def set_killed(db, *, killed: bool) -> None:
    """Persist (``killed=True``) or clear (``killed=False``) the kill flag.

    Called from ``set_autonomy_level`` on every transition: to ``off`` sets
    it, to any other level clears it — so a subsequent ``resume`` un-blocks
    the scheduler, not just the session that issued the resume.
    """
    db.update(
        "email_autonomy_kill",
        {"killed": int(killed), "killed_at": time.time() if killed else None},
        "id = :id",
        {"id": 1},
    )


def is_killed(db) -> bool:
    """True when a kill is currently in effect for this mailbox's state.db.

    Defensive against a missing row (pre-upgrade database that hasn't run
    ``init_schema`` yet) by treating it as "never killed" rather than raising
    — the safe-by-default reading, matching every other autonomy default.
    """
    row = db.query("SELECT killed FROM email_autonomy_kill WHERE id = 1", one=True)
    return bool(row and row["killed"])


__all__ = [
    "EMAIL_AUTONOMY_KILL_DDL",
    "init_schema",
    "is_killed",
    "set_killed",
]
