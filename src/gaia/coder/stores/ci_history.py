# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``ci_history.db`` — workflow-duration cache.

Backs §6.2 of ``docs/plans/coder-agent.mdx``. Stores one row per GitHub Actions
run so the agent can estimate expected duration for a workflow on a given
branch without re-querying the API. The primary key is a compound
``(workflow_name, branch, run_id)``.
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
CREATE TABLE ci_history (
  workflow_name  TEXT NOT NULL,
  branch         TEXT NOT NULL,
  run_id         INTEGER NOT NULL,
  started_at     TEXT NOT NULL,
  completed_at   TEXT,
  duration_s     INTEGER,
  conclusion     TEXT,                      -- success | failure | cancelled | timed_out
  PRIMARY KEY (workflow_name, branch, run_id)
);
"""

TABLE = "ci_history"


class CiHistoryRow(BaseModel):
    """Row mirror of the ``ci_history`` table."""

    workflow_name: str
    branch: str
    run_id: int
    started_at: str
    completed_at: Optional[str] = None
    duration_s: Optional[int] = None
    conclusion: Optional[str] = None


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the ``ci_history`` table."""
    exec_script(conn, DDL)


def open_store(db_path: str | Path) -> sqlite3.Connection:
    """Open ``ci_history.db`` with canonical PRAGMAs, creating tables on first use."""
    conn = open_connection(db_path)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if cur.fetchone() is None:
        create_tables(conn)
    return conn


def _pk_filter(workflow_name: str, branch: str, run_id: int) -> dict[str, Any]:
    return {"workflow_name": workflow_name, "branch": branch, "run_id": run_id}


def insert_row(conn: sqlite3.Connection, row: CiHistoryRow) -> None:
    """Insert a ``CiHistoryRow``."""
    insert(conn, TABLE, row.model_dump())


def get_row(
    conn: sqlite3.Connection,
    workflow_name: str,
    branch: str,
    run_id: int,
) -> CiHistoryRow | None:
    """Fetch by compound primary key."""
    raw = fetch_one(conn, TABLE, _pk_filter(workflow_name, branch, run_id))
    return CiHistoryRow(**raw) if raw is not None else None


def update_row(
    conn: sqlite3.Connection,
    workflow_name: str,
    branch: str,
    run_id: int,
    patch: Mapping[str, Any],
) -> int:
    """Apply ``patch`` to the row keyed by ``(workflow_name, branch, run_id)``."""
    return update(conn, TABLE, _pk_filter(workflow_name, branch, run_id), patch)


def list_rows(
    conn: sqlite3.Connection,
    filter: Mapping[str, Any] | None = None,
) -> list[CiHistoryRow]:
    """List CI history rows matching the equality filter (most recent first)."""
    rows = fetch_all(conn, TABLE, filter, order_by="started_at DESC")
    return [CiHistoryRow(**r) for r in rows]
