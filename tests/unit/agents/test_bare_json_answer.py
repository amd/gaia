# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A JSON reply with no protocol field is the model's answer.

A Python agent asked for a JSON object returns the object correctly. The
decoder used to hand that object back as a protocol reply, the loop found no
answer and no tool call, and it asked again until the step limit. Now a decoded
object carrying none of the loop's fields is the answer, wrapped the same way
as plain text.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.base.verification import strip_verification_scope

_DIRECT = '{"notes_01.md": {"owner": "Ada", "budget": 4200}}'
_LENIENT = (
    '{"notes_01.md": {"owner": "Ada", "note": "line one\nline two", ' '"budget": 4200}}'
)
_EXTRACTED = (
    '{"notes_01.md": {"owner": "Ada", "budget": 4200}}\n'
    "The owner is Ada and the budget is 4200."
)
_NEUTRAL = '{"owner": "Ada", "budget": 4200}'
_QUERY = "Return the owner and budget as a JSON object."


class _NoToolAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAIA_AGENT_VERIFY_EDITS", "0")
    with patch("gaia.agents.base.agent.AgentSDK"):
        made = _NoToolAgent(silent_mode=True, skip_lemonade=True)
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


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reply", [_DIRECT, _LENIENT, _EXTRACTED])
def test_a_bare_object_becomes_the_answer(agent, reply):
    parsed = agent._parse_llm_response(reply)

    assert parsed == {"thought": "", "goal": "", "answer": reply}
    assert "thought_only" not in parsed


def test_surrounding_whitespace_is_stripped_from_the_answer(agent):
    parsed = agent._parse_llm_response(f"  {_DIRECT}\n\n")

    assert parsed["answer"] == _DIRECT


def test_an_empty_object_is_the_answer(agent):
    parsed = agent._parse_llm_response("{}")

    assert parsed == {"thought": "", "goal": "", "answer": "{}"}


@pytest.mark.parametrize(
    "reply",
    [
        '{"answer": "42"}',
        '{"plan": []}',
        '{"needs_plan": true}',
        '{"tool_args": {"page": 1}}',
        '{"tool": "read_page", "tool_args": {"page": 1}}',
        '{"thought": "x", "tool_calls": [{"name": "read_page", "tool_args": {"page": 1}}]}',
    ],
)
def test_a_protocol_object_is_not_wrapped(agent, reply):
    parsed = agent._parse_llm_response(reply)

    assert parsed.get("answer") != reply


def test_a_native_tool_call_is_not_wrapped(agent):
    reply = json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_page",
                        "arguments": json.dumps({"page": 1}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )

    parsed = agent._parse_llm_response(reply)

    assert parsed.get("tool_calls")
    assert parsed.get("answer") != reply


# ---------------------------------------------------------------------------
# Through the loop
# ---------------------------------------------------------------------------


def test_a_bare_object_ends_the_turn_as_the_answer(agent):
    chat = _stub_chat(agent, _NEUTRAL)

    result = agent.process_query(_QUERY, max_steps=8)

    assert len(chat.sent) == 1
    assert result["status"] == "success"
    assert result["max_steps_reached"] is False
    assert strip_verification_scope(result["result"]).startswith(_NEUTRAL)
