# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The composer's spend meter and Settings count the same cloud turns.

Settings left failed turns out of the month, but the meter still listed each
one as a "stopped answer" whose cost is unknown: five turns on a model id that
does not exist read "0 stopped answer(s) here, 5 today".
"""

import pytest

from gaia.llm.inference_location import InferenceLocation
from gaia.ui import spending

from .test_spending_failed_turns import (  # noqa: F401  (fixture)
    FLASH,
    MISSING,
    _stream_turn,
    _StreamAgent,
    db,
)

_SINCE = "2000-01-01T00:00:00Z"


class _CloudAgent(_StreamAgent):
    """A stream agent whose turns are known to run in the cloud."""

    def _inference_location(self):
        return InferenceLocation(provider="fireworks", model=self.model_id, remote=True)


async def _meter(db, model_id, agent):  # noqa: F811
    await _stream_turn(db, model_id, agent)
    [session] = db.list_sessions()
    spend = spending.chat_spend(db, session["id"], model_id, _SINCE)
    return spend, spending.month_report(db)


def _cost_kind(db):  # noqa: F811
    [session] = db.list_sessions()
    [answer] = db.answer_costs(session_id=session["id"])
    return answer["cost"]["kind"]


@pytest.mark.asyncio
async def test_a_turn_that_failed_unanswered_is_in_neither_count(db):  # noqa: F811
    spend, month = await _meter(db, MISSING, _CloudAgent(MISSING))

    assert month["turns"] == 0
    assert (spend["chat"]["answers"], spend["chat"]["unpriced_answers"]) == (0, 0)
    assert (spend["today"]["answers"], spend["today"]["unpriced_answers"]) == (0, 0)
    assert _cost_kind(db) == "no_reply"


@pytest.mark.asyncio
async def test_a_failure_notice_is_in_neither_count(db):  # noqa: F811
    agent = _CloudAgent(MISSING, fail=False, failed_answer="HTTP 404.")
    spend, month = await _meter(db, MISSING, agent)

    assert month["turns"] == 0
    assert spend["today"]["unpriced_answers"] == 0


@pytest.mark.asyncio
async def test_a_turn_stopped_before_any_reply_is_in_neither_count(db):  # noqa: F811
    agent = _CloudAgent(FLASH, stop=True, fail=False)
    spend, month = await _meter(db, FLASH, agent)

    assert month["turns"] == 0
    assert spend["today"]["unpriced_answers"] == 0


@pytest.mark.asyncio
async def test_an_answer_stopped_partway_is_a_stopped_answer_in_both(
    db,  # noqa: F811
):
    agent = _CloudAgent(FLASH, say="The answer is", stop=True, fail=False)
    spend, month = await _meter(db, FLASH, agent)

    assert (month["turns"], month["unreported_turns"]) == (1, 1)
    assert (spend["chat"]["answers"], spend["chat"]["unpriced_answers"]) == (1, 1)
    assert _cost_kind(db) == "unreported"


def test_one_rule_decides_what_is_left_out():
    assert spending.never_answered(True, {}) is True
    assert spending.never_answered(True, {"input_tokens": 0, "output_tokens": 0})
    assert not spending.never_answered(True, {"input_tokens": 9, "output_tokens": 0})
    assert not spending.never_answered(False, {})
