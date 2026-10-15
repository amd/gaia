# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A plan step never runs with stand-in args, and reports the args it runs with."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, _plan_step_placeholder
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_PATH = "C:/scratch/build_wb.py"
_SCRIPT = "import pandas as pd\nprint('built')\n"


class _ArgsConsole(AgentConsole):
    def __init__(self):
        super().__init__()
        self.arguments = []

    def pretty_print_json(self, data, title=None):
        if title == "Arguments":
            self.arguments.append(data)
        return super().pretty_print_json(data, title)


class _ScriptAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        @tool
        def write_script_plan_test(file_path: str, content: str) -> dict:
            """Write a script file."""
            agent.writes.append((file_path, content))
            return {"status": "success", "file_path": file_path}

        @tool
        def run_script_plan_test(file_path: str) -> dict:
            """Run a script file."""
            agent.runs.append(file_path)
            return {"status": "success", "output": "built"}

    def _create_console(self):
        return _ArgsConsole()


@pytest.fixture
def agent():
    snapshot = dict(_TOOL_REGISTRY)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _ScriptAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    built.writes = []
    built.runs = []
    built.streaming = False
    built.verification_scope_statement = lambda: None
    yield built
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _script(agent, *replies):
    queue = list(replies)
    sent = []

    def _reply(*_, **kwargs):
        sent.append([dict(m) for m in kwargs.get("messages", [])])
        return SimpleNamespace(text=queue.pop(0), stats={})

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(side_effect=_reply)
    agent.chat = chat
    return sent


def _plan_reply(write_content, top_content=None):
    plan = [
        {
            "tool": "write_script_plan_test",
            "tool_args": {"file_path": _PATH, "content": write_content},
        },
        {"tool": "run_script_plan_test", "tool_args": {"file_path": _PATH}},
    ]
    reply = {"thought": "Write then run.", "goal": "Build it", "plan": plan}
    if top_content is not None:
        reply["tool"] = "write_script_plan_test"
        reply["tool_args"] = {"file_path": _PATH, "content": top_content}
    return json.dumps(reply)


def test_placeholder_plan_step_is_refused_and_the_model_supplies_the_value(agent):
    sent = _script(
        agent,
        _plan_reply("SCRIPT_PLACEHOLDER"),
        json.dumps(
            {
                "tool": "write_script_plan_test",
                "tool_args": {"file_path": _PATH, "content": _SCRIPT},
            }
        ),
        json.dumps({"tool": "run_script_plan_test", "tool_args": {"file_path": _PATH}}),
        json.dumps({"answer": "Built the workbook."}),
    )

    agent.process_query("build the workbook")

    assert agent.writes == [(_PATH, _SCRIPT)]
    asked = [
        m["content"]
        for m in sent[1]
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    ]
    assert any("SCRIPT_PLACEHOLDER" in c and "NOT run" in c for c in asked)


def test_direct_call_args_replace_the_plan_copy_of_the_same_step(agent):
    # kw-04: the plan held a stand-in, the direct call held the real script.
    _script(
        agent,
        _plan_reply("SCRIPT_PLACEHOLDER", top_content=_SCRIPT),
        json.dumps({"answer": "Built the workbook."}),
    )

    agent.process_query("build the workbook")

    assert agent.writes == [(_PATH, _SCRIPT)]
    assert agent.runs == [_PATH]


def test_plan_run_steps_report_their_args(agent):
    _script(
        agent,
        _plan_reply(_SCRIPT, top_content=_SCRIPT),
        json.dumps({"answer": "Built the workbook."}),
    )

    agent.process_query("build the workbook")

    assert {"file_path": _PATH, "content": _SCRIPT} in agent.console.arguments
    assert {"file_path": _PATH} in agent.console.arguments


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("write_file", {"file_path": "a.py", "content": "SCRIPT_PLACEHOLDER"}),
        ("write_file", {"file_path": "a.py", "content": "REAL_SCRIPT"}),
        ("write_file", {"file_path": "a.py", "content": "<full script here>"}),
        ("write_file", {"file_path": "a.py", "content": "TODO"}),
        ("write_file", {"file_path": "a.py", "content": "..."}),
        ("write_file", {"file_path": "a.py", "content": "  "}),
        ("write_file", {"file_path": "a.py"}),
        ("index_document", {"file_path": "$PREV.file_path"}),
        ("index_document", {"file_path": "FILE_PATH_HERE"}),
        ("edit_file", {"edits": [{"new_string": "NEW_CODE"}]}),
    ],
)
def test_stand_in_values_are_detected(tool_name, args):
    assert _plan_step_placeholder(tool_name, args) is not None


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("write_file", {"file_path": "a.py", "content": _SCRIPT}),
        ("write_file", {"file_path": "a.html", "content": "<p>hi</p>"}),
        ("query_scratchpad", {"table": "FLIGHTS_JAN", "sql": "SELECT 1"}),
        ("read_file", {"file_path": "DATA_2024.csv"}),
        ("set_env", {"name": "ANTHROPIC_API_KEY"}),
        ("search_code", {"pattern": "MAX_VALUE"}),
        ("list_dir", {}),
    ],
)
def test_real_values_are_not_flagged(tool_name, args):
    assert _plan_step_placeholder(tool_name, args) is None
