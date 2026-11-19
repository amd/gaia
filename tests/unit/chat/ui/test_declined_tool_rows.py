# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A call the user said no to reads as declined, not failed, live and after a reload.

The denied result carries ``status: "denied"``; one nobody answered also carries
``timed_out``, and one GAIA turned down without asking carries ``declined_by``;
neither must be shown as the user's no.
"""

from __future__ import annotations

import pytest

from gaia.ui._chat_helpers import _denial
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
    assert _denial(event) == "declined"


def test_an_unanswered_prompt_is_not_a_decline():
    event = _result_event({**DENIED, "timed_out": True})

    assert event["timed_out"] is True
    assert _denial(event) == "timedOut"


def test_a_call_gaias_own_rule_stopped_is_not_a_decline():
    """Nobody was asked, so "You declined a step" would be untrue."""
    event = _result_event({**DENIED, "denied_by": "policy"})

    assert event["denied_by"] == "policy"
    assert _denial(event) == "refused"
    assert "denied_by" in SUMMARY_VIEW_KEYS


def test_a_failed_winget_step_says_what_its_exit_code_means():
    """winget prints to stdout, so the row used to read "Command failed (exit N)"."""
    event = _result_event(
        {
            "status": "success",
            "command": "winget install --id Git.Git",
            "stdout": "An unexpected error occurred",
            "stderr": "",
            "return_code": -1978335138,
            "exit_code_meaning": "winget could not verify the source's certificate.",
        }
    )

    assert event["summary"] == (
        "winget could not verify the source's certificate. (exit -1978335138)"
    )


def test_a_saved_step_keeps_its_refused_flag():
    step = AgentStepResponse(id=1, type="tool", label="Using run_python", refused=True)

    assert step.model_dump()["refused"] is True


def test_a_call_gaia_declined_unasked_is_not_the_users_decline():
    event = _result_event({**DENIED, "declined_by": "agent"})

    assert event["declined_by"] == "agent"
    assert _denial(event) == "autoDeclined"


def test_an_unknown_decliner_is_not_passed_on():
    event = _result_event({**DENIED, "declined_by": "someone"})

    assert "declined_by" not in event
    assert _denial(event) == "declined"


@pytest.mark.parametrize(
    "event, denial",
    [
        ({"type": "tool_result", "data": {"status": "denied"}}, "declined"),
        (
            {
                "type": "tool_result",
                "data": {"status": "denied", "denied_by": "policy"},
            },
            "refused",
        ),
        (
            {"type": "tool_result", "data": {"status": "denied", "timed_out": True}},
            "timedOut",
        ),
        (
            {
                "type": "tool_result",
                "data": {"status": "denied", "declined_by": "agent"},
            },
            "autoDeclined",
        ),
        ({"type": "tool_result", "status": "error"}, None),
    ],
)
def test_a_relayed_result_is_read_from_its_data(event, denial):
    assert _denial(event) == denial


def test_the_relay_carries_the_timeout_flag():
    assert "timed_out" in SUMMARY_VIEW_KEYS


def test_the_relay_carries_who_declined():
    assert "declined_by" in SUMMARY_VIEW_KEYS


def test_a_saved_step_keeps_its_declined_flag():
    step = AgentStepResponse(id=1, type="tool", label="Using run_python", declined=True)

    assert step.model_dump()["declined"] is True


def test_a_saved_step_keeps_its_timed_out_flag():
    step = AgentStepResponse(id=1, type="tool", label="Using run_python", timedOut=True)

    assert step.model_dump()["timedOut"] is True


def test_a_saved_step_keeps_its_auto_declined_flag():
    step = AgentStepResponse(
        id=1, type="tool", label="Using run_shell_command", autoDeclined=True
    )

    assert step.model_dump()["autoDeclined"] is True
