# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The shared failure classifier must not cry wolf, crash, or miss a real failure."""

import json
import time

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tool_outcome import (
    FAILED_STATUSES,
    tool_result_dict,
    tool_result_error_fields,
    tool_result_failed,
)
from gaia.agents.base.turn_scope import _is_tool_failure
from gaia.ui.sse_handler import tool_result_failed as ui_tool_result_failed


def _every_reader(result):
    return {
        "loop": Agent._is_error_result(result),
        "classifier": tool_result_failed(result),
        "ui": ui_tool_result_failed(result),
        "scope": _is_tool_failure(result),
    }


@pytest.mark.parametrize(
    "result",
    [
        "The parser raised an error: it was a typo, fixed now.",
        "Saved. Error: none.",
        "No Error: the run was clean.",
        "error: lowercase lines are log text, not an envelope",
        "Errors: 0, warnings: 2",
        "Error\nreading the manual helps.",
        " Error: leading space is quoted output, not an envelope",
        "{not json at all",
        '{"status": "error"} trailing text makes this not JSON',
        "{}",
        "[]",
        '["error", "status"]',
    ],
    ids=lambda r: r[:30],
)
def test_prose_and_almost_json_are_not_failures(result):
    assert _every_reader(result) == dict.fromkeys(
        ("loop", "classifier", "ui", "scope"), False
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "success", "items": [{"status": "error", "error": "row 3"}]},
        {"ok": True, "results": [{"ok": False}, {"success": False}]},
        {"success": True, "data": {"error": "a field value", "status": "error"}},
        {"rows": [{"error": "x"}], "count": 1},
        {"status": "success", "error": "warning: retried once"},
        {"ok": True, "error": "stale cache refreshed"},
        {"error": None},
        {"error": ""},
        {"error": "   \n"},
        {"error": 0},
        {"error": False},
        {"error": ["a"]},
        {"error": {"code": 5}},
        {"has_errors": False},
        {"return_code": 0, "stderr": "warning: deprecated"},
        {"return_code": 1, "expected_exit": "no matches"},
    ],
)
def test_successful_json_strings_with_nested_errors_are_not_failures(payload):
    text = json.dumps(payload)
    assert not tool_result_failed(text)
    assert not tool_result_failed(payload)
    assert tool_result_failed(text) == tool_result_failed(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": False, "error": {"code": "ErrorItemNotFound"}},
        {"success": False},
        {"status": "error"},
        {"error": "ファイルが見つかりません"},
        {"error": "Datei nicht gefunden: ü.txt", "data": None},
        {"return_code": 127, "stderr": "command not found"},
        {"has_errors": True},
    ],
)
def test_real_failures_are_read_the_same_as_dicts_and_strings(payload):
    for shape in (payload, json.dumps(payload), json.dumps(payload, indent=2)):
        assert tool_result_failed(shape)


def test_unicode_json_without_ascii_escapes_is_read():
    text = json.dumps(
        {"ok": False, "error": "メールが見つかりません"}, ensure_ascii=False
    )
    assert tool_result_failed(text)
    assert tool_result_error_fields(text)["error"] == "メールが見つかりません"


def test_a_refusal_counts_where_refusals_count_and_nowhere_else():
    denied = json.dumps({"status": "denied", "error": "user declined"})
    assert tool_result_failed(denied, FAILED_STATUSES)
    assert ui_tool_result_failed(denied)
    assert _is_tool_failure(denied)
    assert not Agent._is_error_result(denied)


def test_huge_json_is_classified_quickly():
    rows = [{"id": i, "status": "error", "note": "x" * 50} for i in range(60_000)]
    ok = json.dumps({"status": "success", "rows": rows})
    failed = json.dumps({"ok": False, "error": "quota", "rows": rows})
    assert len(ok) > 5_000_000
    start = time.perf_counter()
    assert not tool_result_failed(ok)
    assert tool_result_failed(failed)
    assert time.perf_counter() - start < 3.0


def test_huge_non_json_starting_with_a_brace_is_not_a_failure():
    text = "{" + "x" * 5_000_000
    assert not tool_result_failed(text)
    assert tool_result_dict(text) is None


def test_json_nested_too_deep_to_parse_does_not_crash_the_loop():
    """A tool's text result can be any JSON-looking string; the parser's
    recursion limit used to escape as RecursionError from every reader."""
    deep = '{"a":' * 100_000 + "1" + "}" * 100_000
    assert tool_result_dict(deep) is None
    assert _every_reader(deep) == dict.fromkeys(
        ("loop", "classifier", "ui", "scope"), False
    )
    assert tool_result_error_fields(deep) == {"error": deep}


@pytest.mark.parametrize("result", [True, False, 0, 1, 3.5, [], ["error"], b"Error: x"])
def test_non_text_non_dict_results_never_crash_or_fail(result):
    assert _every_reader(result) == dict.fromkeys(
        ("loop", "classifier", "ui", "scope"), False
    )
