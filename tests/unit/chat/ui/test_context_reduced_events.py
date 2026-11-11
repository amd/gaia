# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A context reduction reaches the Agent UI and the TUI as a lasting notice."""

import json

from gaia.agents.base.context_eviction import context_eviction_message
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

_EVENT = {
    "reason": "proactive",
    "evicted_results": 3,
    "evicted_tokens_est": 12400,
    "prompt_tokens_before": 10100,
    "prompt_tokens_after": 6200,
    "context_window": 16384,
    "threshold_tokens": 9830,
    "items": [
        {
            "tool": "read_file",
            "summary": "path=a.md",
            "artifact": "output_1",
            "chars": 9000,
        },
        {
            "tool": "read_file",
            "summary": "path=b.md",
            "artifact": "output_2",
            "chars": 8000,
        },
        {
            "tool": "search_web",
            "summary": "query=x",
            "artifact": "output_3",
            "chars": 7000,
        },
    ],
}


def _emitted():
    handler = SSEOutputHandler()
    handler.print_context_eviction(_EVENT)
    return handler.event_queue.get_nowait()


def test_the_sse_console_sends_a_context_reduced_status():
    event = _emitted()
    assert event["type"] == "status"
    assert event["status"] == "context_reduced"
    assert event["message"] == context_eviction_message(_EVENT)
    detail = event["context_reduced"]
    assert detail["results"] == 3
    assert detail["tokens_freed"] == 12400
    assert detail["prompt_tokens_after"] == 6200
    assert detail["context_window"] == 16384
    assert detail["reason"] == "proactive"
    assert [i["tool"] for i in detail["items"]] == [
        "read_file",
        "read_file",
        "search_web",
    ]
    json.dumps(event)


def test_the_tui_translation_keeps_the_detail_and_never_dedupes_it():
    translator = CanonicalTranslator(run_id=None, agent_id="gaia", debug=False)
    event = _emitted()

    first = translator.translate(event)
    second = translator.translate(event)

    assert (
        first
        == second
        == [
            {
                "type": "status",
                "message": context_eviction_message(_EVENT),
                "context_reduced": event["context_reduced"],
            }
        ]
    )


def test_the_saved_chat_keeps_the_notice():
    from gaia.ui._chat_helpers import _context_eviction_step

    step = _context_eviction_step(_emitted(), step_id=7, timestamp=5)
    assert step == {
        "id": 7,
        "type": "status",
        "label": context_eviction_message(_EVENT),
        "active": False,
        "notice": True,
        "timestamp": 5,
    }


def test_the_daemon_sidecar_and_stdio_streams_carry_the_same_detail():
    event = _emitted()
    stdio = CanonicalTranslator(run_id=None, agent_id="gaia", debug=False)
    daemon = CanonicalTranslator("run-123", agent_id="gaia")
    (from_stdio,) = stdio.translate(event)
    (from_daemon,) = daemon.translate(event)

    assert from_stdio["context_reduced"] == from_daemon["context_reduced"]
    assert from_daemon["context_reduced"] == event["context_reduced"]
    assert from_stdio["message"] == from_daemon["message"] == event["message"]


def test_a_reattaching_agent_ui_client_replays_the_notice():

    from gaia.ui.run_manager import DONE, Run

    run = Run("session-1")
    run.emit(f"data: {json.dumps(_emitted())}\n\n")
    run.finish()

    q = run.subscribe()
    replayed = []
    while True:
        item = q.get_nowait()
        if item is DONE:
            break
        replayed.append(json.loads(item[len("data: ") :]))
    assert replayed[0]["status"] == "context_reduced"
    assert replayed[0]["context_reduced"]["results"] == 3


def test_a_reloaded_chat_keeps_the_notice_row():
    from gaia.ui._chat_helpers import _context_eviction_step
    from gaia.ui.database import ChatDatabase
    from gaia.ui.models import AgentStepResponse

    db = ChatDatabase(":memory:")
    session = db.create_session(title="t")
    step = _context_eviction_step(_emitted(), step_id=1, timestamp=7)
    db.add_message(session["id"], "assistant", "done", agent_steps=[step])

    (message,) = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    steps = message["agent_steps"]
    if isinstance(steps, str):
        steps = json.loads(steps)
    reloaded = AgentStepResponse(**steps[0])
    assert reloaded.notice is True
    assert reloaded.label == context_eviction_message(_EVENT)
    assert reloaded.type == "status" and reloaded.active is False
