# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn shows one answer, also when an answer check rewrites it (#8696).

The first answer streams, then the loop checks it. The model may call a tool
and answer again in other words. The first answer was a draft: it is taken
back when its replacement starts, so the chat never keeps both.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import ANSWER_CHECK_MESSAGE, Agent
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.ui.sse_handler import ANSWER_CHECK_PHASE, SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

_DRAFT = "The command was declined, so nothing ran and the file is unchanged."
_CHECKED = "The file is still there: the command was declined, so nothing ran."

_RETRACTION = {
    "type": "status",
    "status": "working",
    "phase": ANSWER_CHECK_PHASE,
    "message": ANSWER_CHECK_MESSAGE,
    "retracts_answer": True,
}


class _CheckedAgent(Agent):
    """An agent whose own answer check objects to the first answer."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def answer_problems(self, answer: str):
        return ["It does not say the file was read."] if answer == _DRAFT else []


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        event = handler.event_queue.get()
        if event is not None:
            events.append(event)
    return events


def _answer_as_drawn(events):
    """What a front end shows as the answer: text since the last retraction or tool."""
    text = ""
    for event in events:
        if event["type"] == "chunk":
            text += event["content"]
        elif event["type"] == "answer":
            text = event["content"]
        elif event["type"] == "commentary" or event.get("retracts_answer"):
            text = ""
    return text


def _says(text):
    def call():
        yield SimpleNamespace(text=text, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    return call


def _turn(*calls):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _CheckedAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = True
    agent._is_loaded_ctx_too_small = lambda: False
    agent.console = SSEOutputHandler()
    queue = list(calls)

    def _stream(*_, **__):
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        yield from queue.pop(0)()

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat
    result = agent.process_query("Delete junk2.txt", max_steps=5)
    return result, _drain(agent.console)


@pytest.fixture
def handler():
    return SSEOutputHandler()


def test_a_checked_turn_takes_the_first_answer_back_before_the_second():
    result, events = _turn(_says(_DRAFT), _says(_CHECKED))

    assert result["result"] == _CHECKED
    retractions = [e for e in events if e.get("retracts_answer")]
    assert retractions == [_RETRACTION]
    chunks = [i for i, e in enumerate(events) if e["type"] == "chunk"]
    at = events.index(_RETRACTION)
    # The draft is on screen until its replacement starts.
    assert "".join(events[i]["content"] for i in chunks if i < at) == _DRAFT
    assert "".join(events[i]["content"] for i in chunks if i > at) == _CHECKED
    assert _answer_as_drawn(events) == _CHECKED
    assert [e["content"] for e in events if e["type"] == "answer"] == [_CHECKED]


def test_a_turn_no_check_stops_takes_nothing_back():
    _result, events = _turn(_says(_CHECKED))

    assert not any(e.get("retracts_answer") for e in events)
    assert _answer_as_drawn(events) == _CHECKED


def test_a_tool_the_check_calls_does_not_keep_the_draft_as_a_note(handler):
    handler._emit({"type": "chunk", "content": _DRAFT})
    handler.supersede_answer(ANSWER_CHECK_MESSAGE)
    assert not _drain(handler)[1:]  # nothing is taken back yet

    handler._emit({"type": "tool_start", "tool": "read_file"})
    handler._emit({"type": "chunk", "content": _CHECKED})
    events = _drain(handler)

    assert [e["type"] for e in events] == ["status", "tool_start", "chunk"]
    assert events[0] == _RETRACTION
    assert not any(e["type"] == "commentary" for e in events)
    assert handler.delivered_answer == ""


def test_an_answer_kept_after_the_check_is_never_taken_back(handler):
    """The check passed without a word: the final answer is the one on screen."""
    handler._emit({"type": "chunk", "content": _DRAFT})
    handler.supersede_answer(ANSWER_CHECK_MESSAGE)
    handler.print_final_answer(_DRAFT)
    events = _drain(handler)

    assert not any(e.get("retracts_answer") for e in events)
    assert _answer_as_drawn(events) == _DRAFT
    assert handler.delivered_answer == _DRAFT


def test_a_check_with_no_answer_on_screen_takes_nothing_back(handler):
    handler._emit({"type": "tool_start", "tool": "write_file"})
    handler.supersede_answer(ANSWER_CHECK_MESSAGE)
    handler._emit({"type": "chunk", "content": _CHECKED})

    assert not any(e.get("retracts_answer") for e in _drain(handler))


def test_one_check_does_not_take_back_the_next_turns_answer(handler):
    handler._emit({"type": "chunk", "content": _DRAFT})
    handler.supersede_answer(ANSWER_CHECK_MESSAGE)
    handler.print_processing_start("Next question", 5, "model")
    _drain(handler)
    handler._emit({"type": "chunk", "content": _CHECKED})

    assert not any(e.get("retracts_answer") for e in _drain(handler))


def test_the_retraction_reaches_a_relayed_front_end():
    """The daemon's stream carries the flag and the phase, so the TUI and a
    relayed Agent UI drop the draft too."""
    out = CanonicalTranslator("run-1", agent_id="gaia").translate(dict(_RETRACTION))

    assert out == [
        {
            "type": "status",
            "message": ANSWER_CHECK_MESSAGE,
            "retracts_answer": True,
            "phase": ANSWER_CHECK_PHASE,
        }
    ]
