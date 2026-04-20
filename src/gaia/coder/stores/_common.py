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

import re
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


_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str, *, kind: str = "identifier") -> str:
    """Validate an SQL identifier (table/column name).

    SQLite has no built-in parameterisation for identifiers; whitelist matching
    is the only safe way to interpolate them into SQL. Raises ``ValueError``
    on anything that isn't a plain snake-case identifier. Per CLAUDE.md
    fail-loudly rule, no silent rejection.
    """
    if not isinstance(name, str) or not _SAFE_IDENT_RE.match(name):
        raise ValueError(
            f"unsafe SQL {kind}: {name!r} — must match /^[A-Za-z_][A-Za-z0-9_]*$/"
        )
    return name


def _compile_where(filter_: Mapping[str, Any] | None) -> tuple[str, list[Any]]:
    """Build a ``WHERE`` clause and parameter list from an equality filter.

    Empty/``None`` filter returns ``("", [])`` so callers can concatenate
    unconditionally. Column names are validated via :func:`_safe_ident`
    (SQLite does not parameterise identifiers); values are passed as
    positional parameters.
    """
    if not filter_:
        return "", []
    parts = []
    params: list[Any] = []
    for key, value in filter_.items():
        col = _safe_ident(key, kind="column")
        if value is None:
            parts.append(f"{col} IS NULL")
        else:
            parts.append(f"{col} = ?")
            params.append(value)
    return " WHERE " + " AND ".join(parts), params


def insert(conn: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> None:
    """Insert a single row into ``table``. All columns present in ``row`` are written."""
    tbl = _safe_ident(table, kind="table")
    columns = [_safe_ident(c, kind="column") for c in row.keys()]
    placeholders = ", ".join("?" for _ in columns)
    cols_sql = ", ".join(columns)
    sql = f"INSERT INTO {tbl} ({cols_sql}) VALUES ({placeholders})"
    with conn:
        conn.execute(sql, [row[c] for c in row.keys()])


def fetch_one(
    conn: sqlite3.Connection,
    table: str,
    filter_: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Fetch at most one row from ``table`` matching the equality filter."""
    tbl = _safe_ident(table, kind="table")
    where, params = _compile_where(filter_)
    sql = f"SELECT * FROM {tbl}{where} LIMIT 1"
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
    tbl = _safe_ident(table, kind="table")
    where, params = _compile_where(filter_)
    # order_by may be "col" | "col ASC|DESC" | "col1 ASC, col2 DESC" — validate
    # each comma-separated clause.
    order_sql = ""
    if order_by:
        for clause in (c.strip() for c in order_by.split(",")):
            parts = clause.split()
            if not parts:
                raise ValueError(f"empty ORDER BY clause: {order_by!r}")
            _safe_ident(parts[0], kind="order_by column")
            if len(parts) > 2:
                raise ValueError(f"malformed ORDER BY clause: {clause!r}")
            if len(parts) == 2 and parts[1].upper() not in ("ASC", "DESC"):
                raise ValueError(f"invalid ORDER BY direction: {parts[1]!r}")
        order_sql = f" ORDER BY {order_by}"
    sql = f"SELECT * FROM {tbl}{where}{order_sql}"
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
    tbl = _safe_ident(table, kind="table")
    set_clause = ", ".join(f"{_safe_ident(k, kind='column')} = ?" for k in patch.keys())
    where, where_params = _compile_where(filter_)
    sql = f"UPDATE {tbl} SET {set_clause}{where}"
    params: Iterable[Any] = list(patch.values()) + where_params
    with conn:
        cur = conn.execute(sql, list(params))
        return cur.rowcount
