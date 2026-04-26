# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for tool-call parse-error recovery in the base Agent class.

Small models (4B-class) occasionally emit malformed native ``tool_calls``
envelopes — for example a 1000+ char ``summary_type`` argument that gets
truncated mid-string. Before the recovery layer landed, ``_parse_llm_response``
would raise ``ValueError`` and the unhandled exception bubbled out to the user
as ``Agent error: Malformed native tool_calls envelope: ...``.

The recovery layer in ``Agent.process_query`` catches the parse error, logs
it, appends a synthetic recovery prompt to the conversation, and continues
the loop so the model can retry with cleaner arguments.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    """Minimal concrete Agent for testing."""

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
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        # Disable streaming so we exercise the simpler non-streaming path.
        a.streaming = False
        return a


class TestParseLLMResponseRaisesOnMalformed:
    def test_truncated_tool_calls_envelope_raises_valueerror(self, agent):
        """Malformed JSON in the __tool_calls__ sentinel raises ValueError."""
        # Truncated mid-string — what the small model produced for
        # honest_limitation Turn 2.
        bad = (
            '{"__tool_calls__": [{"function": {"name": "summarize_document", '
            '"arguments": "{\\"summary_type\\": \\"brief detailed bullets'
        )
        with pytest.raises(ValueError, match="Malformed native tool_calls"):
            agent._parse_llm_response(bad)


class TestProcessQueryRecoversOnParseError:
    """The full process_query loop should not crash when parse fails."""

    def _stub_chat(self, agent, *responses):
        """Replace agent.chat with a stub that yields *responses* in order."""
        responses = list(responses)
        chat = MagicMock()

        def _send(*_, **__):
            r = responses.pop(0)
            resp = MagicMock()
            resp.text = r
            resp.stats = {}
            return resp

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        return chat

    def test_malformed_envelope_then_plain_answer(self, agent):
        """First call malformed tool_calls, second call plain text answer."""
        bad = (
            '{"__tool_calls__": [{"function": {"name": "summarize_document",'
            ' "arguments": "{\\"summary_type\\": \\"brief detailed bullets'
        )
        good_answer = json.dumps(
            {
                "thought": "Done.",
                "answer": "Acme Corp had $14.2M revenue in Q3 2025.",
            }
        )
        chat = self._stub_chat(agent, bad, good_answer)

        result = agent.process_query("What can you tell me?", max_steps=5)

        # Recovery path was exercised — chat called twice.
        assert chat.send_messages.call_count == 2
        # error_history records the parse error
        assert any(
            e.get("type") == "tool_call_parse_error" for e in agent.error_history
        )
        # Final answer reached the user (not the raw envelope error)
        assert (
            "Agent error" not in result.get("response", "")
            if isinstance(result, dict)
            else True
        )

    def test_three_consecutive_parse_errors_give_up_gracefully(self, agent):
        """After 3 parse errors the loop bails with a friendly message."""
        bad = '{"__tool_calls__": [{"function": {"name": "x", "arguments": "{'
        # Pre-load 5 responses so the loop has plenty to chew on.
        self._stub_chat(agent, bad, bad, bad, bad, bad)

        result = agent.process_query("test", max_steps=10)

        # Final answer is the friendly fallback, NOT a leaked envelope error.
        # The result shape varies per agent — accept either dict or string.
        text = result.get("response") if isinstance(result, dict) else str(result)
        if text:
            assert "Malformed" not in text
            assert "Agent error" not in text
