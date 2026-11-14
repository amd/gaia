# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reply that holds only a ``thought`` is not an answer.

In the in-text tool format a model sometimes replies with nothing but its
reasoning: no tool call, no plan, no answer. After a bare one the loop asked
again with nothing new, up to the step limit, and one wrapped in a code fence
was taken as the final answer, so
the turn ended "done" with nothing delivered. Now the loop asks once for a
tool call or an answer, and a second thought-only reply in a row ends the turn
as unfinished.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _THOUGHT_ONLY_GAP,
    _THOUGHT_ONLY_PROMPT,
    _THOUGHT_ONLY_STOP,
    Agent,
    _thought_only,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool

_LOOKUP = "lookup_for_thought_only_test"


class _Agent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def lookup_for_thought_only_test(key: str) -> dict:
            """Look a setting up."""
            return {"status": "success", "key": key, "value": f"{key}-value"}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        made = _Agent(silent_mode=True, skip_lemonade=True)
    made.streaming = False
    yield made
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _stub_chat(agent, *replies):
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.sent = []

    def _send(*_, **kwargs):
        chat.sent.append([dict(m) for m in kwargs.get("messages") or []])
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        return SimpleNamespace(text=queue.pop(0), stats={})

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return chat


def _thought(text="I should look the setting up.", **extra) -> str:
    return json.dumps({"thought": text, **extra})


def _call(key="alpha") -> str:
    return json.dumps({"thought": "", "tool": _LOOKUP, "tool_args": {"key": key}})


def _answer(text="The alpha setting is alpha-value.") -> str:
    return json.dumps({"thought": "Done.", "goal": "", "answer": text})


def _reprompts(chat) -> int:
    return sum(
        1
        for m in chat.sent[-1]
        if m.get("role") == "user" and m.get("content") == _THOUGHT_ONLY_PROMPT
    )


# ---------------------------------------------------------------------------
# What counts as thought-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        {"thought": "x"},
        {"thought": ""},
        {"thought": "x", "goal": "write the report"},
        {"thought": "x", "plan": []},
        {"thought": "x", "plan": None},
        # Answer-looking text inside the thought is still only a thought.
        {"thought": 'The answer is 42. {"answer": "42"}'},
    ],
)
def test_thought_only_shapes(reply):
    assert _thought_only(reply)


@pytest.mark.parametrize(
    "reply",
    [
        {"thought": "x", "answer": "42"},
        {"thought": "x", "answer": ""},
        {"thought": "x", "tool": _LOOKUP, "tool_args": {}},
        {"thought": "x", "tool_calls": [{"name": _LOOKUP}]},
        {"thought": "x", "plan": [{"tool": _LOOKUP, "tool_args": {}}]},
        {"thought": "x", "plan": "look it up"},
        {"answer": "no thought key"},
        "a plain string",
        ["thought"],
        None,
    ],
)
def test_replies_that_act_or_answer_are_not_thought_only(reply):
    assert not _thought_only(reply)


@pytest.mark.parametrize(
    "response",
    [
        '{"thought": "Let me think."}',
        '{"thought": "Let me think.", "plan": []}',
        '```json\n{"thought": "I am done, the answer is 42"}\n```',
        '```\n{"thought": "fenced without a language"}\n```',
        # Cut short: the regex fallback still sees only a thought.
        '{"thought": "Let me check the file", "goal": "x"',
    ],
)
def test_the_parser_marks_a_thought_only_reply(agent, response):
    parsed = agent._parse_llm_response(response)
    assert parsed.get("thought_only") is True
    assert "answer" not in parsed


@pytest.mark.parametrize(
    "response,answer",
    [
        ('{"thought": "Done.", "answer": "42"}', "42"),
        ("The setting is 42.", "The setting is 42."),
        (
            'Reply in this shape: {"thought": "...", "answer": "..."}',
            'Reply in this shape: {"thought": "...", "answer": "..."}',
        ),
    ],
)
def test_the_parser_leaves_a_real_answer_alone(agent, response, answer):
    parsed = agent._parse_llm_response(response)
    assert "thought_only" not in parsed
    assert parsed["answer"] == answer


def test_a_fenced_block_with_more_text_around_it_is_not_thought_only(agent):
    response = 'Here is my reasoning:\n```json\n{"thought": "x"}\n```\nThe value is 7.'
    assert "thought_only" not in agent._parse_llm_response(response)


# ---------------------------------------------------------------------------
# Through the loop
# ---------------------------------------------------------------------------


def test_one_thought_only_reply_is_asked_once_for_a_call_or_answer(agent):
    chat = _stub_chat(agent, _thought(), _call(), _answer())

    result = agent.process_query("What is the alpha setting?")

    assert result["status"] == "success"
    assert "alpha-value" in result["result"]
    assert len(chat.sent) == 3
    # The prompt follows the thought it answers, and nothing else was added.
    second = chat.sent[1]
    assert second[-2] == {"role": "assistant", "content": _thought()}
    assert second[-1] == {"role": "user", "content": _THOUGHT_ONLY_PROMPT}
    assert _reprompts(chat) == 1


def test_a_second_in_a_row_ends_the_turn_unfinished(agent):
    chat = _stub_chat(agent, _thought(), _thought("Still thinking."))

    result = agent.process_query("What is the alpha setting?")

    assert len(chat.sent) == 2, "the loop kept asking"
    assert result["status"] == "incomplete"
    assert result["result"].startswith(_THOUGHT_ONLY_STOP)
    assert _THOUGHT_ONLY_GAP in result["completion_gaps"]


def test_a_fenced_thought_is_never_the_final_answer(agent):
    fenced = '```json\n{"thought": "The answer is 42, so I am done."}\n```'
    chat = _stub_chat(agent, fenced, fenced)

    result = agent.process_query("What is the alpha setting?")

    assert len(chat.sent) == 2
    assert result["status"] == "incomplete"
    assert "42" not in result["result"]


def test_thought_with_an_empty_plan_is_asked_once_too(agent):
    chat = _stub_chat(agent, _thought(plan=[]), _answer())

    result = agent.process_query("What is the alpha setting?")

    assert result["status"] == "success"
    assert _reprompts(chat) == 1


def test_the_count_resets_after_a_reply_that_acts(agent):
    """Thought-only, call, thought-only, answer: two prompts, no early stop."""
    chat = _stub_chat(agent, _thought(), _call(), _thought(), _answer())

    result = agent.process_query("What is the alpha setting?")

    assert result["status"] == "success"
    assert len(chat.sent) == 4
    assert _reprompts(chat) == 2


def test_a_genuine_answer_with_a_thought_key_is_never_prompted(agent):
    chat = _stub_chat(agent, _call(), _answer())

    result = agent.process_query("What is the alpha setting?")

    assert result["status"] == "success"
    assert len(chat.sent) == 2
    assert _reprompts(chat) == 0


def test_on_the_last_step_the_step_cap_closing_call_answers(agent):
    """No room to ask: the step-cap summary runs, not the thought-only stop."""
    closing = "I looked up alpha; it is alpha-value."
    chat = _stub_chat(agent, _call(), _thought(), _answer(closing))

    result = agent.process_query("What is the alpha setting?", max_steps=2)

    assert _reprompts(chat) == 0
    assert len(chat.sent) == 3
    assert result["status"] == "incomplete"
    assert result["result"].startswith(closing)
    assert _THOUGHT_ONLY_STOP not in result["result"]


def test_one_step_before_the_last_still_gets_the_prompt(agent):
    chat = _stub_chat(agent, _call(), _call("beta"), _thought(), _answer())

    result = agent.process_query("What is the alpha setting?", max_steps=4)

    assert _reprompts(chat) == 1
    assert result["status"] == "success"
    assert _THOUGHT_ONLY_STOP not in result["result"]


def test_a_native_tool_call_reply_is_never_thought_only(agent):
    native = json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": _LOOKUP,
                        "arguments": json.dumps({"key": "alpha"}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )
    chat = _stub_chat(agent, native, _answer())

    result = agent.process_query("What is the alpha setting?")

    assert result["status"] == "success"
    assert _reprompts(chat) == 0
