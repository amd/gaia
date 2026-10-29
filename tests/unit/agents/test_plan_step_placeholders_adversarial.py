# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Placeholder detection must not refuse real plan steps, and must not be dodged."""

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, _plan_step_placeholder, _write_stand_in
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.verification import check_was_executed

_HTML = (
    "<html><body>\n"
    '<input name="q" placeholder="<placeholder>">\n'
    "<p>{{ title }}</p>\n</body></html>\n"
)


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("write_file", {"file_path": "cfg.py", "content": "MAX_RETRIES = 3\n"}),
        ("write_file", {"file_path": "q.sql", "content": "SELECT 1; -- TODO: index\n"}),
        ("write_file", {"file_path": "n.md", "content": "Step one... then two.\n"}),
        ("write_file", {"file_path": "n.md", "content": "Wait for it…"}),
        ("write_file", {"file_path": "t.html", "content": _HTML}),
        ("write_file", {"file_path": "r.sh", "content": "PREV=1\necho $PREV.txt\n"}),
        ("write_file", {"file_path": "r.sh", "content": 'cp "$PREV.result" out\n'}),
        ("run_python", {"code": "MAX_RETRIES = 3\nprint(MAX_RETRIES)"}),
        ("query_data", {"sql": "SELECT * FROM t WHERE note = 'TODO'"}),
        ("search_code", {"pattern": "MAX_RETRIES"}),
        ("search_code", {"pattern": "TODO"}),
        ("search_code", {"pattern": "FIXME_TODO"}),
        ("search_web", {"query": "TBD"}),
        ("grep", {"regex": "..."}),
        ("read_file", {"file_path": "docs/your_guide.md"}),
        ("write_file", {"file_path": "t.json", "content": {"status": "todo"}}),
        ("write_file", {"file_path": "t.json", "content": ["TODO", "DONE"]}),
        ("write_file", {"file_path": "a.txt", "content": 0}),
    ],
)
def test_legitimate_values_are_not_flagged(tool_name, args):
    assert _plan_step_placeholder(tool_name, args) is None


@pytest.mark.parametrize(
    "tool_name,args,arg",
    [
        (
            "write_file",
            {"file_path": "a.py", "content": "SCRIPT_PLACEHOLDER"},
            "content",
        ),
        ("write_file", {"file_path": "a.py", "content": "<content>"}, "content"),
        ("write_file", {"file_path": "a.py", "content": "  TBD \n"}, "content"),
        ("write_file", {"file_path": "a.py", "content": ""}, "content"),
        ("write_file", {"file_path": "a.py", "content": None}, "content"),
        ("index_document", {"file_path": "$PREV.result"}, "file_path"),
        ("index_document", {"file_path": "$STEP_3.path"}, "file_path"),
        ("run_python", {"code": "PYTHON_CODE"}, "code"),
        (
            "edit_file",
            {"edits": [{"old_string": "x", "new_string": "TBD"}]},
            "edits.0.new_string",
        ),
        (
            "call",
            {"options": {"nested": [{"content": "<content>"}]}},
            "options.nested.0.content",
        ),
        ("read_file", {"file_path": "INSERT_PATH_HERE"}, "file_path"),
    ],
)
def test_stand_ins_are_flagged_wherever_they_sit(tool_name, args, arg):
    found = _plan_step_placeholder(tool_name, args)
    assert found is not None and found[0] == arg


def test_a_megabyte_of_content_is_checked_quickly():
    real = "x = 1  # MAX_RETRIES ... TODO <p>\n" * 30000
    worst = "A_" * 500_000 + "a"  # near-miss for the ALL_CAPS token pattern
    angle = "<" + "a" * 1_000_000  # an angle bracket that never closes
    start = time.perf_counter()
    for content in (real, worst, angle):
        assert len(content) >= 1_000_000
        assert (
            _plan_step_placeholder("write_file", {"file_path": "a", "content": content})
            is None
        )
    assert time.perf_counter() - start < 2.0


class _Bare(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass


@pytest.fixture
def agent():
    snapshot = dict(_TOOL_REGISTRY)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _Bare(silent_mode=True, skip_lemonade=True)
    yield built
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _defer(agent, step, step_results):
    agent.current_plan = [step, {"tool": "run_script", "tool_args": {}}]
    agent.current_step = 0
    messages = []
    return agent._defer_placeholder_plan_step(step, step_results, messages), messages


def test_a_resolved_prev_reference_runs(agent):
    step = {"tool": "index_document", "tool_args": {"file_path": "$PREV.file_path"}}
    refused, messages = _defer(agent, step, [{"file_path": "C:/docs/a.pdf"}])
    assert not refused and messages == []


def test_an_unresolvable_prev_reference_is_refused_and_named(agent):
    step = {"tool": "index_document", "tool_args": {"file_path": "$PREV.result"}}
    refused, messages = _defer(agent, step, [{"file_path": "C:/docs/a.pdf"}])
    assert refused
    assert "$PREV.result" in messages[-1]["content"]
    assert "run_script" in messages[-1]["content"]
    assert agent.current_plan is None


# ---------------------------------------------------------------------------
# Direct calls: a write whose whole content is a stand-in never runs either
# ---------------------------------------------------------------------------

_DOC_WITH_TODO = "# Memo\n\nTODO\n\nThe budget is $40k; see the table below.\n"


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("write_file", {"file_path": "memo.py", "content": "PLACEHOLDER"}),
        ("write_file", {"file_path": "memo.py", "content": "SCRIPT_PLACEHOLDER"}),
        ("write_file", {"file_path": "memo.md", "content": "<content>"}),
        ("write_file", {"file_path": "memo.md", "content": "TODO"}),
        ("write_file", {"file_path": "memo.md", "content": " TBD\n"}),
        ("write_file", {"file_path": "memo.md", "content": "..."}),
        ("write_file", {"file_path": "memo.md", "content": ""}),
        ("write_file", {"file_path": "memo.md", "content": "  \n\t"}),
        ("write_file", {"file_path": "memo.md", "content": None}),
        ("write_file", {"file_path": "init.py", "content": ""}),
        ("write_file", {"file_path": "__init__.py.bak", "content": ""}),
        ("write_python_file", {"file_path": "a.py", "content": "PYTHON_CODE"}),
        ("write_markdown_file", {"file_path": "a.md", "content": "<report here>"}),
        (
            "edit_file",
            {"file_path": "a.py", "old_content": "x", "new_content": "NEW_CODE"},
        ),
        (
            "edit_python_file",
            {"file_path": "a.py", "old_content": "x", "new_content": "..."},
        ),
        ("replace_function", {"file_path": "a.py", "new_implementation": "<impl>"}),
    ],
)
def test_direct_write_stand_ins_are_found(tool_name, args):
    assert _write_stand_in(tool_name, args) is not None


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("write_file", {"file_path": "memo.md", "content": _DOC_WITH_TODO}),
        ("write_file", {"file_path": "memo.md", "content": "TODO: write the intro\n"}),
        ("write_file", {"file_path": "t.html", "content": _HTML}),
        ("write_file", {"file_path": "cfg.py", "content": "MAX_RETRIES = 3\n"}),
        ("write_file", {"file_path": "notes.md", "content": "To be continued..."}),
        ("write_file", {"file_path": "pkg/__init__.py", "content": ""}),
        ("write_file", {"file_path": "C:\\proj\\pkg\\__init__.py", "content": "\n"}),
        ("write_file", {"file_path": "pkg/py.typed", "content": ""}),
        ("write_file", {"file_path": "logs/.gitkeep", "content": ""}),
        ("write_python_file", {"file_path": "pkg/__init__.py", "content": ""}),
        (
            "edit_file",
            {"file_path": "a.py", "old_content": "x = 1\n", "new_content": ""},
        ),
        (
            "edit_file",
            {"file_path": "a.py", "old_content": "x", "new_content": "# TODO"},
        ),
        (
            "replace_function",
            {"file_path": "a.py", "new_implementation": "def f(): ..."},
        ),
        ("read_file", {"file_path": "TODO"}),
        ("run_python", {"code": ""}),
        # Missing argument: dispatch reports that, not a placeholder.
        ("write_file", {"file_path": "a.md"}),
    ],
)
def test_direct_real_writes_are_not_flagged(tool_name, args):
    assert _write_stand_in(tool_name, args) is None


class _WriterAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            agent.writes.append((file_path, content))
            return {"status": "success", "file_path": file_path}

        @tool
        def edit_file(file_path: str, old_content: str, new_content: str) -> dict:
            """Edit a file."""
            agent.writes.append((file_path, new_content))
            return {"status": "success", "file_path": file_path}


@pytest.fixture
def writer():
    snapshot = dict(_TOOL_REGISTRY)
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _WriterAgent(silent_mode=True, skip_lemonade=True)
    built.writes = []
    built.streaming = False
    built.verification_scope_statement = lambda: None
    built._tool_requires_confirmation = lambda *a, **kw: False
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


def _never_asked(*_, **__):
    pytest.fail("a refused write reached the confirmation prompt")


def test_a_direct_placeholder_write_never_runs_or_asks(writer):
    writer._tool_requires_confirmation = lambda *a, **kw: True
    writer.console.confirm_tool_execution = _never_asked
    result = writer._execute_tool(
        "write_file", {"file_path": "memo.py", "content": "PLACEHOLDER"}
    )
    assert writer.writes == []
    assert not check_was_executed(result)
    assert result["error_type"] == "invalid_arguments"
    assert "'PLACEHOLDER'" in result["error"] and "NOT run" in result["error"]


def test_an_empty_write_names_the_files_allowed_to_be_empty(writer):
    result = writer._execute_tool("write_file", {"file_path": "a.md", "content": ""})
    assert writer.writes == []
    assert "__init__.py" in result["error"] and "is empty" in result["error"]


def test_direct_real_writes_still_run(writer):
    for args in (
        {"file_path": "pkg/__init__.py", "content": ""},
        {"file_path": "memo.md", "content": _DOC_WITH_TODO},
    ):
        assert writer._execute_tool("write_file", args)["status"] == "success"
    writer._execute_tool(
        "edit_file", {"file_path": "a.py", "old_content": "x", "new_content": ""}
    )
    assert writer.writes == [
        ("pkg/__init__.py", ""),
        ("memo.md", _DOC_WITH_TODO),
        ("a.py", ""),
    ]


def test_direct_placeholder_write_is_refused_then_rewritten(writer):
    """A direct write_file of "PLACEHOLDER" ran, then was overwritten."""
    memo = "C:/scratch/gaia-scratch-1/make_memo.py"
    real = "print(1)\n"
    sent = _script(
        writer,
        json.dumps(
            {
                "tool": "write_file",
                "tool_args": {"file_path": memo, "content": "PLACEHOLDER"},
            }
        ),
        json.dumps(
            {"tool": "write_file", "tool_args": {"file_path": memo, "content": real}}
        ),
        json.dumps({"answer": "Wrote the memo script."}),
    )
    writer.process_query("write the memo script")
    assert writer.writes == [(memo, real)]
    assert any("NOT run" in str(m.get("content")) for m in sent[1])


def test_a_plan_step_creating_an_empty_init_runs(writer):
    _script(
        writer,
        json.dumps(
            {
                "thought": "make the package",
                "goal": "package",
                "plan": [
                    {
                        "tool": "write_file",
                        "tool_args": {"file_path": "pkg/__init__.py", "content": ""},
                    }
                ],
            }
        ),
        json.dumps({"answer": "Created the package."}),
    )
    writer.process_query("make pkg a package")
    assert writer.writes == [("pkg/__init__.py", "")]
