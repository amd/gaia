# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An out-of-scope read asks the user through the agent's console, once."""

import threading
import time
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tool_grants import PATH_ACCESS_PROMPT_TOOL, grant_scope
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator
from gaia.ui.sse_handler import SSEOutputHandler


class _RecordingConsole(SilentConsole):
    def __init__(self, answer):
        super().__init__()
        self.answer = answer
        self.calls = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.calls.append((tool_name, dict(tool_args)))
        return self.answer


class _ReadAgent(Agent, FileIOToolsMixin):
    def __init__(self, scope, console):
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(
                silent_mode=True, skip_lemonade=True, output_handler=console
            )

    def _get_system_prompt(self) -> str:
        return "read"

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
    (docs / "fed_rate.txt").write_text("The Fed held rates.", encoding="utf-8")
    other = tmp_path / "Elsewhere"
    other.mkdir()
    (other / "notes.txt").write_text("unrelated", encoding="utf-8")
    return {"scope": scope, "file": docs / "fed_rate.txt", "other": other}


def _read(agent, path):
    return agent._execute_tool("read_file", {"file_path": str(path)})


def test_approved_read_prompts_once_and_succeeds(layout):
    console = _RecordingConsole(True)
    agent = _ReadAgent(layout["scope"], console)

    first = _read(agent, layout["file"])
    second = _read(agent, layout["file"])

    assert first["status"] == "success"
    assert "The Fed held rates." in first["content"]
    assert second["status"] == "success"
    assert console.calls == [
        (PATH_ACCESS_PROMPT_TOOL, {"path": str(layout["file"].resolve())})
    ]


def test_denied_read_prompts_once_and_fails(layout):
    console = _RecordingConsole(False)
    agent = _ReadAgent(layout["scope"], console)

    result = _read(agent, layout["file"])

    assert result["status"] == "error"
    assert "Access denied" in result["error"]
    assert len(console.calls) == 1


def test_secret_file_never_reaches_the_console(layout):
    secret = layout["file"].parent / ".env"
    secret.write_text("TOKEN=x", encoding="utf-8")
    console = _RecordingConsole(True)
    agent = _ReadAgent(layout["scope"], console)

    assert _read(agent, secret)["status"] == "error"
    assert console.calls == []


def test_auto_approve_answers_the_path_prompt(layout):
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    agent = _ReadAgent(layout["scope"], handler)

    assert _read(agent, layout["file"])["status"] == "success"


def test_path_prompt_offers_no_always():
    assert grant_scope(PATH_ACCESS_PROMPT_TOOL, {"path": "/home/u/Documents"}) is None


def _answer_next_prompt(handler, **decision):
    deadline = time.monotonic() + 5
    while handler._confirm_event is None and time.monotonic() < deadline:
        time.sleep(0.01)
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    requests = [e for e in events if e.get("type") == "permission_request"]
    handler.resolve_tool_confirmation(**decision)
    return requests


def test_always_answer_grants_only_the_prompted_path(layout):
    handler = SSEOutputHandler()
    agent = _ReadAgent(layout["scope"], handler)
    results = {}

    def read_into(key, path):
        results[key] = _read(agent, path)

    worker = threading.Thread(target=read_into, args=("first", layout["file"]))
    worker.start()
    requests = _answer_next_prompt(handler, approved=True, always=True)
    worker.join(timeout=5)

    assert results["first"]["status"] == "success"
    assert requests[0]["tool"] == PATH_ACCESS_PROMPT_TOOL
    assert "always_scope" not in requests[0]
    assert handler.session_grants() == set()

    # A different folder is asked about again, not covered by that "always".
    handler._confirm_event = None
    worker = threading.Thread(
        target=read_into, args=("second", layout["other"] / "notes.txt")
    )
    worker.start()
    requests = _answer_next_prompt(handler, approved=False)
    worker.join(timeout=5)

    assert len(requests) == 1
    assert results["second"]["status"] == "error"


def test_the_prompt_reads_as_a_question_about_the_path():
    from gaia.ui.sse_translation import CanonicalTranslator

    (event,) = CanonicalTranslator(run_id=None, agent_id="gaia", debug=False).translate(
        {
            "type": "permission_request",
            "tool": PATH_ACCESS_PROMPT_TOOL,
            "args": {"path": r"C:\Users\me\notes\plan.txt"},
        }
    )
    assert event["summary"] == (
        r"Allow GAIA to open C:\Users\me\notes\plan.txt for this session?"
    )
