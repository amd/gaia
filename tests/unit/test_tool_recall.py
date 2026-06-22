# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the tool-recall gate (#1449).

Covers the pure join logic and the log/scorecard parsers — no live eval.
"""

from __future__ import annotations

from gaia.eval.tool_recall import (
    aggregate_escape_hatch,
    compute_recall,
    count_recovery_events_from_log,
    escape_hatch_rate_from_log,
    parse_called_sets_from_scorecard,
    parse_loaded_sets_from_log,
    parse_session_summaries_from_log,
)


def test_full_recall_when_called_subset_of_loaded():
    loaded = [[["read_file", "query_documents"], ["query_documents"]]]
    called = [[["read_file"], ["query_documents"]]]
    report = compute_recall(loaded, called)
    assert report.recall == 1.0
    assert report.all_missing == []


def test_miss_reported_when_called_tool_not_loaded():
    loaded = [[["read_file"]]]
    called = [[["read_file", "query_documents"]]]
    report = compute_recall(loaded, called)
    assert report.recall == 0.0
    assert report.all_missing == ["query_documents"]
    assert report.turns[0].missing == ["query_documents"]


def test_partial_recall_across_turns():
    loaded = [[["read_file"], ["query_documents"]]]
    called = [[["read_file"], ["query_documents", "search_file"]]]
    report = compute_recall(loaded, called)
    assert report.recall == 0.5  # turn 0 ok, turn 1 missing search_file


def test_empty_called_set_is_trivially_satisfied():
    loaded = [[["read_file"], []]]
    called = [[[], []]]
    report = compute_recall(loaded, called)
    assert report.recall == 1.0


def test_scenario_count_mismatch_warns():
    loaded = [[["a"]], [["b"]]]
    called = [[["a"]]]
    report = compute_recall(loaded, called)
    assert any("scenario count mismatch" in w for w in report.alignment_warnings)


def test_turn_count_mismatch_warns_and_scores_overlap():
    loaded = [[["a"], ["b"]]]
    called = [[["a"]]]  # one fewer turn
    report = compute_recall(loaded, called)
    assert any("turn count mismatch" in w for w in report.alignment_warnings)
    assert len(report.turns) == 1  # scored the overlapping turn only


# ── log parsing ───────────────────────────────────────────────────────────


def test_parse_loaded_sets_splits_scenarios_on_turn_1():
    log = "\n".join(
        [
            "some noise",
            'TOOL_LOADER {"turn": 1, "loaded": ["read_file"]}',
            'TOOL_LOADER {"turn": 2, "loaded": ["read_file", "query_documents"]}',
            "[INFO] unrelated line",
            'TOOL_LOADER {"turn": 1, "loaded": ["remember"]}',
        ]
    )
    scenarios = parse_loaded_sets_from_log(log)
    assert scenarios == [
        [["read_file"], ["read_file", "query_documents"]],
        [["remember"]],
    ]


def test_parse_loaded_sets_ignores_escape_hatch_lines():
    log = "\n".join(
        [
            'TOOL_LOADER {"turn": 1, "loaded": ["read_file"]}',
            '{"event": "TOOL_LOADER_ESCAPE_HATCH", "tool": "write_file", "turn": 1}',
        ]
    )
    scenarios = parse_loaded_sets_from_log(log)
    assert scenarios == [[["read_file"]]]


def test_parse_called_sets_from_scorecard():
    scorecard = {
        "scenarios": [
            {
                "turns": [
                    {"agent_tools": ["read_file"]},
                    {"agent_tools": None},
                ]
            },
            {"turns": [{"agent_tools": ["remember"]}]},
        ]
    }
    called = parse_called_sets_from_scorecard(scorecard)
    assert called == [[["read_file"], []], [["remember"]]]


# ── Part 2 (#1450): load_tools coalesce + gate flip ────────────────────────


def test_parse_loaded_sets_unions_load_tools_lines_within_a_turn():
    """A mid-loop load_tools line unions into its turn (not a new turn/scenario)."""
    log = "\n".join(
        [
            'TOOL_LOADER {"turn": 1, "loaded": ["read_file", "load_tools"]}',
            'TOOL_LOADER {"turn": 1, "event": "load_tools", "bundle": '
            '"file_search", "loaded": ["read_file", "load_tools", "search_file"]}',
            'TOOL_LOADER {"turn": 2, "loaded": ["read_file", "load_tools", '
            '"search_file"]}',
        ]
    )
    scenarios = parse_loaded_sets_from_log(log)
    assert len(scenarios) == 1
    assert len(scenarios[0]) == 2  # two turns, not three log lines
    assert scenarios[0][0] == ["load_tools", "read_file", "search_file"]  # unioned


def test_parse_loaded_sets_splits_consecutive_single_turn_scenarios():
    """Two single-turn scenarios still split — only event-less lines move cursor."""
    log = "\n".join(
        [
            'TOOL_LOADER {"turn": 1, "loaded": ["read_file"]}',
            'TOOL_LOADER {"turn": 1, "loaded": ["remember"]}',
        ]
    )
    assert parse_loaded_sets_from_log(log) == [[["read_file"]], [["remember"]]]


def test_load_tools_call_is_always_satisfied():
    """Calling load_tools never counts as a recall miss (it is always-on CORE)."""
    loaded = [[["read_file"]]]
    called = [[["read_file", "load_tools"]]]
    report = compute_recall(loaded, called)
    assert report.recall == 1.0
    assert report.all_missing == []


def test_native_recovery_within_turn_passes_gate():
    """A tool surfaced mid-turn via load_tools is in the loaded set when called."""
    log = "\n".join(
        [
            'TOOL_LOADER {"turn": 1, "loaded": ["read_file", "load_tools"]}',
            'TOOL_LOADER {"turn": 1, "event": "load_tools", "bundle": '
            '"file_search", "loaded": ["read_file", "load_tools", "search_file"]}',
        ]
    )
    scorecard = {
        "scenarios": [{"turns": [{"agent_tools": ["load_tools", "search_file"]}]}]
    }
    report = compute_recall(
        parse_loaded_sets_from_log(log),
        parse_called_sets_from_scorecard(scorecard),
    )
    assert report.recall == 1.0


def test_unrecovered_miss_still_counts_against_recall():
    """A semantic miss with no load_tools recovery fails the gate (exemption gone)."""
    log = 'TOOL_LOADER {"turn": 1, "loaded": ["read_file", "load_tools"]}'
    scorecard = {"scenarios": [{"turns": [{"agent_tools": ["search_file"]}]}]}
    report = compute_recall(
        parse_loaded_sets_from_log(log),
        parse_called_sets_from_scorecard(scorecard),
    )
    assert report.recall == 0.0
    assert report.all_missing == ["search_file"]


# ── escape-hatch session summaries (τ-tuning signal) ───────────────────────


def test_parse_and_aggregate_session_summaries():
    log = "\n".join(
        [
            'TOOL_LOADER_SESSION {"turns": 4, "escape_hatch_count": 1, '
            '"load_tools_count": 1, "escape_hatch_rate": 0.5}',
            'TOOL_LOADER_SESSION {"turns": 6, "escape_hatch_count": 0, '
            '"load_tools_count": 2, "escape_hatch_rate": 0.333}',
        ]
    )
    summaries = parse_session_summaries_from_log(log)
    assert len(summaries) == 2
    agg = aggregate_escape_hatch(summaries)
    assert agg["sessions"] == 2
    assert agg["turns"] == 10  # 4 + 6
    assert agg["escape_hatch_count"] == 1  # 1 + 0
    assert agg["load_tools_count"] == 3  # 1 + 2
    assert agg["escape_hatch_rate"] == (1 + 3) / 10


def test_count_recovery_events_from_log():
    """Both escape-hatch paths counted from raw per-turn lines (no summary needed)."""
    log = "\n".join(
        [
            'TOOL_LOADER {"turn": 1, "loaded": ["read_file", "load_tools"]}',
            'TOOL_LOADER {"turn": 1, "event": "load_tools", "bundle": '
            '"file_search", "loaded": ["read_file", "load_tools", "search_file"]}',
            '{"event": "TOOL_LOADER_ESCAPE_HATCH", "tool": "write_file", "turn": 2}',
            'TOOL_LOADER {"turn": 2, "loaded": ["read_file", "load_tools", '
            '"search_file"]}',
        ]
    )
    free, loads = count_recovery_events_from_log(log)
    assert free == 1  # the ESCAPE_HATCH line
    assert loads == 1  # the load_tools event line


def test_escape_hatch_rate_from_log_works_without_session_summary():
    """Eval case: no TOOL_LOADER_SESSION line, rate derived from per-turn events."""
    log = "\n".join(
        [
            'TOOL_LOADER {"turn": 1, "loaded": ["load_tools"]}',
            'TOOL_LOADER {"turn": 1, "event": "load_tools", "bundle": '
            '"file_search", "loaded": ["load_tools", "search_file"]}',
            'TOOL_LOADER {"turn": 2, "loaded": ["load_tools", "search_file"]}',
            'TOOL_LOADER {"turn": 2, "event": "load_tools", "bundle": '
            '"rag_index", "loaded": ["load_tools", "search_file", "index_document"]}',
        ]
    )
    loaded = parse_loaded_sets_from_log(log)  # 1 scenario, 2 turns
    eh = escape_hatch_rate_from_log(log, loaded)
    assert eh["turns"] == 2
    assert eh["load_tools_count"] == 2
    assert eh["free_recovery_count"] == 0
    assert eh["escape_hatch_rate"] == 1.0  # (0 + 2) / 2 — high ⇒ τ too strict here
    assert eh["session_summaries"] == 0  # eval logs carry none


def test_session_and_selection_parsers_do_not_cross_contaminate():
    log = "\n".join(
        [
            'TOOL_LOADER {"turn": 1, "loaded": ["read_file"]}',
            'TOOL_LOADER_SESSION {"turns": 1, "escape_hatch_count": 0, '
            '"load_tools_count": 0, "escape_hatch_rate": 0.0}',
        ]
    )
    assert parse_loaded_sets_from_log(log) == [[["read_file"]]]
    assert len(parse_session_summaries_from_log(log)) == 1


def test_end_to_end_log_and_scorecard_join():
    log = "\n".join(
        [
            'TOOL_LOADER {"turn": 1, "loaded": ["read_file", "query_documents"]}',
            'TOOL_LOADER {"turn": 2, "loaded": ["read_file", "query_documents"]}',
        ]
    )
    scorecard = {
        "scenarios": [
            {
                "turns": [
                    {"agent_tools": ["read_file"]},
                    {"agent_tools": ["query_documents"]},
                ]
            }
        ]
    }
    report = compute_recall(
        parse_loaded_sets_from_log(log),
        parse_called_sets_from_scorecard(scorecard),
    )
    assert report.recall == 1.0
    assert not report.alignment_warnings
