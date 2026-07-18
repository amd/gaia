# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the sidecar eval harness core (``gaia.eval.sidecar_harness``,
V2-19 / issue #2180).

These cover the PURE classification logic — SSE parsing, sequence matching,
baseline load/validation, and the cross-process serial lock — with NO running
server and NO Lemonade, mirroring the pure-vs-live split in
``behavior_harness``. The live golden path lives in
``tests/integration/eval/test_sidecar_eval.py`` (real_model).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gaia.eval.sidecar_harness import (
    CANONICAL_EVENT_TYPES,
    TERMINAL_TYPES,
    QuerySequenceScenario,
    SequenceBaseline,
    SerialEvalLock,
    SerialEvalTimeout,
    SidecarEvalHarness,
    SidecarUnavailable,
    baselines_dir_for,
    event_types,
    load_baseline,
    load_baselines,
    match_sequence,
    parse_sse,
)

# The email package's committed baselines — used to prove the loader reads the
# real files the /query route is pinned against.
_EMAIL_PKG_ROOT = (
    Path(__file__).resolve().parents[2] / "hub" / "agents" / "python" / "email"
)


# ---------------------------------------------------------------------------
# Canonical vocabulary
# ---------------------------------------------------------------------------


def test_canonical_vocabulary_is_the_frozen_seven():
    assert CANONICAL_EVENT_TYPES == frozenset(
        {
            "status",
            "token",
            "tool_call",
            "tool_result",
            "needs_confirmation",
            "final",
            "error",
        }
    )


def test_terminal_types_are_final_and_error():
    assert TERMINAL_TYPES == frozenset({"final", "error"})


# ---------------------------------------------------------------------------
# parse_sse
# ---------------------------------------------------------------------------


def _sse(*events) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def test_parse_sse_extracts_events_in_order():
    body = _sse(
        {"type": "status", "message": "go"},
        {"type": "tool_call", "tool": "triage_inbox", "args": {}},
        {"type": "final", "answer": "done"},
    )
    events = parse_sse(body)
    assert event_types(events) == ["status", "tool_call", "final"]
    assert events[1]["tool"] == "triage_inbox"


def test_parse_sse_ignores_comments_and_blank_lines_and_crlf():
    body = (
        ": keep-alive\r\n\r\n"
        'data: {"type": "status", "message": "x"}\r\n\r\n'
        "\r\n"
        'data: {"type": "final", "answer": "y"}\r\n\r\n'
    )
    assert event_types(parse_sse(body)) == ["status", "final"]


def test_parse_sse_surfaces_unparseable_frame_not_drops_it():
    body = "data: {not json}\n\n" + _sse({"type": "final", "answer": "z"})
    events = parse_sse(body)
    # A malformed frame is a seam bug the harness must be able to see.
    assert events[0]["type"] == "__unparseable__"
    assert events[-1]["type"] == "final"


# ---------------------------------------------------------------------------
# SequenceBaseline validation
# ---------------------------------------------------------------------------


def test_baseline_rejects_non_canonical_required_type():
    with pytest.raises(ValueError, match="non-canonical"):
        SequenceBaseline(
            scenario_id="bad",
            required_subsequence=("status", "chunk"),  # chunk is source-vocab, not §0.2
        )


def test_baseline_rejects_non_terminal_terminal():
    with pytest.raises(ValueError, match="terminal"):
        SequenceBaseline(
            scenario_id="bad",
            required_subsequence=("status",),
            terminal="status",
        )


def test_baseline_round_trips_through_dict():
    b = SequenceBaseline(
        scenario_id="t",
        required_subsequence=("status", "tool_call", "final"),
        terminal="final",
        forbidden=("error",),
    )
    assert SequenceBaseline.from_dict(b.to_dict()) == b


def test_baseline_from_dict_missing_key_is_loud():
    with pytest.raises(ValueError, match="malformed"):
        SequenceBaseline.from_dict({"scenario_id": "x"})


# ---------------------------------------------------------------------------
# match_sequence
# ---------------------------------------------------------------------------


_GOLDEN = SequenceBaseline(
    scenario_id="golden",
    required_subsequence=("status", "tool_call", "tool_result", "final"),
    terminal="final",
    forbidden=("error",),
)


def test_match_passes_on_exact_golden_shape():
    events = [
        {"type": "status"},
        {"type": "tool_call"},
        {"type": "tool_result"},
        {"type": "final"},
    ]
    verdict = match_sequence(events, _GOLDEN)
    assert verdict.passed
    assert verdict.reasons == []


def test_match_tolerates_extra_variable_count_events_between_milestones():
    # Extra status/token events (LLM-non-deterministic) must not fail the match.
    events = [
        {"type": "status"},
        {"type": "status"},
        {"type": "token"},
        {"type": "tool_call"},
        {"type": "tool_result"},
        {"type": "token"},
        {"type": "final"},
    ]
    assert match_sequence(events, _GOLDEN).passed


def test_match_fails_when_milestone_missing():
    events = [{"type": "status"}, {"type": "final"}]  # no tool_call/tool_result
    verdict = match_sequence(events, _GOLDEN)
    assert not verdict.passed
    assert any("milestones" in r for r in verdict.reasons)


def test_match_fails_on_non_canonical_type():
    events = [
        {"type": "status"},
        {"type": "tool_call"},
        {"type": "tool_result"},
        {"type": "chunk"},  # source-vocab leak — must be caught
        {"type": "final"},
    ]
    verdict = match_sequence(events, _GOLDEN)
    assert not verdict.passed
    assert any("non-canonical" in r for r in verdict.reasons)


def test_match_fails_when_terminal_not_last():
    events = [
        {"type": "status"},
        {"type": "tool_call"},
        {"type": "tool_result"},
        {"type": "final"},
        {"type": "status"},  # something after the terminal
    ]
    verdict = match_sequence(events, _GOLDEN)
    assert not verdict.passed
    assert any("not last" in r for r in verdict.reasons)


def test_match_fails_on_two_terminals():
    events = [
        {"type": "status"},
        {"type": "tool_call"},
        {"type": "tool_result"},
        {"type": "final"},
        {"type": "final"},
    ]
    verdict = match_sequence(events, _GOLDEN)
    assert not verdict.passed
    assert any("exactly one terminal" in r for r in verdict.reasons)


def test_match_fails_when_forbidden_error_present():
    events = [
        {"type": "status"},
        {"type": "tool_call"},
        {"type": "tool_result"},
        {"type": "error"},
    ]
    verdict = match_sequence(events, _GOLDEN)
    assert not verdict.passed
    # Both the wrong terminal AND the forbidden hit are reported (no short-circuit).
    assert any("forbidden" in r for r in verdict.reasons)


def test_match_collects_all_violations_not_just_first():
    events = [
        {"type": "chunk"},
        {"type": "chunk"},
    ]  # non-canonical, no milestones, no terminal
    verdict = match_sequence(events, _GOLDEN)
    assert not verdict.passed
    assert len(verdict.reasons) >= 3


# ---------------------------------------------------------------------------
# Committed baseline loading (the real email-package files)
# ---------------------------------------------------------------------------


def test_baselines_dir_for_points_at_agent_package_convention():
    d = baselines_dir_for(_EMAIL_PKG_ROOT)
    assert d.name == "query_sequences"
    assert d.parent.name == "eval_baselines"


@pytest.mark.skipif(
    not baselines_dir_for(_EMAIL_PKG_ROOT).is_dir(),
    reason="email package baselines not present in this checkout",
)
def test_committed_email_baselines_load_and_validate():
    baselines = load_baselines(baselines_dir_for(_EMAIL_PKG_ROOT))
    # The golden triage path is the primary committed baseline.
    assert "triage_inbox" in baselines
    triage = baselines["triage_inbox"]
    assert triage.terminal == "final"
    assert "tool_call" in triage.required_subsequence
    # Every committed baseline references only canonical §0.2 types (enforced by
    # SequenceBaseline.__post_init__, so a bad commit fails to load here).
    for b in baselines.values():
        assert set(b.required_subsequence) <= CANONICAL_EVENT_TYPES


def test_load_baseline_missing_file_is_loud():
    with pytest.raises(SidecarUnavailable, match="baseline not found"):
        load_baseline(Path("does-not-exist.json"))


def test_load_baselines_missing_dir_is_loud(tmp_path):
    with pytest.raises(SidecarUnavailable, match="does not exist"):
        load_baselines(tmp_path / "nope")


def test_load_baselines_empty_dir_is_loud(tmp_path):
    (tmp_path / "query_sequences").mkdir()
    with pytest.raises(SidecarUnavailable, match="no .*baselines"):
        load_baselines(tmp_path / "query_sequences")


# ---------------------------------------------------------------------------
# SerialEvalLock — the CLAUDE.md serial-eval guarantee
# ---------------------------------------------------------------------------


def test_serial_lock_acquire_and_release(tmp_path):
    lock_path = tmp_path / "eval.lock"
    lock = SerialEvalLock(lock_path)
    with lock:
        assert lock_path.exists()
        assert int(lock_path.read_text().split()[0]) == os.getpid()
    assert not lock_path.exists()  # released on exit


def test_serial_lock_second_holder_times_out_loud(tmp_path):
    lock_path = tmp_path / "eval.lock"
    held = SerialEvalLock(lock_path).acquire()
    try:
        # A second acquirer must NOT proceed in parallel — it fails loud.
        contender = SerialEvalLock(lock_path, timeout=0.2, poll=0.05)
        with pytest.raises(SerialEvalTimeout, match="serial lock"):
            contender.acquire()
    finally:
        held.release()


def test_serial_lock_reclaims_stale_lock_of_dead_holder(tmp_path, monkeypatch):
    lock_path = tmp_path / "eval.lock"
    # Simulate a previous eval process that died without releasing.
    lock_path.write_text("999999999 123.0\n")

    import gaia.eval.sidecar_harness as mod

    monkeypatch.setattr(
        mod.SerialEvalLock, "_pid_alive", staticmethod(lambda pid: False)
    )
    lock = SerialEvalLock(lock_path, timeout=1.0, poll=0.05)
    with lock:
        # Reclaimed: the lock now carries OUR pid.
        assert int(lock_path.read_text().split()[0]) == os.getpid()


def test_serial_lock_env_override(tmp_path, monkeypatch):
    target = tmp_path / "from-env.lock"
    monkeypatch.setenv("GAIA_EVAL_LOCK_PATH", str(target))
    lock = SerialEvalLock()
    assert lock.path == target


# ---------------------------------------------------------------------------
# Live harness — loud failure when the backend is absent (never a silent pass)
# ---------------------------------------------------------------------------


def test_harness_unreachable_backend_raises_actionable(tmp_path, monkeypatch):
    # Point the serial lock somewhere isolated so this never touches a real run.
    monkeypatch.setenv("GAIA_EVAL_LOCK_PATH", str(tmp_path / "eval.lock"))
    # A port nothing listens on → requests.ConnectionError → SidecarUnavailable.
    harness = SidecarEvalHarness("http://127.0.0.1:9", auth_token="tok")
    scenario = QuerySequenceScenario(agent_id="email", query="hi", baseline=_GOLDEN)
    with pytest.raises(SidecarUnavailable, match="could not reach"):
        harness.run_scenario(scenario)
