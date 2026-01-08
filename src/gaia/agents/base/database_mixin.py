# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SQLAlchemy-based database mixin for GAIA agents.

Provides multi-database support (SQLite, PostgreSQL, MySQL) with connection
pooling and thread-safe operations.

This implementation follows Test-Driven Development (TDD) principles and
provides thread-safe database access through SQLAlchemy Core.

BREAKING CHANGES from sqlite3-based DatabaseMixin:
    - Query parameters must use NAMED parameters (dict) instead of positional (tuple)
    - Old: query("SELECT * FROM users WHERE id = ?", (42,))
    - New: execute_query("SELECT * FROM users WHERE id = :id", {"id": 42})
    - The backward compatibility query() method accepts dicts only

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
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool, QueuePool

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
        # Initialize engine attribute (if not already set)
        if not hasattr(self, "engine"):
            self.engine = None

        # Initialize thread-local storage for transaction contexts
        if not hasattr(self, "_local"):
            self._local = threading.local()

        # Close existing engine if any
        if self.engine:
            self.close_database()

        # Track if this is a file-based SQLite database (before URL transformation)
        is_file_sqlite = db_url.startswith("sqlite:///") and not db_url.endswith(
            ":memory:"
        )

        # Create parent directory for file-based SQLite
        if is_file_sqlite:
            db_path = db_url.replace("sqlite:///", "")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # SQLite-specific configuration for thread safety
        connect_args = {}
        poolclass = QueuePool

        if db_url.startswith("sqlite:"):
            connect_args["check_same_thread"] = False

            # In-memory SQLite needs special handling for thread safety
            # StaticPool shares a single connection which causes threading issues
            # Solution: Use a shared-cache named in-memory database with QueuePool
            if ":memory:" in db_url:
                # Convert :memory: to file:memdb_<uuid>?mode=memory&cache=shared
                # This creates a unique named in-memory database that can be shared across threads
                # Each thread gets its own connection from the pool
                # The unique name ensures each init_database() gets a fresh database
                unique_name = f"memdb_{uuid.uuid4().hex[:8]}"
                db_url = db_url.replace(
                    ":memory:", f"file:{unique_name}?mode=memory&cache=shared"
                )
                connect_args["uri"] = True  # Required for file: URI syntax
                poolclass = QueuePool
            else:
                # File-based SQLite should use NullPool to avoid "database is locked" errors
                # SQLite's locking model doesn't work well with connection pooling
                poolclass = NullPool

        # Create engine with connection pooling
        engine_args = {
            "poolclass": poolclass,
            "pool_pre_ping": True,  # Verify connections before use
            "pool_recycle": 3600,  # Recycle connections after 1 hour
            "connect_args": connect_args,
        }

        # QueuePool-specific parameters
        if poolclass == QueuePool:
            engine_args["pool_size"] = pool_size
            engine_args["max_overflow"] = 10

        self.engine = create_engine(db_url, **engine_args)

        # For file-based SQLite, create the database file if it doesn't exist
        # NullPool doesn't create connections eagerly, so we need to force one
        if is_file_sqlite:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))  # Touch the database to create file

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
        Get a connection from the pool or the active transaction.

        Thread-safe: Returns the active transaction connection if in a transaction,
        otherwise returns a new connection from the pool.

        IMPORTANT: If a transaction is active, the returned connection belongs to
        the transaction and should NOT be closed by the caller.

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
                conn.close()  # Only close if NOT in a transaction!
        """
        self._require_db()

        # If we're in a transaction context, return that connection
        if hasattr(self._local, "transaction_conn") and self._local.transaction_conn:
            return self._local.transaction_conn

        # Otherwise, return a new connection from the pool
        return self.engine.connect()

    @property
    def db_ready(self) -> bool:
        """
        True if database is initialized and ready.

        Thread-safe: Can be checked from any thread.

        Returns:
            True if engine exists, False otherwise
        """
        return hasattr(self, "engine") and self.engine is not None

    def _require_db(self) -> None:
        """
        Raise RuntimeError if database not initialized.

        Raises:
            RuntimeError: If database not initialized
        """
        if not hasattr(self, "engine") or not self.engine:
            raise RuntimeError("Database not initialized. Call init_database() first.")

    def _should_close_connection(self, conn) -> bool:
        """
        Check if a connection should be closed.

        Returns False if the connection is part of an active transaction.

        Args:
            conn: SQLAlchemy Connection object

        Returns:
            True if connection should be closed, False otherwise
        """
        # Don't close if this is a transaction connection
        if (
            hasattr(self._local, "transaction_conn")
            and self._local.transaction_conn is conn
        ):
            return False
        return True

    def _validate_identifier(
        self, identifier: str, identifier_type: str = "identifier"
    ) -> None:
        """
        Validate SQL identifier (table/column name) to prevent SQL injection.

        Args:
            identifier: The identifier to validate
            identifier_type: Type description for error message

        Raises:
            ValueError: If identifier contains invalid characters

        Note:
            In practice, table/column names are controlled by agent code, not user input.
            This validation provides defense-in-depth against accidental misuse.
        """
        if not identifier:
            raise ValueError(f"Invalid {identifier_type}: cannot be empty")

        # Allow alphanumeric, underscore, and dot (for schema.table)
        # Disallow quotes, semicolons, spaces, and other SQL meta-characters
        import re

        if not re.match(r"^[a-zA-Z0-9_.]+$", identifier):
            raise ValueError(
                f"Invalid {identifier_type} '{identifier}': "
                f"only alphanumeric, underscore, and dot allowed"
            )

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
        conn = self.get_connection()
        try:
            result = conn.execute(text(sql), params or {})
            # pylint: disable=protected-access
            return [dict(row._mapping) for row in result]
        finally:
            if self._should_close_connection(conn):
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
        self._validate_identifier(table, "table name")
        for col in data.keys():
            self._validate_identifier(col, "column name")
        if returning:
            self._validate_identifier(returning, "returning column")

        conn = self.get_connection()
        should_close = self._should_close_connection(conn)
        try:
            cols = ", ".join(data.keys())
            placeholders = ", ".join(f":{k}" for k in data.keys())

            if returning:
                # PostgreSQL/MySQL style with RETURNING
                try:
                    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING {returning}"
                    result = conn.execute(text(sql), data)
                    return_value = result.scalar()  # Consume result before commit
                    if should_close:  # Only commit if not in transaction
                        conn.commit()
                    return return_value
                except Exception as e:
                    # Fallback for SQLite < 3.35 or databases without RETURNING support
                    if "RETURNING" in str(e):
                        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
                        result = conn.execute(text(sql), data)
                        return_value = result.lastrowid  # Get lastrowid before commit
                        if should_close:  # Only commit if not in transaction
                            conn.commit()
                        return return_value
                    raise
            else:
                # SQLite style - use lastrowid
                sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
                result = conn.execute(text(sql), data)
                return_value = result.lastrowid  # Get lastrowid before commit
                if should_close:  # Only commit if not in transaction
                    conn.commit()
                return return_value
        finally:
            if should_close:
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
        self._validate_identifier(table, "table name")
        for col in data.keys():
            self._validate_identifier(col, "column name")

        conn = self.get_connection()
        should_close = self._should_close_connection(conn)
        try:
            # Prefix data params with __set_ to avoid collision with where params
            set_clause = ", ".join(f"{k} = :__set_{k}" for k in data.keys())
            merged_params = {f"__set_{k}": v for k, v in data.items()}
            merged_params.update(where_params)

            sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
            result = conn.execute(text(sql), merged_params)
            if should_close:  # Only commit if not in transaction
                conn.commit()
            return result.rowcount
        finally:
            if should_close:
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
        self._validate_identifier(table, "table name")

        conn = self.get_connection()
        should_close = self._should_close_connection(conn)
        try:
            sql = f"DELETE FROM {table} WHERE {where}"
            result = conn.execute(text(sql), where_params)
            if should_close:  # Only commit if not in transaction
                conn.commit()
            return result.rowcount
        finally:
            if should_close:
                conn.close()

    @contextmanager
    def transaction(self):
        """
        Execute operations atomically in a transaction.

        Thread-safe: Each transaction gets its own connection from the pool.
        Provides transaction isolation per-thread.

        Auto-commits on success, rolls back on exception.

        All execute_insert(), execute_update(), execute_delete(), execute_query(),
        and execute_raw() operations within the transaction context will use
        the transaction's connection automatically.

        Yields:
            SQLAlchemy Connection object (for advanced usage)

        Example:
            # All operations use the same transaction connection
            with self.transaction():
                user_id = self.execute_insert("users", {"name": "Alice"})
                self.execute_insert("profiles", {"user_id": user_id, "bio": "Hello"})
                # If any operation fails, all are rolled back

            # Can also use the connection directly
            with self.transaction() as conn:
                result = conn.execute(
                    text("INSERT INTO users (name) VALUES (:name) RETURNING id"),
                    {"name": "Alice"}
                )
                user_id = result.scalar()
        """
        self._require_db()

        # Check if already in a transaction (nested transactions not supported)
        if hasattr(self._local, "transaction_conn") and self._local.transaction_conn:
            raise RuntimeError("Nested transactions are not supported")

        conn = self.engine.connect()
        trans = conn.begin()

        # Store connection in thread-local storage
        self._local.transaction_conn = conn

        try:
            yield conn
            trans.commit()
        except Exception:
            trans.rollback()
            raise
        finally:
            # Clear thread-local transaction connection
            self._local.transaction_conn = None
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
        conn = self.get_connection()
        should_close = self._should_close_connection(conn)
        try:
            # Split multiple statements and execute each
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(text(statement))
            if should_close:  # Only commit if not in transaction
                conn.commit()
        finally:
            if should_close:
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
        self._validate_identifier(table, "table name")
        inspector = inspect(self.engine)
        return table in inspector.get_table_names()

    # ===== Backward Compatibility Aliases =====
    # The following aliases maintain compatibility with existing code
    # that uses the old method names from the sqlite3-based mixin.

    def query(
        self, sql: str, params: Optional[Dict[str, Any]] = None, one: bool = False
    ):
        """Backward compatibility alias for execute_query()."""
        results = self.execute_query(sql, params)
        if one:
            return results[0] if results else None
        return results

    def insert(self, table: str, data: Dict[str, Any]):
        """Backward compatibility alias for execute_insert()."""
        return self.execute_insert(table, data)

    def update(
        self, table: str, data: Dict[str, Any], where: str, where_params: Dict[str, Any]
    ):
        """Backward compatibility alias for execute_update()."""
        return self.execute_update(table, data, where, where_params)

    def delete(self, table: str, where: str, where_params: Dict[str, Any]):
        """Backward compatibility alias for execute_delete()."""
        return self.execute_delete(table, where, where_params)

    def execute(
        self, sql: str, params: Optional[Dict[str, Any]] = None
    ):  # pylint: disable=unused-argument
        """Backward compatibility alias for execute_raw()."""
        # Note: params are ignored - old execute() didn't support them either
        return self.execute_raw(sql)

    def close_db(self):
        """Backward compatibility alias for close_database()."""
        return self.close_database()
