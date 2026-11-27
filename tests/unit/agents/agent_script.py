# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Scripted chat scaffolding shared by the agent tests.

The tests drive the real agent loop with a fake model that returns a fixed list
of answers, so the assertions can be about the loop's behavior rather than the
model's.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool


class LookingAgent(Agent):
    """An agent with one tool, so a test can script a look before an answer."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def look_first_test_reader(file_path: str) -> dict:
            """Read a file."""
            return {"status": "success", "file_path": file_path, "content": "x = 1"}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


def script(agent_, *responses):
    """Queue *responses* as the agent's model output and record each request."""
    queue = list(responses)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    agent_.chat = MagicMock()
    agent_.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def answer(text):
    """A model response that gives *text* as the final answer."""
    return json.dumps({"thought": "", "answer": text})
