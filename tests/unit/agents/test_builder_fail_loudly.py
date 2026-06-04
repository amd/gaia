# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for BuilderAgent fail-loudly behaviour (Stage B of issue #1428).

Validates that the builder:
  - Reports success only when create_agent ran AND the file exists
  - Returns an honest failure when the tool result starts with "Error:"
  - Appends a corrective turn (and loops once) when the model fabricates success
    without calling create_agent
  - Never passes a fabricated success to the user
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.builder.agent import BuilderAgent, BuilderAgentConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUCCESS_MARKERS = ("Agent Created", "✅", "File location")


def _make_agent(tmp_path: Path) -> BuilderAgent:
    """Create a BuilderAgent with a temp HOME so no real filesystem is touched."""
    config = BuilderAgentConfig(
        base_url="http://localhost:9999/api/v1",
        model_id="test-model",
        max_steps=5,
        streaming=False,
        silent_mode=True,
    )
    with patch("os.path.expanduser", return_value=str(tmp_path)):
        agent = BuilderAgent(config)
    return agent


def _mock_chat_response(text: str):
    """Return a mock that simulates a chat response with the given text."""
    resp = MagicMock()
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# Stage B tests
# ---------------------------------------------------------------------------


class TestBuilderFailLoudly:
    """Builder must never report success without a real tool call + file on disk."""

    def test_create_agent_error_result_returns_honest_failure(self, tmp_path):
        """If create_agent returns 'Error: ...', the final answer must not claim success."""
        agent = _make_agent(tmp_path)
        agent.console = MagicMock()

        # LLM emits a bare tool call
        tool_call_response = '{"tool": "create_agent", "tool_args": {"name": "Foo"}}'

        def _chat_side_effect(*args, **kwargs):
            return _mock_chat_response(tool_call_response)

        agent.chat = MagicMock()
        agent.chat.send_messages.side_effect = _chat_side_effect

        # create_agent tool returns an error
        with patch.object(
            agent, "_execute_tool", return_value="Error: reserved agent name"
        ):
            result = agent._process_query_impl("create an agent named Foo")

        answer = result["answer"]
        assert not any(
            marker.lower() in answer.lower() for marker in _SUCCESS_MARKERS
        ), f"Answer must not claim success when tool returned an error, got: {answer!r}"
        assert (
            "error" in answer.lower()
            or "fail" in answer.lower()
            or "unable" in answer.lower()
            or "could not" in answer.lower()
        ), f"Answer must indicate failure, got: {answer!r}"

    def test_fabricated_success_text_triggers_corrective_turn(self, tmp_path):
        """When model emits fabricated success markers without calling the tool,
        the builder must NOT return that as the final answer."""
        agent = _make_agent(tmp_path)
        agent.console = MagicMock()

        fabricated = (
            "✅ **Agent Created!**\n"
            "File location: `~/.gaia/agents/fake/agent.py`\n"
            "Your agent is ready!"
        )
        # Second call: a real bare tool call
        real_call = '{"tool": "create_agent", "tool_args": {"name": "Real"}}'
        responses = iter([fabricated, real_call])

        agent.chat = MagicMock()
        agent.chat.send_messages.side_effect = lambda **kwargs: _mock_chat_response(
            next(responses)
        )

        tool_result = "Agent 'Real Agent' created at /tmp/fake/real/agent.py"
        with patch.object(agent, "_execute_tool", return_value=tool_result):
            result = agent._process_query_impl("create an agent")

        # The fabricated text alone must never be the final answer
        answer = result["answer"]
        assert (
            "fake/agent.py" not in answer
        ), f"Hallucinated path must not appear in final answer: {answer!r}"

    def test_fabricated_success_never_returned_when_no_tool_runs(self, tmp_path):
        """When the model ONLY outputs fabricated success and never calls the tool,
        the final answer must indicate failure, not success."""
        agent = _make_agent(tmp_path)
        agent.console = MagicMock()

        fabricated = (
            "✅ **Agent Created!**\n" "File location: `~/.gaia/agents/fake/agent.py`"
        )
        # All responses are fabricated (tool never called)
        agent.chat = MagicMock()
        agent.chat.send_messages.return_value = _mock_chat_response(fabricated)

        with patch.object(agent, "_execute_tool") as mock_execute:
            result = agent._process_query_impl("create an agent named Fake")

        answer = result["answer"]
        mock_execute.assert_not_called()
        # Must not claim the fake path as a success
        assert (
            "fake/agent.py" not in answer
        ), f"Fabricated path must not be in the final answer: {answer!r}"

    def test_real_tool_call_and_file_present_returns_success(self, tmp_path):
        """When create_agent runs and the file exists, the answer must confirm success."""
        agent = _make_agent(tmp_path)
        agent.console = MagicMock()

        tool_call_response = '{"tool": "create_agent", "tool_args": {"name": "Zephyr"}}'
        # First call: tool call; second: the LLM summarizes the result
        summary = "Your Zephyr Agent has been created at ~/.gaia/agents/zephyr/agent.py"
        responses = iter([tool_call_response, summary])

        agent.chat = MagicMock()
        agent.chat.send_messages.side_effect = lambda **kwargs: _mock_chat_response(
            next(responses)
        )

        tool_result = f"Agent 'Zephyr Agent' created at {tmp_path}/zephyr/agent.py"
        with patch.object(agent, "_execute_tool", return_value=tool_result):
            result = agent._process_query_impl("create an agent named Zephyr")

        assert result["answer"] == summary
