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
same store.

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
    fired_at     REAL
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
    """Create the scheduled-jobs table if it doesn't exist. Idempotent."""
    db.execute(EMAIL_SCHEDULED_JOBS_DDL)


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


def claim_job(db, *, job_id: str) -> bool:
    """Atomically transition a job ``pending -> firing``.

    Returns True when this caller won the claim; False when the job was
    already claimed, fired, cancelled, or doesn't exist. The status guard in
    the WHERE clause is what makes a job fire exactly once.
    """
    affected = db.update(
        "email_scheduled_jobs",
        {"status": STATUS_FIRING},
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
    "fetch_due",
    "get_job",
    "init_schema",
    "list_jobs",
    "mark_fired",
    "mark_failed",
]
