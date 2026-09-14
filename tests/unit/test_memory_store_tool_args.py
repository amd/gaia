# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stored tool arguments must parse back, however large the call was.

Cutting the serialized JSON at the size cap made every large write_file and
edit_file call unreadable, and skill synthesis then learned recipes with no
file paths or content in their edit steps.
"""

import json

from gaia.agents.base.memory_store import MAX_FTS_QUERY_LENGTH, _bounded_args_json


def test_small_args_are_stored_verbatim():
    args = {"file_path": "toybox/dates.py", "old": "a", "new": "b"}
    assert json.loads(_bounded_args_json(args)) == args


def test_large_file_content_still_parses_and_keeps_the_path():
    args = {"file_path": "toybox/dates.py", "content": "x" * 100_000}
    stored = _bounded_args_json(args)
    assert len(stored) <= MAX_FTS_QUERY_LENGTH
    parsed = json.loads(stored)
    assert parsed["file_path"] == "toybox/dates.py"
    assert parsed["content"].startswith("x") and parsed["content"].endswith("...")


def test_edit_with_two_large_strings_still_parses():
    args = {"file_path": "a.py", "old_string": "o" * 5000, "new_string": "n" * 5000}
    parsed = json.loads(_bounded_args_json(args))
    assert parsed["file_path"] == "a.py"
    assert set(parsed) == {"file_path", "old_string", "new_string"}


def test_args_too_wide_to_shorten_fall_back_to_their_names():
    args = {f"key_{i:03d}": {"nested": i} for i in range(200)}
    stored = _bounded_args_json(args)
    assert len(stored) <= MAX_FTS_QUERY_LENGTH
    parsed = json.loads(stored)
    assert parsed["_truncated"] is True and parsed["keys"][0] == "key_000"


def test_no_args_stores_nothing():
    assert _bounded_args_json({}) is None
    assert _bounded_args_json(None) is None
