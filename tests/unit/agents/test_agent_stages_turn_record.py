# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent seals a turn's record as it prints the answer.

The daemon's ``final`` event reads the record from the console instead of
waiting for the loop's tail (memory store, trace write), so a tail that outlives
the wait can no longer take what later turns replay down with it.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_TOOL = "read_page_for_staging_test"


class _StagingAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def read_page_for_staging_test(page: int) -> dict:
            """Read one page."""
            return {"status": "success", "text": f"page {page}"}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _call(page: int) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{page}",
                    "type": "function",
                    "function": {
                        "name": _TOOL,
                        "arguments": json.dumps({"page": page}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _agent(replies):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _StagingAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = False
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(
        side_effect=lambda *_, **__: SimpleNamespace(text=replies.pop(0), stats={})
    )
    agent.chat = chat
    return agent


@pytest.mark.usefixtures("clean_registry")
def test_the_record_is_sealed_before_the_answer_prints():
    agent = _agent([_call(1), "Page 1 says hello."])
    order = []
    staged = {}

    def stage(answer, messages):
        order.append("seal")
        staged.update(answer=answer, messages=messages)
        return messages

    def answer(text, **kwargs):
        order.append("print")

    agent.console.stage_final_model_messages = stage
    agent.console.print_final_answer = answer

    result = agent.process_query("read the page")

    assert order == ["seal", "print"]
    assert staged["answer"] == "Page 1 says hello."
    assert staged["messages"][-1] == {
        "role": "assistant",
        "content": "Page 1 says hello.",
    }
    # The sealed list is the result's record: one copy, not two.
    assert result["model_messages"] is staged["messages"]


@pytest.mark.usefixtures("clean_registry")
def test_a_console_without_the_hook_still_records_the_turn():
    agent = _agent([_call(1), "Page 1 says hello."])

    result = agent.process_query("read the page")

    assert result["model_messages"][-1] == {
        "role": "assistant",
        "content": "Page 1 says hello.",
    }
