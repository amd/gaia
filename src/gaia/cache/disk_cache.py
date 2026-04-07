# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SQLite-based disk cache implementation for GAIA.

Provides persistent storage for cache overflow and long-term caching
using SQLite with WAL mode for concurrent access.

Example:
    from gaia.cache import DiskCache

    cache = DiskCache("./gaia_cache.db", max_entries=10000)
    await cache.set("key", b"serialized_data", time.time() + 3600)
    data = await cache.get("key")
    await cache.close()
"""

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gaia.cache.exceptions import CacheConnectionError


class DiskCache:
    """
    SQLite-based disk cache for overflow and persistence.

    Uses SQLite with WAL (Write-Ahead Logging) mode for concurrent
    read/write access. Automatically creates schema on initialization.

    Schema:
        CREATE TABLE cache_entries (
            key TEXT PRIMARY KEY,
            value BLOB NOT NULL,
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL,
            access_count INTEGER DEFAULT 0
        )

    Attributes:
        db_path: Path to SQLite database file
        max_entries: Maximum entries before cleanup

    Example:
        >>> cache = DiskCache("./cache.db", max_entries=10000)
        >>> await cache.set("user:1", serialized_data, time.time() + 3600)
        >>> value = await cache.get("user:1")
        >>> deleted = await cache.cleanup_expired()
        >>> await cache.close()
    """

    # SQL schema for cache table
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS cache_entries (
            key TEXT PRIMARY KEY,
            value BLOB NOT NULL,
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL,
            access_count INTEGER DEFAULT 0,
            last_accessed REAL NOT NULL
        )
    """

    # Index for expiry queries
    INDEX = """
        CREATE INDEX IF NOT EXISTS idx_expires_at ON cache_entries(expires_at)
    """

    def __init__(self, db_path: str, max_entries: int = 10000):
        """
        Initialize disk cache.

        Args:
            db_path: Path to SQLite database file
            max_entries: Maximum entries before cleanup (default: 10000)

        Raises:
            CacheConnectionError: If database cannot be opened/created

        Example:
            >>> cache = DiskCache("./gaia_cache.db")
            >>> print(f"Database: {cache.db_path}")
        """
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        self.db_path = str(Path(db_path).resolve())
        self.max_entries = max_entries
        self._lock = asyncio.Lock()
        self._sync_lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._closed = False

        # Initialize database
        self._init_db()

    def _init_db(self) -> None:
        """
        Initialize database connection and schema.

        Creates tables and indexes, enables WAL mode for concurrency.

        Raises:
            CacheConnectionError: If initialization fails
        """
        try:
            # Create parent directory if needed
            db_dir = Path(self.db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)

            # Connect with timeout for concurrent access
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                check_same_thread=False,
            )

            # Enable WAL mode for concurrent read/write
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            self._conn.execute("PRAGMA temp_store=MEMORY")

            # Create schema
            self._conn.execute(self.SCHEMA)
            self._conn.execute(self.INDEX)
            self._conn.commit()

        except sqlite3.Error as e:
            raise CacheConnectionError(
                f"Failed to initialize database: {e}",
                original_error=e,
            )

    def _get_conn(self) -> sqlite3.Connection:
        """
        Get database connection (thread-safe).

        Returns:
            Active SQLite connection

        Raises:
            CacheConnectionError: If database is closed
        """
        if self._closed or self._conn is None:
            raise CacheConnectionError("Database is closed")
        return self._conn

    async def get(self, key: str) -> Optional[bytes]:
        """
        Retrieve serialized value from disk cache.

        Updates access_count and last_accessed for LRU tracking.

        Args:
            key: Cache key to retrieve

        Returns:
            Serialized bytes if found and not expired, None otherwise

        Example:
            >>> data = await cache.get("user:1")
            >>> if data:
            ...     value = pickle.loads(data)
        """
        async with self._lock:
            conn = self._get_conn()
            current_time = time.time()

            try:
                cursor = conn.execute(
                    """
                    SELECT value FROM cache_entries
                    WHERE key = ? AND expires_at > ?
                    """,
                    (key, current_time),
                )
                row = cursor.fetchone()

                if row:
                    # Update access tracking
                    conn.execute(
                        """
                        UPDATE cache_entries
                        SET access_count = access_count + 1,
                            last_accessed = ?
                        WHERE key = ?
                        """,
                        (current_time, key),
                    )
                    conn.commit()
                    return row[0]

                return None

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to get key {key}: {e}",
                    key=key,
                    original_error=e,
                )

    async def set(
        self,
        key: str,
        value: bytes,
        expires_at: float,
    ) -> bool:
        """
        Store serialized value with TTL.

        Uses INSERT OR REPLACE for upsert behavior.

        Args:
            key: Cache key
            value: Serialized bytes to store
            expires_at: Absolute Unix timestamp for expiration

        Returns:
            True if successful, False on error

        Example:
            >>> import pickle
            >>> data = pickle.dumps({"name": "Alice"})
            >>> expiry = time.time() + 3600
            >>> success = await cache.set("user:1", data, expiry)
        """
        async with self._lock:
            conn = self._get_conn()
            current_time = time.time()

            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cache_entries
                    (key, value, expires_at, created_at, access_count, last_accessed)
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (key, value, expires_at, current_time, current_time),
                )
                conn.commit()
                return True

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to set key {key}: {e}",
                    key=key,
                    original_error=e,
                )

    async def delete(self, key: str) -> bool:
        """
        Delete entry from disk cache.

        Args:
            key: Cache key to delete

        Returns:
            True if key was deleted, False if not found

        Example:
            >>> deleted = await cache.delete("user:1")
            >>> if deleted:
            ...     print("Key removed from disk cache")
        """
        async with self._lock:
            conn = self._get_conn()

            try:
                cursor = conn.execute(
                    "DELETE FROM cache_entries WHERE key = ?",
                    (key,),
                )
                conn.commit()
                return cursor.rowcount > 0

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to delete key {key}: {e}",
                    key=key,
                    original_error=e,
                )

    async def clear(self) -> int:
        """
        Delete all entries from disk cache.

        Returns:
            Number of entries deleted

        Example:
            >>> count = await cache.clear()
            >>> print(f"Cleared {count} entries")
        """
        async with self._lock:
            conn = self._get_conn()

            try:
                cursor = conn.execute("DELETE FROM cache_entries")
                conn.commit()
                return cursor.rowcount

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to clear cache: {e}",
                    original_error=e,
                )

    async def cleanup_expired(self) -> int:
        """
        Remove expired entries from disk cache.

        Returns:
            Number of entries deleted

        Example:
            >>> removed = await cache.cleanup_expired()
            >>> print(f"Cleaned up {removed} expired entries")
        """
        async with self._lock:
            conn = self._get_conn()
            current_time = time.time()

            try:
                cursor = conn.execute(
                    "DELETE FROM cache_entries WHERE expires_at <= ?",
                    (current_time,),
                )
                conn.commit()
                return cursor.rowcount

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to cleanup expired entries: {e}",
                    original_error=e,
                )

    async def cleanup_lru(self, keep_count: Optional[int] = None) -> int:
        """
        Remove least recently used entries to reduce size.

        Args:
            keep_count: Number of entries to keep (uses max_entries if None)

        Returns:
            Number of entries deleted

        Example:
            >>> removed = await cache.cleanup_lru(keep_count=5000)
            >>> print(f"Evicted {removed} LRU entries")
        """
        async with self._lock:
            conn = self._get_conn()

            if keep_count is None:
                keep_count = self.max_entries

            try:
                # Get current count
                cursor = conn.execute("SELECT COUNT(*) FROM cache_entries")
                current_count = cursor.fetchone()[0]

                if current_count <= keep_count:
                    return 0

                # Delete oldest entries by last_accessed
                to_delete = current_count - keep_count
                cursor = conn.execute(
                    """
                    DELETE FROM cache_entries
                    WHERE key IN (
                        SELECT key FROM cache_entries
                        ORDER BY last_accessed ASC
                        LIMIT ?
                    )
                    """,
                    (to_delete,),
                )
                conn.commit()
                return cursor.rowcount

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to cleanup LRU entries: {e}",
                    original_error=e,
                )

    async def count(self) -> int:
        """
        Get total number of entries in disk cache.

        Returns:
            Entry count

        Example:
            >>> count = await cache.count()
            >>> print(f"Disk cache has {count} entries")
        """
        async with self._lock:
            conn = self._get_conn()

            try:
                cursor = conn.execute("SELECT COUNT(*) FROM cache_entries")
                return cursor.fetchone()[0]

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to count entries: {e}",
                    original_error=e,
                )

    async def size_bytes(self) -> int:
        """
        Get approximate disk cache size in bytes.

        Returns:
            Total size of cached data in bytes

        Example:
            >>> size = await cache.size_bytes()
            >>> print(f"Cache uses {size / 1024 / 1024:.1f}MB")
        """
        async with self._lock:
            conn = self._get_conn()

            try:
                cursor = conn.execute(
                    "SELECT SUM(length(value)) FROM cache_entries"
                )
                result = cursor.fetchone()[0]
                return result or 0

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to compute size: {e}",
                    original_error=e,
                )

    async def keys(self) -> List[str]:
        """
        Get all keys in disk cache.

        Returns:
            List of all cache keys

        Example:
            >>> keys = await cache.keys()
            >>> print(f"Keys: {keys[:10]}...")  # Show first 10
        """
        async with self._lock:
            conn = self._get_conn()

            try:
                cursor = conn.execute("SELECT key FROM cache_entries")
                return [row[0] for row in cursor.fetchall()]

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to get keys: {e}",
                    original_error=e,
                )

    async def contains(self, key: str) -> bool:
        """
        Check if key exists and is not expired.

        Args:
            key: Cache key to check

        Returns:
            True if key exists and is valid

        Example:
            >>> exists = await cache.contains("user:1")
            >>> if exists:
            ...     print("Key is in disk cache")
        """
        async with self._lock:
            conn = self._get_conn()
            current_time = time.time()

            try:
                cursor = conn.execute(
                    """
                    SELECT 1 FROM cache_entries
                    WHERE key = ? AND expires_at > ?
                    """,
                    (key, current_time),
                )
                return cursor.fetchone() is not None

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to check key {key}: {e}",
                    key=key,
                    original_error=e,
                )

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get disk cache statistics.

        Returns:
            Dictionary with size, count, and other metrics

        Example:
            >>> stats = await cache.get_stats()
            >>> print(f"Entries: {stats['count']}, Size: {stats['size_bytes']} bytes")
        """
        async with self._lock:
            conn = self._get_conn()
            current_time = time.time()

            try:
                # Total count
                cursor = conn.execute("SELECT COUNT(*) FROM cache_entries")
                total_count = cursor.fetchone()[0]

                # Valid (non-expired) count
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM cache_entries WHERE expires_at > ?",
                    (current_time,),
                )
                valid_count = cursor.fetchone()[0]

                # Total size
                cursor = conn.execute(
                    "SELECT SUM(length(value)) FROM cache_entries"
                )
                total_size = cursor.fetchone()[0] or 0

                # Average access count
                cursor = conn.execute(
                    "SELECT AVG(access_count) FROM cache_entries"
                )
                avg_access = cursor.fetchone()[0] or 0

                return {
                    "total_count": total_count,
                    "valid_count": valid_count,
                    "expired_count": total_count - valid_count,
                    "size_bytes": total_size,
                    "max_entries": self.max_entries,
                    "utilization": total_count / self.max_entries if self.max_entries > 0 else 0,
                    "avg_access_count": round(avg_access, 2),
                    "db_path": self.db_path,
                }

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to get stats: {e}",
                    original_error=e,
                )

    async def vacuum(self) -> None:
        """
        Vacuum database to reclaim space.

        Should be called periodically after bulk deletions.

        Example:
            >>> await cache.vacuum()
            >>> print("Database optimized")
        """
        async with self._lock:
            conn = self._get_conn()

            try:
                conn.execute("VACUUM")
                conn.commit()

            except sqlite3.Error as e:
                raise CacheConnectionError(
                    f"Failed to vacuum: {e}",
                    original_error=e,
                )

    async def close(self) -> None:
        """
        Close database connection.

        Should be called on application shutdown.

        Example:
            >>> await cache.close()
        """
        async with self._lock:
            if self._conn and not self._closed:
                try:
                    self._conn.commit()
                    self._conn.close()
                except sqlite3.Error:
                    pass
                finally:
                    self._conn = None
                    self._closed = True

    async def __aenter__(self) -> "DiskCache":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - closes connection."""
        await self.close()

    def __del__(self) -> None:
        """Destructor - ensure connection is closed."""
        if self._conn and not self._closed:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __repr__(self) -> str:
        """Return string representation."""
        with self._sync_lock:
            status = "closed" if self._closed else "open"
            return f"DiskCache(path={self.db_path!r}, status={status})"
