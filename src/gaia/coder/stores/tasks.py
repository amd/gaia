# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``tasks.db`` — the coder's task queue.

Backs §6.3 of ``docs/plans/coder-agent.mdx``. Every engineering task the agent
picks up lives here through its full lifecycle (pending → running → waiting →
blocked → paused → done → abandoned).
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
CREATE TABLE tasks (
  id                 TEXT PRIMARY KEY,               -- UUIDv7
  priority           INTEGER NOT NULL DEFAULT 50,    -- 0 (lowest) .. 100 (highest)
  state              TEXT NOT NULL DEFAULT 'pending'
                     CHECK (state IN ('pending','running','waiting','blocked','paused','done','abandoned')),
  created_at         TEXT NOT NULL,
  last_heartbeat_at  TEXT,
  stage              TEXT,                           -- current §5.1 stage name
  current_state_name TEXT,                           -- current §5.1 state
  inputs_json        TEXT NOT NULL,                  -- original prompt + params
  result_json        TEXT,                           -- summary + artifacts
  trace_file         TEXT,                           -- path to JSONL trace under sessions/
  cost_usd           REAL NOT NULL DEFAULT 0.0,
  loop_version       INTEGER NOT NULL                -- §7.8 state-machine version
);
CREATE INDEX idx_tasks_state ON tasks(state);
CREATE INDEX idx_tasks_priority ON tasks(priority DESC, created_at);
"""

TABLE = "tasks"


class TaskRow(BaseModel):
    """Row mirror of the ``tasks`` table."""

    id: str
    priority: int = 50
    state: str = Field(
        default="pending",
        description="pending | running | waiting | blocked | paused | done | abandoned",
    )
    created_at: str
    last_heartbeat_at: Optional[str] = None
    stage: Optional[str] = None
    current_state_name: Optional[str] = None
    inputs_json: str
    result_json: Optional[str] = None
    trace_file: Optional[str] = None
    cost_usd: float = 0.0
    loop_version: int


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the ``tasks`` table and its indices."""
    exec_script(conn, DDL)


def open_store(db_path: str | Path) -> sqlite3.Connection:
    """Open ``tasks.db`` with canonical PRAGMAs, creating tables on first use."""
    conn = open_connection(db_path)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if cur.fetchone() is None:
        create_tables(conn)
    return conn


def insert_row(conn: sqlite3.Connection, row: TaskRow) -> None:
    """Insert a ``TaskRow``."""
    insert(conn, TABLE, row.model_dump())


def get_row(conn: sqlite3.Connection, row_id: str) -> TaskRow | None:
    """Fetch a task by primary key."""
    raw = fetch_one(conn, TABLE, {"id": row_id})
    return TaskRow(**raw) if raw is not None else None


def update_row(
    conn: sqlite3.Connection,
    row_id: str,
    patch: Mapping[str, Any],
) -> int:
    """Apply ``patch`` to the task with matching ``id``. Returns rows affected."""
    return update(conn, TABLE, {"id": row_id}, patch)


def list_rows(
    conn: sqlite3.Connection,
    filter: Mapping[str, Any] | None = None,
) -> list[TaskRow]:
    """List tasks matching the equality filter (priority DESC, created_at ASC)."""
    rows = fetch_all(conn, TABLE, filter, order_by="priority DESC, created_at")
    return [TaskRow(**r) for r in rows]
