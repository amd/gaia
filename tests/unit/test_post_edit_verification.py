# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for reading this turn's edits back before the answer is sealed.

A tool result saying "success" is not the same as a file that is still on
disk, still has content, and still parses. Whether a turn checks is currently
a property of the model: given the same tools and the same prompt, some read
their work back and some never do. The loop already knows which files it
wrote, so it can check by itself.

Pinned here: what counts as a problem, what does not, that repair steps are
granted on top of the step limit rather than taken out of it, and that the
whole thing is bounded so a turn cannot loop on it.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _MAX_EDIT_VERIFICATIONS,
    _VERIFY_REPAIR_STEPS,
    Agent,
    verify_edits_enabled,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator


class _Host(Agent):
    def _register_tools(self):
        pass


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _Host(skip_lemonade=True, silent_mode=True)


def _edited(host, *paths):
    host._turn_file_edits = [{"file_path": str(p)} for p in paths]


# ------------------------------------------------------------ clean edits


def test_a_healthy_edit_reports_nothing(agent, tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("def f():\n    return 1\n")
    _edited(agent, target)

    assert agent._verify_turn_edits() == []


def test_a_file_type_it_cannot_parse_is_not_a_problem(agent, tmp_path):
    """Only Python and JSON are parsed. Everything else is checked for
    existence and content and otherwise left alone — guessing at a syntax
    for an unknown file type would invent failures."""
    target = tmp_path / "notes.sv"
    target.write_text("this is not valid anything {{{")
    _edited(agent, target)

    assert agent._verify_turn_edits() == []


def test_no_edits_means_no_findings(agent):
    agent._turn_file_edits = []

    assert agent._verify_turn_edits() == []


# ---------------------------------------------------------- broken edits


def test_a_vanished_file_is_reported(agent, tmp_path):
    target = tmp_path / "gone.py"
    _edited(agent, target)

    (problem,) = agent._verify_turn_edits()

    assert "is not on disk" in problem
    assert "gone.py" in problem


def test_an_emptied_file_is_reported(agent, tmp_path):
    target = tmp_path / "blank.py"
    target.write_text("   \n\n")
    _edited(agent, target)

    (problem,) = agent._verify_turn_edits()

    assert "is empty" in problem


def test_a_python_file_that_no_longer_parses_is_reported(agent, tmp_path):
    target = tmp_path / "broken.py"
    target.write_text("def f(:\n    return 1\n")
    _edited(agent, target)

    (problem,) = agent._verify_turn_edits()

    assert "no longer parses as Python" in problem


def test_a_json_file_that_no_longer_loads_is_reported(agent, tmp_path):
    target = tmp_path / "config.json"
    target.write_text('{"a": 1,}')
    _edited(agent, target)

    (problem,) = agent._verify_turn_edits()

    assert "no longer valid JSON" in problem


def test_each_file_is_reported_once_however_often_it_was_edited(agent, tmp_path):
    target = tmp_path / "broken.py"
    target.write_text("def f(:\n")
    agent._turn_file_edits = [{"file_path": str(target)}] * 4

    assert len(agent._verify_turn_edits()) == 1


def test_findings_accumulate_across_rounds(agent, tmp_path):
    target = tmp_path / "broken.py"
    target.write_text("def f(:\n")
    _edited(agent, target)

    agent._verify_turn_edits()
    agent._verify_turn_edits()

    assert agent._edit_verification_rounds == 2
    assert len(agent._edit_verification_findings) == 2


# ------------------------------------------------------- the loop wiring


BROKEN = "def f(:" + chr(10)
HEALTHY = "def f():" + chr(10) + "    return 1" + chr(10)


class _EditingHost(Agent):
    """Two tools that report the shape a real edit tool reports."""

    def _register_tools(self):
        @tool
        def break_a_file(file_path: str) -> dict:
            """Write a file that does not parse, and report success anyway."""
            Path(file_path).write_text(BROKEN, encoding="utf-8")
            return {
                "status": "success",
                "operation": "edit_file",
                "file_path": file_path,
            }

        @tool
        def repair_a_file(file_path: str) -> dict:
            """Write a file that parses."""
            Path(file_path).write_text(HEALTHY, encoding="utf-8")
            return {
                "status": "success",
                "operation": "edit_file",
                "file_path": file_path,
            }


@pytest.fixture
def editing_agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        host = _EditingHost(silent_mode=True, skip_lemonade=True)
    host.streaming = False
    host._tool_requires_confirmation = lambda *a, **kw: False
    host.console = MagicMock()
    host.console.cancelled = None
    yield host
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _script(host, *turns):
    sent = []

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert turns_left, "unexpected model call"
        return MagicMock(text=json.dumps(turns_left.pop(0)), stats={})

    turns_left = list(turns)
    host.chat = MagicMock()
    host.chat.send_messages.side_effect = send
    return sent


BREAK = {"tool": "break_a_file", "tool_args": {"file_path": "mod.py"}}
REPAIR = {"tool": "repair_a_file", "tool_args": {"file_path": "mod.py"}}


def test_the_loop_reads_the_edit_back_and_asks_for_a_repair(editing_agent):
    sent = _script(
        editing_agent,
        BREAK,
        {"answer": "Done. The module is fixed."},
        REPAIR,
        {"answer": "Corrected the syntax error as well."},
    )

    result = editing_agent.process_query("change mod.py", max_steps=2)

    assert editing_agent._edit_verification_rounds == 2
    assert len(editing_agent._edit_verification_findings) == 1
    repair_prompt = next(
        m["content"]
        for m in sent[-1]
        if isinstance(m.get("content"), str) and "[check:edits]" in m["content"]
    )
    assert "no longer parses as Python" in repair_prompt
    assert "not charged against your step budget" in repair_prompt
    assert result["result"].startswith("Corrected")


def test_the_repair_steps_are_not_charged_to_the_step_budget(editing_agent):
    """The turn that most needs to fix its edit is the one that has already
    spent its budget making it, so the repair runs past the limit."""
    _script(
        editing_agent,
        BREAK,
        {"answer": "Done."},
        REPAIR,
        {"answer": "Corrected."},
    )

    result = editing_agent.process_query("change mod.py", max_steps=2)

    assert result["steps_taken"] > 2
    assert result["steps_taken"] <= 2 + _VERIFY_REPAIR_STEPS
    assert not result["max_steps_reached"]


def test_a_healthy_edit_is_never_asked_about(editing_agent):
    sent = _script(editing_agent, REPAIR, {"answer": "Done."})

    result = editing_agent.process_query("change mod.py", max_steps=2)

    assert editing_agent._edit_verification_rounds == 1
    assert editing_agent._edit_verification_findings == []
    assert "[check:edits]" not in json.dumps(sent[-1])
    assert result["result"].startswith("Done.")


def test_the_loop_stops_asking_after_the_bound(editing_agent):
    """A model that cannot fix its edit must not be asked forever."""
    turns = [BREAK] + [{"answer": "Done."}] * 8
    _script(editing_agent, *turns)

    editing_agent.process_query("change mod.py", max_steps=2)

    assert editing_agent._edit_verification_rounds == _MAX_EDIT_VERIFICATIONS


def test_the_loop_never_asks_when_the_check_is_off(editing_agent, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_VERIFY_EDITS", "0")
    sent = _script(editing_agent, BREAK, {"answer": "Done."})

    result = editing_agent.process_query("change mod.py", max_steps=2)

    assert editing_agent._edit_verification_rounds == 0
    assert "[check:edits]" not in json.dumps(sent[-1])
    assert result["result"].startswith("Done.")


# ---------------------------------------------------------------- the knob


@pytest.mark.parametrize("value", ["1", "true", "on", "YES", ""])
def test_truthy_and_unset_keep_it_on(monkeypatch, value):
    monkeypatch.setenv("GAIA_AGENT_VERIFY_EDITS", value)
    assert verify_edits_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "off", "No"])
def test_falsy_turns_it_off(monkeypatch, value):
    monkeypatch.setenv("GAIA_AGENT_VERIFY_EDITS", value)
    assert verify_edits_enabled() is False


def test_a_typo_is_reported_rather_than_silently_ignored(monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_VERIFY_EDITS", "sometimes")
    with pytest.raises(ValueError, match="GAIA_AGENT_VERIFY_EDITS"):
        verify_edits_enabled()


def test_off_means_the_loop_never_asks(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_AGENT_VERIFY_EDITS", "0")
    with patch("gaia.agents.base.agent.AgentSDK"):
        host = _Host(skip_lemonade=True, silent_mode=True)

    assert host._verify_edits_on is False


def test_an_agent_that_never_ran_init_does_not_crash():
    bare = _Host.__new__(_Host)

    assert bare._verify_turn_edits() == []
    assert bare._edit_verification_rounds == 1


# ------------------------------------- the real file tools, not a stand-in


class _RealToolsHost(Agent, FileIOToolsMixin):
    """The shipped file tools, so the check is pinned against what runs."""

    def __init__(self, **kwargs):
        self.path_validator = PathValidator()
        super().__init__(**kwargs)

    def _register_tools(self):
        self.register_file_io_tools()


@pytest.fixture
def real_agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        host = _RealToolsHost(silent_mode=True, skip_lemonade=True)
    host.path_validator.allowed_paths.add(tmp_path.resolve())
    host.streaming = False
    host._tool_requires_confirmation = lambda *a, **kw: False
    host.console = MagicMock()
    host.console.cancelled = None
    yield host
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def test_a_real_write_file_is_seen_by_the_check(real_agent):
    """Regression. The obvious source for "what did this turn write" is
    `_turn_file_edits`, and it is the wrong one: it is appended only for a
    result carrying `operation == "edit_file"`, which nothing in
    `file_io_tools` sets. Reading it made this check silently do nothing for
    every shipped file tool — the exact failure it exists to catch."""
    _script(
        real_agent,
        {
            "tool": "write_file",
            "tool_args": {"file_path": "mod.py", "content": "def f(:\n"},
        },
        {"answer": "Written."},
        {
            "tool": "write_file",
            "tool_args": {"file_path": "mod.py", "content": "def f():\n    return 1\n"},
        },
        {"answer": "Fixed."},
    )

    real_agent.process_query("write mod.py", max_steps=2)

    assert real_agent._edit_verification_rounds >= 1
    assert any(
        "no longer parses as Python" in f
        for f in real_agent._edit_verification_findings
    )


def test_the_written_path_is_found_without_the_operation_field(real_agent):
    _script(
        real_agent,
        {
            "tool": "write_file",
            "tool_args": {"file_path": "notes.txt", "content": "hello"},
        },
        {"answer": "Written."},
    )

    real_agent.process_query("write notes.txt", max_steps=2)

    assert (
        real_agent._files_written_this_turn()
    ), "a successful write_file left no trace the check can read"
