# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A write outside the chat's folders is asked about once, never twice (#5299)."""

import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.call_risk import RISK_NOTE_ARG
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tool_grants import PATH_ACCESS_PROMPT_TOOL, grant_scope
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import ALLOWED_PATHS_ENV, PathValidator
from gaia.ui.sse_handler import SSEOutputHandler


class _RecordingConsole(SilentConsole):
    def __init__(self, *answers):
        super().__init__()
        self.answers = list(answers)
        self.calls = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.calls.append((tool_name, dict(tool_args)))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class _WriteAgent(Agent, FileIOToolsMixin):
    def __init__(self, scope, console):
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(
                silent_mode=True, skip_lemonade=True, output_handler=console
            )

    def _get_system_prompt(self) -> str:
        return "write"

    def _register_tools(self) -> None:
        self.register_file_io_tools()


@pytest.fixture
def layout(tmp_path, monkeypatch):
    # tmp_path lives in the system temp dir, which is never askable.
    monkeypatch.setattr(
        "gaia.security._system_temp_roots", lambda: {str(tmp_path / "systemp")}
    )
    scope = tmp_path / "scope"
    scope.mkdir()
    docs = tmp_path / "Documents"
    docs.mkdir()
    return {"scope": scope, "docs": docs}


def _write(agent, path, content="hello"):
    return agent._execute_tool(
        "write_file", {"file_path": str(path), "content": content}
    )


def _tools(console):
    return [tool for tool, _ in console.calls]


def test_new_file_outside_scope_asks_once_and_is_written(layout):
    target = layout["docs"] / "new" / "hello.txt"
    console = _RecordingConsole(True)
    agent = _WriteAgent(layout["scope"], console)

    result = _write(agent, target)

    assert result["status"] == "success", result
    assert target.read_text(encoding="utf-8") == "hello"
    assert _tools(console) == ["write_file"]
    shown = console.calls[0][1]
    assert shown["file_path"] == str(target.resolve())
    assert "outside" in shown[RISK_NOTE_ARG]


def test_the_combined_prompt_offers_no_always(layout):
    console = _RecordingConsole(True)
    agent = _WriteAgent(layout["scope"], console)

    _write(agent, layout["docs"] / "hello.txt")

    assert grant_scope("write_file", console.calls[0][1]) is None


def test_approval_grants_the_file_not_its_folder(layout):
    console = _RecordingConsole(True)
    agent = _WriteAgent(layout["scope"], console)
    _write(agent, layout["docs"] / "hello.txt")

    validator = agent.path_validator
    assert validator.is_path_allowed(
        str(layout["docs"] / "hello.txt"), prompt_user=False
    )
    assert not validator.is_path_allowed(
        str(layout["docs"] / "other.txt"), prompt_user=False
    )


def test_a_second_write_to_the_same_file_asks_only_about_the_write(layout):
    target = layout["docs"] / "hello.txt"
    console = _RecordingConsole(True)
    agent = _WriteAgent(layout["scope"], console)
    _write(agent, target)
    agent._execute_tool("read_file", {"file_path": str(target)})

    assert _write(agent, target, "again")["status"] == "success"
    assert _tools(console) == ["write_file", "write_file"]
    assert RISK_NOTE_ARG not in console.calls[1][1]


def test_declining_the_write_grants_nothing_and_asks_nothing_else(layout):
    target = layout["docs"] / "hello.txt"
    console = _RecordingConsole(False)
    agent = _WriteAgent(layout["scope"], console)

    result = _write(agent, target)

    assert result["status"] == "denied"
    assert not target.exists()
    assert _tools(console) == ["write_file"]
    assert not agent.path_validator.is_path_allowed(str(target), prompt_user=False)


def test_a_folder_declined_this_turn_refuses_the_write_without_asking(layout):
    """Asking to approve a write the folder denial would block is a dead prompt."""
    console = _RecordingConsole(False)
    agent = _WriteAgent(layout["scope"], console)
    # The user declines the folder on a read...
    agent._execute_tool(
        "search_code", {"directory": str(layout["docs"]), "pattern": "x"}
    )
    assert _tools(console) == [PATH_ACCESS_PROMPT_TOOL]
    console.answers = [True]

    result = _write(agent, layout["docs"] / "release-notes.md")

    assert result["status"] == "error"
    assert "Access denied" in result["error"]
    assert not (layout["docs"] / "release-notes.md").exists()
    assert _tools(console) == [PATH_ACCESS_PROMPT_TOOL]


def test_a_write_inside_scope_carries_no_location_note(layout):
    console = _RecordingConsole(True)
    agent = _WriteAgent(layout["scope"], console)

    assert _write(agent, layout["scope"] / "a.txt")["status"] == "success"
    assert _tools(console) == ["write_file"]
    assert RISK_NOTE_ARG not in console.calls[0][1]


def _answer_next_prompt(handler, **decision):
    deadline = time.monotonic() + 5
    while handler._confirm_event is None and time.monotonic() < deadline:
        time.sleep(0.01)
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    handler.resolve_tool_confirmation(**decision)
    return [e for e in events if e.get("type") == "permission_request"]


def _write_in_thread(agent, target):
    results = {}
    worker = threading.Thread(
        target=lambda: results.update(write=_write(agent, target))
    )
    worker.start()
    return worker, results


def test_agent_ui_shows_one_prompt_for_a_new_file_outside_scope(layout):
    handler = SSEOutputHandler()
    agent = _WriteAgent(layout["scope"], handler)
    target = layout["docs"] / "gaia-test" / "hello.txt"

    worker, results = _write_in_thread(agent, target)
    requests = _answer_next_prompt(handler, approved=True)
    worker.join(timeout=5)

    assert [r["tool"] for r in requests] == ["write_file"]
    assert "outside" in requests[0]["args"][RISK_NOTE_ARG]
    assert results["write"]["status"] == "success"
    assert target.exists()


def test_an_always_grant_for_the_write_cannot_answer_the_location(layout):
    """An earlier "always" never named this location, so it still asks."""
    handler = SSEOutputHandler()
    agent = _WriteAgent(layout["scope"], handler)
    target = layout["docs"] / "hello.txt"
    handler.grant_call_for_session(
        "write_file", {"file_path": str(target.resolve()), "content": "x"}
    )

    worker, results = _write_in_thread(agent, target)
    requests = _answer_next_prompt(handler, approved=False)
    worker.join(timeout=5)

    assert [r["tool"] for r in requests] == ["write_file"]
    assert results["write"]["status"] == "denied"
    assert not target.exists()


def test_watched_pre_approval_asks_only_about_the_location(layout):
    """Full access without file scope: the write is pre-approved, the path is not."""
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    handler.answers_confirmations = True
    agent = _WriteAgent(layout["scope"], handler)
    target = layout["docs"] / "hello.txt"

    worker, results = _write_in_thread(agent, target)
    requests = _answer_next_prompt(handler, approved=True)
    worker.join(timeout=5)

    assert [r["tool"] for r in requests] == [PATH_ACCESS_PROMPT_TOOL]
    assert results["write"]["status"] == "success"


def test_pre_approved_tools_never_widen_the_scope_for_a_write(layout):
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    agent = _WriteAgent(layout["scope"], handler)
    target = layout["docs"] / "hello.txt"

    result = _write(agent, target)

    assert result["status"] == "error"
    assert "Access denied" in result["error"]
    assert not target.exists()
    assert handler._confirm_event is None
    assert not agent.path_validator.is_path_allowed(str(target), prompt_user=False)


def _link(link, real):
    """A symlink, or on Windows without the privilege, a junction."""
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi

        _winapi.CreateJunction(str(real), str(link))


def test_the_prompt_names_where_a_linked_write_really_lands(layout):
    private = layout["docs"] / "Private"
    private.mkdir()
    _link(layout["scope"] / "link", private)
    console = _RecordingConsole(True)
    agent = _WriteAgent(layout["scope"], console)
    real = (private / "notes.md").resolve()

    result = agent._execute_tool(
        "write_markdown_file",
        {"file_path": str(layout["scope"] / "link" / "notes.md"), "content": "# hi"},
    )

    assert result["status"] == "success", result
    assert _tools(console) == ["write_markdown_file"]
    shown = console.calls[0][1]
    assert shown["file_path"] == str(real)
    assert str(real) in shown[RISK_NOTE_ARG]


def test_a_withheld_folder_refuses_the_write_without_asking(layout):
    console = _RecordingConsole(True)
    agent = _WriteAgent(layout["scope"], console)
    agent.path_validator.withhold_path(str(layout["docs"]))

    result = _write(agent, layout["docs"] / "hello.txt")

    assert result["status"] == "error"
    assert console.calls == []


def test_a_write_outside_gaia_allowed_paths_is_refused_without_asking(
    layout, monkeypatch
):
    monkeypatch.setenv(ALLOWED_PATHS_ENV, str(layout["scope"]))
    console = _RecordingConsole(True)
    agent = _WriteAgent(layout["scope"], console)

    result = _write(agent, layout["docs"] / "hello.txt")

    assert result["status"] == "error"
    assert console.calls == []
    assert not (layout["docs"] / "hello.txt").exists()


def test_the_terminal_ui_summary_carries_the_location_note():
    from gaia.ui.sse_translation import CanonicalTranslator

    note = "/home/u/Documents/a.txt is outside this chat's folders."
    out = CanonicalTranslator(run_id=None, agent_id="gaia").translate(
        {
            "type": "permission_request",
            "tool": "write_file",
            "args": {"file_path": "/home/u/Documents/a.txt", RISK_NOTE_ARG: note},
            "confirm_id": "c-1",
        }
    )

    prompt = next(e for e in out if e["type"] == "needs_confirmation")
    assert note in prompt["summary"]


def test_full_access_asks_only_about_the_write(layout):
    """Full access covers the location, so the write's prompt carries no note."""
    handler = SSEOutputHandler()
    handler.full_access = True
    agent = _WriteAgent(layout["scope"], handler)
    target = layout["docs"] / "hello.txt"

    worker, results = _write_in_thread(agent, target)
    requests = _answer_next_prompt(handler, approved=True)
    worker.join(timeout=5)

    assert [r["tool"] for r in requests] == ["write_file"]
    assert RISK_NOTE_ARG not in requests[0]["args"]
    assert results["write"]["status"] == "success"


def test_a_secret_target_is_refused_before_any_prompt(layout):
    console = _RecordingConsole(True)
    agent = _WriteAgent(layout["scope"], console)

    result = _write(agent, Path(layout["docs"]) / ".env")

    assert result["status"] == "error"
    assert console.calls == []
