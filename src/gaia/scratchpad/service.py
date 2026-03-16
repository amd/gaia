# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""SQLite scratchpad service for structured data analysis."""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.database.mixin import DatabaseMixin
from gaia.logger import get_logger

log = get_logger(__name__)


class ScratchpadService(DatabaseMixin):
    """SQLite-backed working memory for multi-document data analysis.

    Inherits from DatabaseMixin for all database operations.
    Uses the same database file as FileSystemIndexService but with
    a 'scratch_' prefix on all table names to avoid collisions.

    Tables are user-created via tools and can persist across sessions
    or be cleaned up after analysis.

    Limits:
        - Max 100 tables
        - Max 1M rows per table
        - Max 100MB total scratchpad size
    """

    TABLE_PREFIX = "scratch_"
    MAX_TABLES = 100
    MAX_ROWS_PER_TABLE = 1_000_000
    MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024  # 100MB

    DEFAULT_DB_PATH = "~/.gaia/file_index.db"

    def __init__(self, db_path: Optional[str] = None):
        """Initialize scratchpad service.

        Args:
            db_path: Path to SQLite database. Defaults to ~/.gaia/file_index.db
        """
        path = db_path or self.DEFAULT_DB_PATH
        resolved = str(Path(path).expanduser())
        self.init_db(resolved)
        # Enable WAL mode for concurrent access.
        # Use _db.execute() directly because PRAGMA does not work reliably
        # with the mixin's execute() which calls executescript().
        self._db.execute("PRAGMA journal_mode=WAL")

    def create_table(self, name: str, columns: str) -> str:
        """Create a prefixed scratchpad table.

        Args:
            name: Table name (will be prefixed with 'scratch_').
            columns: Column definitions in SQLite syntax,
                     e.g., "date TEXT, amount REAL, description TEXT"

        Returns:
            Confirmation message string.

        Raises:
            ValueError: If table limit exceeded or name is invalid.
        """
        safe_name = self._sanitize_name(name)
        full_name = f"{self.TABLE_PREFIX}{safe_name}"

        # Check table limit
        existing = self._count_tables()
        if existing >= self.MAX_TABLES:
            raise ValueError(
                f"Table limit reached ({self.MAX_TABLES}). "
                "Drop unused tables before creating new ones."
            )

        # Validate columns string (basic check)
        if not columns or not columns.strip():
            raise ValueError("Column definitions cannot be empty.")

        # Create table using execute() (outside any transaction)
        self.execute(f"CREATE TABLE IF NOT EXISTS {full_name} ({columns})")

        log.info(f"Scratchpad table created: {safe_name}")
        return f"Table '{safe_name}' created with columns: {columns}"

    def insert_rows(self, table: str, data: List[Dict[str, Any]]) -> int:
        """Bulk insert rows into a scratchpad table.

        Args:
            table: Table name (without prefix).
            data: List of dicts, each dict is a row with column:value pairs.

        Returns:
            Number of rows inserted.

        Raises:
            ValueError: If table does not exist or row limit would be exceeded.
        """
        safe_name = self._sanitize_name(table)
        full_name = f"{self.TABLE_PREFIX}{safe_name}"

        if not self.table_exists(full_name):
            raise ValueError(
                f"Table '{safe_name}' does not exist. "
                "Create it first with create_table()."
            )

        if not data:
            return 0

        # Check row limit
        current_count = self._get_row_count(full_name)
        if current_count + len(data) > self.MAX_ROWS_PER_TABLE:
            raise ValueError(
                f"Row limit would be exceeded. Current: {current_count}, "
                f"Adding: {len(data)}, Max: {self.MAX_ROWS_PER_TABLE}"
            )

        count = 0
        with self.transaction():
            for row in data:
                self.insert(full_name, row)
                count += 1

        log.info(f"Inserted {count} rows into scratchpad table '{safe_name}'")
        return count

    def query_data(self, sql: str) -> List[Dict[str, Any]]:
        """Execute a SELECT query against the scratchpad.

        Only SELECT statements are allowed for security.
        The query should reference tables WITH the 'scratch_' prefix.

        Args:
            sql: SQL SELECT query.

        Returns:
            List of dicts with query results.

        Raises:
            ValueError: If query is not a SELECT statement or contains
                        disallowed keywords.
        """
        normalized = sql.strip()
        upper = normalized.upper()

        # Security: only allow SELECT
        if not upper.startswith("SELECT"):
            raise ValueError(
                "Only SELECT queries are allowed via query_data(). "
                "Use insert_rows() for inserts or drop_table() for deletions."
            )

        # Block dangerous keywords even in SELECT (subquery attacks)
        dangerous = [
            "INSERT ",
            "UPDATE ",
            "DELETE ",
            "DROP ",
            "ALTER ",
            "CREATE ",
            "ATTACH ",
        ]
        for keyword in dangerous:
            if keyword in upper:
                raise ValueError(
                    f"Query contains disallowed keyword: {keyword.strip()}"
                )

        return self.query(normalized)

    def list_tables(self) -> List[Dict[str, Any]]:
        """List all scratchpad tables with schema and row count.

        Returns:
            List of dicts with 'name', 'columns', and 'rows' keys.
        """
        tables = self.query(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE :prefix",
            {"prefix": f"{self.TABLE_PREFIX}%"},
        )

        result = []
        for t in tables:
            display_name = t["name"].replace(self.TABLE_PREFIX, "", 1)
            schema = self.query(f"PRAGMA table_info({t['name']})")
            count_result = self.query(
                f"SELECT COUNT(*) as count FROM {t['name']}", one=True
            )
            row_count = count_result["count"] if count_result else 0

            result.append(
                {
                    "name": display_name,
                    "columns": [{"name": c["name"], "type": c["type"]} for c in schema],
                    "rows": row_count,
                }
            )

        return result

    def drop_table(self, name: str) -> str:
        """Drop a scratchpad table.

        Args:
            name: Table name (without prefix).

        Returns:
            Confirmation message.
        """
        safe_name = self._sanitize_name(name)
        full_name = f"{self.TABLE_PREFIX}{safe_name}"

        if not self.table_exists(full_name):
            return f"Table '{safe_name}' does not exist."

        self.execute(f"DROP TABLE IF EXISTS {full_name}")
        log.info(f"Scratchpad table dropped: {safe_name}")
        return f"Table '{safe_name}' dropped."

    def clear_all(self) -> str:
        """Drop all scratchpad tables.

        Returns:
            Summary of tables dropped.
        """
        tables = self.query(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE :prefix",
            {"prefix": f"{self.TABLE_PREFIX}%"},
        )

        count = 0
        for t in tables:
            self.execute(f"DROP TABLE IF EXISTS {t['name']}")
            count += 1

        log.info(f"Cleared {count} scratchpad tables")
        return f"Dropped {count} scratchpad table(s)."

    def get_size_bytes(self) -> int:
        """Get total size of all scratchpad data in bytes (approximate).

        Uses a rough estimate of 200 bytes per row across all
        scratchpad tables.

        Returns:
            Estimated size in bytes.
        """
        try:
            tables = self.list_tables()
            total_rows = sum(t["rows"] for t in tables)

            if total_rows == 0:
                return 0

            # Rough estimate: 200 bytes per row average
            return total_rows * 200
        except Exception:
            return 0

    def _sanitize_name(self, name: str) -> str:
        """Sanitize table/column names to prevent SQL injection.

        Only allows alphanumeric and underscore characters.
        Prepends 't_' if name starts with a digit.

        Args:
            name: Raw table name.

        Returns:
            Sanitized name safe for use in SQL identifiers.

        Raises:
            ValueError: If name is empty or None.
        """
        if not name:
            raise ValueError("Table name cannot be empty.")

        clean = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        if not clean or clean[0].isdigit():
            clean = f"t_{clean}"
        # Truncate to reasonable length
        if len(clean) > 64:
            clean = clean[:64]
        return clean

    def _count_tables(self) -> int:
        """Count existing scratchpad tables."""
        result = self.query(
            "SELECT COUNT(*) as count FROM sqlite_master "
            "WHERE type='table' AND name LIKE :prefix",
            {"prefix": f"{self.TABLE_PREFIX}%"},
            one=True,
        )
        return result["count"] if result else 0

    def _get_row_count(self, full_table_name: str) -> int:
        """Get row count for a specific table.

        Args:
            full_table_name: Full table name including prefix.

        Returns:
            Number of rows in the table.
        """
        result = self.query(
            f"SELECT COUNT(*) as count FROM {full_table_name}", one=True
        )
        return result["count"] if result else 0
