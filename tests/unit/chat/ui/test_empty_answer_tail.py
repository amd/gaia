# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A turn that ends with no answer text says why, unless a card answered it.

Runs real streamed turns through the SSE producer: a turn that ran tools and
then wrote nothing used to end on an empty bubble with no explanation.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.run_manager import RunManager

pytestmark = pytest.mark.asyncio


class _SilentAgent:
    """Cached agent that runs the given tool events, then answers nothing."""

    def __init__(self, tool_events):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.tool_events = tool_events
        self._session_id = None

    def _register_tools(self):
        pass

    def process_query(self, message):
        handler = helpers._active_sse_handlers.get(self._session_id)
        for event in self.tool_events:
            handler._emit(event)
        return {
            "result": "",
            "model_messages": [
                {"role": "user", "content": message},
                {"role": "assistant", "content": ""},
            ],
        }


_TOOL_START = {"type": "tool_start", "tool": "search_file", "detail": "notes"}
_TOOL_RESULT = {"type": "tool_result", "title": "Found 2 files", "success": True}
_CARD_RESULT = {
    **_TOOL_RESULT,
    "render": "email_list",
    "data": {"messages": [{"subject": "Hi"}]},
}


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    manager = RunManager()
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield db
    helpers._agent_cache.clear()
    db.close()


async def _run(db, tool_events):
    agent = _SilentAgent(tool_events)
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent._session_id = session["id"]
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    request = ChatRequest(
        session_id=session["id"], message="Find my notes", stream=True
    )
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    await asyncio.wait_for(consume(), timeout=15)
    return session["id"], events


def _of(events, kind):
    return [e for e in events if e.get("type") == kind]


async def test_tools_without_an_answer_end_with_an_actionable_message(runtime):
    sid, events = await _run(runtime, [_TOOL_START, _TOOL_RESULT])

    [done] = _of(events, "done")
    assert done["content"] == helpers._NO_ANSWER_AFTER_TOOLS_MSG
    assert done["failed"] is True
    assert "cancelled" not in done
    assert _of(events, "answer")[-1]["content"] == helpers._NO_ANSWER_AFTER_TOOLS_MSG
    saved = runtime.get_messages(sid)[-1]
    assert saved["id"] == done["message_id"]
    assert saved["content"] == helpers._NO_ANSWER_AFTER_TOOLS_MSG
    assert [s["tool"] for s in saved["agent_steps"]] == ["search_file"]
    assert runtime.get_session(sid)["last_run_status"] == "failed"


async def test_a_card_that_answered_keeps_the_turn_empty(runtime):
    sid, events = await _run(runtime, [_TOOL_START, _CARD_RESULT])

    [done] = _of(events, "done")
    assert done["content"] == ""
    assert "failed" not in done
    saved = runtime.get_messages(sid)[-1]
    assert saved["content"] == ""
    assert saved["agent_steps"][0]["render"] == "email_list"
    assert runtime.get_session(sid)["last_run_status"] == "done"


async def test_nothing_from_a_down_backend_reports_an_error(runtime):
    problem = "No answer came back because Lemonade Server isn't responding."
    with patch.object(
        helpers, "_empty_answer_backend_problem", AsyncMock(return_value=problem)
    ) as probe:
        sid, events = await _run(runtime, [])

    probe.assert_awaited_once_with("M-GGUF")
    assert not _of(events, "done")
    assert _of(events, "error")[-1]["content"] == problem
    assert runtime.get_messages(sid)[-1]["content"] == problem


async def test_nothing_from_a_healthy_backend_says_the_model_was_empty(runtime):
    with patch.object(
        helpers, "_empty_answer_backend_problem", AsyncMock(return_value=None)
    ):
        sid, events = await _run(runtime, [])

    assert not _of(events, "error")
    [done] = _of(events, "done")
    assert done["content"] == helpers._EMPTY_MODEL_ANSWER_MSG
    assert done["failed"] is True
    saved = runtime.get_messages(sid)[-1]
    assert saved["content"] == helpers._EMPTY_MODEL_ANSWER_MSG
    assert "Lemonade" not in saved["content"]
    assert runtime.get_session(sid)["last_run_status"] == "failed"


async def test_a_turn_with_steps_never_probes_the_backend(runtime):
    with patch.object(helpers, "_empty_answer_backend_problem", AsyncMock()) as probe:
        await _run(runtime, [_TOOL_START, _TOOL_RESULT])

    probe.assert_not_awaited()


_AGENT_DOWN = {
    "type": "agent_error",
    "content": "GAIA couldn't start its agent. Send your message again.",
}


async def test_an_agent_that_could_not_start_is_not_called_a_tool_run(runtime):
    """rc10: the start failure was followed by "GAIA ran its tools but..."."""
    sid, events = await _run(runtime, [_AGENT_DOWN])

    [done] = _of(events, "done")
    assert done["failed"] is True
    assert done["content"] == ""
    assert all(
        helpers._NO_ANSWER_AFTER_TOOLS_MSG not in (e.get("content") or "")
        for e in events
    )
    saved = runtime.get_messages(sid)[-1]
    assert saved["content"] == ""
    [step] = saved["agent_steps"]
    assert step["type"] == "error" and "couldn't start its agent" in step["detail"]
    assert runtime.get_session(sid)["last_run_status"] == "failed"


async def test_a_tool_run_that_then_errors_still_says_no_answer_came(runtime):
    sid, events = await _run(
        runtime, [_TOOL_START, _TOOL_RESULT, {**_AGENT_DOWN, "content": "boom"}]
    )

    [done] = _of(events, "done")
    assert done["content"] == helpers._NO_ANSWER_AFTER_TOOLS_MSG
