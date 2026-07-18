# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Durable store for the single daemon-owned clock (V2-15, #2156).

Two tables, one SQLite file:

- ``daemon_jobs`` — every periodic job the daemon now owns, whatever clock it
  came from. ``claim_due`` is an atomic ``pending -> firing`` UPDATE guarded on
  status, so a job fires exactly once even when two drivers poll the same store
  (the same proven guard the email ``schedule_store`` uses).
- ``daemon_migration_ledger`` — one row per migrated source job, keyed
  ``(source, source_job_id)`` UNIQUE. This is the exactly-once spine: a second
  reconcile pass hits the UNIQUE constraint and skips, so an in-sidecar job
  adopted once is never adopted (and never double-fired) again.

All helpers are pure functions over a ``DatabaseMixin``-typed handle, mirroring
the email package's ``schedule_store``/``action_store`` — they never reach into a
class. This module is intentionally custody-agnostic: when V2-12 (#2153) lands
the SQLite handle is swapped for the host-custody store; nothing here assumes a
particular file location.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

from gaia.daemon.scheduler.models import (
    KIND_RECURRING,
    STATUS_FAILED,
    STATUS_FIRED,
    STATUS_FIRING,
    STATUS_PENDING,
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

DAEMON_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS daemon_jobs (
    job_id          TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    source_job_id   TEXT,
    kind            TEXT NOT NULL,
    fire_at         REAL,
    interval_seconds INTEGER,
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    error           TEXT,
    created_at      REAL NOT NULL,
    fired_at        REAL,
    run_count       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_daemon_jobs_due
    ON daemon_jobs(status, fire_at);

CREATE TABLE IF NOT EXISTS daemon_migration_ledger (
    source          TEXT NOT NULL,
    source_job_id   TEXT NOT NULL,
    daemon_job_id   TEXT NOT NULL,
    migrated_at     REAL NOT NULL,
    PRIMARY KEY (source, source_job_id)
);
"""


def init_schema(db) -> None:
    """Create both tables if absent. Idempotent."""
    db.execute(DAEMON_JOBS_DDL)


def _row_to_job(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "source": row["source"],
        "source_job_id": row["source_job_id"],
        "kind": row["kind"],
        "fire_at": row["fire_at"],
        "interval_seconds": row["interval_seconds"],
        "payload": json.loads(row["payload_json"]) if row["payload_json"] else {},
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
        "fired_at": row["fired_at"],
        "run_count": row["run_count"],
    }


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------


def register_job(
    db,
    *,
    source: str,
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
    source_job_id: Optional[str] = None,
    fire_at: Optional[float] = None,
    interval_seconds: Optional[int] = None,
    job_id: Optional[str] = None,
) -> str:
    """Insert a pending job into the single clock and return its daemon job_id.

    ``job_id`` may be supplied so a migration can make the daemon id stable and
    idempotent; otherwise a fresh uuid is minted.
    """
    jid = job_id or uuid.uuid4().hex
    db.insert(
        "daemon_jobs",
        {
            "job_id": jid,
            "source": source,
            "source_job_id": source_job_id,
            "kind": kind,
            "fire_at": fire_at,
            "interval_seconds": interval_seconds,
            "payload_json": json.dumps(payload or {}),
            "status": STATUS_PENDING,
            "error": None,
            "created_at": time.time(),
            "fired_at": None,
            "run_count": 0,
        },
    )
    return jid


def get_job(db, *, job_id: str) -> Optional[Dict[str, Any]]:
    row = db.query(
        "SELECT * FROM daemon_jobs WHERE job_id = :id", {"id": job_id}, one=True
    )
    return _row_to_job(row) if row else None


def list_jobs(db, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
    if status is None:
        rows = db.query("SELECT * FROM daemon_jobs ORDER BY created_at")
    else:
        rows = db.query(
            "SELECT * FROM daemon_jobs WHERE status = :st ORDER BY created_at",
            {"st": status},
        )
    return [_row_to_job(r) for r in rows or ()]


def fetch_due(db, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Return every pending job whose ``fire_at`` is at or before ``now``.

    A NULL ``fire_at`` is treated as never-due — a recurring job is scheduled
    with a concrete next ``fire_at`` at registration, so a NULL means "not yet
    scheduled" and must not fire blindly.
    """
    rows = db.query(
        "SELECT * FROM daemon_jobs "
        "WHERE status = :st AND fire_at IS NOT NULL AND fire_at <= :now "
        "ORDER BY fire_at",
        {"st": STATUS_PENDING, "now": now if now is not None else time.time()},
    )
    return [_row_to_job(r) for r in rows or ()]


def claim_job(db, *, job_id: str) -> bool:
    """Atomically transition ``pending -> firing``. True iff this caller won.

    The status guard in the WHERE clause is what makes a job fire exactly once
    across concurrent drivers — the transition guard the whole reconciliation
    rests on (no double-run when both old and new paths briefly coexist).
    """
    affected = db.update(
        "daemon_jobs",
        {"status": STATUS_FIRING},
        "job_id = :id AND status = :pending",
        {"id": job_id, "pending": STATUS_PENDING},
    )
    return bool(affected == 1)


def mark_fired(db, *, job_id: str) -> None:
    """Mark a one-shot job fired, or roll a recurring job to its next fire.

    Recurring jobs go back to ``pending`` with ``fire_at`` advanced by their
    interval, so the single clock keeps firing them — no per-job timer object,
    just a row and the driver.
    """
    job = get_job(db, job_id=job_id)
    if job is None:
        return
    now = time.time()
    run_count = (job["run_count"] or 0) + 1
    if job["kind"] == KIND_RECURRING and job["interval_seconds"]:
        db.update(
            "daemon_jobs",
            {
                "status": STATUS_PENDING,
                "fired_at": now,
                "fire_at": now + job["interval_seconds"],
                "run_count": run_count,
            },
            "job_id = :id",
            {"id": job_id},
        )
    else:
        db.update(
            "daemon_jobs",
            {"status": STATUS_FIRED, "fired_at": now, "run_count": run_count},
            "job_id = :id",
            {"id": job_id},
        )


def mark_failed(db, *, job_id: str, error: str) -> None:
    db.update(
        "daemon_jobs",
        {"status": STATUS_FAILED, "error": error, "fired_at": time.time()},
        "job_id = :id",
        {"id": job_id},
    )


# ---------------------------------------------------------------------------
# Migration ledger — the exactly-once spine
# ---------------------------------------------------------------------------


def ledger_entry(db, *, source: str, source_job_id: str) -> Optional[Dict[str, Any]]:
    row = db.query(
        "SELECT * FROM daemon_migration_ledger "
        "WHERE source = :s AND source_job_id = :sid",
        {"s": source, "sid": source_job_id},
        one=True,
    )
    return dict(row) if row else None


def record_migration(
    db, *, source: str, source_job_id: str, daemon_job_id: str
) -> bool:
    """Record that ``(source, source_job_id)`` was migrated to ``daemon_job_id``.

    Returns True on a fresh insert, False when the ledger already had the pair
    (the UNIQUE PK lost the race / a prior pass owned it). The caller uses the
    False return to keep the migration idempotent instead of double-adopting.
    """
    try:
        db.insert(
            "daemon_migration_ledger",
            {
                "source": source,
                "source_job_id": source_job_id,
                "daemon_job_id": daemon_job_id,
                "migrated_at": time.time(),
            },
        )
        return True
    except sqlite3.IntegrityError:
        return False


def list_ledger(db, *, source: Optional[str] = None) -> List[Dict[str, Any]]:
    if source is None:
        rows = db.query("SELECT * FROM daemon_migration_ledger")
    else:
        rows = db.query(
            "SELECT * FROM daemon_migration_ledger WHERE source = :s",
            {"s": source},
        )
    return [dict(r) for r in rows or ()]


__all__ = [
    "DAEMON_JOBS_DDL",
    "claim_job",
    "fetch_due",
    "get_job",
    "init_schema",
    "ledger_entry",
    "list_jobs",
    "list_ledger",
    "mark_failed",
    "mark_fired",
    "record_migration",
    "register_job",
]
