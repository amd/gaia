# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``spend.db`` — cost ledger for every Anthropic API call.

Backs §6.6 of ``docs/plans/coder-agent.mdx``. One row per model call — input,
cache, and output tokens plus the resolved USD amount. Used by the budget
tooling and the weekly spend summary.
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
    insert,
    open_connection,
    update,
)

DDL = """
CREATE TABLE spend (
  id             TEXT PRIMARY KEY,          -- UUIDv7
  occurred_at    TEXT NOT NULL,
  task_id        TEXT,                      -- FK tasks.id; NULL for non-task calls
  call_site      TEXT NOT NULL,             -- 'triage' | 'plan' | 'pass_6_adversarial' | 'continuous_critique' | etc.
  model          TEXT NOT NULL,             -- 'claude-opus-4-7'
  input_tokens   INTEGER NOT NULL,
  cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
  cache_create_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens  INTEGER NOT NULL,
  usd            REAL NOT NULL
);
CREATE INDEX idx_spend_occurred ON spend(occurred_at);
CREATE INDEX idx_spend_task ON spend(task_id);
"""

TABLE = "spend"


class SpendRow(BaseModel):
    """Row mirror of the ``spend`` table."""

    id: str
    occurred_at: str
    task_id: Optional[str] = None
    call_site: str
    model: str
    input_tokens: int
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    output_tokens: int
    usd: float


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the ``spend`` table and its indices."""
    exec_script(conn, DDL)


def open_store(db_path: str | Path) -> sqlite3.Connection:
    """Open ``spend.db`` with canonical PRAGMAs, creating tables on first use."""
    conn = open_connection(db_path)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if cur.fetchone() is None:
        create_tables(conn)
    return conn


def insert_row(conn: sqlite3.Connection, row: SpendRow) -> None:
    """Insert a ``SpendRow``."""
    insert(conn, TABLE, row.model_dump())


def get_row(conn: sqlite3.Connection, row_id: str) -> SpendRow | None:
    """Fetch a spend entry by primary key."""
    raw = fetch_one(conn, TABLE, {"id": row_id})
    return SpendRow(**raw) if raw is not None else None


def update_row(
    conn: sqlite3.Connection,
    row_id: str,
    patch: Mapping[str, Any],
) -> int:
    """Apply ``patch`` to the spend entry. Returns rows affected."""
    return update(conn, TABLE, {"id": row_id}, patch)


def list_rows(
    conn: sqlite3.Connection,
    filter: Mapping[str, Any] | None = None,
) -> list[SpendRow]:
    """List spend entries matching the equality filter (most recent first)."""
    rows = fetch_all(conn, TABLE, filter, order_by="occurred_at DESC")
    return [SpendRow(**r) for r in rows]
