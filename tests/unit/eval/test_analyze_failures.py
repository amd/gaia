# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.eval.analyze_failures``.

The end-to-end pipeline (``main()``) needs disk fixtures; these tests
cover the pure helpers that do most of the analyzer's work: tool/style
derivation, timestamp parsing/offset, log/turn correlation, per-tool
rollup, failure-record building, and the ``--expected-tool-count`` gate.
"""

from datetime import datetime, timedelta, timezone

import pytest

from gaia.eval.analyze_failures import (
    FailureRecord,
    ToolLogEntry,
    ToolScenario,
    _bucket_counts,
    _derive_tool_and_style,
    _fmt_pct,
    _parse_tool_timestamp,
    _self_check,
    build_failure_records,
    build_per_tool_report,
    correlate_log_to_turn,
)

# ---------------------------------------------------------------------------
# _derive_tool_and_style
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario_id,filename,expected",
    [
        (
            "tool_perf_silent_verbatim",
            "tool_perf_silent_verbatim.yaml",
            ("perf_silent", "verbatim"),
        ),
        (
            "tool_dark_mode_on_paraphrase",
            "tool_dark_mode_on_paraphrase.yaml",
            ("dark_mode_on", "paraphrase"),
        ),
        ("foo_bar", "foo_bar.yaml", ("foo_bar", "unknown")),
        # Strips the leading "tool_" prefix only after style suffix removal
        ("tool_x_verbatim", "tool_x_verbatim.yaml", ("x", "verbatim")),
        # Scenario id wins when filename doesn't match the convention
        ("tool_alpha_paraphrase", "weird-name.yaml", ("alpha", "paraphrase")),
    ],
)
def test_derive_tool_and_style(scenario_id, filename, expected):
    assert _derive_tool_and_style(scenario_id, filename) == expected


# ---------------------------------------------------------------------------
# _parse_tool_timestamp
# ---------------------------------------------------------------------------


def test_parse_tool_timestamp_default_utc():
    """With the default tz_offset (UTC) the input is treated as UTC."""
    dt = _parse_tool_timestamp("2026-04-26 16:33:26.918")
    assert dt == datetime(2026, 4, 26, 16, 33, 26, 918000, tzinfo=timezone.utc)


def test_parse_tool_timestamp_applies_negative_offset():
    """An EDT (-4h) log timestamp should round-trip to 4 hours later in UTC."""
    dt = _parse_tool_timestamp("2026-04-26 12:00:00", tz_offset=timedelta(hours=-4))
    assert dt == datetime(2026, 4, 26, 16, 0, 0, tzinfo=timezone.utc)


def test_parse_tool_timestamp_returns_none_on_garbage():
    assert _parse_tool_timestamp("not a timestamp") is None
    assert _parse_tool_timestamp("") is None


# ---------------------------------------------------------------------------
# correlate_log_to_turn — buckets when there's no log data, no match, etc.
# ---------------------------------------------------------------------------


def _entry(ts_iso: str, tool: str) -> ToolLogEntry:
    """Build a minimal ToolLogEntry with a UTC timestamp."""
    return ToolLogEntry(
        timestamp=datetime.fromisoformat(ts_iso).replace(tzinfo=timezone.utc),
        tool_name=tool,
        args="",
        result="",
        raw_line="",
        log_file="test.log",
    )


def test_correlate_returns_no_log_data_when_entries_empty():
    out = correlate_log_to_turn(
        scenario_start=datetime(2026, 4, 26, 0, 0, 0, tzinfo=timezone.utc),
        turn_offset_s=0.0,
        expected_tool="tool_x",
        observed_tools=[],
        tool_entries=[],
    )
    assert out["bucket"] == "no_log_data"


def test_correlate_returns_no_service_call_when_window_is_empty():
    """Entries exist but none fall in the window → no_service_call."""
    out = correlate_log_to_turn(
        scenario_start=datetime(2026, 4, 26, 0, 0, 0, tzinfo=timezone.utc),
        turn_offset_s=0.0,
        expected_tool="tool_x",
        observed_tools=["tool_x"],
        tool_entries=[_entry("2026-05-01T00:00:00", "tool_x")],  # far away
        window_s=60,
    )
    assert out["bucket"] == "no_service_call"


def test_correlate_returns_service_ok_when_expected_tool_in_window():
    out = correlate_log_to_turn(
        scenario_start=datetime(2026, 4, 26, 0, 0, 0, tzinfo=timezone.utc),
        turn_offset_s=10.0,
        expected_tool="tool_x",
        observed_tools=["tool_x"],
        tool_entries=[_entry("2026-04-26T00:00:15", "tool_x")],
        window_s=60,
    )
    assert out["bucket"] == "service_ok"
    assert out["service_saw_call"] is True


# ---------------------------------------------------------------------------
# _bucket_counts
# ---------------------------------------------------------------------------


def _record(tool: str, log_bucket: str, status: str = "FAIL") -> FailureRecord:
    return FailureRecord(
        scenario_id=f"tool_{tool}_verbatim",
        tool=tool,
        utterance_style="verbatim",
        iteration=1,
        status=status,
        failure_category="generic",
        observations={"tool_service_log": {"bucket": log_bucket}},
        reproduction={},
    )


def test_bucket_counts_aggregates_by_service_log_bucket():
    records = [
        _record("a", "wrong_tool"),
        _record("b", "wrong_tool"),
        _record("c", "no_service_call"),
    ]
    counts = _bucket_counts(records)
    assert counts["wrong_tool"] == 2
    assert counts["no_service_call"] == 1


def test_bucket_counts_empty():
    assert _bucket_counts([]) == {}


def test_bucket_counts_falls_back_to_no_log_data():
    """Records without ``observations.tool_service_log`` count as no_log_data."""
    rec = FailureRecord(
        scenario_id="tool_x_verbatim",
        tool="x",
        utterance_style="verbatim",
        iteration=1,
        status="FAIL",
        failure_category="wrong_tool",
        observations={},  # no tool_service_log key
        reproduction={},
    )
    counts = _bucket_counts([rec])
    assert counts == {"no_log_data": 1}


# ---------------------------------------------------------------------------
# build_failure_records / build_per_tool_report — synthetic traces
# ---------------------------------------------------------------------------


def _scenario(sid: str, tool: str, style: str = "verbatim") -> ToolScenario:
    return ToolScenario(
        scenario_id=sid,
        tool_name=tool,
        utterance_style=style,
        expected_behavior="Agent calls the right tool.",
        user_messages=["do the thing"],
    )


def _trace(
    sid: str, status: str = "PASS", run_id: str = "eval-test-1", tool_called: str = ""
) -> dict:
    """Build a minimal trace JSON dict matching what gaia eval agent emits."""
    return {
        "scenario_id": sid,
        "status": status,
        "elapsed_s": 1.0,
        "turns": [
            {
                "user_message": "do the thing",
                "agent_response": "ok",
                "agent_tools": [tool_called] if tool_called else [],
            }
        ],
        "_run_id": run_id,
        "_run_dir": f"eval/results/{run_id}",
        "_iteration": 1,
    }


def test_build_failure_records_skips_passes():
    scenarios = {"tool_x_verbatim": _scenario("tool_x_verbatim", "x")}
    traces = [_trace("tool_x_verbatim", status="PASS", tool_called="x")]
    records = build_failure_records(traces, scenarios, [], {}, {})
    assert records == []


def test_build_failure_records_captures_failures():
    scenarios = {"tool_x_verbatim": _scenario("tool_x_verbatim", "x")}
    traces = [_trace("tool_x_verbatim", status="FAIL", tool_called="wrong_tool")]
    records = build_failure_records(traces, scenarios, [], {}, {})
    assert len(records) == 1
    assert records[0].tool == "x"
    assert records[0].status == "FAIL"


def test_per_tool_report_groups_iterations_and_computes_pass_rate():
    scenarios = {
        "tool_x_verbatim": _scenario("tool_x_verbatim", "x"),
    }
    # Two iterations, one pass + one fail → 50% pass rate
    traces = [
        _trace("tool_x_verbatim", status="PASS", run_id="eval-a", tool_called="x"),
        _trace("tool_x_verbatim", status="FAIL", run_id="eval-b", tool_called="x"),
    ]
    rows = build_per_tool_report(traces, scenarios, pass_threshold=0.9)
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "x"
    assert row["combined_pass_rate"] == pytest.approx(0.5)
    assert row["combined_total"] == 2
    assert row["combined_pass"] == 1
    assert row["meets_gate"] is False  # 0.5 < 0.9


# ---------------------------------------------------------------------------
# _self_check — the genericization gate
# ---------------------------------------------------------------------------


def test_self_check_silent_when_expected_tool_count_is_none():
    """Bot-review fix: public runs (no expected_tool_count) must not warn."""
    warnings = _self_check(
        per_tool_rows=[{"tool": "a"}, {"tool": "b"}],
        records=[],
        scorecards=[],
        expected_tool_count=None,
    )
    assert not any("Expected" in w and "unique tools" in w for w in warnings)


def test_self_check_warns_when_tool_count_mismatch():
    warnings = _self_check(
        per_tool_rows=[{"tool": "a"}, {"tool": "b"}],
        records=[],
        scorecards=[],
        expected_tool_count=41,
    )
    assert any("Expected 41 unique tools" in w for w in warnings)


def test_self_check_silent_on_matching_count():
    warnings = _self_check(
        per_tool_rows=[{"tool": "a"}],
        records=[],
        scorecards=[],
        expected_tool_count=1,
    )
    assert not any("unique tools" in w for w in warnings)


# ---------------------------------------------------------------------------
# _fmt_pct
# ---------------------------------------------------------------------------


def test_fmt_pct_handles_none():
    assert _fmt_pct(None) == "n/a"


def test_fmt_pct_formats_percent():
    assert _fmt_pct(0.5) == "50%"
    assert _fmt_pct(1.0) == "100%"
