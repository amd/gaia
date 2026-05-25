# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for AgentSDK tool-message handling.

Regression coverage for the "continue" sentinel bug:
  When a tool call was the last message, _prepare_messages_for_llm injected
  {"role": "user", "content": "continue"} as a sentinel.  Because tool messages
  are converted to "assistant" role inside send_messages, this sentinel became
  the final user message the LLM saw — causing responses like
  "What do you want me to continue?" instead of answering the actual query.

Fix: remove the sentinel; convert tool messages to "user" role so the LLM
     receives the tool result as a proper user turn.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sdk():
    """Return an AgentSDK with a mocked LLM client."""
    from gaia.chat.sdk import AgentConfig, AgentSDK

    with patch("gaia.chat.sdk.create_client") as mock_create:
        mock_client = MagicMock()
        mock_client.chat.return_value = "Nice, sounds fun!"
        mock_create.return_value = mock_client

        config = AgentConfig(model="test-model")
        agent = AgentSDK(config)
        agent._mock_client = mock_client
        yield agent


class TestPrepareMessagesForLLM:
    """_prepare_messages_for_llm should not inject a 'continue' sentinel."""

    def test_empty_messages(self, sdk):
        assert sdk._prepare_messages_for_llm([]) == []

    def test_user_message_unchanged(self, sdk):
        msgs = [{"role": "user", "content": "hi"}]
        assert sdk._prepare_messages_for_llm(msgs) == msgs

    def test_tool_message_last_no_sentinel(self, sdk):
        """Old code appended continue; new code must NOT."""
        msgs = [
            {"role": "user", "content": "I like playing games!"},
            {"role": "assistant", "content": '{"tool":"remember","tool_args":{}}'},
            {
                "role": "tool",
                "name": "remember",
                "tool_call_id": "abc",
                "content": [{"type": "text", "text": '{"status":"ok"}'}],
            },
        ]
        result = sdk._prepare_messages_for_llm(msgs)
        # Must NOT have a trailing 'continue' user message
        assert result[-1]["role"] == "tool", "Last message should still be tool role"
        assert not any(
            m.get("content") == "continue" for m in result
        ), "'continue' sentinel must not be injected"

    def test_returns_copy(self, sdk):
        msgs = [{"role": "user", "content": "hello"}]
        result = sdk._prepare_messages_for_llm(msgs)
        result.append({"role": "user", "content": "extra"})
        assert len(msgs) == 1, "_prepare_messages_for_llm should return a copy"


class TestToolMessageConversion:
    """send_messages must convert role='tool' to role='user', not 'assistant'."""

    def _capture_structured(self, sdk):
        """Return the 'structured' list actually sent to llm_client.chat."""
        captured = []

        def fake_chat(messages, **kwargs):
            captured.extend(messages)
            return "ok"

        sdk._mock_client.chat.side_effect = fake_chat
        return captured

    def test_tool_becomes_user(self, sdk):
        captured = self._capture_structured(sdk)
        msgs = [
            {"role": "user", "content": "I like playing games!"},
            {"role": "assistant", "content": '{"tool":"remember","tool_args":{}}'},
            {
                "role": "tool",
                "name": "remember",
                "tool_call_id": "abc",
                "content": [{"type": "text", "text": '{"status":"ok"}'}],
            },
        ]
        sdk.send_messages(msgs)

        tool_msgs = [m for m in captured if "[Tool result:" in m.get("content", "")]
        assert tool_msgs, "Tool result should appear in structured messages"
        assert all(
            m["role"] == "user" for m in tool_msgs
        ), "Tool results must be role='user', not 'assistant'"

    def test_no_continue_in_structured(self, sdk):
        """The literal string 'continue' must not appear as a standalone user message."""
        captured = self._capture_structured(sdk)
        msgs = [
            {"role": "user", "content": "I like playing games!"},
            {
                "role": "tool",
                "name": "remember",
                "tool_call_id": "abc",
                "content": [{"type": "text", "text": '{"status":"ok"}'}],
            },
        ]
        sdk.send_messages(msgs)

        assert not any(
            m.get("role") == "user" and m.get("content") == "continue" for m in captured
        ), "Bare 'continue' sentinel must not be sent to the LLM"

    def test_original_user_message_preserved(self, sdk):
        """The user's real message must still be visible to the LLM."""
        captured = self._capture_structured(sdk)
        msgs = [
            {"role": "user", "content": "I like playing games!"},
            {
                "role": "tool",
                "name": "remember",
                "tool_call_id": "abc",
                "content": [{"type": "text", "text": '{"status":"ok"}'}],
            },
        ]
        sdk.send_messages(msgs)

        user_msgs = [m for m in captured if m.get("role") == "user"]
        contents = [m["content"] for m in user_msgs]
        assert any(
            "I like playing games!" in c for c in contents
        ), "Original user message must be present in the structured messages"
