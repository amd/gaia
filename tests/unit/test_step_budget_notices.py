# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the step-budget notices the agent loop puts into its own context.

Before these existed, the only thing the loop ever told the model about its
step budget was the prompt sent *after* the last step was spent. A model that
cannot see its budget cannot manage it, and a turn that runs out does so
without ever having had the chance to spend differently.

What is pinned here:

* a notice lands at each level, once, and in context for the next model call;
* it reports the real numbers;
* the trigger is **steps remaining**, so the same notices land at the same
  distance from the end whatever ``max_steps`` is — a proportional trigger
  pushes the first notice to step 360 on a 400-step budget, which is past the
  point most turns reach, so the feature switches itself off on exactly the
  budgets where it is cheapest to run;
* on a budget too small to hold the absolute figures, a proportional floor
  takes over, and never rounds down to zero;
* crossing two levels in one step sends the level that is true now;
* nothing fires on a budget too small to act on;
* the whole thing can be switched off, so the previous behaviour is still
  reachable for anyone comparing against it.
"""

import os
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import (
    _BUDGET_NOTICES,
    _MIN_STEPS_FOR_BUDGET_NOTICES,
    Agent,
    _budget_notice_trigger,
    budget_notices_enabled,
)


class _Host(Agent):
    def _register_tools(self):
        pass


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _Host(skip_lemonade=True, silent_mode=True)


def _run(host, limit, upto=None):
    """Drive the announcer over a whole budget; return the notices sent."""
    messages, conversation, sent = [], [], []
    for step in range(1, (upto or limit) + 1):
        notice = host._announce_step_budget(messages, conversation, step, limit)
        if notice is not None:
            sent.append((step, notice))
    assert len(messages) == len(conversation) == len(sent)
    return sent, messages


def test_one_notice_per_level_over_a_whole_budget(agent):
    sent, _ = _run(agent, 60)

    # 20, 10 and 5 steps from the end.
    assert [step for step, _ in sent] == [40, 50, 55]
    assert len(sent) == len(_BUDGET_NOTICES)


def test_the_notice_is_in_context_for_the_next_call(agent):
    _sent, messages = _run(agent, 60, upto=40)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "Step budget" in messages[0]["content"]


def test_the_numbers_are_real(agent):
    sent, _ = _run(agent, 60, upto=40)
    _step, notice = sent[0]

    assert "40 of 60 used" in notice
    assert "20 left" in notice


def test_the_last_notice_says_how_little_is_left(agent):
    sent, _ = _run(agent, 60)
    _step, notice = sent[-1]

    assert "5 of 60 steps left" in notice


# --------------------------------------------------------------- cap invariance


def test_the_same_notices_fire_at_a_large_cap(agent):
    """The regression this design exists for.

    A proportional trigger puts the first notice at step 200 of a 400-step
    budget. Turns that finish well inside a generous cap never reach it, so
    the feature silently switched itself off on exactly the budgets where it
    costs least to run.
    """
    sent, _ = _run(agent, 400)

    assert [step for step, _ in sent] == [380, 390, 395]
    assert len(sent) == len(_BUDGET_NOTICES)


def test_the_trigger_is_steps_remaining_not_fraction_consumed(agent):
    """The first notice lands 20 steps from the end on every workable cap."""
    remaining_at_first_notice = {}
    for cap in (60, 150, 400, 1000):
        host = _Host.__new__(_Host)
        host._budget_notices_on = True
        host._budget_notices_sent = set()
        sent, _ = _run(host, cap)
        first_step = sent[0][0]
        remaining_at_first_notice[cap] = cap - first_step

    assert remaining_at_first_notice == {60: 20, 150: 20, 400: 20, 1000: 20}


def test_a_cap_of_150_fires_in_its_last_twenty_steps(agent):
    """The cap the follow-up sweep runs at, pinned explicitly."""
    sent, _ = _run(agent, 150)

    assert [step for step, _ in sent] == [130, 140, 145]


# ------------------------------------------------------------ small-cap floor


def test_a_small_cap_falls_back_to_the_proportional_floor(agent):
    """On a 12-step budget "20 steps left" would fire before anything happened.

    The floor is the remaining-budget mirror of the 50/75/90%-consumed rule
    this replaces, so short turns behave as they always did.
    """
    sent, _ = _run(agent, 12)

    assert [step for step, _ in sent] == [6, 9, 11]


def test_the_floor_never_rounds_down_to_zero(agent):
    """A trigger of 0 remaining could only fire once the budget was gone."""
    for cap in range(_MIN_STEPS_FOR_BUDGET_NOTICES, 30):
        for steps_left, fraction, _text in _BUDGET_NOTICES:
            assert _budget_notice_trigger(steps_left, fraction, cap) >= 1


def test_every_level_still_fires_on_the_smallest_workable_budget(agent):
    sent, _ = _run(agent, _MIN_STEPS_FOR_BUDGET_NOTICES)

    assert len(sent) == len(_BUDGET_NOTICES)


def test_the_absolute_figure_wins_once_the_budget_can_hold_it(agent):
    # 0.50 * 40 = 20, the crossover: at and above this cap the absolute
    # figure binds, below it the fraction does.
    assert _budget_notice_trigger(20, 0.50, 40) == 20
    assert _budget_notice_trigger(20, 0.50, 400) == 20
    assert _budget_notice_trigger(20, 0.50, 20) == 10


# ------------------------------------------------------------------ mechanics


def test_messages_and_conversation_do_not_share_the_dict(agent):
    """Downstream code edits these entries in place."""
    messages, conversation = [], []
    agent._announce_step_budget(messages, conversation, 40, 60)

    assert messages[0] is not conversation[0]
    assert messages[0] == conversation[0]


def test_crossing_two_levels_at_once_sends_the_one_that_is_true_now(agent):
    """A limit that moves, or a short budget, can jump past a level. The
    stale notice is marked sent rather than queued behind the current one."""
    messages, conversation = [], []

    # 55/60 is past all three levels at once.
    notice = agent._announce_step_budget(messages, conversation, 55, 60)

    assert notice is not None
    assert "5 of 60 steps left" in notice  # the most urgent text
    assert "start it now" not in notice  # not the 20-steps-left text
    assert len(messages) == 1
    # The two it skipped are spent, not queued.
    assert agent._announce_step_budget(messages, conversation, 56, 60) is None


def test_a_later_notice_is_never_followed_by_a_less_urgent_one(agent):
    """A turn told it has 5 steps left must not then be told it has 20."""
    messages, conversation = [], []
    agent._announce_step_budget(messages, conversation, 395, 400)

    for step in range(396, 401):
        assert agent._announce_step_budget(messages, conversation, step, 400) is None
    assert len(messages) == 1


def test_nothing_fires_on_a_budget_too_small_to_act_on(agent):
    for limit in range(1, _MIN_STEPS_FOR_BUDGET_NOTICES):
        sent, _ = _run(agent, limit)
        assert sent == [], f"notice fired at max_steps={limit}"
        agent._budget_notices_sent = set()


def test_nothing_fires_before_the_first_step(agent):
    assert agent._announce_step_budget([], [], 0, 60) is None


# ----------------------------------------------------------------- the switch


def test_the_notices_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_BUDGET_NOTICES", "0")
    with patch("gaia.agents.base.agent.AgentSDK"):
        host = _Host(skip_lemonade=True, silent_mode=True)

    sent, messages = _run(host, 60)

    assert sent == []
    assert messages == []


@pytest.mark.parametrize("value", ["1", "true", "on", "YES", ""])
def test_truthy_and_unset_keep_them_on(monkeypatch, value):
    monkeypatch.setenv("GAIA_AGENT_BUDGET_NOTICES", value)
    assert budget_notices_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "off", "No"])
def test_falsy_turns_them_off(monkeypatch, value):
    monkeypatch.setenv("GAIA_AGENT_BUDGET_NOTICES", value)
    assert budget_notices_enabled() is False


def test_a_typo_is_reported_rather_than_silently_ignored(monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_BUDGET_NOTICES", "maybe")
    with pytest.raises(ValueError, match="GAIA_AGENT_BUDGET_NOTICES"):
        budget_notices_enabled()


def test_unset_is_the_default(monkeypatch):
    monkeypatch.delenv("GAIA_AGENT_BUDGET_NOTICES", raising=False)
    assert "GAIA_AGENT_BUDGET_NOTICES" not in os.environ
    assert budget_notices_enabled() is True


def test_an_agent_that_never_ran_init_does_not_crash(agent):
    """Mirrors the loop's other per-turn guards: a half-built test agent gets
    no notice rather than an AttributeError."""
    bare = _Host.__new__(_Host)

    assert bare._announce_step_budget([], [], 30, 60) is None
