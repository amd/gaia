# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reply that carries both tool calls and a plan runs each step once."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_PATH = "C:/scratch/report.py"
_SCRIPT = "print('report')\n"
_WRITE = ("write_native_plan_test", {"file_path": _PATH, "content": _SCRIPT})
_RUN = ("run_native_plan_test", {"file_path": _PATH})
_READ = ("read_native_plan_test", {"file_path": "notes.txt"})


class _WriteRunAgent(Agent):
    def __init__(self, *args, **kwargs):
        self.calls = []
        super().__init__(*args, **kwargs)

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        @tool
        def write_native_plan_test(file_path: str, content: str) -> dict:
            """Write a file."""
            agent.calls.append(("write", file_path, content))
            return {"status": "success", "file_path": file_path}

        @tool
        def run_native_plan_test(file_path: str) -> dict:
            """Run a script."""
            agent.calls.append(("run", file_path))
            return {"status": "success", "output": "report"}

        @tool
        def read_native_plan_test(file_path: str) -> dict:
            """Read a file."""
            agent.calls.append(("read", file_path))
            return {"status": "success", "content": "hello"}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture(params=[False, True], ids=["non-streaming", "streaming"])
def agent(request):
    snapshot = dict(_TOOL_REGISTRY)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _WriteRunAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    built.streaming = request.param
    built.verification_scope_statement = lambda: None
    yield built
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _script(agent, *replies):
    queue = list(replies)
    asked = []

    def _next():
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        asked.append(1)
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

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(side_effect=_send)
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat
    return asked


def _plan(*steps):
    return [{"tool": name, "tool_args": args} for name, args in steps]


def _native(*calls, content=None) -> str:
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


def _answer(text="Done.") -> str:
    return json.dumps({"thought": "", "goal": "", "answer": text})


def test_native_call_matching_plan_step_one_runs_once(agent):
    plan_text = json.dumps({"thought": "Write then run.", "plan": _plan(_WRITE, _RUN)})
    asked = _script(agent, _native(_WRITE, content=plan_text), _answer())

    agent.process_query("write and run the report script")

    assert agent.calls.count(("write", _PATH, _SCRIPT)) == 1
    assert len(asked) == 2


def test_native_call_not_matching_plan_runs_once_and_plan_is_not_replayed(agent):
    plan_text = json.dumps({"thought": "Write then run.", "plan": _plan(_WRITE, _RUN)})
    asked = _script(agent, _native(_READ, content=plan_text), _answer())

    agent.process_query("read the notes, then write and run the report")

    # The calls the reply made run; the plan in its text is not run behind
    # the model's back — the model sees the results and decides what is next.
    assert agent.calls == [("read", "notes.txt")]
    assert len(asked) == 2


def test_native_calls_for_every_plan_step_each_run_once(agent):
    plan_text = json.dumps({"thought": "Write then run.", "plan": _plan(_WRITE, _RUN)})
    _script(agent, _native(_WRITE, _RUN, content=plan_text), _answer())

    agent.process_query("write and run the report script")

    assert agent.calls == [("write", _PATH, _SCRIPT), ("run", _PATH)]


def test_json_call_matching_plan_step_one_runs_once(agent):
    reply = json.dumps(
        {
            "thought": "Write then run.",
            "plan": _plan(_WRITE, _RUN),
            "tool": _WRITE[0],
            "tool_args": _WRITE[1],
        }
    )
    _script(agent, reply, _answer())

    agent.process_query("write and run the report script")

    assert agent.calls == [("write", _PATH, _SCRIPT), ("run", _PATH)]
