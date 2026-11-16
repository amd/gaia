# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The turn log records the answer the user saw (#5087).

A streamed ``{"answer": ...}`` reply goes out as an ``answer`` event before the
stream closes. The stdio front-end ends the turn on it and cancels the run, so
the run left through its Stop path and logged ``answer_chars: 0`` for a turn
that had answered.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.ui.sse_handler import SSEOutputHandler

_ANSWER = "Roy Thomas Fielding is the first-listed author of RFC 2068."


class _EndsTurnOnAnswer(SSEOutputHandler):
    """Cancels the run on the first answer event, as ``stdio.run_turn`` does."""

    def _emit(self, event):
        super()._emit(event)
        if event.get("type") == "answer":
            self.cancelled.set()


class _Agent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        return _EndsTurnOnAnswer()


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _streaming_agent(reply: str) -> Agent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _Agent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = True
    agent.verification_scope_statement = lambda: None

    def _stream(*_, **__):
        yield SimpleNamespace(text=reply, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat
    return agent


def _records(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_a_turn_ended_on_its_streamed_answer_logs_that_answer(
    clean_registry, monkeypatch, tmp_path
):  # pylint: disable=unused-argument
    log = tmp_path / "turns.jsonl"
    monkeypatch.setenv("GAIA_TURN_LOG", str(log))
    agent = _streaming_agent(json.dumps({"answer": _ANSWER}))

    agent.process_query("who wrote RFC 2068?")

    (record,) = _records(log)
    assert record["answer_chars"] == len(_ANSWER)


def test_a_stop_before_any_answer_still_logs_none(
    clean_registry, monkeypatch, tmp_path
):  # pylint: disable=unused-argument
    log = tmp_path / "turns.jsonl"
    monkeypatch.setenv("GAIA_TURN_LOG", str(log))
    agent = _streaming_agent("partial text the user stopped")
    agent.console.cancelled.set()

    agent.process_query("who wrote RFC 2068?")

    (record,) = _records(log)
    assert record["answer_chars"] == 0


def test_a_new_turn_forgets_the_last_answer():
    handler = SSEOutputHandler()
    handler.print_final_answer(_ANSWER)
    assert handler.delivered_answer == _ANSWER

    handler.print_processing_start("next", 10)
    assert handler.delivered_answer == ""
