# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reused agent reports each query's own errors, not every past one (#3859)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent

ANSWER = json.dumps({"thought": "done", "answer": "Recovered"})
FAIL = RuntimeError("synthetic backend failure")


class _DummyAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True, max_steps=1)
        a.streaming = False
        return a


def _script(agent, *replies):
    """Each send_messages call takes the next reply: text, an exception, or a callable."""
    replies = list(replies)

    def _send(*_, **__):
        reply = replies.pop(0)
        if callable(reply) and not isinstance(reply, BaseException):
            reply = reply()
        if isinstance(reply, BaseException):
            raise reply
        resp = MagicMock()
        resp.text = reply
        resp.stats = None
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)


def _query(agent, *replies):
    _script(agent, *replies)
    return agent.process_query("question", max_steps=1)


def test_failed_then_successful_query_reports_success(agent):
    first = _query(agent, FAIL)
    second = _query(agent, ANSWER)

    assert first["status"] == "failed"
    assert first["error_count"] >= 1
    assert second["result"].startswith("Recovered")
    assert second["status"] == "success"
    assert second["error_count"] == 0
    assert second["error_history"] == []


def test_successful_then_failed_query_reports_failure(agent):
    first = _query(agent, ANSWER)
    second = _query(agent, FAIL)

    assert first["status"] == "success"
    assert second["status"] == "failed"
    assert second["error_count"] == len(second["error_history"]) >= 1


def test_successful_queries_stay_clean(agent):
    for _ in range(3):
        result = _query(agent, ANSWER)
        assert result["status"] == "success"
        assert result["error_count"] == 0


def test_earlier_result_is_not_changed_by_a_later_query(agent):
    first = _query(agent, FAIL)
    snapshot = list(first["error_history"])

    _query(agent, FAIL)
    _query(agent, ANSWER)

    assert first["error_history"] == snapshot
    assert first["error_count"] == len(snapshot)
    assert first["error_history"] is not agent.error_history


def test_error_inside_the_turn_still_fails_a_usable_answer(agent):
    _query(agent, ANSWER)

    def _error_then_answer():
        agent.error_history.append("tool blew up mid-turn")
        return ANSWER

    result = _query(agent, _error_then_answer)

    assert result["result"].startswith("Recovered")
    assert result["status"] == "failed"
    assert result["error_history"] == ["tool blew up mid-turn"]


def test_cumulative_history_keeps_every_query(agent):
    _query(agent, FAIL)
    _query(agent, ANSWER)
    _query(agent, FAIL)

    history = agent.get_error_history()
    assert len(history) == len(agent.error_history) >= 2
    history.clear()
    assert agent.error_history, "get_error_history must return a copy"


def test_recovery_marks_only_this_querys_errors(agent):
    """A clean parse this turn must not mark an earlier turn's errors recovered."""
    agent.error_history.append({"type": "tool_call_parse_error", "error": "old"})
    _query(agent, ANSWER)

    assert "recovered" not in agent.error_history[0]


def test_error_recorded_before_any_query_is_not_blamed_on_the_first(agent):
    agent.error_history.append("setup hiccup")

    result = _query(agent, ANSWER)

    assert result["status"] == "success"
    assert result["error_count"] == 0
    assert agent.get_error_history() == ["setup hiccup"]
