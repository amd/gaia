# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""gaiacpp's tool calls reach the Agent UI worded like the Python agent's.

gaiacpp names its tools ``bash_execute`` / ``file_edit`` and sends its own
argument names, while every card and label in the Agent UI is keyed on the
flagship's. The events below are the shapes gaiacpp's sidecar really sends.
"""

import json
from pathlib import Path

import pytest

import gaia.ui.sidecar.relay as relay_module
from gaia.ui.sidecar.native_tools import (
    NATIVE_TOOL_LABELS,
    NATIVE_TOOL_NAMES,
    as_flagship_call,
    native_result_summary,
)
from gaia.ui.sidecar.profiles import GAIA_PROFILE

_FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "gaiacpp_tools.json"


class _Handler:
    confirm_timeout_seconds = None

    def __init__(self):
        self.events = []
        self.started = []

    def _emit(self, event):
        self.events.append(event)

    def set_relay_question(self, request_id):
        pass

    def set_relay_confirmation(self, *args):
        pass

    def record_tool_started(self, tool):
        self.started.append(tool)

    def record_tool_args(self, args):
        pass

    def record_tool_result(self, data, success):
        pass


def _relay(*events):
    handler = _Handler()
    for event in events:
        relay_module._dispatch_one(
            handler,
            event,
            GAIA_PROFILE,
            answers_confirmations=True,
            offers_always=True,
        )
    return handler


def _prompt(action, args, summary=None, **extra):
    return {
        "type": "needs_confirmation",
        "run_id": "r1",
        "action": action,
        "summary": summary or f"Use {action}",
        "confirm_id": "c1",
        "risk": "write",
        "tool_label": action,
        "args": args,
        **extra,
    }


class TestApprovalCards:
    def test_a_shell_command_is_asked_as_a_command(self):
        [card] = _relay(
            _prompt(
                "bash_execute",
                {"command": "pytest -q"},
                summary="Run: pytest -q",
                risk="execute",
                always_scope="bash_execute:pytest",
                always_label="Run pytest commands",
            )
        ).events

        assert card["tool"] == "run_shell_command"
        assert card["tool_label"] == "Running a shell command"
        assert card["summary"] == "pytest -q"
        assert card["args"] == {"command": "pytest -q"}

    def test_an_edit_carries_its_before_and_after(self):
        [card] = _relay(
            _prompt(
                "file_edit",
                {
                    "path": "proj/inventory.py",
                    "old_string": "count = 1",
                    "new_string": "count = 2",
                },
            )
        ).events

        assert card["tool"] == "edit_file"
        assert card["tool_label"] == "Editing file"
        assert card["args"] == {
            "file_path": "proj/inventory.py",
            "old_content": "count = 1",
            "new_content": "count = 2",
        }
        assert "file_edit" not in json.dumps(card)

    def test_a_write_carries_what_will_be_saved(self):
        [card] = _relay(
            _prompt("file_write", {"path": "notes.md", "content": "hello\n"})
        ).events

        assert card["tool"] == "write_file"
        assert card["tool_label"] == "Writing file"
        assert card["args"] == {"file_path": "notes.md", "content": "hello\n"}
        assert card["summary"] == "Writing file?"

    def test_the_answer_still_reaches_the_tool_that_asked(self):
        """The sidecar matches a decision and a grant on its own values."""
        [card] = _relay(
            _prompt(
                "bash_execute",
                {"command": "git status"},
                summary="Run: git status",
                always_scope="bash_execute:git status",
                always_label="Run git status commands",
            )
        ).events

        assert card["confirm_id"] == "c1"
        assert card["always_scope"] == "bash_execute:git status"
        assert card["always_label"] == "Run git status commands"

    def test_the_python_agents_own_prompt_is_left_alone(self):
        event = _prompt(
            "write_file",
            {"file_path": "notes.md", "content": "hello\n"},
            summary="Write notes.md?",
        )
        event["tool_label"] = "Writing file"
        [card] = _relay(event).events

        assert card["tool"] == "write_file"
        assert card["summary"] == "Write notes.md?"
        assert card["args"] == {"file_path": "notes.md", "content": "hello\n"}


class TestActivityLines:
    def test_an_edit_step_reads_like_the_python_agents(self):
        handler = _relay(
            {
                "type": "tool_call",
                "tool": "file_edit",
                "args": {
                    "path": "proj/inventory.py",
                    "old_string": "count = 1",
                    "new_string": "count = 2",
                },
            },
            {
                "type": "tool_result",
                "tool": "file_edit",
                "data": {
                    "success": True,
                    "path": "proj/inventory.py",
                    "replacements": 1,
                    "content_hash": "9f2c1a7e",
                },
            },
        )
        start, args, marker, result = handler.events

        assert start["tool"] == args["tool"] == result["tool"] == "edit_file"
        assert start["detail"] == "Editing file"
        assert start["narration"] == "Editing file: proj/inventory.py"
        assert args["args"]["old_content"] == "count = 1"
        assert marker["type"] == "status" and "edit_file" in marker["message"]
        assert result["summary"] == "Changed 1 place in inventory.py"
        assert handler.started == ["edit_file"]

    def test_a_command_step_shows_the_command_and_its_output(self):
        handler = _relay(
            {
                "type": "tool_call",
                "tool": "bash_execute",
                "args": {"command": "pytest -q"},
            },
            {
                "type": "tool_result",
                "tool": "bash_execute",
                "data": {
                    "stdout": "3 passed\n",
                    "stderr": "",
                    "exit_code": 0,
                    "timed_out": False,
                    "cwd": "C:/work",
                },
            },
        )

        assert handler.events[0]["tool"] == "run_shell_command"
        assert handler.events[0]["narration"] == "Running command: pytest -q"
        assert handler.events[-1]["summary"] == "3 passed"

    def test_a_search_result_is_named_as_its_call_was(self):
        handler = _relay(
            {
                "type": "tool_call",
                "tool": "file_search",
                "args": {"pattern": "*.py", "path": "proj", "content_pattern": "TODO"},
            },
            {
                "type": "tool_result",
                "tool": "file_search",
                "data": {"matches": [{"path": "a.py"}], "total": 1},
            },
        )

        assert handler.events[1]["args"] == {
            "directory": "proj",
            "pattern": "TODO",
            "file_pattern": "*.py",
        }
        assert [e["tool"] for e in handler.events] == ["search_file_content"] * 3
        assert handler.events[-1]["summary"] == "Found 1 match"

    def test_a_tool_with_no_python_twin_still_reads_in_words(self):
        handler = _relay({"type": "tool_call", "tool": "git_status", "args": {}})

        assert handler.events[0]["detail"] == "Checking git status"
        assert handler.events[0]["narration"] == "Checking git status"


@pytest.mark.parametrize(
    "tool,data,summary",
    [
        (
            "file_write",
            {"success": True, "path": "C:\\w\\notes.md", "bytes_written": 1234},
            "Saved notes.md (1,234 bytes)",
        ),
        (
            "file_read",
            {"content": "a\nb\n", "lines": 2, "path": "a/b.txt", "truncated": True},
            "Read 2 lines from b.txt, shortened",
        ),
        (
            "bash_execute",
            {"stdout": "", "stderr": "boom", "exit_code": 2, "timed_out": False},
            "Command failed (exit 2): boom",
        ),
        (
            "bash_execute",
            {"stdout": "", "stderr": "", "exit_code": 0, "timed_out": False},
            "Command completed (no output)",
        ),
        (
            "web_fetch",
            {"status": "success", "url": "http://x", "text": "…", "total_chars": 5000},
            "Read 5,000 characters from the page",
        ),
    ],
)
def test_a_finished_step_reads_in_plain_words(tool, data, summary):
    assert native_result_summary(tool, data) == summary


def test_a_failed_step_keeps_the_tools_own_sentence():
    data = {"error": "old_string not found in file: a.py", "replacements": 0}
    assert native_result_summary("file_edit", data) is None


@pytest.fixture(scope="module")
def listed():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class TestEveryGaiacppToolHasWords:
    """The list cpp/tests holds gaiacpp's registry to, against the labels here."""

    def test_every_tool_has_a_label(self, listed):
        unlabeled = [
            tool
            for tool in listed["tools"]
            if as_flagship_call(tool, None)[0] not in GAIA_PROFILE.tool_labels
            and tool not in NATIVE_TOOL_LABELS
        ]
        assert not unlabeled, f"gaiacpp tools with no label: {unlabeled}"

    def test_every_gated_tool_is_marked_as_a_change_or_a_read(self, listed):
        """A gated call writes or runs something, except the two that only read."""
        reads = {"web_fetch", "take_screenshot"}
        unmarked = [
            tool
            for tool in listed["gated"]
            if tool not in reads
            and as_flagship_call(tool, None)[0] not in GAIA_PROFILE.mutating_tools
        ]
        assert not unmarked, f"gated gaiacpp tools not marked as changes: {unmarked}"

    def test_no_translated_name_is_a_python_tool(self):
        """A name both agents use must not be rewritten under the Python one."""
        assert not NATIVE_TOOL_NAMES & set(GAIA_PROFILE.tool_labels)


def test_only_gaiacpps_search_code_is_read_as_the_code_index():
    """The Python agent's own search_code takes a pattern and keeps its name."""
    assert as_flagship_call("search_code", {"query": "parse args"}) == (
        "search_code_index",
        {"query": "parse args"},
    )
    python_call = {"pattern": "TODO", "directory": "."}
    assert as_flagship_call("search_code", python_call) == ("search_code", python_call)
