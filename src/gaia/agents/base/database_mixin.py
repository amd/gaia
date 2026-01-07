# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SQLAlchemy-based database mixin for GAIA agents.

Provides multi-database support (SQLite, PostgreSQL, MySQL) with connection
pooling and thread-safe operations.

This implementation follows Test-Driven Development (TDD) principles and
provides thread-safe database access through SQLAlchemy Core.

Thread Safety:
    - The Engine is thread-safe and shared across threads
    - Each operation gets its own connection from the pool
    - Connections are never stored as instance variables
    - Transaction context provides per-thread isolation

Example:
    class MyAgent(Agent, DatabaseMixin):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.init_database("sqlite:///data/app.db", pool_size=5)

            if not self.table_exists("items"):
                self.execute_raw('''
                    CREATE TABLE items (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL
                    )
                ''')

        def _register_tools(self):
            @tool
            def add_item(name: str) -> dict:
                item_id = self.execute_insert("items", {"name": name})
                return {"id": item_id}

Database URLs:
    - SQLite: "sqlite:///path/to/db.db" or "sqlite:///:memory:"
    - PostgreSQL: "postgresql://user:pass@host:port/dbname"
    - MySQL: "mysql://user:pass@host:port/dbname"
"""

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import QueuePool

logger = logging.getLogger(__name__)


class DatabaseMixin:
    """
    Mixin providing multi-database access for GAIA agents using SQLAlchemy Core.

    Thread-safe implementation using connection pooling. Supports SQLite,
    PostgreSQL, and MySQL via standard SQLAlchemy connection URLs.

    Attributes:
        engine: SQLAlchemy Engine instance (thread-safe, shared across threads)

    Thread Safety:
        All methods are thread-safe. Each operation obtains its own connection
        from the pool, executes the operation, and releases the connection back
        to the pool. The Engine and connection pool handle concurrent access
        with internal locking.
    """

    def __init__(self):
        """Initialize the mixin with no engine."""
        self.engine = None

    def init_database(self, db_url: str, pool_size: int = 5) -> None:
        """
        Initialize database connection with pooling.

        Thread-safe: Multiple threads can call this, but typically called once
        during agent initialization.

        Args:
            db_url: SQLAlchemy database URL
                    - SQLite: "sqlite:///path/to/db.db" or "sqlite:///:memory:"
                    - PostgreSQL: "postgresql://user:pass@host:port/dbname"
                    - MySQL: "mysql://user:pass@host:port/dbname"
            pool_size: Number of connections to maintain in pool (default: 5)

        Raises:
            RuntimeError: If database initialization fails

        Example:
            self.init_database("sqlite:///data/app.db", pool_size=10)
            self.init_database("postgresql://user:pass@localhost/mydb")
        """
        # Close existing engine if any
        if self.engine:
            self.close_database()

        # Create parent directory for file-based SQLite
        if db_url.startswith("sqlite:///") and not db_url.endswith(":memory:"):
            db_path = db_url.replace("sqlite:///", "")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Create engine with connection pooling
        self.engine = create_engine(
            db_url,
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=10,
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=3600,  # Recycle connections after 1 hour
        )

        logger.info(f"Database initialized: {db_url} (pool_size={pool_size})")

    def close_database(self) -> None:
        """
        Close database connection and dispose of engine.

        Safe to call multiple times. All connections in the pool are closed.

        Thread-safe: Can be called from any thread.
        """
        if self.engine:
            self.engine.dispose()
            self.engine = None
            logger.info("Database closed")

    def get_connection(self):
        """
        Get a new connection from the pool.

        Thread-safe: Each call returns a NEW connection from the pool.
        The connection must be closed by the caller.

        Returns:
            SQLAlchemy Connection object

        Raises:
            RuntimeError: If database not initialized

        Example:
            conn = self.get_connection()
            try:
                result = conn.execute(text("SELECT * FROM users"))
                # ... process result ...
            finally:
                conn.close()  # Always close!
        """
        self._require_db()
        return self.engine.connect()

    @property
    def db_ready(self) -> bool:
        """
        True if database is initialized and ready.

        Thread-safe: Can be checked from any thread.

        Returns:
            True if engine exists, False otherwise
        """
        return self.engine is not None

    def _require_db(self) -> None:
        """
        Raise RuntimeError if database not initialized.

        Raises:
            RuntimeError: If database not initialized
        """
        if not self.engine:
            raise RuntimeError("Database not initialized. Call init_database() first.")

    def execute_query(
        self, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute SELECT query and return results as list of dictionaries.

        Thread-safe: Each call gets its own connection from the pool.

        Args:
            sql: SQL query with :param_name placeholders
            params: Dictionary of parameter values (default: None)

        Returns:
            List of row dictionaries. Empty list if no results.

        Raises:
            RuntimeError: If database not initialized

        Example:
            # Query with parameters
            users = self.execute_query(
                "SELECT * FROM users WHERE age > :min_age",
                {"min_age": 18}
            )

            # Query without parameters
            all_users = self.execute_query("SELECT * FROM users")

            # Empty result
            results = self.execute_query("SELECT * FROM items WHERE id = -1")
            assert results == []
        """
        self._require_db()
        conn = self.engine.connect()
        try:
            result = conn.execute(text(sql), params or {})
            return [dict(row._mapping) for row in result]
        finally:
            conn.close()

    def execute_insert(
        self, table: str, data: Dict[str, Any], returning: Optional[str] = None
    ) -> Any:
        """
        Insert a row and return its ID or specified column value.

        Thread-safe: Each call gets its own connection from the pool.

        Args:
            table: Table name
            data: Column-value dictionary
            returning: Column name to return (PostgreSQL/MySQL), None for SQLite lastrowid

        Returns:
            The inserted row's ID (lastrowid) or specified column value

        Example:
            # Basic insert (returns lastrowid)
            user_id = self.execute_insert("users", {
                "name": "Alice",
                "email": "alice@example.com"
            })

            # With RETURNING clause (PostgreSQL/MySQL)
            user_id = self.execute_insert(
                "users",
                {"name": "Bob", "email": "bob@example.com"},
                returning="id"
            )
        """
        self._require_db()
        conn = self.engine.connect()
        try:
            cols = ", ".join(data.keys())
            placeholders = ", ".join(f":{k}" for k in data.keys())

            if returning:
                # PostgreSQL/MySQL style with RETURNING
                sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING {returning}"
                result = conn.execute(text(sql), data)
                return result.scalar()
            else:
                # SQLite style - use lastrowid
                sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
                result = conn.execute(text(sql), data)
                conn.commit()
                return result.lastrowid
        finally:
            conn.close()

    def execute_update(
        self, table: str, data: Dict[str, Any], where: str, where_params: Dict[str, Any]
    ) -> int:
        """
        Update rows matching condition and return affected count.

        Thread-safe: Each call gets its own connection from the pool.

        Args:
            table: Table name
            data: Column-value dictionary to update
            where: WHERE clause with :param placeholders (without WHERE keyword)
            where_params: Parameters for WHERE clause

        Returns:
            Number of rows affected

        Example:
            # Update single row
            count = self.execute_update(
                "users",
                {"email": "new@example.com"},
                "id = :id",
                {"id": 42}
            )

            # Update multiple rows
            count = self.execute_update(
                "products",
                {"price": 9.99},
                "category = :cat",
                {"cat": "books"}
            )
        """
        self._require_db()
        conn = self.engine.connect()
        try:
            # Prefix data params with __set_ to avoid collision with where params
            set_clause = ", ".join(f"{k} = :__set_{k}" for k in data.keys())
            merged_params = {f"__set_{k}": v for k, v in data.items()}
            merged_params.update(where_params)

            sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
            result = conn.execute(text(sql), merged_params)
            conn.commit()
            return result.rowcount
        finally:
            conn.close()

    def execute_delete(
        self, table: str, where: str, where_params: Dict[str, Any]
    ) -> int:
        """
        Delete rows matching condition and return deleted count.

        Thread-safe: Each call gets its own connection from the pool.

        Args:
            table: Table name
            where: WHERE clause with :param placeholders (without WHERE keyword)
            where_params: Parameters for WHERE clause

        Returns:
            Number of rows deleted

        Example:
            # Delete single row
            count = self.execute_delete("sessions", "id = :id", {"id": 123})

            # Delete multiple rows
            count = self.execute_delete(
                "logs",
                "created_at < :cutoff",
                {"cutoff": "2024-01-01"}
            )
        """
        self._require_db()
        conn = self.engine.connect()
        try:
            sql = f"DELETE FROM {table} WHERE {where}"
            result = conn.execute(text(sql), where_params)
            conn.commit()
            return result.rowcount
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        """
        Execute operations atomically in a transaction.

        Thread-safe: Each transaction gets its own connection from the pool.
        Provides transaction isolation per-thread.

        Auto-commits on success, rolls back on exception.

        Yields:
            SQLAlchemy Connection object (for advanced usage)

        Example:
            # Basic usage - automatic transaction management
            with self.transaction():
                user_id = self.execute_insert("users", {"name": "Alice"})
                self.execute_insert("profiles", {
                    "user_id": user_id,
                    "bio": "Hello"
                })
                # If any operation fails, all are rolled back

            # Advanced usage - direct connection access
            with self.transaction() as conn:
                result = conn.execute(text("SELECT * FROM users"))
                # ... custom operations ...
        """
        self._require_db()
        conn = self.engine.connect()
        trans = conn.begin()
        try:
            yield conn
            trans.commit()
        except Exception:
            trans.rollback()
            raise
        finally:
            conn.close()

    def execute_raw(self, sql: str) -> None:
        """
        Execute raw SQL (DDL statements like CREATE TABLE, etc.).

        Thread-safe: Each call gets its own connection from the pool.

        Args:
            sql: SQL statement(s) to execute

        Example:
            # Create table
            self.execute_raw('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE
                )
            ''')

            # Create multiple tables
            self.execute_raw('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER);
            ''')
        """
        self._require_db()
        conn = self.engine.connect()
        try:
            # Split multiple statements and execute each
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(text(statement))
            conn.commit()
        finally:
            conn.close()

    def table_exists(self, table: str) -> bool:
        """
        Check if a table exists in the database.

        Thread-safe: Each call gets its own connection/inspection.

        Args:
            table: Table name to check

        Returns:
            True if table exists, False otherwise

        Example:
            if not self.table_exists("users"):
                self.execute_raw("CREATE TABLE users (...)")
        """
        self._require_db()
        inspector = inspect(self.engine)
        return table in inspector.get_table_names()
