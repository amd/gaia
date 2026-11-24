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
