# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill a turn runs on reaches the Agent UI and stays in the saved turn."""

import pytest

from gaia.ui._chat_helpers import _insert_skill_step
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


def test_skill_use_is_one_event_naming_the_skill_and_how():
    handler = SSEOutputHandler()

    handler.print_skill_use(
        "analyze-recording", ["gaia-voice", "analyze-recording"], "auto"
    )

    event = handler.event_queue.get_nowait()
    assert event == {
        "type": "skill_use",
        "skill": "analyze-recording",
        "how": "auto",
        "loaded": ["gaia-voice", "analyze-recording"],
    }


def _tool(step_id, active):
    return {
        "id": step_id,
        "type": "tool",
        "tool": "analyze_transcript",
        "active": active,
    }


def test_the_skill_step_goes_before_the_call_that_brought_it_in():
    steps = [{"id": 1, "type": "thinking"}, _tool(2, active=True)]

    _insert_skill_step(steps, {"id": 3, "type": "tool", "tool": "use_skill"})

    assert [s["id"] for s in steps] == [1, 3, 2]


@pytest.mark.parametrize(
    "how,expected",
    [
        ("auto", [{"type": "status", "message": "Using skill: analyze-recording"}]),
        ("loaded", [{"type": "status", "message": "Using skill: analyze-recording"}]),
        # Its load_skill call already went out as a tool_call.
        ("model", []),
    ],
)
def test_query_clients_see_the_skill_in_use(how, expected):
    translator = CanonicalTranslator("run-1", agent_id="gaia")
    event = {
        "type": "skill_use",
        "skill": "analyze-recording",
        "how": how,
        "loaded": [],
    }

    out = translator.translate(event)

    assert [{k: e[k] for k in ("type", "message")} for e in out] == expected


def test_with_no_call_running_the_skill_step_goes_last():
    steps = [_tool(1, active=False), {"id": 2, "type": "thinking"}]

    _insert_skill_step(steps, {"id": 3, "type": "tool", "tool": "use_skill"})

    assert [s["id"] for s in steps] == [1, 2, 3]
