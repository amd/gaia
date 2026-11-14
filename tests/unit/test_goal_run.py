# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Goal mode policy: when a goal run continues, and how it ends."""

import pytest

from gaia.agents.base.goal_run import (
    STALL_ROUNDS,
    GoalLedger,
    GoalSpec,
    GoalVerdict,
    next_action,
    outcome_message,
    round_prompt,
)


def _not_met(*missing, **kw):
    return GoalVerdict(met=False, missing=list(missing), **kw)


@pytest.mark.parametrize("condition", ["", "   "])
def test_a_goal_needs_a_condition(condition):
    with pytest.raises(ValueError, match="needs a condition"):
        GoalSpec(condition=condition)


def test_bounds_must_be_positive():
    with pytest.raises(ValueError, match="max_rounds"):
        GoalSpec(condition="x", max_rounds=0)
    with pytest.raises(ValueError, match="max_seconds"):
        GoalSpec(condition="x", max_seconds=0)


def test_no_round_yet_means_run_one():
    assert next_action(GoalSpec("x"), GoalLedger()) is None


def test_met_ends_the_run_even_when_cleared_or_on_the_last_round():
    spec = GoalSpec("x", max_rounds=1)
    ledger = GoalLedger()
    ledger.record(GoalVerdict(met=True, evidence=["ok ok ok"]), [])
    assert next_action(spec, ledger, cleared=True) == "met"


def test_not_met_with_progress_continues():
    ledger = GoalLedger()
    ledger.record(_not_met("a", "b"), ["f1"])
    ledger.record(_not_met("b"), [])
    assert next_action(GoalSpec("x"), ledger) is None


def test_blocked_asks_the_user():
    ledger = GoalLedger()
    ledger.record(_not_met("file", blocked=True, question="Where is plan.txt?"), [])
    assert next_action(GoalSpec("x"), ledger) == "needs_user"


def test_cleared_stops_a_goal_not_yet_met():
    ledger = GoalLedger()
    ledger.record(_not_met("a"), [])
    assert next_action(GoalSpec("x"), ledger, cleared=True) == "cleared"


def test_rounds_without_progress_stall():
    ledger = GoalLedger()
    for _ in range(STALL_ROUNDS + 1):
        ledger.record(_not_met("convert b.csv"), [])
    assert ledger.stalled()
    assert next_action(GoalSpec("x"), ledger) == "stalled"


def test_a_new_file_written_is_progress():
    ledger = GoalLedger()
    ledger.record(_not_met("a"), [])
    for n in range(STALL_ROUNDS):
        ledger.record(_not_met("a"), [f"out{n}.csv"])
    assert not ledger.stalled()


def test_new_proof_is_progress_even_with_the_same_missing_list():
    ledger = GoalLedger()
    ledger.record(_not_met("a", evidence=["read part1.csv"]), [])
    ledger.record(_not_met("a", evidence=["read part1.csv", "read part2.csv"]), [])
    assert ledger.rounds[-1].progressed
    ledger.record(_not_met("a", evidence=["read part2.csv"]), [])
    assert not ledger.rounds[-1].progressed


def test_the_same_missing_list_in_another_order_is_not_progress():
    ledger = GoalLedger()
    ledger.record(_not_met("a", "b"), [])
    ledger.record(_not_met("B", "a"), [])
    assert not ledger.rounds[-1].progressed


def test_round_budget_ends_the_run():
    ledger = GoalLedger()
    ledger.record(_not_met("a"), [])
    ledger.record(_not_met("b"), [])
    assert next_action(GoalSpec("x", max_rounds=2), ledger) == "budget"


def test_time_budget_ends_the_run():
    ledger = GoalLedger(started_at=100.0)
    ledger.record(_not_met("a"), [])
    spec = GoalSpec("x", max_seconds=60)
    assert next_action(spec, ledger, now=150.0) is None
    assert next_action(spec, ledger, now=161.0) == "budget"


def test_round_one_is_the_users_request():
    assert round_prompt(GoalSpec("x"), GoalLedger(), "do it") == "do it"


def test_later_rounds_carry_the_goal_and_what_is_missing():
    ledger = GoalLedger()
    ledger.record(_not_met("convert b.csv", "fix test_x"), ["clean/a.parquet"])
    prompt = round_prompt(GoalSpec("all CSVs converted", max_rounds=5), ledger, "go")
    assert "round 2 of at most 5" in prompt
    assert "all CSVs converted" in prompt
    assert "- convert b.csv" in prompt and "- fix test_x" in prompt
    assert "clean/a.parquet" in prompt
    assert "not from your answer" in prompt
    assert "The original request: go" in prompt


def test_later_rounds_carry_a_long_request_cut_short():
    ledger = GoalLedger()
    ledger.record(_not_met("a"), [])
    prompt = round_prompt(GoalSpec("x"), ledger, "spec " * 1000)
    request = prompt.split("The original request: ", 1)[1].split("\n", 1)[0]
    assert request.endswith("…") and len(request) <= 1501


def test_reworded_evaluator_items_are_not_progress():
    ledger = GoalLedger()
    ledger.record(_not_met("convert part7.csv"), [])
    ledger.record(_not_met("part7.csv still needs converting"), [])
    assert not ledger.rounds[-1].progressed


def test_a_check_failing_with_new_timings_is_not_progress_but_fewer_failures_is():
    def failing(line):
        return _not_met(f"`pytest` fails (exit 1): {line}", decided_by="check")

    ledger = GoalLedger()
    ledger.record(failing("=== 3 failed in 1.52s ==="), [])
    ledger.record(failing("=== 3 failed in 1.71s ==="), [])
    assert not ledger.rounds[-1].progressed
    ledger.record(failing("=== 2 failed in 1.70s ==="), [])
    assert ledger.rounds[-1].progressed


def test_cited_collects_each_rounds_proven_quotes_once():
    ledger = GoalLedger()
    ledger.record(_not_met("a", evidence=["12 passed"]), [])
    ledger.record(_not_met("b", evidence=["12 passed", "wrote 3 files"]), [])
    assert ledger.cited() == ["12 passed", "wrote 3 files"]


@pytest.mark.parametrize("outcome", ["needs_user", "stalled", "budget", "cleared"])
def test_every_unmet_ending_says_not_done_or_cleared(outcome):
    ledger = GoalLedger()
    ledger.record(_not_met("b.csv", blocked=outcome == "needs_user", question="?"), [])
    message = outcome_message(outcome, ledger)
    assert "Goal met" not in message
    assert message.startswith(("Goal not done", "Goal cleared"))


def test_met_says_how_it_was_decided():
    ledger = GoalLedger()
    ledger.record(GoalVerdict(met=True, evidence=["ok"], decided_by="evaluator"), [])
    message = outcome_message("met", ledger)
    assert message.startswith("Goal met after 1 round")
    assert "not proven by a check" in message

    ledger.record(GoalVerdict(met=True, evidence=["ok"], checks_passed=2), [])
    assert "its checks passed" in outcome_message("met", ledger)


def test_an_unchecked_goal_says_so():
    assert outcome_message("error", GoalLedger()).startswith("Goal not done")
