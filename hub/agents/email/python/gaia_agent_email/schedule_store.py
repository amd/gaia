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
same store. The claim is a lease: ``claim_job`` returns a ``claim_id`` token,
the firing driver renews ``claimed_at`` with ``renew_claim`` while the send
runs, and ``mark_fired`` / ``mark_failed`` succeed only for the holder of the
token. ``find_stale_firing`` + ``recover_claim`` let another driver move on a
lease that stopped being renewed instead of leaving it ``firing`` forever (the
confirmation timing lives in :mod:`gaia.daemon.scheduler.lease`).

All public helpers are pure functions taking a ``DatabaseMixin``-typed first
argument, mirroring ``action_store``. They never reach into the agent class.
"""

from __future__ import annotations

import json
import sqlite3
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
    claimed_at   REAL,
    claim_id     TEXT,
    superseded_claim TEXT
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


_LEASE_COLUMNS = (
    ("claimed_at", "REAL"),
    ("claim_id", "TEXT"),
    ("superseded_claim", "TEXT"),
)


def init_schema(db) -> None:
    """Create the scheduled-jobs table if absent, and add newer columns."""
    db.execute(EMAIL_SCHEDULED_JOBS_DDL)
    columns = {r["name"] for r in db.query("PRAGMA table_info(email_scheduled_jobs)")}
    for name, sql_type in _LEASE_COLUMNS:
        if name in columns:
            continue
        try:
            db.execute(f"ALTER TABLE email_scheduled_jobs ADD COLUMN {name} {sql_type}")
        except sqlite3.OperationalError as e:
            # Another connection (the scheduler, a second agent) added it first.
            if "duplicate column name" not in str(e):
                raise
    # A row left firing by a build that recorded no claim time starts its
    # stale window now, rather than counting as hours old on sight.
    db.update(
        "email_scheduled_jobs",
        {"claimed_at": time.time()},
        "status = :firing AND claimed_at IS NULL",
        {"firing": STATUS_FIRING},
    )


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
        "claim_id": row["claim_id"],
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


def claim_job(db, *, job_id: str, now: Optional[float] = None) -> Optional[str]:
    """Atomically transition a job ``pending -> firing``.

    Returns the claim's ``claim_id`` token when this caller won, else None
    (already claimed, fired, cancelled, or unknown). The status guard in the
    WHERE clause is what makes a job fire exactly once. Pass the token to
    ``renew_claim`` and ``mark_fired`` / ``mark_failed``.
    """
    claim_id = uuid.uuid4().hex
    affected = db.update(
        "email_scheduled_jobs",
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
        "email_scheduled_jobs",
        {"claimed_at": now},
        "job_id = :id AND status = :firing AND claim_id = :claim",
        {"id": job_id, "firing": STATUS_FIRING, "claim": claim_id},
    )
    return affected == 1


# The holder of a claim may still report its outcome after another driver
# recovered the job, as long as the job was not claimed again since: the
# holder knows what really happened, and the recovery only guessed.
_HOLDER_GUARD = (
    "job_id = :id AND ((status = :firing AND claim_id = :claim) OR "
    "(superseded_claim = :claim AND status IN ({statuses})))"
)


def mark_fired(db, *, job_id: str, claim_id: str) -> bool:
    """Record a successful fire. False if the claim was taken over meanwhile.

    Also succeeds over a recovery that failed or re-queued the job, so a send
    that did go out is never left looking failed.
    """
    affected = db.update(
        "email_scheduled_jobs",
        {
            "status": STATUS_FIRED,
            "fired_at": time.time(),
            "error": None,
            "claim_id": None,
            "superseded_claim": None,
        },
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


def mark_failed(db, *, job_id: str, claim_id: str, error: str) -> bool:
    """Record a failed fire. False if the claim was taken over meanwhile.

    Replaces a recovery's generic failure with the real error, but never
    overrides a re-queued job: its re-run decides the outcome.
    """
    affected = db.update(
        "email_scheduled_jobs",
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


#: Kinds that are safe to run a second time. A snooze restore only puts a
#: message back in the inbox; a scheduled send would send twice.
RERUNNABLE_KINDS = frozenset({KIND_SNOOZE})


def find_stale_firing(db, *, now: float, stale_after: float) -> List[Dict[str, Any]]:
    """Every ``firing`` job whose lease has not been renewed in ``stale_after``."""
    rows = db.query(
        "SELECT * FROM email_scheduled_jobs WHERE status = :firing "
        "AND (claimed_at IS NULL OR claimed_at <= :cutoff)",
        {"firing": STATUS_FIRING, "cutoff": now - stale_after},
    )
    return [_row_to_job(r) for r in rows or ()]


def recover_claim(db, job: Dict[str, Any], *, now: float) -> Optional[str]:
    """Move on from an abandoned lease. Returns ``"requeued"``, ``"failed"``,
    or None if the lease changed since ``job`` was read (it is alive).

    A snooze goes back to ``pending`` and fires on the next pass. Any other
    kind, a scheduled send above all, is marked ``failed`` with the reason and
    never run again: the send may already have gone out. If the original
    holder turns out to be alive, its own ``mark_fired`` / ``mark_failed``
    still lands (see ``superseded_claim``).
    """
    claim_id, claimed = job["claim_id"], job["claimed_at"]
    guard = "job_id = :id AND status = :firing AND " + " AND ".join(
        (
            "claim_id IS NULL" if claim_id is None else "claim_id = :claim",
            "claimed_at IS NULL" if claimed is None else "claimed_at = :claimed",
        )
    )
    when = "at an unknown time" if claimed is None else f"at {claimed:.0f}"
    if job["kind"] in RERUNNABLE_KINDS:
        values = {"status": STATUS_PENDING, "claimed_at": None}
        outcome = "requeued"
    else:
        values = {
            "status": STATUS_FAILED,
            "fired_at": now,
            "error": (
                f"interrupted while firing (last heartbeat {when}). Not run "
                "again because it may already have taken effect; check the "
                "mailbox and schedule it again if it did not."
            ),
        }
        outcome = "failed"
    values.update({"claim_id": None, "superseded_claim": claim_id})
    affected = db.update(
        "email_scheduled_jobs",
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


def list_recent_failures(db, *, since: float) -> List[Dict[str, Any]]:
    """Jobs that failed at or after ``since``, newest first."""
    rows = db.query(
        "SELECT * FROM email_scheduled_jobs WHERE status = :failed "
        "AND fired_at >= :since ORDER BY fired_at DESC",
        {"failed": STATUS_FAILED, "since": since},
    )
    return [_row_to_job(r) for r in rows or ()]


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
    "RERUNNABLE_KINDS",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_FIRED",
    "STATUS_FIRING",
    "STATUS_PENDING",
    "cancel_job",
    "claim_job",
    "create_job",
    "fetch_due",
    "find_stale_firing",
    "get_job",
    "init_schema",
    "list_jobs",
    "list_recent_failures",
    "mark_fired",
    "mark_failed",
    "recover_claim",
    "renew_claim",
]
