#  Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.coder.debug_loop` (§5.9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

import pytest

from gaia.coder.debug_loop import (
    MAX_DEBUG_ROUNDS,
    MIN_HYPOTHESES,
    PROPOSE_FIX_CONFIDENCE_THRESHOLD,
    DebugContext,
    DebugDisciplineError,
    DebugState,
    DebugSubLoop,
    Hypothesis,
)
from gaia.coder.stores import feedback as feedback_store
from gaia.coder.stores import memory as memory_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ctx(**kwargs) -> DebugContext:
    return DebugContext(
        task_id=kwargs.pop("task_id", "t1"),
        feedback_id=kwargs.pop("feedback_id", ""),
        error_signature=kwargs.pop("error_signature", "KeyError"),
        repro_command=kwargs.pop("repro_command", "pytest"),
        **kwargs,
    )


def _good_repro_fn(**kw):
    return {
        "reproduced": True,
        "attempts": 3,
        "attempts_reproduced": 3,
        "actual_output": "KeyError: user_id",
        "match_score": 0.9,
    }


def _bad_repro_fn(**kw):
    return {
        "reproduced": False,
        "attempts": 3,
        "attempts_reproduced": 1,
        "actual_output": "nope",
        "match_score": 0.1,
    }


# ---------------------------------------------------------------------------
# reproduce
# ---------------------------------------------------------------------------


def test_reproduce_happy_path_advances_to_bisect():
    loop = DebugSubLoop(context=_ctx(), repro_fn=_good_repro_fn)
    next_state = loop.reproduce()
    assert next_state == DebugState.BISECT
    assert loop.context.reproduced is True


def test_reproduce_raises_when_not_reproduced():
    loop = DebugSubLoop(context=_ctx(), repro_fn=_bad_repro_fn)
    with pytest.raises(DebugDisciplineError, match="I cannot reproduce"):
        loop.reproduce()


def test_reproduce_missing_fn_raises():
    loop = DebugSubLoop(context=_ctx())
    with pytest.raises(DebugDisciplineError, match="no repro_fn wired"):
        loop.reproduce()


# ---------------------------------------------------------------------------
# bisect
# ---------------------------------------------------------------------------


def test_bisect_advances_to_hypothesise():
    def fake_bisect(**kw):
        return {"culprit_sha": "abc123", "log": "..."}

    loop = DebugSubLoop(context=_ctx(), repro_fn=_good_repro_fn, bisect_fn=fake_bisect)
    assert loop.bisect("HEAD~10", "HEAD") == DebugState.HYPOTHESISE
    assert loop.context.culprit_sha == "abc123"


def test_bisect_can_be_skipped():
    loop = DebugSubLoop(context=_ctx())
    assert loop.bisect("a", "b", skip=True) == DebugState.HYPOTHESISE
    assert loop.context.bisect_skipped is True


# ---------------------------------------------------------------------------
# hypothesise — discipline rule 3
# ---------------------------------------------------------------------------


def test_hypothesise_requires_three():
    loop = DebugSubLoop(context=_ctx())
    with pytest.raises(DebugDisciplineError, match=f">= {MIN_HYPOTHESES}"):
        loop.hypothesise([Hypothesis("only one")])


def test_hypothesise_accepts_three():
    loop = DebugSubLoop(context=_ctx())
    hyps = [Hypothesis(f"hyp{i}") for i in range(MIN_HYPOTHESES)]
    assert loop.hypothesise(hyps) == DebugState.PROBE
    assert len(loop.context.hypotheses) == MIN_HYPOTHESES


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def test_probe_advances_to_localise_when_confidence_tips():
    """A probe that boosts one hypothesis past the threshold → LOCALISE_BUG."""

    def probe_fn(hyp: Hypothesis, ctx: DebugContext) -> Tuple[bool, str, int]:
        if hyp.text == "correct":
            return (True, "experiment confirms", 90)
        return (False, "refuted", 5)

    loop = DebugSubLoop(context=_ctx(), probe_fn=probe_fn)
    loop.context.hypotheses = [
        Hypothesis("wrong-a"),
        Hypothesis("correct"),
        Hypothesis("wrong-b"),
    ]
    assert loop.probe() == DebugState.LOCALISE_BUG


def test_probe_returns_to_hypothesise_when_inconclusive():
    def probe_fn(hyp, ctx):
        return (False, "flat", 10)

    loop = DebugSubLoop(context=_ctx(), probe_fn=probe_fn)
    loop.context.hypotheses = [Hypothesis(f"h{i}") for i in range(3)]
    assert loop.probe() == DebugState.HYPOTHESISE


def test_probe_enforces_round_cap():
    """> MAX_DEBUG_ROUNDS → DebugDisciplineError."""
    loop = DebugSubLoop(context=_ctx(), probe_fn=lambda h, c: (False, "", 0))
    loop.context.hypotheses = [Hypothesis("h") for _ in range(3)]
    loop.context.round_count = MAX_DEBUG_ROUNDS  # next probe will exceed
    with pytest.raises(DebugDisciplineError, match="exceeded MAX_DEBUG_ROUNDS"):
        loop.probe()


# ---------------------------------------------------------------------------
# propose_fix — discipline rules 1 & 2
# ---------------------------------------------------------------------------


def test_propose_fix_refuses_without_reproduce():
    """Discipline rule 1."""
    loop = DebugSubLoop(context=_ctx())
    loop.context.hypotheses = [
        Hypothesis("h", confidence=PROPOSE_FIX_CONFIDENCE_THRESHOLD + 5)
    ]
    with pytest.raises(
        DebugDisciplineError, match="reproduce did not return reproduced=True"
    ):
        loop.propose_fix(summary="...")


def test_propose_fix_refuses_below_confidence_threshold():
    """Discipline rule 2."""
    ctx = _ctx()
    ctx.reproduced = True
    ctx.hypotheses = [Hypothesis("maybe", confidence=50)]
    loop = DebugSubLoop(context=ctx)
    with pytest.raises(
        DebugDisciplineError, match=f"< {PROPOSE_FIX_CONFIDENCE_THRESHOLD}%"
    ):
        loop.propose_fix(summary="shotgun attempt")


def test_propose_fix_shotgun_override_requires_em_elevation():
    ctx = _ctx()
    ctx.reproduced = True
    ctx.hypotheses = [Hypothesis("maybe", confidence=40)]
    loop = DebugSubLoop(context=ctx, em_elevation_granted=False)
    with pytest.raises(DebugDisciplineError, match="Pass shotgun=True AND"):
        loop.propose_fix(summary="x", shotgun=True)


def test_propose_fix_shotgun_with_elevation_allows_low_confidence():
    ctx = _ctx()
    ctx.reproduced = True
    ctx.hypotheses = [Hypothesis("maybe", confidence=40)]
    loop = DebugSubLoop(context=ctx, em_elevation_granted=True)
    next_state = loop.propose_fix(summary="x", shotgun=True)
    assert next_state == DebugState.POSTMORTEM
    assert ctx.shotgun is True


def test_propose_fix_happy_path():
    ctx = _ctx()
    ctx.reproduced = True
    ctx.hypotheses = [
        Hypothesis("solid", confidence=PROPOSE_FIX_CONFIDENCE_THRESHOLD + 5)
    ]
    loop = DebugSubLoop(context=ctx)
    assert loop.propose_fix(summary="fix it") == DebugState.POSTMORTEM
    assert ctx.fix_summary == "fix it"


# ---------------------------------------------------------------------------
# postmortem
# ---------------------------------------------------------------------------


def test_postmortem_writes_to_memory_and_feedback(tmp_path: Path):
    """feedback row + memory row both updated."""
    memory_conn = memory_store.open_store(tmp_path / "memory.db")
    feedback_conn = feedback_store.open_store(tmp_path / "feedback.db")
    try:
        # Seed the feedback row the postmortem will update.
        feedback_id = "fb-1"
        feedback_store.insert_row(
            feedback_conn,
            feedback_store.FeedbackRow(
                id=feedback_id,
                received_at="2026-04-20T00:00:00Z",
                from_handle="em",
                channel="cli",
                severity="high",
                body="...",
            ),
        )
        ctx = _ctx(feedback_id=feedback_id)
        ctx.reproduced = True
        ctx.hypotheses = [Hypothesis("solid", confidence=95)]
        ctx.fix_summary = "one-line fix"
        ctx.localised_file = "src/x.py"
        loop = DebugSubLoop(context=ctx)
        loop.postmortem(
            feedback_conn=feedback_conn,
            memory_conn=memory_conn,
            fix_pr_url="https://pr/1",
        )

        mem_rows = memory_store.list_rows(
            memory_conn, filter={"topic": "failure_patterns"}
        )
        fb_row = feedback_store.get_row(feedback_conn, feedback_id)
    finally:
        memory_conn.close()
        feedback_conn.close()

    assert len(mem_rows) == 1
    payload = json.loads(mem_rows[0].payload_json)
    assert payload["root_cause"] == "solid"
    assert payload["fix_pr_url"] == "https://pr/1"

    notes = json.loads(fb_row.notes_json)
    assert any(n["stage"] == "postmortem" for n in notes)
    assert loop.context.postmortem_written is True


def test_postmortem_requires_propose_fix_to_have_run():
    loop = DebugSubLoop(context=_ctx())
    with pytest.raises(DebugDisciplineError, match="before propose_fix"):
        loop.postmortem()


def test_require_postmortem_or_raise():
    """§5.9 rule 5 — closing without postmortem is blocked."""
    loop = DebugSubLoop(context=_ctx())
    with pytest.raises(DebugDisciplineError, match="refuse to close"):
        loop.require_postmortem_or_raise()

    loop.context.postmortem_written = True
    loop.require_postmortem_or_raise()  # no longer raises
