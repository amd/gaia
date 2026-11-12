# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Put a SQLite database in WAL mode when another process may be opening it too."""

import random
import sqlite3
import time

_RETRY_SLEEP_S = (0.005, 0.025)


def _is_locked(exc: sqlite3.OperationalError) -> bool:
    return "locked" in str(exc).lower()


def enable_wal(conn: sqlite3.Connection) -> None:
    """Switch *conn*'s database to WAL mode.

    The switch needs a lock SQLite will not wait for: while another connection
    holds it (a second GAIA process creating the same new database) it fails at
    once with "database is locked", whatever the busy timeout. So a locked
    switch is retried until the database is in WAL mode or the connection's
    busy timeout runs out, which raises with the mode the database is in.
    """
    configured_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    start = time.monotonic()
    deadline = start + configured_ms / 1000
    attempts = 0

    def set_remaining() -> None:
        # Every statement waits the busy timeout itself, so give it only what is left.
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        conn.execute(f"PRAGMA busy_timeout={remaining_ms}")

    try:
        while True:
            attempts += 1
            set_remaining()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if not _is_locked(exc):
                    raise
                last = exc
            set_remaining()
            try:
                mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            except sqlite3.OperationalError as exc:
                if not _is_locked(exc):
                    raise
                mode = "unknown (locked)"
            if mode == "wal":
                return
            if time.monotonic() >= deadline:
                path = conn.execute("PRAGMA database_list").fetchone()[2]
                raise sqlite3.OperationalError(
                    f"{last}: could not switch {path or 'the database'} to WAL "
                    f"mode after {time.monotonic() - start:.1f} s "
                    f"({attempts} {'attempt' if attempts == 1 else 'attempts'}); "
                    f"it is still in {mode} mode and another connection holds "
                    "it. Close the other program using it and retry."
                ) from last
            time.sleep(random.uniform(*_RETRY_SLEEP_S))
    finally:
        conn.execute(f"PRAGMA busy_timeout={configured_ms}")
