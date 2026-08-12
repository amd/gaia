# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""SQLite database mixin for GAIA agents."""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from gaia.database.sql_safety import readonly, validate_identifier

logger = logging.getLogger(__name__)


def _require_column_map(data: Any, table: str, context: str) -> None:
    """Reject a data mapping that can't be safely interpolated as columns."""
    if not isinstance(data, dict):
        raise ValueError(
            f"{context}: 'data' must be a dict of column names to values, got "
            f"{type(data).__name__}. Example: {{'name': 'Alice'}}."
        )
    if not data:
        raise ValueError(
            f"{context}: 'data' is empty; supply at least one column-value "
            f"pair for table {table!r}."
        )
    for key in data:
        validate_identifier(key, "column name", context)


def _require_where(where: Any, table: str, context: str) -> None:
    """Reject a where fragment that would produce a malformed statement."""
    verb = "delete" if context.endswith("delete") else "update"
    if not isinstance(where, str):
        raise ValueError(
            f"{context}: 'where' must be a SQL fragment string, got "
            f"{type(where).__name__}. Example: 'id = :id'."
        )
    if not where.strip():
        raise ValueError(
            f"{context}: 'where' is empty. Pass a WHERE fragment, or '1 = 1' "
            f"to {verb} every row in {table!r}."
        )


class DatabaseMixin:
    """
    Mixin providing SQLite database access for GAIA agents.

    A lean, zero-dependency mixin that uses Python's built-in sqlite3 module.

    Example:
        class MyAgent(Agent, DatabaseMixin):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.init_db("data/app.db")

                if not self.table_exists("items"):
                    self.execute('''
                        CREATE TABLE items (
                            id INTEGER PRIMARY KEY,
                            name TEXT NOT NULL
                        )
                    ''')

            def _register_tools(self):
                @tool
                def add_item(name: str) -> dict:
                    item_id = self.insert("items", {"name": name})
                    return {"id": item_id}

    Security:
        Values are always bound. Table and column names are validated as bare
        identifiers. The ``where`` argument to update()/delete() is a **trusted
        SQL fragment** — it is interpolated, not bound, so it must be a literal
        written by you, never a string built from LLM or end-user input. To
        filter on untrusted input, bind it via :param placeholders inside a
        literal ``where``, or use the structured-condition tools on
        DatabaseAgent. Use query_readonly() for untrusted SQL.
    """

    _db: Optional[sqlite3.Connection] = None
    _in_tx: bool = False

    def init_db(self, path: str = ":memory:", *, quiet: bool = False) -> None:
        """
        Initialize SQLite database.

        Args:
            path: Database file path, or ":memory:" for in-memory database.
                  Parent directories are created automatically.
            quiet: Skip the "Database initialized" INFO line. Defaults to
                   False — every existing one-shot-at-startup caller keeps
                   today's behavior unchanged. Set True only for a caller that
                   opens a fresh connection on a recurring cadence (e.g. a
                   polling driver opening one per pass), where that same INFO
                   line would otherwise become a permanent, unbounded log-noise
                   cost rather than a one-time startup confirmation.

        Example:
            self.init_db("data/myagent.db")  # File-based
            self.init_db()                    # In-memory (for testing)
        """
        if self._db:
            self.close_db()

        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._in_tx = False
        if not quiet:
            logger.info("Database initialized: %s", path)

    def close_db(self) -> None:
        """
        Close database connection.

        Safe to call multiple times.
        """
        if self._db:
            self._db.close()
            self._db = None
            self._in_tx = False

    @property
    def db_ready(self) -> bool:
        """True if database is initialized."""
        return self._db is not None

    def _require_db(self) -> None:
        """Raise RuntimeError if database not initialized."""
        if not self._db:
            raise RuntimeError("Database not initialized. Call init_db() first.")

    def query(
        self,
        sql: str,
        params: Optional[Dict[str, Any]] = None,
        one: bool = False,
    ) -> Union[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Execute SELECT query and return results as dicts.

        Args:
            sql: SQL query with :param_name placeholders
            params: Dictionary of parameter values
            one: If True, return single row dict or None

        Returns:
            List of row dicts, or single dict/None if one=True

        Example:
            # Get all
            users = self.query("SELECT * FROM users")

            # Get one
            user = self.query(
                "SELECT * FROM users WHERE id = :id",
                {"id": 42},
                one=True
            )
        """
        self._require_db()
        assert self._db is not None
        cursor = self._db.execute(sql, params or {})
        rows = [dict(row) for row in cursor.fetchall()]
        if one:
            return rows[0] if rows else None
        return rows

    def query_readonly(
        self,
        sql: str,
        params: Optional[Dict[str, Any]] = None,
        one: bool = False,
    ) -> Union[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Execute a query with writes and schema changes blocked by SQLite.

        Use this for any SQL that originates outside trusted Python code — an
        LLM tool call, a user-supplied filter. Writes, DDL, ATTACH, and pragmas
        outside a read-only allowlist are refused by a SQLite authorizer rather
        than by inspecting the statement text.

        Args:
            sql: SQL query with :param_name placeholders
            params: Dictionary of parameter values
            one: If True, return single row dict or None

        Returns:
            List of row dicts, or single dict/None if one=True

        Raises:
            PermissionError: If the statement attempts anything but a read.

        Example:
            rows = self.query_readonly("SELECT * FROM users WHERE id = :id",
                                       {"id": 42})
        """
        self._require_db()
        assert self._db is not None
        denied: List[str] = []
        mark = 0
        try:
            # The authorizer fires at prepare time, so fetch inside the window.
            with readonly(self._db) as denied:
                # Compare against a mark, not emptiness: a nested window hands
                # back denials accumulated by the enclosing one.
                mark = len(denied)
                cursor = self._db.execute(sql, params or {})
                rows = [dict(row) for row in cursor.fetchall()]
        except sqlite3.DatabaseError as e:
            # Classify on what the authorizer did, not on SQLite's message.
            if len(denied) == mark:
                raise
            preview = sql if len(sql) <= 200 else f"{sql[:200]}..."
            raise PermissionError(
                f"Read-only query blocked by SQLite: {preview}. Only SELECT "
                f"(plus PRAGMA table_info/table_xinfo/index_list/index_info/"
                f"foreign_key_list) is permitted here. Use insert(), update(), "
                f"or delete() to modify data."
            ) from e
        if one:
            return rows[0] if rows else None
        return rows

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        """
        Insert a row and return its ID.

        Args:
            table: Table name
            data: Column-value dictionary

        Returns:
            The inserted row's ID (lastrowid)

        Example:
            user_id = self.insert("users", {
                "name": "Alice",
                "email": "alice@example.com"
            })

        Raises:
            ValueError: If the table name or any column name is not a bare
                identifier, or if data is empty.
        """
        self._require_db()
        assert self._db is not None
        # Table and dict keys are interpolated into the SQL, not bound.
        table = validate_identifier(table, "table name", "DatabaseMixin.insert")
        _require_column_map(data, table, "DatabaseMixin.insert")
        cols = ", ".join(data.keys())
        placeholders = ", ".join(f":{k}" for k in data.keys())
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        cursor = self._db.execute(sql, data)
        if not self._in_tx:
            self._db.commit()
        return cursor.lastrowid or 0

    def update(
        self,
        table: str,
        data: Dict[str, Any],
        where: str,
        params: Dict[str, Any],
    ) -> int:
        """
        Update rows matching condition and return affected count.

        Args:
            table: Table name
            data: Column-value dictionary to update
            where: WHERE clause with :param placeholders (without WHERE keyword)
            params: Parameters for WHERE clause

        Returns:
            Number of rows affected

        Example:
            count = self.update(
                "users",
                {"email": "new@example.com"},
                "id = :id",
                {"id": 42}
            )

        Raises:
            ValueError: If the table name or any column name is not a bare
                identifier, or if data or where is empty.
        """
        self._require_db()
        assert self._db is not None
        table = validate_identifier(table, "table name", "DatabaseMixin.update")
        _require_column_map(data, table, "DatabaseMixin.update")
        _require_where(where, table, "DatabaseMixin.update")
        # Prefix data params with __set_ to avoid collision with where params
        set_clause = ", ".join(f"{k} = :__set_{k}" for k in data.keys())
        merged_params = {f"__set_{k}": v for k, v in data.items()}
        clashes = sorted(set(merged_params) & set(params or {}))
        if clashes:
            raise ValueError(
                f"DatabaseMixin.update: where params {clashes} collide with the "
                f"reserved __set_ prefix used for column values. Rename these "
                f"placeholders in the where clause."
            )
        merged_params.update(params or {})
        sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
        cursor = self._db.execute(sql, merged_params)
        if not self._in_tx:
            self._db.commit()
        return cursor.rowcount

    def delete(self, table: str, where: str, params: Dict[str, Any]) -> int:
        """
        Delete rows matching condition and return deleted count.

        Args:
            table: Table name
            where: WHERE clause with :param placeholders (without WHERE keyword)
            params: Parameters for WHERE clause

        Returns:
            Number of rows deleted

        Example:
            count = self.delete("sessions", "expires_at < :now", {"now": now})

        Raises:
            ValueError: If the table name is not a bare identifier, or if
                where is empty.
        """
        self._require_db()
        assert self._db is not None
        table = validate_identifier(table, "table name", "DatabaseMixin.delete")
        _require_where(where, table, "DatabaseMixin.delete")
        sql = f"DELETE FROM {table} WHERE {where}"
        cursor = self._db.execute(sql, params or {})
        if not self._in_tx:
            self._db.commit()
        return cursor.rowcount

    @contextmanager
    def transaction(self):
        """
        Execute operations atomically.

        Auto-commits on success, rolls back on exception.

        Example:
            with self.transaction():
                user_id = self.insert("users", {"name": "Alice"})
                self.insert("profiles", {"user_id": user_id, "bio": "Hello"})
                # If any operation fails, all are rolled back
        """
        self._require_db()
        self._in_tx = True
        try:
            yield
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        finally:
            self._in_tx = False

    def execute(self, sql: str) -> None:
        """
        Execute raw SQL (CREATE TABLE, etc).

        Supports multiple statements separated by semicolons.

        WARNING: Do NOT call inside a transaction() block. This method uses
        executescript() which auto-commits any pending transaction.

        Args:
            sql: SQL statement(s) to execute

        Raises:
            RuntimeError: If called inside a transaction() block

        Example:
            self.execute('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE posts (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    content TEXT
                );
            ''')
        """
        self._require_db()
        assert self._db is not None
        if self._in_tx:
            raise RuntimeError(
                "execute() cannot be called inside a transaction() block. "
                "Use query() for SELECT or individual insert/update/delete calls."
            )
        self._db.executescript(sql)

    def table_exists(self, name: str) -> bool:
        """
        Check if a table exists in the database.

        Args:
            name: Table name to check

        Returns:
            True if table exists, False otherwise

        Example:
            if not self.table_exists("users"):
                self.execute("CREATE TABLE users (...)")
        """
        result = self.query(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name",
            {"name": name},
            one=True,
        )
        return result is not None
