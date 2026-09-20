# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the user hears when the loop guard stops a turn on repeated calls.

Repeating one call is not evidence the work failed — a model that finished a
task and then re-ran the same check three times used to have its answer
replaced by "I can't confirm the task is finished". The guard now asks the
model once more, with the same tools but ``tool_choice="none"``, and that
reply is the answer. Repeats that errored or were refused keep their own
message: there the failure IS the evidence.
"""

import json
import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.verification import VERIFICATION_SCOPE_PREFIX
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_TOOL = "run_checks_for_loop_break_test"
_DONE = {"status": "success", "stdout": "3 passed in 0.4s"}
_ANSWER = "I fixed the loader and pytest passed: 3 passed, 0 failed."
_CANNED = "can't confirm the task is finished"
_STATS = {"input_tokens": 100, "output_tokens": 10}
_REPEATS = 3


class _DummyAgent(Agent):
    """Minimal concrete Agent with one tool whose result each test sets."""

    tool_result = _DONE

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        @tool
        def run_checks_for_loop_break_test(path: str) -> dict:
            """Run the checks for a path."""
            del path
            if callable(agent.tool_result):
                return agent.tool_result()
            return agent.tool_result

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _make_agent(streaming: bool, model_id=DEFAULT_MODEL_NAME) -> _DummyAgent:
    """The default model takes tools natively, so the loop sends ``tools=``."""
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _DummyAgent(
            silent_mode=True,
            skip_lemonade=True,
            model_id=model_id,
            max_consecutive_repeats=_REPEATS,
        )
    agent.streaming = streaming
    return agent


@pytest.fixture(params=[False, True], ids=["non-streaming", "streaming"])
def agent(request, clean_registry):  # pylint: disable=unused-argument
    return _make_agent(request.param)


def _stub_chat(agent, *replies):
    """Script the model: each entry is reply text, or an exception to raise."""
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})

    def _next():
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def _send(*_, **__):
        return SimpleNamespace(text=_next(), stats=dict(_STATS))

    def _stream(*_, **__):
        text = _next()
        if text.startswith('{"__tool_calls__"'):
            yield SimpleNamespace(text=text, is_complete=True, stats=dict(_STATS))
            return
        yield SimpleNamespace(text=text, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats=dict(_STATS))

    chat.send_messages = MagicMock(side_effect=_send)
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat
    return chat


def _model_calls(agent, chat):
    return (
        chat.send_messages_stream.call_args_list
        if agent.streaming
        else chat.send_messages.call_args_list
    )


def _native_call(number: int) -> str:
    """The same call every time — identical name and arguments trip the guard."""
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{number}",
                    "type": "function",
                    "function": {
                        "name": _TOOL,
                        "arguments": json.dumps({"path": "tests/"}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _json_call() -> str:
    """The embedded-JSON shape a model without native tool calls sends."""
    return json.dumps(
        {"thought": "check", "tool": _TOOL, "tool_args": {"path": "tests/"}}
    )


def _answer(text: str) -> str:
    return json.dumps({"thought": "done", "answer": text})


def _scope_lines(text: str) -> list:
    return [
        line for line in text.splitlines() if line.startswith(VERIFICATION_SCOPE_PREFIX)
    ]


def _repeat_then(*trailing):
    """Three identical native calls, then whatever the closing call gets."""
    return [_native_call(n) for n in range(1, _REPEATS + 1)] + list(trailing)


# ---------------------------------------------------------------------------
# Repeats that worked: the model's closing reply is the answer
# ---------------------------------------------------------------------------


def test_repeated_success_answers_from_the_work_done(agent):
    _stub_chat(agent, *_repeat_then(_ANSWER))

    result = agent.process_query("fix the loader and run the tests")

    assert result["result"].startswith(_ANSWER)
    assert _CANNED not in result["result"]
    assert len(_scope_lines(result["result"])) == 1


@pytest.mark.usefixtures("clean_registry")
def test_json_protocol_model_answers_after_repeats_too():
    """The legacy embedded-JSON path must behave like the native one."""
    agent = _make_agent(streaming=False, model_id=None)
    chat = _stub_chat(agent, *([_json_call()] * _REPEATS), _answer(_ANSWER))

    result = agent.process_query("fix the loader and run the tests")

    assert result["result"].startswith(_ANSWER)
    assert _CANNED not in result["result"]
    assert chat.send_messages.call_count == _REPEATS + 1
    # No native tools to send, so nothing for tool_choice to govern.
    assert chat.send_messages.call_args.kwargs["tools"] is None
    assert "tool_choice" not in chat.send_messages.call_args.kwargs
    # Nothing to seal either: this path carries no tool-call turn to answer.
    sent = chat.send_messages.call_args.kwargs["messages"]
    assert "not run" not in json.dumps(sent).lower()


def test_closing_request_keeps_the_tools_but_forbids_calls(agent):
    chat = _stub_chat(agent, *_repeat_then(_ANSWER))

    agent.process_query("fix the loader and run the tests")

    calls = _model_calls(agent, chat)
    assert len(calls) == _REPEATS + 1
    for loop_call in calls[:_REPEATS]:
        assert "tool_choice" not in loop_call.kwargs

    wrap_up = calls[-1].kwargs
    # Same tools as the loop (Anthropic rejects tool history without them, and
    # an unchanged prefix keeps the prompt cache); calling one is forbidden.
    assert _TOOL in [t["function"]["name"] for t in wrap_up["tools"]]
    assert wrap_up["tools"] == calls[0].kwargs["tools"]
    assert wrap_up["tool_choice"] == "none"
    assert wrap_up["system_prompt"] == calls[0].kwargs["system_prompt"]
    instruction = wrap_up["messages"][-1]
    assert instruction["role"] == "user"
    assert _TOOL in instruction["content"] and str(_REPEATS) in instruction["content"]
    assert "answer now" in instruction["content"]


def test_closing_request_answers_every_tool_call(agent):
    """The guard stops mid-call, so the last call has no result yet.

    A tool-call turn with a missing result is rejected outright by
    spec-strict providers, so the request must account for every id.
    """
    chat = _stub_chat(agent, *_repeat_then(_ANSWER))

    agent.process_query("fix the loader and run the tests")

    sent = _model_calls(agent, chat)[-1].kwargs["messages"]
    called = [
        call["id"]
        for msg in sent
        if msg.get("role") == "assistant"
        for call in msg.get("tool_calls") or []
    ]
    answered = [msg["tool_call_id"] for msg in sent if msg.get("role") == "tool"]
    assert called == [f"call_{n}" for n in range(1, _REPEATS + 1)]
    assert answered == called
    # The call the guard stopped never ran, and the transcript says so.
    assert "not run" in json.dumps(sent[-2]).lower()


def test_closing_call_counts_its_tokens_and_step(agent):
    _stub_chat(agent, *_repeat_then(_ANSWER))

    result = agent.process_query("fix the loader and run the tests")

    assert result["steps_taken"] == _REPEATS + 1
    # The step the guard stops never reaches the point where the loop books its
    # stats, so the total is the steps that completed plus the closing call.
    counted = _REPEATS - 1 + 1
    assert result["output_tokens"] == counted * _STATS["output_tokens"]
    assert result["input_tokens"] == counted * _STATS["input_tokens"]


# ---------------------------------------------------------------------------
# Repeats that failed: today's message, and no closing call
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_result, expected",
    [
        (
            {"status": "error", "error": "gh: API rate limit exceeded (HTTP 403)"},
            "API rate limit exceeded",
        ),
        (
            {"status": "error", "error": "Connection refused"},
            "Connection refused",
        ),
        (
            {"status": "denied", "error": "You declined to run this tool"},
            _CANNED,
        ),
        ({"status": "error", "error": "boom"}, "boom"),
    ],
    ids=["rate-limited", "connection-failure", "not-permitted", "generic-error"],
)
def test_failed_repeats_keep_their_message(agent, tool_result, expected):
    agent.tool_result = tool_result
    chat = _stub_chat(agent, *_repeat_then())

    result = agent.process_query("fix the loader and run the tests")

    assert expected in result["result"]
    # No closing call: the failure the user needs to hear already happened.
    assert len(_model_calls(agent, chat)) == _REPEATS
    assert result["steps_taken"] == _REPEATS


# ---------------------------------------------------------------------------
# The closing call fails: today's message, plus a line saying why
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply, reason",
    [
        (
            ConnectionError("Lemonade Server not reachable at http://localhost:8000"),
            "Lemonade Server not reachable at http://localhost:8000",
        ),
        ("", "empty reply"),
        (
            json.dumps({"thought": "again", "tool": _TOOL, "tool_args": {"path": "."}}),
            f"asked to run {_TOOL} instead of answering",
        ),
        (_native_call(4), f"asked to run {_TOOL} instead of answering"),
    ],
    ids=["call-raises", "empty-reply", "asks-for-a-tool", "calls-a-tool-anyway"],
)
def test_failed_closing_call_returns_the_notice_and_why(agent, caplog, reply, reason):
    _stub_chat(agent, *_repeat_then(reply))

    with caplog.at_level(logging.WARNING, logger="gaia.agents.base.agent"):
        result = agent.process_query("fix the loader and run the tests")

    answer = result["result"]
    assert _CANNED in answer
    failure_lines = [line for line in answer.splitlines() if reason in line]
    assert len(failure_lines) == 1
    assert "couldn't" in failure_lines[0]
    assert len(_scope_lines(answer)) == 1
    assert any(
        r.levelno == logging.WARNING and reason in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Cancellation is honoured, not papered over with a summary
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_stop_during_the_closing_reply_cancels_the_turn():
    agent = _make_agent(streaming=True)
    console = MagicMock()
    console.cancelled = threading.Event()
    agent.console = console
    chat = _stub_chat(agent, *_repeat_then())

    def _closing_stream(*_, **__):
        console.cancelled.set()
        yield SimpleNamespace(text="I fixed", is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats=dict(_STATS))

    real_stream = chat.send_messages_stream.side_effect
    chat.send_messages_stream.side_effect = [real_stream() for _ in range(_REPEATS)] + [
        _closing_stream()
    ]

    result = agent.process_query("fix the loader and run the tests")

    assert result["status"] == "cancelled"
    assert result["result"] == ""
