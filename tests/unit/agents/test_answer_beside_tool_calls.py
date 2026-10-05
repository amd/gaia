# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An answer the model sends alongside its last tool calls reaches the user.

A model that answers and then cleans up in the same reply ("here is the table"
+ ``drop_table``) closes the turn with a short wrap-up ("Scratch table cleaned
up."). Only that wrap-up used to become the final answer, so the user never
saw the answer itself. Planning narration sent with a call ("Let me read the
file first.") is not an answer and must stay out.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_ANSWER = (
    "**Gadget Pro — $2,700** in the North.\n\n"
    "| Product | Revenue | Units |\n|---|---|---|\n"
    "| Gadget Pro | $2,700 | 9 |\n| Widget | $1,200 | 40 |\n\n"
    "Worth noting the inversion: Widget sells more units but less revenue."
)
_WRAP_UP = "Scratch table cleaned up."


class _DataAgent(Agent):
    """Records every answer the completion checks are handed."""

    def __init__(self, *args, **kwargs):
        self.finalized = []
        super().__init__(*args, **kwargs)

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def query_beside_test(sql: str) -> str:
            """Run a query."""
            del sql
            return "Gadget Pro|2700|9\nWidget|1200|40"

        @tool
        def drop_beside_test(table_name: str) -> str:
            """Drop a table."""
            return f"Table '{table_name}' dropped."

        @tool
        def read_beside_test(path: str) -> str:
            """Read a file."""
            del path
            return "hello"

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()

    def finalize_answer(self, answer, _conversation):
        self.finalized.append(answer)
        return answer


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture(params=[False, True], ids=["non-streaming", "streaming"])
def agent(request, clean_registry):  # pylint: disable=unused-argument
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _DataAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    built.streaming = request.param
    built.verification_scope_statement = lambda: None
    return built


def _script(agent, *replies):
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})

    def _next():
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        return queue.pop(0)

    def _send(*_, **__):
        return SimpleNamespace(text=_next(), stats={})

    def _stream(*_, **__):
        text = _next()
        if text.startswith('{"__tool_calls__"'):
            yield SimpleNamespace(text=text, is_complete=True, stats={})
            return
        yield SimpleNamespace(text=text, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    chat.send_messages = MagicMock(side_effect=_send)
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat


def _calls(*calls, content=None) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for i, (name, args) in enumerate(calls)
            ],
            "finish_reason": "tool_calls",
            "content": content,
        }
    )


_QUERY = ("query_beside_test", {"sql": "SELECT product, revenue FROM sales"})
_DROP = ("drop_beside_test", {"table_name": "sales"})
_READ = ("read_beside_test", {"path": "notes.txt"})


def test_answer_sent_with_cleanup_call_reaches_the_user(agent):
    _script(agent, _calls(_QUERY), _calls(_DROP, content=_ANSWER), _WRAP_UP)

    result = agent.process_query("which product earned most in the North?")

    assert result["result"].startswith(_ANSWER)
    assert result["result"].rstrip().endswith(_WRAP_UP)
    # The completion checks saw the same text the user did.
    assert agent.finalized == [f"{_ANSWER}\n\n{_WRAP_UP}"]


def test_trailing_next_step_narration_is_left_out(agent):
    beside = f"{_ANSWER}\n\nLet me clean up the scratch table."
    _script(agent, _calls(_QUERY), _calls(_DROP, content=beside), _WRAP_UP)

    result = agent.process_query("which product earned most in the North?")

    assert result["result"].startswith(_ANSWER)
    assert "Let me clean up" not in result["result"]


@pytest.mark.parametrize(
    "narration", ["Let me read the file first.", "I'll read the file first."]
)
def test_planning_narration_beside_a_call_is_not_promoted(agent, narration):
    _script(agent, _calls(_READ, content=narration), "It says hello.")

    result = agent.process_query("what does notes.txt say?")

    assert result["result"].strip() == "It says hello."


def test_restated_answer_is_not_duplicated(agent):
    final = f"{_ANSWER}\n\nThe scratch table has been dropped."
    _script(agent, _calls(_QUERY), _calls(_DROP, content=_ANSWER), final)

    result = agent.process_query("which product earned most in the North?")

    assert result["result"].count("Gadget Pro — $2,700") == 1


def test_text_before_a_later_tool_call_is_not_promoted(agent):
    _script(
        agent,
        _calls(_QUERY, content=_ANSWER),
        _calls(_READ),
        "It says hello.",
    )

    result = agent.process_query("which product earned most in the North?")

    assert result["result"].strip() == "It says hello."


def test_reasoning_beside_a_call_never_reaches_the_answer(agent):
    beside = f"<think>secret scratch work</think>{_ANSWER}"
    _script(agent, _calls(_QUERY), _calls(_DROP, content=beside), _WRAP_UP)

    result = agent.process_query("which product earned most in the North?")

    assert "secret scratch work" not in result["result"]
    assert result["result"].startswith(_ANSWER)
