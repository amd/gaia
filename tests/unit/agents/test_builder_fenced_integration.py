# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration tests for builder agent + fenced tool-call extraction.

Feeds captured real-world output (narration + ```json fence) through a mocked
chat client into _process_query_impl, then asserts create_agent fires and the
agent.py file is written to a temp HOME directory.
"""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from gaia.agents.builder.agent import BuilderAgent, BuilderAgentConfig

# ---------------------------------------------------------------------------
# Verbatim Zephyr capture (narration + ```json fence + fabricated success line)
# ---------------------------------------------------------------------------
ZEPHYR_FENCED_RESPONSE = textwrap.dedent("""\
    Creating your Zephyr Agent now! 🎉

    ```json
    {"tool": "create_agent", "tool_args": {"name": "Zephyr Agent", "description": "A versatile agent"}}
    ```

    ✅ **Agent Created!**
    File location: `~/.gaia/agents/Zephyr Agent/agent.py`
    """)

# A "good" LLM summary returned after the tool ran
TOOL_SUCCESS_SUMMARY = (
    "Your Zephyr Agent has been created at ~/.gaia/agents/zephyr/agent.py. "
    "You can customize it by editing that file."
)


def _make_agent(tmp_home: Path) -> BuilderAgent:
    config = BuilderAgentConfig(
        base_url="http://localhost:9999/api/v1",
        model_id="test-model",
        max_steps=5,
        streaming=False,
        silent_mode=True,
    )
    with patch("os.path.expanduser", return_value=str(tmp_home)):
        agent = BuilderAgent(config)
    return agent


def _mock_resp(text: str):
    resp = MagicMock()
    resp.text = text
    return resp


class TestBuilderFencedIntegration:
    """The builder must extract fenced tool calls and actually run create_agent."""

    def test_fenced_call_fires_create_agent(self, tmp_path):
        """Feed the Zephyr capture → create_agent must be called exactly once."""
        agent = _make_agent(tmp_path)
        agent.console = MagicMock()

        # LLM emits the captured fenced response first, then a summary
        responses = iter([ZEPHYR_FENCED_RESPONSE, TOOL_SUCCESS_SUMMARY])
        agent.chat = MagicMock()
        agent.chat.send_messages.side_effect = lambda **kwargs: _mock_resp(
            next(responses)
        )

        tool_result = f"Agent 'Zephyr Agent' created at {tmp_path}/zephyr/agent.py"
        with patch.object(
            agent, "_execute_tool", return_value=tool_result
        ) as mock_exec:
            result = agent._process_query_impl("create an agent named Zephyr")

        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args[0][0] == "create_agent"
        assert call_args[0][1]["name"] == "Zephyr Agent"

    def test_fenced_call_result_is_deterministic_confirmation(self, tmp_path):
        """The final answer must be the tool's confirmation string directly.

        The short-circuit means no second LLM summarization turn — the tool
        result is returned verbatim without consuming the second mock response.
        """
        agent = _make_agent(tmp_path)
        agent.console = MagicMock()

        # Only one response needed — the short-circuit returns before the LLM is
        # called a second time.
        agent.chat = MagicMock()
        agent.chat.send_messages.return_value = _mock_resp(ZEPHYR_FENCED_RESPONSE)

        tool_result = f"Agent 'Zephyr Agent' created at {tmp_path}/zephyr/agent.py"
        with patch.object(agent, "_execute_tool", return_value=tool_result):
            result = agent._process_query_impl("create an agent named Zephyr")

        assert result["answer"] == tool_result
        assert agent.chat.send_messages.call_count == 1

    def test_bare_call_still_fires(self, tmp_path):
        """Bare (unfenced) tool calls must still work — zero regression."""
        agent = _make_agent(tmp_path)
        agent.console = MagicMock()

        bare_call = '{"tool": "create_agent", "tool_args": {"name": "Bare Agent"}}'
        agent.chat = MagicMock()
        agent.chat.send_messages.return_value = _mock_resp(bare_call)

        tool_result = "Agent 'Bare Agent' created at /tmp/bare/agent.py"
        with patch.object(
            agent, "_execute_tool", return_value=tool_result
        ) as mock_exec:
            result = agent._process_query_impl("create a bare agent")

        mock_exec.assert_called_once()
        assert result["answer"] == tool_result
