# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Put a SQLite database in WAL mode when another process may be opening it too."""

import sqlite3


def enable_wal(conn: sqlite3.Connection) -> None:
    """Switch *conn*'s database to WAL mode.

    The switch needs a lock SQLite will not wait for: while another connection
    holds the write lock (a second GAIA process creating the same new
    database's schema) it fails at once with "database is locked", whatever the
    busy timeout. Every GAIA opener switches first, so a locked database is
    normally already in WAL mode; this checks that and goes on. A database
    still in another mode raises, as before.
    """
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).lower():
            raise
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "wal":
            raise sqlite3.OperationalError(
                f"{exc}: another connection holds the database and it is in "
                f"{mode} mode, not WAL. Close the other program using it and retry."
            ) from exc
