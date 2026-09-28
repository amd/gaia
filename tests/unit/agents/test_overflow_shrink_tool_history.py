# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for context-overflow shrinking (#3578).

``_shrink_messages_for_overflow`` used to rebuild a verbose assistant turn
from scratch, keeping only ``role`` and ``content``. That silently dropped
``tool_calls``, orphaning the ``role="tool"`` messages that follow it — so a
provider that encodes tool history natively (Anthropic) fell back to
flattening the whole group into prose.
"""

from __future__ import annotations

from gaia.agents.base.agent import Agent


class _TestAgent(Agent):
    """Minimal concrete Agent: no LLM/network access, no tools."""

    def _get_system_prompt(self):
        return "test"

    def _register_tools(self):
        pass


def make_agent() -> _TestAgent:
    return _TestAgent(silent_mode=True, skip_lemonade=True)


def _verbose_assistant_turn() -> dict:
    return {
        "role": "assistant",
        "content": "planning preamble " * 100,  # > 800 chars
        "tool_calls": [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q": "gaia"}'},
            }
        ],
    }


def test_verbose_assistant_turn_keeps_its_tool_calls():
    messages = [
        {"role": "user", "content": "find the spec"},
        _verbose_assistant_turn(),
        {
            "role": "tool",
            "name": "search",
            "tool_call_id": "call_abc123",
            "content": "x" * 5000,
        },
    ]

    shrunk = make_agent()._shrink_messages_for_overflow(messages)

    assistant = shrunk[1]
    assert assistant["content"].endswith("... (truncated)")
    assert len(assistant["content"]) < len(messages[1]["content"])
    assert assistant["tool_calls"] == messages[1]["tool_calls"]


def test_every_tool_message_still_has_a_matching_call_id():
    """No ``role="tool"`` message may be left orphaned after shrinking."""
    messages = [
        {"role": "user", "content": "find the spec"},
        _verbose_assistant_turn(),
        {
            "role": "tool",
            "name": "search",
            "tool_call_id": "call_abc123",
            "content": "y" * 5000,
        },
        _verbose_assistant_turn() | {"tool_calls": [{"id": "call_def456"}]},
        {
            "role": "tool",
            "name": "search",
            "tool_call_id": "call_def456",
            "content": "z" * 5000,
        },
    ]

    shrunk = make_agent()._shrink_messages_for_overflow(messages)

    announced = {
        call["id"]
        for m in shrunk
        if m.get("role") == "assistant"
        for call in m.get("tool_calls") or []
    }
    results = [m["tool_call_id"] for m in shrunk if m.get("role") == "tool"]
    assert results == ["call_abc123", "call_def456"]
    assert set(results) <= announced


def test_shrinking_does_not_mutate_the_caller_s_messages():
    messages = [
        {"role": "user", "content": "find the spec"},
        _verbose_assistant_turn(),
    ]
    original = messages[1]["content"]

    make_agent()._shrink_messages_for_overflow(messages)

    assert messages[1]["content"] == original
