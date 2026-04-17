# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""SQLite scratchpad service for structured data analysis."""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.database.mixin import DatabaseMixin
from gaia.logger import get_logger

log = get_logger(__name__)

# Column DDL validation. Each column definition must look like
# ``identifier TYPE [constraint ...]`` where TYPE is a known SQLite affinity
# and constraints are a limited allowlist. The columns string comes from the
# LLM via the ``create_table`` tool — because DatabaseMixin.execute() calls
# executescript(), any stray ``;`` would enable multi-statement injection.
_VALID_SQL_TYPES = {
    # Core SQLite affinities
    "TEXT",
    "INTEGER",
    "REAL",
    "NUMERIC",
    "BLOB",
    # Common synonyms SQLite accepts and maps to an affinity
    "BOOLEAN",
    "DATE",
    "DATETIME",
    "TIMESTAMP",
    "VARCHAR",
    "CHAR",
    "DECIMAL",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "FLOAT",
    "DOUBLE",
}
_COLUMN_DEF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ScratchpadService(DatabaseMixin):
    """SQLite-backed working memory for multi-document data analysis.

    Inherits from DatabaseMixin for all database operations. Uses its own
    database file (``~/.gaia/scratchpad.db`` by default) — separate from
    ``FileSystemIndexService``'s ``~/.gaia/file_index.db`` so each service's
    ``PRAGMA integrity_check`` only sees its own schema.

    All table names are prefixed with ``scratch_`` for defense-in-depth even
    though the two services now live in separate files.

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

    # Keep on its own file so FileSystemIndexService's integrity_check doesn't
    # see scratch_* tables as "unexpected corruption" and vice-versa (#495
    # review feedback).
    DEFAULT_DB_PATH = "~/.gaia/scratchpad.db"

    def __init__(self, db_path: Optional[str] = None):
        """Initialize scratchpad service.

        Args:
            db_path: Path to SQLite database. Defaults to ~/.gaia/scratchpad.db
        """
        path = db_path or self.DEFAULT_DB_PATH
        resolved = str(Path(path).expanduser())
        self.init_db(resolved)
        # Open path: try PRAGMAs, and if anything complains about a
        # malformed DB, rebuild from scratch. Mirrors
        # FileSystemIndexService._check_integrity so a corrupted
        # ``~/.gaia/scratchpad.db`` (power loss, disk full) heals itself
        # instead of crashing every turn with a cryptic
        # ``sqlite3.DatabaseError: file is not a database``.
        if not self._open_or_rebuild(resolved):
            log.warning("Scratchpad DB at %s was corrupt; rebuilt empty.", resolved)

    def _open_or_rebuild(self, db_path: str) -> bool:
        """Set PRAGMA journal mode + run integrity check, rebuild on failure.

        Returns True if the existing DB is healthy, False if it had to be
        rebuilt (caller may want to log).
        """
        try:
            # Both statements fail loudly on a corrupt file — catch together.
            self._db.execute("PRAGMA journal_mode=WAL")
            row = self._db.execute("PRAGMA integrity_check").fetchone()
            if row and row[0] == "ok":
                return True
            log.error("Scratchpad integrity_check returned %s", row)
        except Exception as exc:  # pylint: disable=broad-except
            log.error("Scratchpad integrity check failed: %s", exc)

        # Rebuild: close, delete the file, re-init.
        try:
            self.close_db()
        except Exception as exc:  # pylint: disable=broad-except
            # close_db can fail if the connection is already broken (which
            # is why we're rebuilding). Log at debug instead of swallowing
            # silently — CLAUDE.md prohibits bare except/pass.
            log.debug("close_db during scratchpad rebuild raised (%s); continuing", exc)
        try:
            Path(db_path).unlink(missing_ok=True)
        except OSError as exc:
            log.error("Failed to delete corrupt scratchpad DB: %s", exc)
        self.init_db(db_path)
        # Fresh DB — these now succeed.
        self._db.execute("PRAGMA journal_mode=WAL")
        return False

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

        # Validate and normalize the column DDL. This is the single most
        # important guardrail in this class — `self.execute()` is backed by
        # sqlite3.executescript() which allows multiple statements, so an
        # unchecked `columns` string is a direct SQL-injection vector.
        safe_columns = self._validate_columns(columns)

        # Create table using execute() (outside any transaction)
        self.execute(f"CREATE TABLE IF NOT EXISTS {full_name} ({safe_columns})")

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

        # Defense in depth: validate every column name in every row before
        # building SQL. DatabaseMixin.insert interpolates dict keys directly
        # into the SQL string (``INSERT INTO t (keys...) VALUES (:keys...)``);
        # sqlite3 happens to reject multi-statement attacks because execute()
        # accepts only one statement, but relying on that is brittle. Enforce
        # here that keys match the same identifier grammar as column names in
        # create_table.
        for i, row in enumerate(data):
            if not isinstance(row, dict):
                raise ValueError(f"Row {i} is not a dict: got {type(row).__name__}")
            for key in row.keys():
                if not isinstance(key, str) or not _COLUMN_DEF_RE.match(key):
                    raise ValueError(
                        f"Row {i} has invalid column name {key!r}: must match "
                        "[A-Za-z_][A-Za-z0-9_]*"
                    )

        # Check row limit
        current_count = self._get_row_count(full_name)
        if current_count + len(data) > self.MAX_ROWS_PER_TABLE:
            raise ValueError(
                f"Row limit would be exceeded. Current: {current_count}, "
                f"Adding: {len(data)}, Max: {self.MAX_ROWS_PER_TABLE}"
            )

        # Enforce the global scratchpad size cap. Without this, an agent
        # could fill 100 tables * 1 M rows * ~200 bytes = 20 GB by staying
        # under each individual cap. get_size_bytes() is an estimate (200
        # bytes/row average) — acceptable given the ~30% slack in the cap
        # and that a real enforcement via PRAGMA page_count is too
        # SQLite-version-specific to rely on here.
        current_size = self.get_size_bytes()
        if current_size >= self.MAX_TOTAL_SIZE_BYTES:
            raise ValueError(
                f"Scratchpad size limit reached "
                f"({current_size / (1024 * 1024):.1f} MB "
                f"/ {self.MAX_TOTAL_SIZE_BYTES // (1024 * 1024)} MB). "
                "Drop unused tables before inserting more rows."
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

        # Block dangerous keywords even in SELECT (subquery attacks). Match
        # on word boundaries so column names like ``email_insert_ts`` or
        # string literals such as ``'UPDATE PENDING'`` are not false-positives.
        # We also strip quoted string literals entirely before scanning so
        # the keyword search can only trigger on actual SQL tokens.
        #
        # Note: column names like ``created_at`` tokenize to {CREATED, AT}, so
        # ``CREATE`` itself is *not* a false-positive — safe to include.
        scan_target = _strip_sql_string_literals(upper)
        dangerous = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH"}
        tokens = set(re.findall(r"\b[A-Z]+\b", scan_target))
        hits = tokens & dangerous
        if hits:
            raise ValueError(f"Query contains disallowed keyword: {sorted(hits)[0]}")

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

    def _validate_columns(self, columns: str) -> str:
        """Validate a user-supplied CREATE TABLE column DDL string.

        The ``columns`` argument arrives from the LLM through the
        ``create_table`` tool. Because :meth:`DatabaseMixin.execute` dispatches
        to ``sqlite3.executescript``, an unsanitized string would allow
        multi-statement SQL injection (e.g. ``id INT); DROP TABLE ...; --``).

        Strategy — defense-in-depth, **not** full SQL parsing:

        1. **Hard-deny statement separators and comments**: no ``;``, ``--``,
           ``/*``, ``*/``. Without these tokens the executed script can only
           be a single CREATE TABLE statement, so even arbitrary constraint
           expressions can't chain into a second statement.
        2. **Balanced parens**: an unbalanced string could close the outer
           ``CREATE TABLE`` early.
        3. **Per-column shape check**: split on top-level commas and verify
           each part starts with ``<identifier> <TYPE>[(size)]``. The ``<TYPE>``
           root must be a known SQLite affinity. Constraint text after the
           type (including ``CHECK(...)``, ``DEFAULT CURRENT_TIMESTAMP``,
           ``REFERENCES foo(bar)``) is passed through unchanged — parens are
           already balanced and separator tokens are already denied.

        Args:
            columns: Raw column definition string.

        Returns:
            The original columns string (trimmed) — safe to interpolate into
            a CREATE TABLE statement once it passes these checks.

        Raises:
            ValueError: If any part of ``columns`` fails validation.
        """
        if not columns or not columns.strip():
            raise ValueError("Column definitions cannot be empty.")

        # 1. Hard deny statement terminators / comments before any parsing.
        for forbidden in (";", "--", "/*", "*/"):
            if forbidden in columns:
                raise ValueError(
                    f"Invalid column definitions: contains forbidden token "
                    f"{forbidden!r}"
                )

        # 2. Parens must balance to zero, otherwise the CREATE TABLE
        # expression could be terminated early.
        if columns.count("(") != columns.count(")"):
            raise ValueError("Invalid column definitions: unbalanced parentheses.")

        # 3. Split on top-level commas (commas outside any parens) and shape-check.
        defs = _split_top_level(columns, ",")
        defs = [d.strip() for d in defs if d.strip()]
        if not defs:
            raise ValueError("Column definitions cannot be empty.")
        if len(defs) > 64:
            raise ValueError("Too many columns (max 64).")

        seen_names: set = set()
        for part in defs:
            # Skip table-level constraints that start with keywords like
            # ``CHECK(...)``, ``PRIMARY KEY(...)``, ``FOREIGN KEY(...)`` --
            # these are valid SQLite DDL but not per-column definitions.
            first_token = part.split(None, 1)[0].upper()
            if first_token in ("CHECK", "PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT"):
                continue

            tokens = part.split(None, 2)
            if len(tokens) < 2:
                raise ValueError(
                    f"Invalid column definition {part!r}: expected "
                    "'<name> <TYPE> [constraints...]'."
                )
            name, sql_type = tokens[0], tokens[1]

            if not _COLUMN_DEF_RE.match(name):
                raise ValueError(
                    f"Invalid column name {name!r}: must match "
                    "[A-Za-z_][A-Za-z0-9_]*"
                )
            if name.lower() in seen_names:
                raise ValueError(f"Duplicate column name: {name!r}")
            seen_names.add(name.lower())

            # Strip optional size spec like VARCHAR(255) or DECIMAL(10,2)
            type_root = re.sub(r"\(.*$", "", sql_type).upper()
            if type_root not in _VALID_SQL_TYPES:
                raise ValueError(
                    f"Invalid column type {sql_type!r}. Allowed roots: "
                    f"{sorted(_VALID_SQL_TYPES)}"
                )

        return columns.strip()


def _split_top_level(text: str, separator: str) -> List[str]:
    """Split *text* on *separator*, ignoring separators inside parens.

    Used by ``_validate_columns`` so that commas inside ``CHECK(x, y)`` or
    ``DECIMAL(10, 2)`` don't split a column into two parts.
    """
    parts: List[str] = []
    depth = 0
    buf: List[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == separator and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _strip_sql_string_literals(sql_upper: str) -> str:
    """Replace SQL string literals with empty strings for safe keyword scanning.

    Handles both single-quoted ('foo') and double-quoted ("bar") literals and
    SQLite's doubled-quote escape ('it''s'). Used by ``query_data`` so that a
    SELECT whose literal mentions ``DROP`` or ``UPDATE`` isn't falsely rejected.
    """
    return re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", "''", sql_upper)
