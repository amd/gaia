# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``em_inbox.db`` — the EM input queue.

Backs §4.5 of ``docs/plans/coder-agent.mdx``. Every inbound message from the
bound Engineering Manager (CLI, TUI, gh-comment, email, daily-standup reply)
lands here first and is graduated into ``feedback.db`` only if the triage
classifier decides it is feedback-class.

The DDL below is the §15.1 schema verbatim.
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
CREATE TABLE em_inbox (
  id                 TEXT PRIMARY KEY,               -- UUIDv7
  received_at        TEXT NOT NULL,                  -- ISO-8601 UTC
  from_handle        TEXT NOT NULL,                  -- GitHub handle
  channel            TEXT NOT NULL                   -- cli | tui | gh-comment | email | daily-standup-reply
                     CHECK (channel IN ('cli','tui','gh-comment','email','daily-standup-reply')),
  severity           TEXT NOT NULL
                     CHECK (severity IN ('info','question','critical')),
  body               TEXT NOT NULL,
  state              TEXT NOT NULL DEFAULT 'pending' -- pending → seen → (answered|escalated) → closed
                     CHECK (state IN ('pending','seen','answered','escalated','closed')),
  answer             TEXT,
  escalated_to       TEXT,                           -- feedback.id if reclassified
  ack_sent_at        TEXT,                           -- < 5s after received_at (§10.6)
  answered_at        TEXT,
  closed_at          TEXT
);
CREATE INDEX idx_em_inbox_state ON em_inbox(state);
CREATE INDEX idx_em_inbox_received ON em_inbox(received_at);
"""

TABLE = "em_inbox"


class EmInboxRow(BaseModel):
    """Row mirror of the ``em_inbox`` table."""

    id: str
    received_at: str
    from_handle: str
    channel: str = Field(
        description="cli | tui | gh-comment | email | daily-standup-reply"
    )
    severity: str = Field(description="info | question | critical")
    body: str
    state: str = Field(
        default="pending",
        description="pending | seen | answered | escalated | closed",
    )
    answer: Optional[str] = None
    escalated_to: Optional[str] = None
    ack_sent_at: Optional[str] = None
    answered_at: Optional[str] = None
    closed_at: Optional[str] = None


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the ``em_inbox`` table and its indices."""
    exec_script(conn, DDL)


def open_store(db_path: str | Path) -> sqlite3.Connection:
    """Open ``em_inbox.db`` with canonical PRAGMAs, creating tables on first use."""
    conn = open_connection(db_path)
    # ``CREATE TABLE`` without ``IF NOT EXISTS`` is verbatim from §15.1. We skip
    # re-running the DDL if the table is already present so callers can reopen
    # the store freely.
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if cur.fetchone() is None:
        create_tables(conn)
    return conn


def insert_row(conn: sqlite3.Connection, row: EmInboxRow) -> None:
    """Insert an ``EmInboxRow``."""
    insert(conn, TABLE, row.model_dump())


def get_row(conn: sqlite3.Connection, row_id: str) -> EmInboxRow | None:
    """Fetch a row by primary key."""
    raw = fetch_one(conn, TABLE, {"id": row_id})
    return EmInboxRow(**raw) if raw is not None else None


def update_row(
    conn: sqlite3.Connection,
    row_id: str,
    patch: Mapping[str, Any],
) -> int:
    """Apply ``patch`` to the row with matching ``id``. Returns rows affected."""
    return update(conn, TABLE, {"id": row_id}, patch)


def list_rows(
    conn: sqlite3.Connection,
    filter: Mapping[str, Any] | None = None,
) -> list[EmInboxRow]:
    """List rows matching the equality filter (most recent first)."""
    rows = fetch_all(conn, TABLE, filter, order_by="received_at DESC")
    return [EmInboxRow(**r) for r in rows]
