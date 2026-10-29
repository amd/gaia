# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Put a SQLite database in WAL mode when another process may be opening it too."""

import random
import sqlite3
import time

_RETRY_SLEEP_S = (0.005, 0.025)


def _is_locked(exc: sqlite3.OperationalError) -> bool:
    return "locked" in str(exc).lower()


def _database_path(conn: sqlite3.Connection) -> str:
    """Return the main database path, or a useful name for non-file databases."""
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    return path or "the in-memory or temporary database"


def enable_wal(conn: sqlite3.Connection) -> None:
    """Switch *conn*'s database to WAL mode.

    The switch needs a lock SQLite will not wait for: while another connection
    holds it (a second GAIA process creating the same new database) it fails at
    once with "database is locked", whatever the busy timeout. So a locked
    switch is retried until the database is in WAL mode or the connection's
    busy timeout runs out, which raises with the mode the database is in.
    """
    busy_timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    timeout_s = busy_timeout_ms / 1000
    started = time.monotonic()
    deadline = started + timeout_s
    attempts = 0
    mode = "unknown (locked)"
    while True:
        attempts += 1
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc):
                raise
            last = exc
        # Never let this diagnostic read wait out a second busy timeout.
        conn.execute("PRAGMA busy_timeout = 0")
        try:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc):
                raise
            mode = "unknown (locked)"
        finally:
            conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        if mode == "wal":
            return
        if time.monotonic() >= deadline:
            elapsed_s = time.monotonic() - started
            database_path = _database_path(conn)
            attempt_word = "attempt" if attempts == 1 else "attempts"
            raise sqlite3.OperationalError(
                f"{last}: could not switch {database_path} to WAL mode after "
                f"{elapsed_s:.2f} s ({attempts} {attempt_word}); it is still in "
                f"{mode} mode and another connection holds it. Close the other "
                "program using it and retry."
            ) from last
        time.sleep(random.uniform(*_RETRY_SLEEP_S))
