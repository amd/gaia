# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``feedback.db`` — the feedback queue graduated from ``em_inbox``.

Backs §7.3 of ``docs/plans/coder-agent.mdx``. Every EM-authored critique that
the triage classifier accepts as feedback lives here through its full fix
lifecycle. The ``fix_class`` CHECK lists **eight** values — the seven agent-fix
classes plus ``out-of-scope``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field

from gaia.coder.stores._common import (
    exec_script,
    fetch_all,
    fetch_one,
    insert,
    open_connection,
    update,
)

DDL = """
CREATE TABLE feedback (
  id                    TEXT PRIMARY KEY,            -- UUIDv7
  received_at           TEXT NOT NULL,
  from_handle           TEXT NOT NULL,               -- bound EM or pre-authorised reviewer
  channel               TEXT NOT NULL,
  severity              TEXT NOT NULL
                        CHECK (severity IN ('low','med','high','critical')),
  body                  TEXT NOT NULL,
  context_url           TEXT,                        -- PR/issue/commit URL
  fix_class             TEXT                         -- prompt|doc|test|tool|policy|architectural|state-machine|out-of-scope
                        CHECK (fix_class IN ('prompt','doc','test','tool','policy','architectural','state-machine','out-of-scope')),
  state                 TEXT NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending','triaged','in-fix','fix-pr-open','verified','rejected','closed')),
  fix_pr_url            TEXT,
  regression_test_path  TEXT,                        -- required non-null for state='verified'
  root_cause            TEXT,
  success_criterion     TEXT,                        -- from Stage 3 §5.1 plan
  notes_json            TEXT NOT NULL DEFAULT '[]'   -- append-only state-transition audit
);
CREATE INDEX idx_feedback_state ON feedback(state);
CREATE INDEX idx_feedback_fix_class ON feedback(fix_class);
"""

TABLE = "feedback"


class FeedbackRow(BaseModel):
    """Row mirror of the ``feedback`` table."""

    id: str
    received_at: str
    from_handle: str
    channel: str
    severity: str = Field(description="low | med | high | critical")
    body: str
    context_url: Optional[str] = None
    fix_class: Optional[str] = Field(
        default=None,
        description=(
            "prompt | doc | test | tool | policy | architectural | "
            "state-machine | out-of-scope"
        ),
    )
    state: str = Field(
        default="pending",
        description=(
            "pending | triaged | in-fix | fix-pr-open | verified | rejected | closed"
        ),
    )
    fix_pr_url: Optional[str] = None
    regression_test_path: Optional[str] = None
    root_cause: Optional[str] = None
    success_criterion: Optional[str] = None
    notes_json: str = "[]"


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the ``feedback`` table and its indices."""
    exec_script(conn, DDL)


def open_store(db_path: str | Path) -> sqlite3.Connection:
    """Open ``feedback.db`` with canonical PRAGMAs, creating tables on first use."""
    conn = open_connection(db_path)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if cur.fetchone() is None:
        create_tables(conn)
    return conn


def insert_row(conn: sqlite3.Connection, row: FeedbackRow) -> None:
    """Insert a ``FeedbackRow``."""
    insert(conn, TABLE, row.model_dump())


def get_row(conn: sqlite3.Connection, row_id: str) -> FeedbackRow | None:
    """Fetch feedback by primary key."""
    raw = fetch_one(conn, TABLE, {"id": row_id})
    return FeedbackRow(**raw) if raw is not None else None


def update_row(
    conn: sqlite3.Connection,
    row_id: str,
    patch: Mapping[str, Any],
) -> int:
    """Apply ``patch`` to feedback with matching ``id``. Returns rows affected."""
    return update(conn, TABLE, {"id": row_id}, patch)


def list_rows(
    conn: sqlite3.Connection,
    filter: Mapping[str, Any] | None = None,
) -> list[FeedbackRow]:
    """List feedback matching the equality filter (most recent first)."""
    rows = fetch_all(conn, TABLE, filter, order_by="received_at DESC")
    return [FeedbackRow(**r) for r in rows]
