# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The last autonomy cycle's outcome, persisted so it outlives the request that
ran it (#2618).

A cycle's report used to exist only as the HTTP response body of
``POST /v1/email/agent/autonomy/run``. When the caller's read timed out, or a
scheduler ran the cycle with no caller at all, the report was simply lost:
"ran and found nothing to do" looked the same as "never ran". Every cycle now
writes one summary row to the shared ``state.db`` (the same file the trust
ledger and kill flag live in), and ``autonomy_status`` reads it back.

Pure functions over a ``DatabaseMixin``-typed first argument, mirroring
``autonomy_kill``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

# Single-row table (id is always 1): only the most recent cycle is kept. A
# NULL summary means no cycle has run against this state.db yet.
EMAIL_AUTONOMY_LAST_CYCLE_DDL = """
CREATE TABLE IF NOT EXISTS email_autonomy_last_cycle (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    finished_at REAL,
    summary     TEXT
);
"""

#: ``outcome`` values a summary can carry.
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"


def init_schema(db) -> None:
    """Create the last-cycle table and seed its single empty row. Idempotent."""
    db.execute(EMAIL_AUTONOMY_LAST_CYCLE_DDL)
    db.execute(
        "INSERT OR IGNORE INTO email_autonomy_last_cycle (id, finished_at, summary) "
        "VALUES (1, NULL, NULL);"
    )


def summarize_report(
    report: Dict[str, Any], *, started_at: float, finished_at: float
) -> Dict[str, Any]:
    """Counts-only summary of a completed cycle's report — no message bodies,
    senders, or ids, so the persisted row stays small and mailbox-free."""
    return {
        "outcome": OUTCOME_COMPLETED,
        "started_at": started_at,
        "finished_at": finished_at,
        "level": report.get("level"),
        "executed": len(report.get("executed") or []),
        "proposals": len(report.get("proposals") or []),
        "decisions": len(report.get("decisions") or []),
        "skipped": int(report.get("skipped") or 0),
        "already_proposed": int(report.get("already_proposed") or 0),
        "errors": len(report.get("errors") or []),
        "stopped": report.get("stopped"),
    }


def summarize_failure(
    level: Optional[str], error: str, *, started_at: float, finished_at: float
) -> Dict[str, Any]:
    """Summary of a cycle that raised before producing a report."""
    return {
        "outcome": OUTCOME_FAILED,
        "started_at": started_at,
        "finished_at": finished_at,
        "level": level,
        "error": error,
    }


def record(db, summary: Dict[str, Any]) -> None:
    """Replace the stored last-cycle summary with *summary*."""
    db.update(
        "email_autonomy_last_cycle",
        {"finished_at": summary["finished_at"], "summary": json.dumps(summary)},
        "id = :id",
        {"id": 1},
    )


def read_last(db) -> Optional[Dict[str, Any]]:
    """The most recent cycle's summary, or ``None`` when no cycle has run."""
    row = db.query(
        "SELECT summary FROM email_autonomy_last_cycle WHERE id = 1", one=True
    )
    if not row or row["summary"] is None:
        return None
    return json.loads(row["summary"])


__all__ = [
    "EMAIL_AUTONOMY_LAST_CYCLE_DDL",
    "OUTCOME_COMPLETED",
    "OUTCOME_FAILED",
    "init_schema",
    "read_last",
    "record",
    "summarize_failure",
    "summarize_report",
]
