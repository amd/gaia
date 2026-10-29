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
    timeout_s = conn.execute("PRAGMA busy_timeout").fetchone()[0] / 1000
    deadline = time.monotonic() + timeout_s
    attempts = 0
    while True:
        attempts += 1
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc):
                raise
            last = exc
        try:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc):
                raise
            mode = "unknown (locked)"
        if mode == "wal":
            return
        if time.monotonic() >= deadline:
            raise sqlite3.OperationalError(
                f"{last}: could not switch the database to WAL mode within "
                f"{timeout_s:g} s ({attempts} attempts); it is still in {mode} "
                "mode and another connection holds it. Close the other program "
                "using it and retry."
            ) from last
        time.sleep(random.uniform(*_RETRY_SLEEP_S))
