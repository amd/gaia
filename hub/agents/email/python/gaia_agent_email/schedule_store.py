# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Persistent one-shot job store for scheduled send + snooze (#1609).

``email_scheduled_jobs`` holds fire-once-at-T jobs (scheduled send, snooze
re-surface) with a cancel handle. This is deliberately NOT the recurring
scheduler from ``src/gaia/ui/scheduler.py`` (#550): the acceptance criteria —
"fires once at/after its time and not before", "cancel prevents re-surfacing" —
need one-shot semantics with an atomic claim, which an ``every 6h`` interval
scheduler doesn't provide.

Scheduler seam: this store is the source of truth; *what drives it* is
pluggable. Today ``gaia_agent_email.scheduler.EmailJobScheduler`` polls it from
a background thread. When the ``gaia schedule`` cron dispatcher lands (#1371,
autonomy epic #555), that dispatcher can call
``EmailJobScheduler.fire_due_jobs()`` on its cadence instead — no store or tool
changes required.

Status lifecycle::

    pending --claim_job--> firing --mark_fired--> fired
        |                     \\--mark_failed--> failed
        \\--cancel_job--> cancelled

``claim_job`` is an atomic ``pending -> firing`` transition (single UPDATE
guarded on status), so a job fires exactly once even if two drivers poll the
same store. It records ``claimed_at``; ``fail_stale_firing`` marks a claim that
never finished ``failed`` instead of leaving it ``firing`` forever.

All public helpers are pure functions taking a ``DatabaseMixin``-typed first
argument, mirroring ``action_store``. They never reach into the agent class.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

EMAIL_SCHEDULED_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS email_scheduled_jobs (
    job_id       TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    due_at       REAL NOT NULL,
    payload_json TEXT NOT NULL,
    mailbox      TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    error        TEXT,
    created_at   REAL NOT NULL,
    fired_at     REAL,
    claimed_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_email_scheduled_jobs_due
    ON email_scheduled_jobs(status, due_at);
"""

# Job kinds. The executor registry in ``EmailJobScheduler`` is keyed on these.
KIND_SCHEDULED_SEND = "scheduled_send"
KIND_SNOOZE = "snooze"

STATUS_PENDING = "pending"
STATUS_FIRING = "firing"
STATUS_FIRED = "fired"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


def init_schema(db) -> None:
    """Create the scheduled-jobs table if absent, and add newer columns."""
    db.execute(EMAIL_SCHEDULED_JOBS_DDL)
    columns = {r["name"] for r in db.query("PRAGMA table_info(email_scheduled_jobs)")}
    if "claimed_at" not in columns:
        db.execute("ALTER TABLE email_scheduled_jobs ADD COLUMN claimed_at REAL")


def _row_to_job(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
    return {
        "job_id": row["job_id"],
        "kind": row["kind"],
        "due_at": row["due_at"],
        "payload": payload,
        "mailbox": row["mailbox"],
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
        "fired_at": row["fired_at"],
        "claimed_at": row["claimed_at"],
    }


def create_job(
    db,
    *,
    kind: str,
    due_at: float,
    payload: Optional[Dict[str, Any]] = None,
    mailbox: Optional[str] = None,
) -> str:
    """Insert a pending one-shot job, return the new job_id.

    ``payload`` carries the data needed to fire the job later — e.g. for
    ``scheduled_send`` the backend draft_id (never the full body: the body
    lives in the mailbox as a draft, not in the unencrypted SQLite); for
    ``snooze`` the message id + prior labels needed to re-surface it.
    """
    job_id = uuid.uuid4().hex
    db.insert(
        "email_scheduled_jobs",
        {
            "job_id": job_id,
            "kind": kind,
            "due_at": due_at,
            "payload_json": json.dumps(payload or {}),
            "mailbox": mailbox,
            "status": STATUS_PENDING,
            "error": None,
            "created_at": time.time(),
            "fired_at": None,
        },
    )
    return job_id


def get_job(db, *, job_id: str) -> Optional[Dict[str, Any]]:
    row = db.query(
        "SELECT * FROM email_scheduled_jobs WHERE job_id = :id",
        {"id": job_id},
        one=True,
    )
    return _row_to_job(row) if row else None


def fetch_due(db, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Return every pending job whose due_at is at or before ``now``."""
    rows = db.query(
        "SELECT * FROM email_scheduled_jobs "
        "WHERE status = :st AND due_at <= :now ORDER BY due_at",
        {"st": STATUS_PENDING, "now": now if now is not None else time.time()},
    )
    return [_row_to_job(r) for r in rows or ()]


def list_jobs(db, *, status: str = STATUS_PENDING) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM email_scheduled_jobs WHERE status = :st ORDER BY due_at",
        {"st": status},
    )
    return [_row_to_job(r) for r in rows or ()]


def claim_job(db, *, job_id: str, now: Optional[float] = None) -> bool:
    """Atomically transition a job ``pending -> firing``.

    Returns True when this caller won the claim; False when the job was
    already claimed, fired, cancelled, or doesn't exist. The status guard in
    the WHERE clause is what makes a job fire exactly once.
    """
    affected = db.update(
        "email_scheduled_jobs",
        {
            "status": STATUS_FIRING,
            "claimed_at": now if now is not None else time.time(),
        },
        "job_id = :id AND status = :pending",
        {"id": job_id, "pending": STATUS_PENDING},
    )
    return affected == 1


def mark_fired(db, *, job_id: str) -> None:
    db.update(
        "email_scheduled_jobs",
        {"status": STATUS_FIRED, "fired_at": time.time()},
        "job_id = :id",
        {"id": job_id},
    )


def mark_failed(db, *, job_id: str, error: str) -> None:
    db.update(
        "email_scheduled_jobs",
        {"status": STATUS_FAILED, "error": error, "fired_at": time.time()},
        "job_id = :id",
        {"id": job_id},
    )


def fail_stale_firing(db, *, now: float, stale_after: float) -> List[str]:
    """Mark ``failed`` every ``firing`` job claimed more than ``stale_after`` ago.

    A claim stays ``firing`` forever when its process dies mid-fire, or when the
    outcome write hits a locked DB. The job is never run again, because a
    scheduled send may already have gone out, but it surfaces as a failure the
    user can see and reschedule. A row with no ``claimed_at`` was claimed before
    claim times were recorded, so it is stale on sight. Returns the failed ids.
    """
    rows = db.query(
        "SELECT * FROM email_scheduled_jobs WHERE status = :firing "
        "AND (claimed_at IS NULL OR claimed_at <= :cutoff)",
        {"firing": STATUS_FIRING, "cutoff": now - stale_after},
    )
    failed: List[str] = []
    for row in rows or ():
        claimed = row["claimed_at"]
        when = "at an unknown time" if claimed is None else f"at {claimed:.0f}"
        # Guarded on the claim we read, so a claim renewed meanwhile is kept.
        guard = "job_id = :id AND status = :firing AND " + (
            "claimed_at IS NULL" if claimed is None else "claimed_at = :claimed"
        )
        affected = db.update(
            "email_scheduled_jobs",
            {
                "status": STATUS_FAILED,
                "fired_at": now,
                "error": (
                    f"interrupted while firing (claimed {when}, never finished). "
                    "Not run again because it may already have taken effect; "
                    "check the mailbox and schedule it again if it did not."
                ),
            },
            guard,
            {"id": row["job_id"], "firing": STATUS_FIRING, "claimed": claimed},
        )
        if affected == 1:
            failed.append(row["job_id"])
    return failed


def cancel_job(db, *, job_id: str) -> bool:
    """Cancel a pending job. Returns False when the job is not cancellable
    (already fired/firing/cancelled, or unknown) — callers must surface that
    loudly, never treat it as success.
    """
    affected = db.update(
        "email_scheduled_jobs",
        {"status": STATUS_CANCELLED},
        "job_id = :id AND status = :pending",
        {"id": job_id, "pending": STATUS_PENDING},
    )
    return affected == 1


__all__ = [
    "EMAIL_SCHEDULED_JOBS_DDL",
    "KIND_SCHEDULED_SEND",
    "KIND_SNOOZE",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_FIRED",
    "STATUS_FIRING",
    "STATUS_PENDING",
    "cancel_job",
    "claim_job",
    "create_job",
    "fail_stale_firing",
    "fetch_due",
    "get_job",
    "init_schema",
    "list_jobs",
    "mark_fired",
    "mark_failed",
]
