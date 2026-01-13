# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Database testing utilities for GAIA SDK users."""

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from gaia.database.mixin import DatabaseMixin


class _TempDB(DatabaseMixin):
    """Internal helper for temp_db fixture."""


@contextmanager
def temp_db(schema: Optional[str] = None):
    """
    Create a temporary SQLite database for testing.

    Creates a temporary database file that is automatically cleaned up
    after the context exits. Useful for testing agents that use DatabaseMixin.

    Args:
        schema: Optional SQL to execute on the database (CREATE TABLE, etc.)

    Yields:
        str: Path to the temporary database file

    Example:
        from gaia.database import temp_db

        def test_my_agent():
            schema = '''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
                )
            '''

            with temp_db(schema) as db_path:
                agent = MyAgent(skip_lemonade=True)
                agent.init_db(db_path)  # Use temp database
                agent.insert("users", {"name": "Alice"})
                assert len(agent.query("SELECT * FROM users")) == 1

        def test_empty_db():
            with temp_db() as db_path:
                agent = MyAgent(skip_lemonade=True)
                agent.init_db(db_path)
                # Agent can create its own schema
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")

        if schema:
            db = _TempDB()
            db.init_db(db_path)
            db.execute(schema)
            db.close_db()

        yield db_path
