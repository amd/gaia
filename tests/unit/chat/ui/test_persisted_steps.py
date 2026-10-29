# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reloaded answer keeps its step panel.

On rc13 one ``list_files`` step stored bare file names, which the
response model rejects, and the whole 46-step panel was dropped on reload.
"""

import json
import logging

from gaia.ui.models import FileListResponse
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.utils import message_to_response


def _message(steps) -> dict:
    return {
        "id": 7,
        "session_id": "s",
        "role": "assistant",
        "content": "Done.",
        "created_at": "2026-10-15T00:00:00",
        "agent_steps": json.dumps(steps),
    }


_STEPS = [
    {"id": 1, "type": "thinking", "label": "Thinking", "detail": "plan"},
    {
        "id": 2,
        "type": "tool",
        "label": "Using list_files",
        "tool": "list_files",
        "fileList": {"files": ["2025-05-08-wpwg-minutes.html"], "total": 12},
    },
    {"id": 3, "type": "plan", "label": "Created plan", "planSteps": ["read", "save"]},
]


def test_bare_file_names_keep_every_step():
    steps = message_to_response(_message(_STEPS)).agent_steps
    assert [s.id for s in steps] == [1, 2, 3]
    assert steps[1].fileList.files == [{"name": "2025-05-08-wpwg-minutes.html"}]


def test_one_bad_step_is_dropped_loudly_and_the_rest_survive(caplog):
    bad = {"type": "tool", "label": "no id"}
    with caplog.at_level(logging.ERROR, logger="gaia.ui.utils"):
        steps = message_to_response(_message([_STEPS[0], bad, _STEPS[2]])).agent_steps
    assert [s.id for s in steps] == [1, 3]
    assert any("dropped step" in r.getMessage() for r in caplog.records)


def test_unreadable_steps_json_is_logged(caplog):
    msg = _message([])
    msg["agent_steps"] = "{not json"
    with caplog.at_level(logging.ERROR, logger="gaia.ui.utils"):
        assert message_to_response(msg).agent_steps is None
    assert any("unreadable agent_steps" in r.getMessage() for r in caplog.records)


def test_file_list_entries_keep_dicts_and_wrap_names():
    files = FileListResponse(files=[{"name": "a", "directory": "d"}, "b"]).files
    assert files == [{"name": "a", "directory": "d"}, {"name": "b"}]


def test_live_file_list_event_carries_named_entries():
    handler = SSEOutputHandler()
    handler.pretty_print_json({"files": ["a.html", {"name": "b.md"}], "count": 2})
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    result = next(e for e in events if e.get("type") == "tool_result")
    assert result["result_data"]["files"] == [{"name": "a.html"}, {"name": "b.md"}]
