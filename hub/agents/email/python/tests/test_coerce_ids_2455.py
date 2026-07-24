# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression for #2455 — ``_coerce_ids`` must strip LLM quoting/brackets.

The model naturally emits batch ids as a quoted, comma-joined string
(``"id1","id2"``). Splitting on ``,`` left the literal quotes attached, so
Gmail rejected every id with "Invalid id value" and nothing was archived.
"""

import pytest
from gaia_agent_email.tools.organize_tools import _coerce_ids

_EXPECTED = ["id1", "id2", "id3"]


@pytest.mark.parametrize(
    "raw",
    [
        '"id1","id2","id3"',  # quoted, comma-joined (the #2455 repro)
        "id1,id2,id3",  # unquoted, comma-joined
        "id1\nid2\nid3".replace("\n", ","),  # newline collapsed to comma path
        '["id1","id2","id3"]',  # JSON-array-shaped string
        "id1; id2; id3",  # semicolon separator with spaces
        ["id1", "id2", "id3"],  # already a Python list
        ['"id1"', '"id2"', '"id3"'],  # Python list of quoted strings
    ],
)
def test_coerce_ids_normalizes_to_bare_ids(raw):
    assert _coerce_ids(raw) == _EXPECTED


def test_coerce_ids_single_bare_id():
    # Single-message batches: no comma to split on, so this exercises a
    # different branch than the multi-id cases above.
    assert _coerce_ids("id1") == ["id1"]
    assert _coerce_ids(["id1"]) == ["id1"]
    assert _coerce_ids('"id1"') == ["id1"]


def test_coerce_ids_empty_and_none():
    assert _coerce_ids(None) == []
    assert _coerce_ids("") == []
    assert _coerce_ids("[]") == []
    assert _coerce_ids([]) == []
    assert _coerce_ids('" , "') == []
