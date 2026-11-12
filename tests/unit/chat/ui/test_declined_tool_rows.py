# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A call the user said no to reads as declined, not failed, live and after a reload.

The denied result carries ``status: "denied"``; one nobody answered also carries
``timed_out`` and must not be shown as the user's no.
"""

from __future__ import annotations

import pytest

from gaia.ui._chat_helpers import _declined_call
from gaia.ui.models import AgentStepResponse
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import SUMMARY_VIEW_KEYS

DENIED = {"status": "denied", "error": "Tool 'run_python' was denied by the user."}


def _drain(handler: SSEOutputHandler) -> list:
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _result_event(data: dict) -> dict:
    handler = SSEOutputHandler()
    handler.pretty_print_json(data, title="Result")
    return next(e for e in _drain(handler) if e["type"] == "tool_result")


def test_a_declined_result_says_so():
    event = _result_event(DENIED)

    assert event["status"] == "denied"
    assert "timed_out" not in event
    assert _declined_call(event)


def test_an_unanswered_prompt_is_not_a_decline():
    event = _result_event({**DENIED, "timed_out": True})

    assert event["timed_out"] is True
    assert not _declined_call(event)


@pytest.mark.parametrize(
    "event, declined",
    [
        ({"type": "tool_result", "data": {"status": "denied"}}, True),
        (
            {"type": "tool_result", "data": {"status": "denied", "timed_out": True}},
            False,
        ),
        ({"type": "tool_result", "status": "error"}, False),
    ],
)
def test_a_relayed_result_is_read_from_its_data(event, declined):
    assert _declined_call(event) is declined


def test_the_relay_carries_the_timeout_flag():
    assert "timed_out" in SUMMARY_VIEW_KEYS


def test_a_saved_step_keeps_its_declined_flag():
    step = AgentStepResponse(id=1, type="tool", label="Using run_python", declined=True)

    assert step.model_dump()["declined"] is True
