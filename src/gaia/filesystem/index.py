# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""SQLite-backed persistent file system index for GAIA."""

import datetime
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gaia.database.mixin import DatabaseMixin
from gaia.filesystem.categorizer import auto_categorize as _auto_categorize

logger = logging.getLogger(__name__)

# Default directory exclusion patterns
_DEFAULT_EXCLUDES = {
    "__pycache__",
    ".git",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    ".env",
}

_WINDOWS_EXCLUDES = {
    "$Recycle.Bin",
    "System Volume Information",
    "Windows",
}

_UNIX_EXCLUDES = {
    "proc",
    "sys",
    "dev",
}

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    extension TEXT,
    mime_type TEXT,
    size INTEGER,
    created_at TIMESTAMP,
    modified_at TIMESTAMP,
    content_hash TEXT DEFAULT NULL,
    parent_dir TEXT NOT NULL,
    depth INTEGER,
    is_directory BOOLEAN DEFAULT FALSE,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    name, path, extension,
    content='files',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, name, path, extension)
        VALUES (new.id, new.name, new.path, new.extension);
END;

CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name, path, extension)
        VALUES('delete', old.id, old.name, old.path, old.extension);
END;

CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name, path, extension)
        VALUES('delete', old.id, old.name, old.path, old.extension);
    INSERT INTO files_fts(rowid, name, path, extension)
        VALUES (new.id, new.name, new.path, new.extension);
END;

CREATE TABLE IF NOT EXISTS directory_stats (
    path TEXT PRIMARY KEY,
    total_size INTEGER,
    file_count INTEGER,
    dir_count INTEGER,
    deepest_depth INTEGER,
    common_extensions TEXT,
    last_scanned TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    label TEXT,
    category TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY,
    directory TEXT NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    files_scanned INTEGER,
    files_added INTEGER,
    files_updated INTEGER,
    files_removed INTEGER,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS file_categories (
    file_id INTEGER,
    category TEXT,
    subcategory TEXT,
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_parent ON files(parent_dir);
CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension);
CREATE INDEX IF NOT EXISTS idx_files_modified ON files(modified_at);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash)
    WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_categories ON file_categories(category, subcategory);
CREATE INDEX IF NOT EXISTS idx_bookmarks_path ON bookmarks(path);
"""


class FileSystemIndexService(DatabaseMixin):
    """
    SQLite-backed persistent file system index.

    Provides fast file search via FTS5, metadata-based change detection,
    directory statistics, bookmarks, and auto-categorization. Uses WAL mode
    for concurrent access.

    Example:
        service = FileSystemIndexService()
        result = service.scan_directory("C:/Users/me/Documents")
        files = service.query_files(name="report", extension="pdf")
    """

    DB_PATH = "~/.gaia/file_index.db"
    SCHEMA_VERSION = 1

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the file system index service.

        Args:
            db_path: Path to the SQLite database file. Defaults to
                     ``~/.gaia/file_index.db``.
        """
        resolved_path = str(Path(db_path or self.DB_PATH).expanduser())
        self.init_db(resolved_path)

        # WAL must be set via direct execute, not executescript
        self._db.execute("PRAGMA journal_mode=WAL")

        self._ensure_schema()
        self._check_integrity()

        logger.info("FileSystemIndexService initialized: %s", resolved_path)

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Create tables if missing and run pending migrations."""
        if not self.table_exists("schema_version"):
            self.execute(_SCHEMA_SQL)
            # Record the initial schema version
            self.insert(
                "schema_version",
                {
                    "version": self.SCHEMA_VERSION,
                    "applied_at": _now_iso(),
                    "description": "Initial schema",
                },
            )
            logger.info("Schema created at version %d", self.SCHEMA_VERSION)
        else:
            self.migrate()

    def _check_integrity(self) -> bool:
        """
        Run ``PRAGMA integrity_check`` on the database.

        If corruption is detected the database file is deleted and the
        schema is recreated from scratch.

        Returns:
            True if the database is healthy, False if it was rebuilt.
        """
        try:
            result = self.query("PRAGMA integrity_check", one=True)
            if result and result.get("integrity_check") == "ok":
                return True
        except Exception as exc:
            logger.error("Integrity check failed: %s", exc)

        logger.warning("Database corruption detected, rebuilding...")
        db_path = self._db.execute("PRAGMA database_list").fetchone()[2]
        self.close_db()

        try:
            Path(db_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.error("Failed to delete corrupt database: %s", exc)

        self.init_db(db_path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self.execute(_SCHEMA_SQL)
        self.insert(
            "schema_version",
            {
                "version": self.SCHEMA_VERSION,
                "applied_at": _now_iso(),
                "description": "Initial schema (rebuilt after corruption)",
            },
        )
        return False

    def _get_schema_version(self) -> int:
        """
        Get the current schema version from the database.

        Returns:
            Current schema version number, or 0 if no version recorded.
        """
        if not self.table_exists("schema_version"):
            return 0
        row = self.query("SELECT MAX(version) AS ver FROM schema_version", one=True)
        return row["ver"] if row and row["ver"] is not None else 0

    def migrate(self) -> None:
        """
        Apply pending schema migrations.

        Each migration is guarded by a version check so it runs at most once.
        """
        current = self._get_schema_version()

        if current < self.SCHEMA_VERSION:
            logger.info(
                "Migrating schema from v%d to v%d", current, self.SCHEMA_VERSION
            )
            # Future migrations go here as elif blocks:
            # if current < 2:
            #     self.execute("ALTER TABLE files ADD COLUMN tags TEXT")
            #     self.insert("schema_version", {"version": 2, ...})

            # Ensure tables exist (idempotent CREATE IF NOT EXISTS)
            self.execute(_SCHEMA_SQL)
            if current < 1:
                self.insert(
                    "schema_version",
                    {
                        "version": 1,
                        "applied_at": _now_iso(),
                        "description": "Initial schema",
                    },
                )

    # ------------------------------------------------------------------
    # Directory scanning
    # ------------------------------------------------------------------

    def scan_directory(
        self,
        path: str,
        max_depth: int = 10,
        exclude_patterns: Optional[List[str]] = None,
        incremental: bool = True,
    ) -> Dict[str, Any]:
        """
        Walk a directory tree and populate the file index.

        Uses ``os.scandir()`` for performance.  For incremental scans the
        file's size and mtime are compared against the existing index
        entry -- unchanged files are skipped.

        Args:
            path: Root directory to scan.
            max_depth: Maximum directory depth to descend into.
            exclude_patterns: Additional directory/file names to skip.
            incremental: If True, only update changed files.

        Returns:
            Dict with keys: ``files_scanned``, ``files_added``,
            ``files_updated``, ``files_removed``, ``duration_ms``.
        """
        root = Path(path).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Directory not found: {path}")

        started_at = _now_iso()
        t0 = time.monotonic()

        excludes = self._build_excludes(exclude_patterns)

        # Collect existing indexed paths under this root for stale-detection
        root_str = str(root)
        existing_paths: set = set()
        if incremental:
            rows = self.query(
                "SELECT path FROM files WHERE path LIKE :prefix",
                {"prefix": root_str + "%"},
            )
            existing_paths = {r["path"] for r in rows}

        stats = {
            "files_scanned": 0,
            "files_added": 0,
            "files_updated": 0,
            "files_removed": 0,
        }
        seen_paths: set = set()

        self._walk(root, 0, max_depth, excludes, incremental, stats, seen_paths)

        # Remove stale entries (files in index that no longer exist on disk)
        if incremental:
            stale = existing_paths - seen_paths
            if stale:
                stats["files_removed"] = self._remove_paths(stale)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        stats["duration_ms"] = elapsed_ms

        # Update directory_stats for the root
        self._update_directory_stats(root_str)

        # Log the scan
        completed_at = _now_iso()
        self.insert(
            "scan_log",
            {
                "directory": root_str,
                "started_at": started_at,
                "completed_at": completed_at,
                "files_scanned": stats["files_scanned"],
                "files_added": stats["files_added"],
                "files_updated": stats["files_updated"],
                "files_removed": stats["files_removed"],
                "duration_ms": elapsed_ms,
            },
        )

        logger.info(
            "Scan complete: %s  scanned=%d added=%d updated=%d removed=%d (%dms)",
            root_str,
            stats["files_scanned"],
            stats["files_added"],
            stats["files_updated"],
            stats["files_removed"],
            elapsed_ms,
        )
        return stats

    def _walk(
        self,
        directory: Path,
        current_depth: int,
        max_depth: int,
        excludes: set,
        incremental: bool,
        stats: Dict[str, int],
        seen_paths: set,
    ) -> None:
        """Recursively walk *directory* using ``os.scandir``."""
        if current_depth > max_depth:
            return

        try:
            entries = list(os.scandir(str(directory)))
        except (PermissionError, OSError) as exc:
            logger.debug("Skipping inaccessible directory %s: %s", directory, exc)
            return

        for entry in entries:
            try:
                name = entry.name
            except UnicodeDecodeError:
                logger.debug("Skipping entry with undecodable name in %s", directory)
                continue

            if name in excludes:
                continue

            try:
                entry_path = str(Path(entry.path).resolve())
            except (OSError, ValueError):
                continue

            seen_paths.add(entry_path)

            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue

            if is_dir:
                # Index the directory itself
                self._index_entry(
                    entry,
                    entry_path,
                    current_depth,
                    is_directory=True,
                    incremental=incremental,
                    stats=stats,
                )
                self._walk(
                    Path(entry_path),
                    current_depth + 1,
                    max_depth,
                    excludes,
                    incremental,
                    stats,
                    seen_paths,
                )
            elif is_file:
                self._index_entry(
                    entry,
                    entry_path,
                    current_depth,
                    is_directory=False,
                    incremental=incremental,
                    stats=stats,
                )

    def _index_entry(
        self,
        entry: os.DirEntry,
        resolved_path: str,
        depth: int,
        is_directory: bool,
        incremental: bool,
        stats: Dict[str, int],
    ) -> None:
        """Index a single file or directory entry."""
        stats["files_scanned"] += 1

        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError as exc:
            logger.debug("Cannot stat %s: %s", resolved_path, exc)
            return

        size = stat.st_size if not is_directory else 0
        mtime_iso = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()
        try:
            ctime_iso = datetime.datetime.fromtimestamp(stat.st_ctime).isoformat()
        except (OSError, ValueError):
            ctime_iso = mtime_iso

        name = entry.name
        extension = _get_extension(name)
        parent_dir = str(Path(resolved_path).parent)

        # Incremental: check if unchanged
        if incremental:
            existing = self.query(
                "SELECT id, size, modified_at FROM files WHERE path = :path",
                {"path": resolved_path},
                one=True,
            )
            if existing:
                if existing["size"] == size and existing["modified_at"] == mtime_iso:
                    return  # unchanged
                # File changed -- update
                mime_type = mimetypes.guess_type(name)[0] if not is_directory else None
                self.update(
                    "files",
                    {
                        "name": name,
                        "extension": extension,
                        "mime_type": mime_type,
                        "size": size,
                        "created_at": ctime_iso,
                        "modified_at": mtime_iso,
                        "parent_dir": parent_dir,
                        "depth": depth,
                        "is_directory": is_directory,
                        "indexed_at": _now_iso(),
                    },
                    "id = :id",
                    {"id": existing["id"]},
                )
                self._upsert_categories(existing["id"], extension)
                stats["files_updated"] += 1
                return

        # New entry
        mime_type = mimetypes.guess_type(name)[0] if not is_directory else None
        file_id = self.insert(
            "files",
            {
                "path": resolved_path,
                "name": name,
                "extension": extension,
                "mime_type": mime_type,
                "size": size,
                "created_at": ctime_iso,
                "modified_at": mtime_iso,
                "parent_dir": parent_dir,
                "depth": depth,
                "is_directory": is_directory,
                "indexed_at": _now_iso(),
            },
        )
        self._upsert_categories(file_id, extension)
        stats["files_added"] += 1

    def _upsert_categories(self, file_id: int, extension: Optional[str]) -> None:
        """Insert or replace category rows for a file."""
        # Remove existing categories
        self.delete("file_categories", "file_id = :fid", {"fid": file_id})

        if not extension:
            return

        category, subcategory = _auto_categorize(extension)
        self.insert(
            "file_categories",
            {
                "file_id": file_id,
                "category": category,
                "subcategory": subcategory,
            },
        )

    def _remove_paths(self, paths: set) -> int:
        """Remove stale paths from the index. Returns count removed."""
        removed = 0
        for p in paths:
            removed += self.delete("files", "path = :path", {"path": p})
        return removed

    def _update_directory_stats(self, root_path: str) -> None:
        """Compute and cache directory statistics for *root_path*."""
        rows = self.query(
            "SELECT size, extension, depth, is_directory FROM files "
            "WHERE path LIKE :prefix",
            {"prefix": root_path + "%"},
        )

        total_size = 0
        file_count = 0
        dir_count = 0
        deepest_depth = 0
        ext_counter: Dict[str, int] = {}

        for r in rows:
            if r["is_directory"]:
                dir_count += 1
            else:
                file_count += 1
                total_size += r["size"] or 0
            depth = r["depth"] or 0
            if depth > deepest_depth:
                deepest_depth = depth
            ext = r["extension"]
            if ext:
                ext_counter[ext] = ext_counter.get(ext, 0) + 1

        # Top 10 most common extensions
        sorted_exts = sorted(ext_counter.items(), key=lambda x: x[1], reverse=True)
        common_extensions = ",".join(e for e, _ in sorted_exts[:10])

        # Upsert into directory_stats
        existing = self.query(
            "SELECT path FROM directory_stats WHERE path = :path",
            {"path": root_path},
            one=True,
        )
        now = _now_iso()
        if existing:
            self.update(
                "directory_stats",
                {
                    "total_size": total_size,
                    "file_count": file_count,
                    "dir_count": dir_count,
                    "deepest_depth": deepest_depth,
                    "common_extensions": common_extensions,
                    "last_scanned": now,
                },
                "path = :path",
                {"path": root_path},
            )
        else:
            self.insert(
                "directory_stats",
                {
                    "path": root_path,
                    "total_size": total_size,
                    "file_count": file_count,
                    "dir_count": dir_count,
                    "deepest_depth": deepest_depth,
                    "common_extensions": common_extensions,
                    "last_scanned": now,
                },
            )

    def _build_excludes(self, user_patterns: Optional[List[str]] = None) -> set:
        """Merge default and platform-specific excludes with user patterns."""
        excludes = set(_DEFAULT_EXCLUDES)

        if sys.platform == "win32":
            excludes.update(_WINDOWS_EXCLUDES)
        else:
            excludes.update(_UNIX_EXCLUDES)

        if user_patterns:
            excludes.update(user_patterns)

        return excludes

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query_files(
        self,
        name: Optional[str] = None,
        extension: Optional[str] = None,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        modified_after: Optional[str] = None,
        modified_before: Optional[str] = None,
        parent_dir: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """
        Query the file index with flexible filters.

        Uses FTS5 ``MATCH`` for name queries and SQL ``WHERE`` clauses for
        everything else.  Filters are combined with ``AND``.

        Args:
            name: Full-text search on file name (FTS5 MATCH).
            extension: Exact extension match (without leading dot).
            min_size: Minimum file size in bytes.
            max_size: Maximum file size in bytes.
            modified_after: ISO timestamp lower bound.
            modified_before: ISO timestamp upper bound.
            parent_dir: Filter by parent directory path.
            category: Filter by file category.
            limit: Maximum results to return (default 25).

        Returns:
            List of file dicts.
        """
        params: Dict[str, Any] = {}
        conditions: List[str] = []
        joins: List[str] = []

        if name:
            # Use FTS5 for name search
            joins.append("JOIN files_fts ON files.id = files_fts.rowid")
            conditions.append("files_fts MATCH :name")
            params["name"] = name

        if extension:
            conditions.append("files.extension = :ext")
            params["ext"] = extension.lower().lstrip(".")

        if min_size is not None:
            conditions.append("files.size >= :min_size")
            params["min_size"] = min_size

        if max_size is not None:
            conditions.append("files.size <= :max_size")
            params["max_size"] = max_size

        if modified_after:
            conditions.append("files.modified_at >= :mod_after")
            params["mod_after"] = modified_after

        if modified_before:
            conditions.append("files.modified_at <= :mod_before")
            params["mod_before"] = modified_before

        if parent_dir:
            conditions.append("files.parent_dir = :parent_dir")
            params["parent_dir"] = parent_dir

        if category:
            joins.append("JOIN file_categories fc ON files.id = fc.file_id")
            conditions.append("fc.category = :category")
            params["category"] = category

        join_sql = " ".join(joins)
        where_sql = " AND ".join(conditions) if conditions else "1=1"

        sql = (
            f"SELECT DISTINCT files.* FROM files {join_sql} "
            f"WHERE {where_sql} "
            f"ORDER BY files.modified_at DESC "
            f"LIMIT :lim"
        )
        params["lim"] = limit

        return self.query(sql, params)

    # ------------------------------------------------------------------
    # Directory stats
    # ------------------------------------------------------------------

    def get_directory_stats(self, path: str) -> Optional[Dict[str, Any]]:
        """
        Get cached directory statistics.

        Args:
            path: Directory path to look up.

        Returns:
            Dict with ``total_size``, ``file_count``, ``dir_count``,
            ``deepest_depth``, ``common_extensions``, ``last_scanned``,
            or None if the directory has not been scanned.
        """
        resolved = str(Path(path).resolve())
        return self.query(
            "SELECT * FROM directory_stats WHERE path = :path",
            {"path": resolved},
            one=True,
        )

    # ------------------------------------------------------------------
    # Categorization
    # ------------------------------------------------------------------

    def auto_categorize(self, file_path: str) -> Tuple[str, str]:
        """
        Categorize a file by its extension.

        Delegates to :func:`gaia.filesystem.categorizer.auto_categorize`.

        Args:
            file_path: Path to the file.

        Returns:
            Tuple of ``(category, subcategory)``.
        """
        ext = _get_extension(Path(file_path).name)
        return _auto_categorize(ext) if ext else ("other", "unknown")

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_statistics(self) -> Dict[str, Any]:
        """
        Return aggregate index statistics.

        Returns:
            Dict with ``total_files``, ``total_directories``,
            ``total_size_bytes``, ``categories``, ``top_extensions``,
            and ``last_scan``.
        """
        total_files_row = self.query(
            "SELECT COUNT(*) AS cnt FROM files WHERE is_directory = 0", one=True
        )
        total_dirs_row = self.query(
            "SELECT COUNT(*) AS cnt FROM files WHERE is_directory = 1", one=True
        )
        size_row = self.query(
            "SELECT COALESCE(SUM(size), 0) AS total FROM files "
            "WHERE is_directory = 0",
            one=True,
        )

        categories = self.query(
            "SELECT category, COUNT(*) AS cnt FROM file_categories "
            "GROUP BY category ORDER BY cnt DESC"
        )

        top_exts = self.query(
            "SELECT extension, COUNT(*) AS cnt FROM files "
            "WHERE extension IS NOT NULL AND is_directory = 0 "
            "GROUP BY extension ORDER BY cnt DESC LIMIT 15"
        )

        last_scan_row = self.query(
            "SELECT * FROM scan_log ORDER BY completed_at DESC LIMIT 1",
            one=True,
        )

        return {
            "total_files": total_files_row["cnt"] if total_files_row else 0,
            "total_directories": total_dirs_row["cnt"] if total_dirs_row else 0,
            "total_size_bytes": size_row["total"] if size_row else 0,
            "categories": {r["category"]: r["cnt"] for r in categories},
            "top_extensions": {r["extension"]: r["cnt"] for r in top_exts},
            "last_scan": dict(last_scan_row) if last_scan_row else None,
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup_stale(self, max_age_days: int = 30) -> int:
        """
        Remove entries for files that no longer exist on disk.

        Args:
            max_age_days: Only check files indexed more than this many days
                          ago.  Set to 0 to check all entries.

        Returns:
            Number of stale entries removed.
        """
        if max_age_days > 0:
            cutoff = (
                datetime.datetime.now() - datetime.timedelta(days=max_age_days)
            ).isoformat()
            rows = self.query(
                "SELECT id, path FROM files WHERE indexed_at < :cutoff",
                {"cutoff": cutoff},
            )
        else:
            rows = self.query("SELECT id, path FROM files")

        removed = 0
        for row in rows:
            if not Path(row["path"]).exists():
                self.delete("files", "id = :id", {"id": row["id"]})
                removed += 1

        logger.info("Cleaned up %d stale entries", removed)
        return removed

    # ------------------------------------------------------------------
    # Bookmarks
    # ------------------------------------------------------------------

    def add_bookmark(
        self,
        path: str,
        label: Optional[str] = None,
        category: Optional[str] = None,
    ) -> int:
        """
        Add a bookmark for a file or directory.

        Args:
            path: Absolute path to bookmark.
            label: Human-readable label.
            category: Bookmark category (e.g., "project", "docs").

        Returns:
            The bookmark's row id.
        """
        resolved = str(Path(path).resolve())
        # Check for existing bookmark
        existing = self.query(
            "SELECT id FROM bookmarks WHERE path = :path",
            {"path": resolved},
            one=True,
        )
        if existing:
            self.update(
                "bookmarks",
                {"label": label, "category": category},
                "id = :id",
                {"id": existing["id"]},
            )
            return existing["id"]

        return self.insert(
            "bookmarks",
            {
                "path": resolved,
                "label": label,
                "category": category,
                "created_at": _now_iso(),
            },
        )

    def remove_bookmark(self, path: str) -> bool:
        """
        Remove a bookmark by path.

        Args:
            path: The bookmarked path to remove.

        Returns:
            True if a bookmark was removed, False otherwise.
        """
        resolved = str(Path(path).resolve())
        count = self.delete("bookmarks", "path = :path", {"path": resolved})
        return count > 0

    def list_bookmarks(self) -> List[Dict[str, Any]]:
        """
        List all bookmarks.

        Returns:
            List of bookmark dicts with ``id``, ``path``, ``label``,
            ``category``, and ``created_at``.
        """
        return self.query("SELECT * FROM bookmarks ORDER BY created_at DESC")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.datetime.now().isoformat()


def _get_extension(filename: str) -> Optional[str]:
    """
    Extract the lowercase extension from *filename* without leading dot.

    Returns None for files with no extension.
    """
    _, dot, ext = filename.rpartition(".")
    if dot and ext:
        return ext.lower()
    return None
