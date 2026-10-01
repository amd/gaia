# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool call cut off at the output cap is retried with shorter arguments.

Asked for a one-line docstring, local Qwen3-30B put the whole file in
edit_file's new_content, hit the 8192-token cap after ~7 minutes, and — told
only to "emit exactly ONE tool call ... use the documented enum values" — sent
the same oversized edit twice more.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, ToolCallTruncated
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


class EditAgent(Agent):
    def _get_system_prompt(self):
        return "Edit files."

    def _register_tools(self):
        @tool
        def edit_file(file_path: str, old_content: str, new_content: str) -> dict:
            """Replace text in a file."""
            return {"status": "success", "file_path": file_path}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = EditAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _truncated_call():
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {
                        "name": "edit_file",
                        "arguments": '{"file_path": "a.py", "new_content": "class A:\n',
                    },
                }
            ],
            "finish_reason": "length",
        }
    )


def test_the_recovery_turn_asks_for_shorter_arguments(agent):
    sent = []
    replies = [_truncated_call(), json.dumps({"answer": "Done."})]

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        return MagicMock(text=replies.pop(0), stats={})

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    agent.process_query("Add a docstring to class A in a.py")

    recovery = sent[1][-1]["content"]
    assert "cut off" in recovery and "nothing ran" in recovery
    assert "only the few lines" in recovery
    assert "enum values" not in recovery


def test_the_truncation_is_its_own_error_type():
    err = ToolCallTruncated("Qwen3-30B", 8192)
    assert isinstance(err, ValueError) and err.cap == 8192
    assert "8192 max" in str(err)
