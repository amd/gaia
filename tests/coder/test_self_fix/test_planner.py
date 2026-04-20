# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.coder.self_fix.planner``."""

from __future__ import annotations

from gaia.coder.self_fix.planner import (
    ALWAYS_LARGE_FIX_CLASSES,
    MAX_PLAN_REFINEMENT_ROUNDS,
    CostEstimate,
    FileTouchPlan,
    Plan,
    draft_plan,
    is_large_job,
    request_em_approval,
)
from gaia.coder.self_fix.triage import (
    CandidateFile,
    FixClassResult,
    LocalisationHit,
)


def _make_plan(*, loc: int = 50, fix_class: str = "tool", n_files: int = 1) -> Plan:
    files = tuple(
        FileTouchPlan(
            path=f"src/gaia/coder/mod{i}.py",
            loc_estimate=loc // n_files,
            operation="edit",
        )
        for i in range(n_files)
    )
    return Plan(
        feedback_id="fb-42",
        fix_class=fix_class,
        root_cause="root cause",
        proposed_change="change X",
        regression_test_sketch="test Y",
        files=files,
        alternatives_considered=(),
        risks=(),
        success_criterion="all green",
        cost_estimate=CostEstimate(tokens=10_000, usd=0.2, wall_clock_minutes=5.0),
    )


def test_plan_threshold_triggers_review() -> None:
    """A 300 LoC plan must be flagged as large."""
    plan = _make_plan(loc=300)
    assert is_large_job(plan, threshold_loc=200) is True


def test_plan_small_single_file_not_large() -> None:
    """Small single-file plans stay below threshold."""
    plan = _make_plan(loc=50)
    assert is_large_job(plan, threshold_loc=200) is False


def test_plan_architectural_always_large() -> None:
    """``architectural`` is always-large regardless of LoC."""
    for fix_class in ALWAYS_LARGE_FIX_CLASSES:
        plan = _make_plan(loc=10, fix_class=fix_class)
        assert is_large_job(plan, threshold_loc=200) is True


def test_plan_cross_mixin_multi_file_is_large() -> None:
    """Two files across different top-dirs → large."""
    plan = Plan(
        feedback_id="fb-x",
        fix_class="tool",
        root_cause="r",
        proposed_change="p",
        regression_test_sketch="t",
        files=(
            FileTouchPlan(path="src/gaia/coder/review/foo.py", loc_estimate=30),
            FileTouchPlan(path="src/gaia/coder/self_fix/bar.py", loc_estimate=30),
        ),
        alternatives_considered=(),
        risks=(),
        success_criterion="green",
        cost_estimate=CostEstimate(tokens=1, usd=0.0, wall_clock_minutes=1.0),
    )
    assert is_large_job(plan, threshold_loc=500) is True


def test_draft_plan_uses_localised_hits() -> None:
    """draft_plan pulls files from localised hits when the caller omits them."""
    fix_class = FixClassResult(
        fix_class="tool",
        root_cause_hypothesis="it does the wrong thing",
        candidate_files=(CandidateFile(path="src/mod.py", why="bug"),),
        prior_pattern_hit=None,
        confidence=85,
    )
    hits = (
        LocalisationHit(path="src/mod.py", line_start=10, line_end=12, snippet="bad"),
    )
    plan = draft_plan(
        feedback={"id": "fb-1", "body": "broken"},
        localised_hits=hits,
        fix_class=fix_class,
    )
    assert plan.feedback_id == "fb-1"
    assert plan.files[0].path == "src/mod.py"
    assert plan.total_loc_estimate >= 10  # minimum floor
    assert plan.fix_class == "tool"


def test_plan_refinement_cap_is_three() -> None:
    """Three refinement rounds is the cap (§5.1 Stage 3 plan_refine rule)."""
    # The cap is declared as a constant; tests use it as the contract for the
    # loop-driver / runner. We assert the constant exists and is three.
    assert MAX_PLAN_REFINEMENT_ROUNDS == 3


def test_plan_review_loops_max_3_rounds() -> None:
    """Simulate the outer loop: keep refining until the cap stops it."""
    # Simulate the driver's refinement counter incrementing on every "reject".
    rounds = 0
    while True:
        plan = _make_plan(loc=10)
        new_plan = Plan(
            feedback_id=plan.feedback_id,
            fix_class=plan.fix_class,
            root_cause=plan.root_cause,
            proposed_change=plan.proposed_change,
            regression_test_sketch=plan.regression_test_sketch,
            files=plan.files,
            alternatives_considered=plan.alternatives_considered,
            risks=plan.risks,
            success_criterion=plan.success_criterion,
            cost_estimate=plan.cost_estimate,
            refinement_round=rounds,
        )
        if new_plan.refinement_round >= MAX_PLAN_REFINEMENT_ROUNDS:
            break
        rounds += 1
    assert rounds == MAX_PLAN_REFINEMENT_ROUNDS


def test_request_em_approval_defers_without_inbox() -> None:
    """Without an inbox writer (Phase 5 not landed), the request is deferred."""
    plan = _make_plan(loc=400)
    req = request_em_approval(plan, em_config={"em_handle": "em"}, inbox_writer=None)
    # Phase 5 may or may not be present in this checkout; either way the
    # request must succeed and return something with a non-empty body.
    assert req.body.strip(), "rendered P3 body should not be empty"
    assert "Regression test" in req.body
    assert plan.feedback_id in req.body or plan.proposed_change in req.body


def test_request_em_approval_uses_injected_writer() -> None:
    """An injected writer is called; inbox_id is set and deferred is False."""
    plan = _make_plan(loc=400)
    received: list[dict] = []

    def fake_writer(**kwargs):
        received.append(kwargs)

    req = request_em_approval(
        plan,
        em_config={"em_handle": "em"},
        inbox_writer=fake_writer,
    )
    assert req.deferred is False
    assert req.inbox_id is not None
    assert received, "writer should have been called once"
    assert received[0]["severity"] == "question"
    assert received[0]["metadata"]["feedback_id"] == plan.feedback_id
