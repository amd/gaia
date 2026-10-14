# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Placeholder detection must not refuse real plan steps, and must not be dodged."""

import time
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent, _plan_step_placeholder
from gaia.agents.base.tools import _TOOL_REGISTRY

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
