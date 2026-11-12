# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn that gives up says so, and names what it changed before it did.

* A reply stream cut off on every retry fails the turn and prints its notice,
  instead of leaving the cut-off fragment as the turn's last word.
* A reply the output-token limit keeps cutting off prints its notice too.
* Files a native tool call edited are named when the turn then fails on an
  empty reply, as they already were for the text-JSON and plan paths.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import _CUT_OFF_FAILURE_ANSWER, Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.chat.sdk import AgentResponse
from gaia.llm.providers.lemonade import LemonadeStreamCutOffError


class _EditAgent(Agent):
    def _get_system_prompt(self):
        return "Use the tools."

    def _register_tools(self):
        @tool
        def edit_file(file_path: str, old_content: str, new_content: str) -> dict:
            """Replace text in a file."""
            path = Path(file_path)
            path.write_text(path.read_text().replace(old_content, new_content))
            return {
                "status": "success",
                "operation": "edit_file",
                "file_path": str(path),
                "backup_path": str(path) + ".bak",
            }


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAIA_AGENT_VERIFY_EDITS", "0")
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _EditAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _printed_answers(agent):
    return [c.args[0] for c in agent.console.print_final_answer.call_args_list]


def _stream_cut_off_every_time(agent):
    calls = []

    def stream(*a, **kw):
        calls.append(1)
        yield AgentResponse(text="Let me edit the file", is_complete=False)
        raise LemonadeStreamCutOffError()

    agent.streaming = True
    agent.chat = MagicMock()
    agent.chat.send_messages_stream.side_effect = stream
    return calls


def test_a_stream_cut_off_until_give_up_fails_the_turn_and_prints_why(agent):
    calls = _stream_cut_off_every_time(agent)

    result = agent.process_query("Edit a.txt.")

    assert len(calls) == 3
    assert result["model_failed"] is True
    notice = LemonadeStreamCutOffError().user_message
    printed = _printed_answers(agent)
    assert len(printed) == 1
    assert notice in printed[0]
    assert "Let me edit the file" not in printed[0]
    agent.console.mark_turn_failed.assert_called_once()


def test_an_output_limit_cut_off_until_give_up_prints_why(agent):
    def send(*a, **kw):
        return MagicMock(text="Half a thought", stats={}, finish_reason="length")

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send

    result = agent.process_query("Explain it.", max_steps=1)

    assert result["model_failed"] is True
    printed = _printed_answers(agent)
    assert len(printed) == 1
    assert _CUT_OFF_FAILURE_ANSWER in printed[0]


def _native_call(name, **args):
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ]
        }
    )


def test_a_native_edit_is_named_when_the_turn_then_comes_back_empty(agent, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("old line\n")
    replies = [
        _native_call(
            "edit_file",
            file_path=str(target),
            old_content="old",
            new_content="new",
        ),
        "",
    ]

    def send(*a, **kw):
        assert replies, "unexpected model call"
        return MagicMock(text=replies.pop(0), stats={}, finish_reason="stop")

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send

    # Two steps, so the empty reply lands on the last one and is not re-asked.
    result = agent.process_query("Change old to new in notes.txt.", max_steps=2)

    assert target.read_text() == "new line\n"
    assert "Files modified before the turn failed:" in result["result"]
    assert f"- {target} (backup: {target}.bak)" in result["result"]
