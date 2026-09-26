# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Table tests for ``gaia.utils.parsing.extract_json_from_text``.

Every VLM structured-extraction path parses model output through this
function. The basic cases live in ``test_file_watcher.py::TestJsonExtraction``;
these cover the shapes models actually emit around the JSON.
"""

import pytest

from gaia.utils.parsing import extract_json_from_text

FOUND = [
    pytest.param(
        '```json\n{"a": 1, "b": [1, 2]}\n```',
        {"a": 1, "b": [1, 2]},
        id="fenced-object",
    ),
    pytest.param(
        'Here you go:\n```json\n[{"row": 1}, {"row": 2}]\n```\nAnything else?',
        [{"row": 1}, {"row": 2}],
        id="fenced-array-of-objects-with-prose",
    ),
    pytest.param(
        'Rows: [{"s": "a]b"}, {"s": "c"}] done',
        [{"s": "a]b"}, {"s": "c"}],
        id="close-bracket-inside-string-in-array",
    ),
    pytest.param(
        'Result: {"rows": [{"a": {"b": [1]}}]} -- end of output',
        {"rows": [{"a": {"b": [1]}}]},
        id="deeply-nested-with-trailing-text",
    ),
    pytest.param(
        '{"a": 1} and later {"b": 2}',
        {"a": 1},
        id="first-of-two-objects",
    ),
    pytest.param(
        'Name: {"name": "Zoë ✓"}',
        {"name": "Zoë ✓"},
        id="non-ascii-values",
    ),
    pytest.param(
        '[1, 2 was cut off, but then {"ok": 1}',
        {"ok": 1},
        id="unclosed-array-then-object",
    ),
    pytest.param(
        'Filled the {field} template: {"field": "total", "value": 3}',
        {"field": "total", "value": 3},
        id="placeholder-braces-before-object",
    ),
    pytest.param(
        'Use {a} and {b}, then {"x": [1]}',
        {"x": [1]},
        id="several-placeholders-before-object",
    ),
]


@pytest.mark.parametrize("text,expected", FOUND)
def test_extracts_the_json_value(text, expected):
    assert extract_json_from_text(text) == expected


NOT_FOUND = [
    pytest.param('{"a": 1, "b": 2', id="truncated-object"),
    pytest.param("The answer is {unknown}.", id="only-placeholder-braces"),
    # A malformed outer object must not yield one of its inner objects: that
    # would hand the caller a fragment that looks like a complete answer.
    pytest.param('{"a": {"b": 1}, oops}', id="malformed-outer-object"),
    pytest.param('{"a": {"b": 1}, "c": ', id="truncated-outer-object"),
]


@pytest.mark.parametrize("text", NOT_FOUND)
def test_returns_none_rather_than_a_fragment(text):
    assert extract_json_from_text(text) is None
