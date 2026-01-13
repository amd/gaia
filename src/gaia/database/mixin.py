# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""SQLite database mixin for GAIA agents."""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


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
    """

    _db: Optional[sqlite3.Connection] = None
    _in_tx: bool = False

    def init_db(self, path: str = ":memory:") -> None:
        """
        Initialize SQLite database.

        Args:
            path: Database file path, or ":memory:" for in-memory database.
                  Parent directories are created automatically.

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
        cursor = self._db.execute(sql, params or {})
        rows = [dict(row) for row in cursor.fetchall()]
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
        """
        self._require_db()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(f":{k}" for k in data.keys())
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        cursor = self._db.execute(sql, data)
        if not self._in_tx:
            self._db.commit()
        return cursor.lastrowid

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
        """
        self._require_db()
        # Prefix data params with __set_ to avoid collision with where params
        set_clause = ", ".join(f"{k} = :__set_{k}" for k in data.keys())
        merged_params = {f"__set_{k}": v for k, v in data.items()}
        merged_params.update(params)
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
        """
        self._require_db()
        sql = f"DELETE FROM {table} WHERE {where}"
        cursor = self._db.execute(sql, params)
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
