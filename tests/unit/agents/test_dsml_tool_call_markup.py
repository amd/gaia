# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool call DeepSeek writes in its own DSML markup is still a tool call.

On a TheRock benchmark task, fireworks.deepseek-v4p1-flash answered with its
native ``<｜DSML｜ calls>`` markup as plain text. The loop found no tool call,
took the markup as the final answer, and the edit never happened.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.dsml import parse_dsml_tool_calls
from gaia.agents.base.verification import strip_verification_scope

_BENCH_PATH = (
    "/Users/Shared/gaia-bench-work/runs-therock/fireworks_deepseek-v4p1-flash/"
    "tr-7998/test_tools/determine_rocm_test_dependencies.py"
)
_PROSE = (
    "Baseline confirmed empirically just now:\n\n"
    "**58 passed** (+19 subtests), rc=0 — recorded above from an actual "
    "run this turn (`pytest -q`).\n\n"
    "Now let me make the fix real rather than describing it. First change "
    "— teach the loader about components that have no node of their own:"
)
_OLD = (
    "Algorithm\n---------\n1. Load the consumer graph.\n"
    "2. For each changed subproject, walk its `consumers` edges to a depth set "
    "by the\n   component's policy level (see the level ladder below).\n"
    "3. UNION the per-subproject walk results across all changed subprojects"
)

# The tr-7998 reply verbatim: the output-token limit cut it off mid-argument.
TR7998_REPLY = (
    _PROSE + "\n\n<｜DSML｜ calls>\n"
    '<｜DSML｜ invoke name="edit_file">\n'
    '<｜DSML｜ parameter name="file_path" string="true">'
    + _BENCH_PATH
    + "</｜DSML｜ parameter>\n"
    '<｜DSML｜ parameter name="old_content" string="true">' + _OLD
)
_NEW = _OLD + "\n4. Expand bundled components to the subprojects that bundle them."

# The same reply as the model meant it: every element closed.
TR7998_COMPLETE = (
    TR7998_REPLY + "</｜DSML｜ parameter>\n"
    '<｜DSML｜ parameter name="new_content" string="true">'
    + _NEW
    + "</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>"
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_tr7998_reply_parses_to_edit_file_call():
    calls, prose = parse_dsml_tool_calls(TR7998_COMPLETE)

    assert [c["name"] for c in calls] == ["edit_file"]
    assert calls[0]["tool_args"] == {
        "file_path": _BENCH_PATH,
        "old_content": _OLD,
        "new_content": _NEW,
    }
    assert prose == _PROSE


def test_spec_spelling_without_spaces_and_json_values():
    text = (
        "<｜DSML｜function_calls>\n"
        '<｜DSML｜invoke name="read_file">\n'
        '<｜DSML｜parameter name="path" string="true">a.py</｜DSML｜parameter>\n'
        '<｜DSML｜parameter name="limit" string="false">40</｜DSML｜parameter>\n'
        "</｜DSML｜invoke>\n"
        '<｜DSML｜invoke name="list_dir">\n'
        '<｜DSML｜parameter name="paths" string="false">["a", "b"]</｜DSML｜parameter>\n'
        "</｜DSML｜invoke>\n"
        "</｜DSML｜function_calls>"
    )

    calls, prose = parse_dsml_tool_calls(text)

    assert [(c["name"], c["tool_args"]) for c in calls] == [
        ("read_file", {"path": "a.py", "limit": 40}),
        ("list_dir", {"paths": ["a", "b"]}),
    ]
    assert prose == ""


def test_text_without_markup_is_not_a_call():
    assert parse_dsml_tool_calls("DeepSeek's DSML format wraps calls in tags.") is None


def test_truncated_markup_names_the_cut_off_parameter():
    with pytest.raises(ValueError) as exc:
        parse_dsml_tool_calls(TR7998_REPLY)

    msg = str(exc.value)
    assert "cut off" in msg
    assert "'old_content'" in msg and "edit_file" in msg


def test_invalid_json_value_is_named():
    text = (
        '<｜DSML｜ calls>\n<｜DSML｜ invoke name="read_file">\n'
        '<｜DSML｜ parameter name="limit" string="false">forty</｜DSML｜ parameter>\n'
        "</｜DSML｜ invoke>\n</｜DSML｜ calls>"
    )
    with pytest.raises(ValueError, match="'limit'.*not valid JSON"):
        parse_dsml_tool_calls(text)


def test_invoke_outside_a_calls_block_is_malformed():
    text = '<｜DSML｜ invoke name="read_file">\n</｜DSML｜ invoke>'
    with pytest.raises(ValueError, match="calls"):
        parse_dsml_tool_calls(text)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


class _DummyAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        return a


def _register_edit_file(agent):
    calls = []

    def _edit_file(file_path, old_content, new_content):
        calls.append(
            {
                "file_path": file_path,
                "old_content": old_content,
                "new_content": new_content,
            }
        )
        return {"status": "success", "file_path": file_path}

    agent._instance_tools = {
        "edit_file": {
            "name": "edit_file",
            "description": "stub",
            "parameters": {
                "file_path": {"type": "string", "required": True},
                "old_content": {"type": "string", "required": True},
                "new_content": {"type": "string", "required": True},
            },
            "function": _edit_file,
            "atomic": True,
        }
    }
    return calls


def _stub_chat(agent, *replies):
    replies = list(replies)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = replies.pop(0)
        resp.stats = {}
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def test_dsml_reply_runs_edit_file_and_turn_continues(agent):
    edits = _register_edit_file(agent)
    agent.console.confirm_tool_execution = MagicMock(return_value=True)
    sent = _stub_chat(agent, TR7998_COMPLETE, "Edited the algorithm docstring.")

    result = agent.process_query("Fix the selection logic", max_steps=10)

    assert edits == [
        {"file_path": _BENCH_PATH, "old_content": _OLD, "new_content": _NEW}
    ]
    assert len(sent) == 2
    final = strip_verification_scope(result["result"]).strip()
    assert final == "Edited the algorithm docstring."
    assert "DSML" not in final


def test_truncated_dsml_reply_is_corrected_not_answered(agent):
    edits = _register_edit_file(agent)
    sent = _stub_chat(agent, TR7998_REPLY, "Done.")

    result = agent.process_query("Fix the selection logic", max_steps=10)

    assert edits == []
    assert len(sent) == 2
    correction = sent[1][-1]["content"]
    assert "cut off" in correction and "'old_content'" in correction
    assert "DSML" not in strip_verification_scope(result["result"])


def test_plain_json_answer_path_is_unaffected(agent):
    _stub_chat(agent, json.dumps({"thought": "", "answer": "All good."}))

    result = agent.process_query("hi", max_steps=5)

    assert strip_verification_scope(result["result"]).strip() == "All good."
