# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``audit.log.db`` — append-only tool-call audit log.

Backs §15.1 of ``docs/plans/coder-agent.mdx``. Every tool call the agent makes
appends one row here: the tool name, arguments, result, duration, error (if
any), plus the loop version and state it ran under. This is the ground-truth
timeline used by §7.7 introspection and §8 review passes.

The primary key is ``INTEGER PRIMARY KEY AUTOINCREMENT``, so ``insert_row``
does not require an ``id`` — the database assigns one and we return it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel

from gaia.coder.stores._common import (
    exec_script,
    fetch_all,
    fetch_one,
    open_connection,
    update,
)

DDL = """
CREATE TABLE audit (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at  TEXT NOT NULL,
  task_id      TEXT,                        -- FK tasks.id; NULL outside tasks
  stage        TEXT,                        -- §5.1 stage name
  state_name   TEXT,                        -- §5.1 current state
  tool_name    TEXT NOT NULL,
  args_json    TEXT NOT NULL,
  result_json  TEXT,
  duration_ms  INTEGER,
  error        TEXT,                        -- exception class name; NULL on success
  loop_version INTEGER NOT NULL
);
CREATE INDEX idx_audit_occurred ON audit(occurred_at);
CREATE INDEX idx_audit_task ON audit(task_id);
CREATE INDEX idx_audit_tool ON audit(tool_name);
"""

TABLE = "audit"


class AuditRow(BaseModel):
    """Row mirror of the ``audit`` table."""

    id: Optional[int] = None
    occurred_at: str
    task_id: Optional[str] = None
    stage: Optional[str] = None
    state_name: Optional[str] = None
    tool_name: str
    args_json: str
    result_json: Optional[str] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    loop_version: int


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the ``audit`` table and its indices."""
    exec_script(conn, DDL)


def open_store(db_path: str | Path) -> sqlite3.Connection:
    """Open ``audit.log.db`` with canonical PRAGMAs, creating tables on first use."""
    conn = open_connection(db_path)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if cur.fetchone() is None:
        create_tables(conn)
    return conn


def insert_row(conn: sqlite3.Connection, row: AuditRow) -> int:
    """Insert an ``AuditRow`` and return the auto-generated id.

    ``row.id`` is ignored — the database assigns it. The returned integer is
    the new row's ``id`` (``lastrowid``).
    """
    payload = row.model_dump(exclude={"id"})
    columns = list(payload.keys())
    placeholders = ", ".join("?" for _ in columns)
    cols_sql = ", ".join(columns)
    sql = f"INSERT INTO {TABLE} ({cols_sql}) VALUES ({placeholders})"
    with conn:
        cur = conn.execute(sql, [payload[c] for c in columns])
        row_id = cur.lastrowid
    # SQLite guarantees a positive integer rowid after INSERT into an
    # AUTOINCREMENT column; surface loudly if somehow not.
    if row_id is None:
        raise RuntimeError("audit insert did not return a lastrowid")
    return row_id


def get_row(conn: sqlite3.Connection, row_id: int) -> AuditRow | None:
    """Fetch an audit row by integer primary key."""
    raw = fetch_one(conn, TABLE, {"id": row_id})
    return AuditRow(**raw) if raw is not None else None


def update_row(
    conn: sqlite3.Connection,
    row_id: int,
    patch: Mapping[str, Any],
) -> int:
    """Apply ``patch`` to the audit row with matching ``id``. Returns rows affected.

    Audit is conceptually append-only; callers should generally treat rows as
    immutable. ``update_row`` is provided for late-arriving data (e.g., filling
    in ``duration_ms`` after a tool completes) and for tests.
    """
    return update(conn, TABLE, {"id": row_id}, patch)


def list_rows(
    conn: sqlite3.Connection,
    filter: Mapping[str, Any] | None = None,
) -> list[AuditRow]:
    """List audit rows matching the equality filter (oldest first)."""
    rows = fetch_all(conn, TABLE, filter, order_by="occurred_at, id")
    return [AuditRow(**r) for r in rows]
