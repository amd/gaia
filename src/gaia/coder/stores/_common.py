# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared SQLite helpers for ``gaia.coder.stores``.

Every SQLite-backed store in this package uses :func:`open_connection` to get a
properly-configured :class:`sqlite3.Connection`: WAL journalling, foreign keys
enforced, and a 5-second busy timeout. This matches the behaviour required by
§15.1 of ``docs/plans/coder-agent.mdx``.

The module also exposes small CRUD primitives that individual store modules
wrap with typed signatures. The primitives intentionally stay untyped at the
row level — each store attaches its own Pydantic model for that.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping


def open_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with the store's canonical PRAGMAs.

    PRAGMAs applied:

    * ``journal_mode=WAL`` — write-ahead logging for concurrent readers.
    * ``foreign_keys=ON`` — enforce declared FK constraints.
    * ``busy_timeout=5000`` — 5-second wait when the DB is locked.

    The parent directory of ``db_path`` is created if it does not exist so
    callers can point at a nested path under ``~/.gaia/coder/`` without first
    making the directory themselves.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def exec_script(conn: sqlite3.Connection, ddl: str) -> None:
    """Execute a multi-statement DDL script inside a transaction."""
    with conn:
        conn.executescript(ddl)


def _compile_where(filter_: Mapping[str, Any] | None) -> tuple[str, list[Any]]:
    """Build a ``WHERE`` clause and parameter list from an equality filter.

    Empty/``None`` filter returns ``("", [])`` so callers can concatenate
    unconditionally. Values are passed as positional parameters — no string
    interpolation — so this is safe against injection.
    """
    if not filter_:
        return "", []
    parts = []
    params: list[Any] = []
    for key, value in filter_.items():
        if value is None:
            parts.append(f"{key} IS NULL")
        else:
            parts.append(f"{key} = ?")
            params.append(value)
    return " WHERE " + " AND ".join(parts), params


def insert(conn: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> None:
    """Insert a single row into ``table``. All columns present in ``row`` are written."""
    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    cols_sql = ", ".join(columns)
    sql = f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders})"
    with conn:
        conn.execute(sql, [row[c] for c in columns])


def fetch_one(
    conn: sqlite3.Connection,
    table: str,
    filter_: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Fetch at most one row from ``table`` matching the equality filter."""
    where, params = _compile_where(filter_)
    sql = f"SELECT * FROM {table}{where} LIMIT 1"
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row is not None else None


def fetch_all(
    conn: sqlite3.Connection,
    table: str,
    filter_: Mapping[str, Any] | None = None,
    *,
    order_by: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch every row from ``table`` matching the equality filter."""
    where, params = _compile_where(filter_)
    order_sql = f" ORDER BY {order_by}" if order_by else ""
    sql = f"SELECT * FROM {table}{where}{order_sql}"
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def update(
    conn: sqlite3.Connection,
    table: str,
    filter_: Mapping[str, Any],
    patch: Mapping[str, Any],
) -> int:
    """Update rows matching ``filter_`` with values from ``patch``.

    Returns the number of rows affected. Raises :class:`ValueError` if the
    patch is empty (prevents accidental no-op UPDATEs that hide caller bugs).
    """
    if not patch:
        raise ValueError("update requires at least one column in patch")
    set_clause = ", ".join(f"{k} = ?" for k in patch.keys())
    where, where_params = _compile_where(filter_)
    sql = f"UPDATE {table} SET {set_clause}{where}"
    params: Iterable[Any] = list(patch.values()) + where_params
    with conn:
        cur = conn.execute(sql, list(params))
        return cur.rowcount
