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
from datetime import datetime
from typing import Any, Dict, List, Optional

from gaia.daemon.scheduler.models import (
    KIND_RECURRING,
    STATUS_CANCELLED,
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
    claimed_at      REAL,
    claim_id        TEXT,
    superseded_claim TEXT
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


_LEASE_COLUMNS = (
    ("claimed_at", "REAL"),
    ("claim_id", "TEXT"),
    ("superseded_claim", "TEXT"),
)


def init_schema(db) -> None:
    """Create both tables if absent, and add columns newer code relies on."""
    db.execute(DAEMON_JOBS_DDL)
    columns = {r["name"] for r in db.query("PRAGMA table_info(daemon_jobs)")}
    for name, sql_type in _LEASE_COLUMNS:
        if name in columns:
            continue
        try:
            db.execute(f"ALTER TABLE daemon_jobs ADD COLUMN {name} {sql_type}")
        except sqlite3.OperationalError as e:
            # Another connection added it between our check and the ALTER.
            if "duplicate column name" not in str(e):
                raise
    # A row left firing by a build that recorded no claim time starts its
    # stale window now, rather than counting as hours old on sight.
    db.update(
        "daemon_jobs",
        {"claimed_at": time.time()},
        "status = :firing AND claimed_at IS NULL",
        {"firing": STATUS_FIRING},
    )


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
        "claim_id": row["claim_id"],
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


def claim_job(db, *, job_id: str, now: Optional[float] = None) -> Optional[str]:
    """Atomically transition ``pending -> firing``. Returns the claim token
    iff this caller won, else None.

    The status guard in the WHERE clause is what makes a job fire exactly once
    across concurrent drivers — the transition guard the whole reconciliation
    rests on (no double-run when both old and new paths briefly coexist). The
    claim is a lease: renew it with :func:`renew_claim` while the job runs,
    and pass the token to :func:`mark_fired` / :func:`mark_failed`.
    """
    claim_id = uuid.uuid4().hex
    affected = db.update(
        "daemon_jobs",
        {
            "status": STATUS_FIRING,
            "claimed_at": now if now is not None else time.time(),
            "claim_id": claim_id,
            "superseded_claim": None,
        },
        "job_id = :id AND status = :pending",
        {"id": job_id, "pending": STATUS_PENDING},
    )
    return claim_id if affected == 1 else None


def renew_claim(db, *, job_id: str, claim_id: str, now: float) -> bool:
    """Heartbeat a firing claim. False once the claim is no longer ours."""
    affected = db.update(
        "daemon_jobs",
        {"claimed_at": now},
        "job_id = :id AND status = :firing AND claim_id = :claim",
        {"id": job_id, "firing": STATUS_FIRING, "claim": claim_id},
    )
    return affected == 1


# The holder of a claim may still report its outcome after another driver
# recovered the job, as long as the job was not claimed again since.
_HOLDER_GUARD = (
    "job_id = :id AND ((status = :firing AND claim_id = :claim) OR "
    "(superseded_claim = :claim AND status IN ({statuses})))"
)


def mark_fired(db, *, job_id: str, claim_id: str) -> bool:
    """Mark a one-shot job fired, or roll a recurring job to its next fire.

    Recurring jobs go back to ``pending`` with ``fire_at`` advanced by their
    interval, so the single clock keeps firing them — no per-job timer object,
    just a row and the driver. Returns False if the claim was taken over and
    the job claimed again meanwhile; a recovery that only failed or re-queued
    the job is overwritten, since the holder knows what really happened.
    """
    job = get_job(db, job_id=job_id)
    if job is None:
        return False
    now = time.time()
    values: Dict[str, Any] = {
        "fired_at": now,
        "run_count": (job["run_count"] or 0) + 1,
        "error": None,
        "claim_id": None,
        "superseded_claim": None,
    }
    if job["kind"] == KIND_RECURRING and job["interval_seconds"]:
        values.update(
            {"status": STATUS_PENDING, "fire_at": now + job["interval_seconds"]}
        )
    else:
        values["status"] = STATUS_FIRED
    affected = db.update(
        "daemon_jobs",
        values,
        _HOLDER_GUARD.format(statuses=":failed, :pending"),
        {
            "id": job_id,
            "firing": STATUS_FIRING,
            "claim": claim_id,
            "failed": STATUS_FAILED,
            "pending": STATUS_PENDING,
        },
    )
    return affected == 1


def cancel_job(db, *, job_id: str) -> bool:
    """Cancel a job still waiting to fire. False if it is not pending."""
    affected = db.update(
        "daemon_jobs",
        {"status": STATUS_CANCELLED},
        "job_id = :id AND status = :pending",
        {"id": job_id, "pending": STATUS_PENDING},
    )
    return affected == 1


def mark_failed(db, *, job_id: str, claim_id: str, error: str) -> bool:
    """Mark a job failed. False if the claim was taken over meanwhile.

    Replaces a recovery's generic failure with the real error, but never
    overrides a re-queued job: its re-run decides the outcome.
    """
    affected = db.update(
        "daemon_jobs",
        {
            "status": STATUS_FAILED,
            "error": error,
            "fired_at": time.time(),
            "claim_id": None,
            "superseded_claim": None,
        },
        _HOLDER_GUARD.format(statuses=":failed"),
        {
            "id": job_id,
            "firing": STATUS_FIRING,
            "claim": claim_id,
            "failed": STATUS_FAILED,
        },
    )
    return affected == 1


def find_stale_firing(db, *, now: float, stale_after: float) -> List[Dict[str, Any]]:
    """Every ``firing`` job whose lease has not been renewed in ``stale_after``."""
    rows = db.query(
        "SELECT * FROM daemon_jobs WHERE status = :firing "
        "AND (claimed_at IS NULL OR claimed_at <= :cutoff)",
        {"firing": STATUS_FIRING, "cutoff": now - stale_after},
    )
    return [_row_to_job(r) for r in rows or ()]


def _describe_heartbeat(claimed: Optional[float], now: float) -> str:
    """``at 14:05 on 2026-10-18 (3 hours ago)``, in local time."""
    if claimed is None:
        return "at an unknown time"
    stamp = datetime.fromtimestamp(claimed).strftime("%H:%M on %Y-%m-%d")
    minutes = max(0, int((now - claimed) // 60))
    if minutes >= 120:
        ago = f"{minutes // 60} hours ago"
    elif minutes >= 1:
        ago = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    else:
        ago = "under a minute ago"
    return f"at {stamp} ({ago})"


def recover_claim(db, job: Dict[str, Any], *, now: float) -> Optional[str]:
    """Move on from an abandoned lease. Returns ``"requeued"``, ``"failed"``,
    or None if the lease changed since ``job`` was read (it is alive).

    A recurring row, or a one-shot whose payload sets ``"rerunnable": True``,
    goes back to ``pending`` and fires on the next pass. Any other one-shot row
    is marked ``failed``: its executor may already have taken effect (an email
    sent), so running it again is not safe. If the holder
    turns out to be alive, its own outcome still lands (``superseded_claim``).
    """
    claim_id, claimed = job["claim_id"], job["claimed_at"]
    guard = "job_id = :id AND status = :firing AND " + " AND ".join(
        (
            "claim_id IS NULL" if claim_id is None else "claim_id = :claim",
            "claimed_at IS NULL" if claimed is None else "claimed_at = :claimed",
        )
    )
    when = _describe_heartbeat(claimed, now)
    recurring = job["kind"] == KIND_RECURRING and job["interval_seconds"]
    if recurring or job["payload"].get("rerunnable") is True:
        values: Dict[str, Any] = {
            "status": STATUS_PENDING,
            "claimed_at": None,
            "error": (
                f"re-queued: its lease stopped being renewed (last heartbeat "
                f"{when}); the daemon stopped or the store was locked mid-fire"
            ),
        }
        outcome = "requeued"
    else:
        values = {
            "status": STATUS_FAILED,
            "fired_at": now,
            "error": (
                f"interrupted while firing (last heartbeat {when}). Not run "
                "again because it may already have taken effect. Check its "
                "result before scheduling it again: if it did run, this job "
                "may still change to fired."
            ),
        }
        outcome = "failed"
    values.update({"claim_id": None, "superseded_claim": claim_id})
    affected = db.update(
        "daemon_jobs",
        values,
        guard,
        {
            "id": job["job_id"],
            "firing": STATUS_FIRING,
            "claim": claim_id,
            "claimed": claimed,
        },
    )
    return outcome if affected == 1 else None


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
    "find_stale_firing",
    "get_job",
    "init_schema",
    "ledger_entry",
    "list_jobs",
    "list_ledger",
    "mark_failed",
    "mark_fired",
    "record_migration",
    "recover_claim",
    "register_job",
    "renew_claim",
]
