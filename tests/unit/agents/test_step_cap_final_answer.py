# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the user hears when the agent loop runs out of steps.

The loop used to end with a canned note ("Reached maximum steps limit", plus a
tools list that was always empty) that never said what the agent found or why
it stopped. It now asks the model once more, with the same tools but
``tool_choice="none"``, and that reply is the answer. If that call fails, the
canned note comes back with a line saying why.
"""

import json
import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.verification import VERIFICATION_NOTE_OPENER
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_TOOL = "gh_issue_view_for_step_cap_test"
_RATE_LIMITED = {
    "status": "error",
    "error": "gh: API rate limit exceeded for user (HTTP 403)",
}
_SUMMARY = (
    "GitHub's API rate-limited every request, so I couldn't read the issue. "
    "Wait for the limit to reset or set GH_TOKEN, then ask again."
)
_STATS = {"input_tokens": 100, "output_tokens": 10}


class _DummyAgent(Agent):
    """Minimal concrete Agent with one tool whose result each test sets."""

    tool_result = _RATE_LIMITED

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        @tool
        def gh_issue_view_for_step_cap_test(number: int) -> dict:
            """Show a GitHub issue."""
            del number
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


_NOTE = f"{VERIFICATION_NOTE_OPENER} — sentinel."


def _make_agent(streaming: bool, model_id=DEFAULT_MODEL_NAME) -> _DummyAgent:
    """The default model takes tools natively, so the loop sends ``tools=``."""
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _DummyAgent(silent_mode=True, skip_lemonade=True, model_id=model_id)
    agent.streaming = streaming
    # Most turns carry no note; pin one so "attached exactly once" is testable.
    agent.verification_scope_statement = lambda: _NOTE
    return agent


@pytest.fixture(params=[False, True], ids=["non-streaming", "streaming"])
def agent(request, clean_registry):  # pylint: disable=unused-argument
    return _make_agent(request.param)


def _stub_chat(agent, *replies):
    """Script the model: each entry is reply text, or an exception to raise.

    Serves ``send_messages`` and ``send_messages_stream`` from one queue. A
    native tool-call envelope arrives on the stream as a single complete chunk,
    the way ``AgentSDK.send_messages_stream`` delivers it.
    """
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


def _tool_call(number: int) -> str:
    """A native tool call, the shape the default (tool-calling) model sends."""
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{number}",
                    "type": "function",
                    "function": {
                        "name": _TOOL,
                        "arguments": json.dumps({"number": number}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _answer(text: str) -> str:
    return json.dumps({"thought": "done", "answer": text})


def _scope_lines(text: str) -> list:
    return [
        line for line in text.splitlines() if line.startswith(VERIFICATION_NOTE_OPENER)
    ]


# ---------------------------------------------------------------------------
# The step limit runs out: the model's no-tool-call reply is the answer
# ---------------------------------------------------------------------------


def test_step_cap_answer_is_the_models_no_tool_call_reply(agent):
    _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3), _SUMMARY)

    result = agent.process_query("summarize issue 42", max_steps=3)

    assert result["result"].startswith(_SUMMARY)
    assert "I ran out of steps" not in result["result"]
    assert len(_scope_lines(result["result"])) == 1
    # The task did hit the cap, whatever the reply says.
    assert result["status"] == "incomplete"
    assert result["max_steps_reached"] is True


@pytest.mark.usefixtures("clean_registry")
def test_json_protocol_model_answers_at_the_cap_too():
    """A model without native tool calls replies in the JSON envelope."""
    agent = _make_agent(streaming=False, model_id=None)
    calls = [
        json.dumps({"thought": "look", "tool": _TOOL, "tool_args": {"number": n}})
        for n in (1, 2, 3)
    ]
    chat = _stub_chat(agent, *calls, _answer(_SUMMARY))

    result = agent.process_query("summarize issue 42", max_steps=3)

    assert result["result"].startswith(_SUMMARY)
    assert chat.send_messages.call_count == 4
    # No native tools to send, so nothing for tool_choice to govern.
    assert chat.send_messages.call_args.kwargs["tools"] is None
    assert "tool_choice" not in chat.send_messages.call_args.kwargs


def test_step_cap_request_keeps_the_tools_but_forbids_calls(agent):
    agent.tool_result = {"status": "success", "body": "issue text"}
    chat = _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3), _SUMMARY)

    agent.process_query("summarize issue 42", max_steps=3)

    calls = _model_calls(agent, chat)
    assert len(calls) == 4
    for loop_call in calls[:3]:
        offered = [t["function"]["name"] for t in loop_call.kwargs["tools"]]
        assert _TOOL in offered
        assert "tool_choice" not in loop_call.kwargs

    wrap_up = calls[3].kwargs
    # Same tools as the loop (Anthropic rejects tool history without them, and
    # an unchanged prefix keeps the prompt cache); calling one is forbidden.
    assert wrap_up["tools"] == calls[2].kwargs["tools"]
    assert wrap_up["tool_choice"] == "none"
    assert wrap_up["system_prompt"] == calls[0].kwargs["system_prompt"]
    # Same conversation, tool results included, plus one closing instruction.
    sent = wrap_up["messages"]
    assert sent[0] == {"role": "user", "content": "summarize issue 42"}
    assert [m["role"] for m in sent] == ["user"] + ["assistant", "tool"] * 3 + ["user"]
    assert [m["tool_call_id"] for m in sent if m["role"] == "tool"] == [
        "call_1",
        "call_2",
        "call_3",
    ]
    assert "3 steps" in sent[-1]["content"]
    assert "can't call more tools" in sent[-1]["content"]


def test_step_cap_call_counts_its_tokens_and_step(agent):
    agent.tool_result = {"status": "success", "body": "issue text"}
    _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3), _SUMMARY)

    result = agent.process_query("summarize issue 42", max_steps=3)

    assert result["steps_taken"] == 4
    assert result["output_tokens"] == 4 * _STATS["output_tokens"]
    assert result["input_tokens"] == 4 * _STATS["input_tokens"]


def test_answer_before_the_cap_is_untouched(agent):
    chat = _stub_chat(agent, _tool_call(1), _answer("Issue 42 is about login."))

    result = agent.process_query("summarize issue 42", max_steps=5)

    assert len(_model_calls(agent, chat)) == 2
    assert result["result"] == agent._with_verification_scope(
        "Issue 42 is about login."
    )
    assert result["max_steps_reached"] is False
    assert "I ran out of steps" not in result["result"]


# ---------------------------------------------------------------------------
# The closing call fails: canned note, plus a line saying why
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
            json.dumps({"thought": "retry", "tool": _TOOL, "tool_args": {"number": 4}}),
            f"asked to run {_TOOL} instead of answering",
        ),
        (_tool_call(4), f"asked to run {_TOOL} instead of answering"),
    ],
    ids=["call-raises", "empty-reply", "asks-for-a-tool", "calls-a-tool-anyway"],
)
def test_failed_step_cap_call_returns_the_note_and_why(agent, caplog, reply, reason):
    _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3), reply)

    with caplog.at_level(logging.WARNING, logger="gaia.agents.base.agent"):
        result = agent.process_query("summarize issue 42", max_steps=3)

    answer = result["result"]
    assert "I ran out of steps before I could finish — I used 3 of the 3" in answer
    assert "`--max-steps 53`" in answer
    assert f"{_TOOL}: 3x" in answer
    failure_lines = [line for line in answer.splitlines() if reason in line]
    assert len(failure_lines) == 1
    assert "couldn't" in failure_lines[0]
    assert len(_scope_lines(answer)) == 1
    assert result["status"] == "incomplete"
    assert result["max_steps_reached"] is True
    assert any(
        r.levelno == logging.WARNING and reason in r.getMessage()
        for r in caplog.records
    )


def test_max_steps_message_lists_the_tools_that_ran(agent):
    _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3), _SUMMARY)
    result = agent.process_query("summarize issue 42", max_steps=3)

    message = agent._generate_max_steps_message(result["conversation"], 3, 3)

    tools_block = message.split("Here's what I ran:\n", 1)[1].split("\n\n", 1)[0]
    assert tools_block.strip() == f"- {_TOOL}: 3x"


# ---------------------------------------------------------------------------
# Cancellation is honoured, not papered over with a summary
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_stop_during_the_closing_reply_cancels_the_turn():
    agent = _make_agent(streaming=True)
    console = MagicMock()
    console.cancelled = threading.Event()
    agent.console = console
    chat = _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3))

    def _closing_stream(*_, **__):
        console.cancelled.set()
        yield SimpleNamespace(text="GitHub's API", is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats=dict(_STATS))

    real_stream = chat.send_messages_stream.side_effect
    chat.send_messages_stream.side_effect = [
        real_stream(),
        real_stream(),
        real_stream(),
        _closing_stream(),
    ]

    result = agent.process_query("summarize issue 42", max_steps=3)

    assert result["status"] == "cancelled"
    assert result["result"] == ""


def test_cancelled_run_skips_the_closing_call(agent):
    calls_seen = []

    def _rate_limited_then_cancel():
        calls_seen.append(1)
        if len(calls_seen) == 3:
            agent._cancel_event = threading.Event()
            agent._cancel_event.set()
        return _RATE_LIMITED

    agent.tool_result = _rate_limited_then_cancel
    chat = _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3))

    result = agent.process_query("summarize issue 42", max_steps=3)

    assert len(_model_calls(agent, chat)) == 3
    assert (
        "I ran out of steps before I could finish — I used 3 of the 3"
        in result["result"]
    )
    assert "cancelled" in result["result"]


# ---------------------------------------------------------------------------
# A requested file was never written: the findings survive, the gap is listed
# ---------------------------------------------------------------------------

_FINDINGS = (
    "Recommendation: the CM5 carrier board. Confirmed subtotal $9,000 for 40 "
    "units; PSU and cooler prices are unverified."
)


def test_step_cap_keeps_the_findings_when_a_requested_file_is_missing(agent):
    """kw-02: the closing reply held the whole recommendation, and the gate
    replaced it with only "I can't confirm this is done"."""
    agent.tool_result = {"status": "success", "body": "datasheet text"}
    _stub_chat(agent, _tool_call(1), _tool_call(2), _tool_call(3), _FINDINGS)

    result = agent.process_query("Save the recommendation as report.md", max_steps=3)

    answer = result["result"]
    assert answer.startswith(_FINDINGS)
    assert "I can't confirm this is done" in answer
    assert "report.md" in answer.split(_FINDINGS, 1)[1]
    assert result["status"] == "incomplete"
    assert result["completion_gaps"]


@pytest.mark.usefixtures("clean_registry")
def test_failed_closing_call_still_carries_the_latest_findings():
    """No closing summary: the model's last write-up goes into the note."""
    agent = _make_agent(streaming=False, model_id=None)
    calls = [
        json.dumps(
            {
                "thought": f"Board {n} checked. {_FINDINGS}",
                "tool": _TOOL,
                "tool_args": {"number": n},
            }
        )
        for n in (1, 2, 3)
    ]
    _stub_chat(agent, *calls, ConnectionError("gateway down"))

    result = agent.process_query("Save the recommendation as report.md", max_steps=3)

    answer = result["result"]
    assert "I ran out of steps" in answer
    assert f"Board 3 checked. {_FINDINGS}" in answer
    assert "gateway down" in answer
    assert "report.md" in answer


def test_latest_findings_prefers_the_newest_prose(agent):
    conversation = [
        {"role": "assistant", "content": {"thought": "old"}},
        {"role": "tool", "name": _TOOL, "content": "x"},
        {"role": "assistant", "content": {"thought": "  ", "tool": _TOOL}},
        {"role": "assistant", "content": {"answer": "newest", "thought": "t"}},
        {"role": "user", "content": "ignored"},
    ]
    assert agent._latest_findings(conversation) == "newest"
    assert agent._latest_findings([]) == ""


# ---------------------------------------------------------------------------
# Budget notices reach the model but not the saved history
# ---------------------------------------------------------------------------


def test_budget_notices_are_sent_but_not_saved(agent):
    agent.tool_result = {"status": "success", "body": "datasheet text"}
    chat = _stub_chat(agent, *[_tool_call(n) for n in range(1, 9)], _SUMMARY)

    result = agent.process_query("summarize issue 42", max_steps=8)

    sent = [
        m["content"]
        for call in _model_calls(agent, chat)
        for m in call.kwargs["messages"]
        if isinstance(m.get("content"), str) and "Step budget" in m["content"]
    ]
    assert sent, "no budget notice reached the model"
    assert all("Step budget" not in str(m) for m in result["model_messages"])


# ---------------------------------------------------------------------------
# The plan-iteration cap: never a forced stop with most of the budget unspent
# ---------------------------------------------------------------------------


def _plan(number: int) -> str:
    return json.dumps(
        {
            "thought": f"plan {number}",
            "goal": "read the claims",
            "plan": [{"tool": _TOOL, "tool_args": {"number": number}}],
            "tool": _TOOL,
            "tool_args": {"number": number},
        }
    )


def _forced_stops(agent, chat) -> list:
    # The last request holds the whole turn's context.
    return [
        m["content"]
        for m in _model_calls(agent, chat)[-1].kwargs["messages"]
        if isinstance(m.get("content"), str)
        and m["content"].startswith("Maximum plan iterations")
    ]


@pytest.mark.usefixtures("clean_registry")
def test_three_short_plans_do_not_force_a_stop_on_a_large_budget():
    """kw-06: three plans in ~15 of 50 steps forced "the audit is not done"."""
    agent = _make_agent(streaming=False, model_id=None)
    agent.tool_result = {"status": "success", "body": "rows"}
    chat = _stub_chat(agent, _plan(1), _plan(2), _plan(3), _plan(4), _answer("Done."))

    result = agent.process_query("audit the claims", max_steps=50)

    assert agent.plan_iterations == 4
    assert _forced_stops(agent, chat) == []
    assert result["result"].startswith("Done.")


@pytest.mark.usefixtures("clean_registry")
def test_the_plan_cap_still_binds_late_and_asks_for_the_deliverables():
    agent = _make_agent(streaming=False, model_id=None)
    agent.tool_result = {"status": "success", "body": "rows"}
    chat = _stub_chat(agent, _plan(1), _plan(2), _plan(3), _answer("Partial."))

    agent.process_query("audit the claims", max_steps=10)

    forced = _forced_stops(agent, chat)
    assert len(forced) == 1
    assert "write them now from what you already have" in forced[0]
    assert "unverified" in forced[0]


@pytest.mark.parametrize(
    "plans, cap, taken, limit, expected",
    [
        (3, 3, 15, 50, False),  # most of the budget left
        (3, 3, 25, 50, True),  # half spent
        (2, 3, 49, 50, False),  # under the plan count
        (9, 0, 49, 50, False),  # 0 = unlimited
        (3, 3, 75, 150, True),
        (3, 3, 74, 150, False),
    ],
)
def test_plan_cap_reached(agent, plans, cap, taken, limit, expected):
    agent.plan_iterations = plans
    agent.max_plan_iterations = cap
    assert agent._plan_cap_reached(taken, limit) is expected
