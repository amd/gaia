# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Two processes opening the same new transcript must both succeed."""

import pathlib
from unittest.mock import patch

from gaia.agents.base.history import SessionHistory


def test_a_file_created_by_another_process_first_does_not_raise(tmp_path):
    path = tmp_path / "transcript.db"
    path.touch()  # the other process won the create

    # The other process created the file after this one looked and found none.
    with patch.object(pathlib.Path, "exists", return_value=False):
        history = SessionHistory(path)

    history.append({"role": "user", "content": "hi"})
    assert path.exists()


def test_a_new_transcript_is_private(tmp_path):
    path = tmp_path / "new.db"

    SessionHistory(path)

    assert path.stat().st_mode & 0o077 == 0


def test_a_new_transcript_is_created_with_the_owner_column(tmp_path):
    import sqlite3

    path = tmp_path / "fresh.db"
    SessionHistory(path)

    columns = [
        row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(turns)")
    ]
    assert "owner" in columns


def test_two_opens_of_a_new_transcript_both_append(tmp_path):
    path = tmp_path / "shared.db"
    first = SessionHistory(path)
    second = SessionHistory(path)

    first.append({"role": "user", "content": "a"})
    second.append({"role": "user", "content": "b"})
