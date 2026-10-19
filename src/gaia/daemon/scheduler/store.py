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
    run_count       INTEGER NOT NULL DEFAULT 0,
    claimed_at      REAL
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
    """Create both tables if absent, and add columns newer code relies on."""
    db.execute(DAEMON_JOBS_DDL)
    columns = {r["name"] for r in db.query("PRAGMA table_info(daemon_jobs)")}
    if "claimed_at" not in columns:
        db.execute("ALTER TABLE daemon_jobs ADD COLUMN claimed_at REAL")


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
        "claimed_at": row["claimed_at"],
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


def count_jobs(db, *, status: str) -> int:
    """``SELECT COUNT(*)`` for one status — used by the status route, which any
    client can poll, so it must never materialise every matching row just to
    take a length."""
    row = db.query(
        "SELECT COUNT(*) AS n FROM daemon_jobs WHERE status = :st",
        {"st": status},
        one=True,
    )
    return int(row["n"]) if row else 0


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


def claim_job(db, *, job_id: str, now: Optional[float] = None) -> bool:
    """Atomically transition ``pending -> firing``. True iff this caller won.

    The status guard in the WHERE clause is what makes a job fire exactly once
    across concurrent drivers — the transition guard the whole reconciliation
    rests on (no double-run when both old and new paths briefly coexist).
    ``claimed_at`` is what lets :func:`recover_stale_firing` tell a claim that
    is still running from one a dead process left behind.
    """
    affected = db.update(
        "daemon_jobs",
        {
            "status": STATUS_FIRING,
            "claimed_at": now if now is not None else time.time(),
        },
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
                "error": None,
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


def recover_stale_firing(db, *, now: float, stale_after: float) -> Dict[str, List[str]]:
    """Move on every ``firing`` row whose claim is older than ``stale_after``.

    A claim stays ``firing`` forever when its process dies mid-fire, or when the
    outcome write hits a locked DB. A recurring row goes back to ``pending`` and
    fires on the next pass. A one-shot row is marked ``failed``: its executor may
    already have taken effect (an email sent), so running it again is not safe.
    A row with no ``claimed_at`` was claimed before claim times were recorded,
    by a process that is long gone, so it is stale on sight.

    Returns ``{"requeued": [...], "failed": [...]}`` daemon job ids.
    """
    cutoff = now - stale_after
    rows = db.query(
        "SELECT * FROM daemon_jobs WHERE status = :firing "
        "AND (claimed_at IS NULL OR claimed_at <= :cutoff)",
        {"firing": STATUS_FIRING, "cutoff": cutoff},
    )
    requeued: List[str] = []
    failed: List[str] = []
    for row in rows or ():
        job = _row_to_job(row)
        claimed = job["claimed_at"]
        when = "at an unknown time" if claimed is None else f"at {claimed:.0f}"
        # Guarded on the claim we read, so a claim renewed meanwhile is kept.
        guard = "job_id = :id AND status = :firing AND " + (
            "claimed_at IS NULL" if claimed is None else "claimed_at = :claimed"
        )
        params = {"id": job["job_id"], "firing": STATUS_FIRING, "claimed": claimed}
        if job["kind"] == KIND_RECURRING and job["interval_seconds"]:
            values = {
                "status": STATUS_PENDING,
                "claimed_at": None,
                "error": (
                    f"re-queued: claimed {when} but never finished (the daemon "
                    "stopped or the store was locked mid-fire)"
                ),
            }
            target = requeued
        else:
            values = {
                "status": STATUS_FAILED,
                "fired_at": now,
                "error": (
                    f"interrupted while firing (claimed {when}, never finished). "
                    "Not run again because it may already have taken effect; "
                    "check its result and schedule it again if it did not."
                ),
            }
            target = failed
        if db.update("daemon_jobs", values, guard, params) == 1:
            target.append(job["job_id"])
    return {"requeued": requeued, "failed": failed}


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
    "count_jobs",
    "fetch_due",
    "get_job",
    "init_schema",
    "ledger_entry",
    "list_jobs",
    "list_ledger",
    "mark_failed",
    "mark_fired",
    "record_migration",
    "recover_stale_firing",
    "register_job",
]
