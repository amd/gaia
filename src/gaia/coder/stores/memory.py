# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``memory.db`` — the relational side of the hybrid memory store.

Backs §6.8 and §15.1 of ``docs/plans/coder-agent.mdx``. Production-plan is a
single ``memory`` table keyed by ``topic``, with one FAISS index file per
topic co-located alongside this SQLite database. **FAISS wiring is explicitly
out of scope for this module** (Phase 10, §6.8); we only create the SQL side
and expose an ``embedding_key`` column that later phases will populate.

Topics (CHECK constraint — 8 values):

* ``review_patterns`` — cross-review learnings (§8).
* ``failure_patterns`` — canonical failure signatures (§7.2).
* ``flaky_tests`` — known flakes with evidence.
* ``em_preferences`` — EM-specific style rules (§4.4).
* ``adr_decisions`` — architectural decisions (ADR log).
* ``tool_usage_heuristics`` — when-to-use-what rules per tool.
* ``task_outcomes`` — what-worked / what-didn't per task.
* ``mutation_seeds`` — §8.4 adversarial mutation seeds.
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
-- One table per topic for simple cases; in production these are views over a single `memory` table
-- keyed by topic for FAISS co-location.
CREATE TABLE memory (
  id               TEXT PRIMARY KEY,         -- UUIDv7
  topic            TEXT NOT NULL             -- review_patterns | failure_patterns | flaky_tests | em_preferences | adr_decisions | tool_usage_heuristics | task_outcomes | mutation_seeds
                   CHECK (topic IN ('review_patterns','failure_patterns','flaky_tests',
                                    'em_preferences','adr_decisions','tool_usage_heuristics',
                                    'task_outcomes','mutation_seeds')),
  created_at       TEXT NOT NULL,
  source_kind      TEXT NOT NULL,            -- feedback | pr | issue | task | event | audit | reconcile
  source_id        TEXT,                     -- FK-like reference into feedback/tasks/audit
  payload_json     TEXT NOT NULL,            -- topic-specific schema (see §6.8.1)
  embedding_key    TEXT NOT NULL,            -- FAISS id
  confidence       INTEGER NOT NULL DEFAULT 80
                   CHECK (confidence BETWEEN 0 AND 100),
  last_recalled_at TEXT,
  recall_count     INTEGER NOT NULL DEFAULT 0,
  superseded_by    TEXT                      -- FK memory.id; soft-replacement
);
CREATE INDEX idx_memory_topic ON memory(topic);
CREATE INDEX idx_memory_recalled ON memory(last_recalled_at);
"""

TABLE = "memory"


class MemoryRow(BaseModel):
    """Row mirror of the ``memory`` table."""

    id: str
    topic: str = Field(
        description=(
            "review_patterns | failure_patterns | flaky_tests | em_preferences | "
            "adr_decisions | tool_usage_heuristics | task_outcomes | mutation_seeds"
        )
    )
    created_at: str
    source_kind: str = Field(
        description="feedback | pr | issue | task | event | audit | reconcile"
    )
    source_id: Optional[str] = None
    payload_json: str
    embedding_key: str = Field(description="FAISS id; wired up in Phase 10")
    confidence: int = 80
    last_recalled_at: Optional[str] = None
    recall_count: int = 0
    superseded_by: Optional[str] = None


def create_tables(conn: sqlite3.Connection) -> None:
    """Create the ``memory`` table and its indices."""
    exec_script(conn, DDL)


def open_store(db_path: str | Path) -> sqlite3.Connection:
    """Open ``memory.db`` with canonical PRAGMAs, creating tables on first use."""
    conn = open_connection(db_path)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    )
    if cur.fetchone() is None:
        create_tables(conn)
    return conn


def insert_row(conn: sqlite3.Connection, row: MemoryRow) -> None:
    """Insert a ``MemoryRow``.

    FAISS indexing is intentionally left to Phase 10; callers supply a
    pre-computed ``embedding_key`` so the relational side is complete now.
    """
    insert(conn, TABLE, row.model_dump())


def get_row(conn: sqlite3.Connection, row_id: str) -> MemoryRow | None:
    """Fetch a memory row by primary key."""
    raw = fetch_one(conn, TABLE, {"id": row_id})
    return MemoryRow(**raw) if raw is not None else None


def update_row(
    conn: sqlite3.Connection,
    row_id: str,
    patch: Mapping[str, Any],
) -> int:
    """Apply ``patch`` to the memory row with matching ``id``. Returns rows affected."""
    return update(conn, TABLE, {"id": row_id}, patch)


def list_rows(
    conn: sqlite3.Connection,
    filter: Mapping[str, Any] | None = None,
) -> list[MemoryRow]:
    """List memory rows matching the equality filter (most recent first)."""
    rows = fetch_all(conn, TABLE, filter, order_by="created_at DESC")
    return [MemoryRow(**r) for r in rows]
