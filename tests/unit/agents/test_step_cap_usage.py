# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn that ran out of steps still reports the tokens it spent.

The step-limit answer printed its output count but not its input count, and a
turn that returned its answer instead of printing it carried no counts to the
front end at all — the TUI cost meter read every such turn as unmeasured.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_TOOL = "read_page_for_step_cap_usage_test"
_STATS = {"prompt_tokens": 1000, "completion_tokens": 40, "cached_tokens": 600}


class _StepAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def read_page_for_step_cap_usage_test(page: int) -> dict:
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


@pytest.mark.usefixtures("clean_registry")
def test_the_step_limit_answer_reports_input_and_cached_tokens():
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _StepAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = False
    replies = [_call(1), _call(2), "Pages 1 and 2 say the same thing."]
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(
        side_effect=lambda *_, **__: SimpleNamespace(
            text=replies.pop(0), stats=dict(_STATS)
        )
    )
    agent.chat = chat
    printed = MagicMock(wraps=agent.console.print_final_answer)
    agent.console.print_final_answer = printed

    result = agent.process_query("read the pages", max_steps=2)

    assert result["max_steps_reached"] is True
    kwargs = printed.call_args.kwargs
    assert kwargs["input_tokens"] == 3000
    assert kwargs["cached_tokens"] == 1800
    assert kwargs["total_tokens"] == 120
    assert result["input_tokens"] == 3000
    assert result["cached_tokens"] == 1800
