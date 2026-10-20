# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An out-of-scope read asks the user through the agent's console, once."""

import json
import threading
import time
from pathlib import Path
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
        (
            PATH_ACCESS_PROMPT_TOOL,
            {"path": str(layout["file"].resolve()), "kind": "file"},
        )
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


def test_pre_approved_tools_never_widen_the_file_scope(layout):
    """Eval harnesses and API servers pre-approve tools; nobody saw the path."""
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    agent = _ReadAgent(layout["scope"], handler)

    assert _read(agent, layout["file"])["status"] != "success"


def test_full_access_answers_the_path_prompt(layout):
    handler = SSEOutputHandler()
    handler.full_access = True
    agent = _ReadAgent(layout["scope"], handler)

    assert _read(agent, layout["file"])["status"] == "success"


def _relayed_handler(full_access):
    """The relayed sidecar's handler: shell gates on, a person watching."""
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = full_access
    handler.grants_paths = full_access
    handler.answers_confirmations = True
    return handler


def test_relayed_full_access_grants_a_named_folder_without_asking(layout):
    handler = _relayed_handler(full_access=True)
    agent = _ReadAgent(layout["scope"], handler)

    assert _read(agent, layout["file"])["status"] == "success"
    assert handler._confirm_event is None


def test_relayed_full_access_still_refuses_the_home_folder(layout, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: layout["file"].parent))
    handler = _relayed_handler(full_access=True)
    agent = _ReadAgent(layout["scope"], handler)

    result = agent._execute_tool(
        "search_code", {"directory": str(layout["file"].parent), "pattern": "x"}
    )

    assert "too broad" in json.dumps(result)
    assert handler._confirm_event is None


def test_full_access_grants_no_path_on_an_unattended_run(layout):
    handler = _relayed_handler(full_access=True)
    handler.answers_confirmations = False
    agent = _ReadAgent(layout["scope"], handler)

    assert _read(agent, layout["file"])["status"] != "success"
    assert handler._confirm_event is None


@pytest.mark.parametrize("approved", [True, False])
def test_relayed_ask_mode_asks_for_the_path(layout, approved):
    handler = _relayed_handler(full_access=False)
    agent = _ReadAgent(layout["scope"], handler)
    results = {}

    worker = threading.Thread(
        target=lambda: results.update(read=_read(agent, layout["file"]))
    )
    worker.start()
    requests = _answer_next_prompt(handler, approved=approved)
    worker.join(timeout=5)

    assert [r["tool"] for r in requests] == [PATH_ACCESS_PROMPT_TOOL]
    assert (results["read"]["status"] == "success") is approved


@pytest.mark.parametrize("approved", [True, False])
def test_pre_approved_tools_ask_for_the_path_when_someone_can_answer(layout, approved):
    """The relayed Agent UI under Full access: pre-approved, and watched."""
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    handler.answers_confirmations = True
    agent = _ReadAgent(layout["scope"], handler)
    results = {}

    worker = threading.Thread(
        target=lambda: results.update(read=_read(agent, layout["file"]))
    )
    worker.start()
    requests = _answer_next_prompt(handler, approved=approved)
    worker.join(timeout=5)

    assert [r["tool"] for r in requests] == [PATH_ACCESS_PROMPT_TOOL]
    assert requests[0]["args"]["path"] == str(layout["file"].resolve())
    assert (results["read"]["status"] == "success") is approved


def test_pre_approval_still_answers_ordinary_prompts_when_watched():
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    handler.answers_confirmations = True

    assert handler.confirm_tool_execution("write_file", {"file_path": "a"}) is True
    assert handler._confirm_event is None


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


def test_a_folder_prompt_says_folder(layout):
    console = _RecordingConsole(False)
    agent = _ReadAgent(layout["scope"], console)

    agent._execute_tool(
        "search_code", {"directory": str(layout["other"]), "pattern": "x"}
    )

    assert console.calls[0][1]["kind"] == "folder"


@pytest.mark.parametrize(
    "kind, question",
    [
        ("file", r"Allow GAIA to use the file C:\Users\me\sales.csv for this session?"),
        (
            "folder",
            r"Allow GAIA to use the folder C:\Users\me\reports and everything in it "
            "for this session?",
        ),
        (None, r"Allow GAIA to use C:\Users\me\reports for this session?"),
    ],
)
def test_the_prompt_reads_as_a_question_about_the_path(kind, question):
    from gaia.ui.sse_translation import CanonicalTranslator

    path = r"C:\Users\me\sales.csv" if kind == "file" else r"C:\Users\me\reports"
    args = {"path": path, **({"kind": kind} if kind else {})}
    (event,) = CanonicalTranslator(run_id=None, agent_id="gaia", debug=False).translate(
        {"type": "permission_request", "tool": PATH_ACCESS_PROMPT_TOOL, "args": args}
    )
    assert event["summary"] == question


# -- Relayed Full access: what it must never grant, and how Ask fails closed --


def _granted(agent):
    return {Path(p).resolve() for p in agent.path_validator.allowed_paths}


def _search(agent, directory):
    return agent._execute_tool(
        "search_code", {"directory": str(directory), "pattern": "x"}
    )


def test_relayed_full_access_grants_the_named_folder_not_its_parent(layout):
    handler = _relayed_handler(full_access=True)
    agent = _ReadAgent(layout["scope"], handler)

    _search(agent, layout["other"])

    assert _granted(agent) == {
        layout["scope"].resolve(),
        layout["other"].resolve(),
    }


def test_relayed_full_access_never_grants_a_drive_root(layout):
    handler = _relayed_handler(full_access=True)
    agent = _ReadAgent(layout["scope"], handler)
    root = Path(layout["scope"].resolve().anchor)

    result = _search(agent, root)

    assert "too broad" in json.dumps(result)
    assert _granted(agent) == {layout["scope"].resolve()}


def test_relayed_full_access_judges_a_dotdot_path_where_it_lands(layout, monkeypatch):
    home = layout["file"].parent
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    handler = _relayed_handler(full_access=True)
    agent = _ReadAgent(layout["scope"], handler)

    result = _search(agent, layout["scope"] / ".." / home.name)

    assert "too broad" in json.dumps(result)
    assert _granted(agent) == {layout["scope"].resolve()}


@pytest.mark.parametrize("name", [".env", "id_rsa", "server.pem"])
def test_relayed_full_access_never_reads_a_secret(layout, name):
    secret = layout["other"] / name
    secret.write_text("TOKEN=x", encoding="utf-8")
    handler = _relayed_handler(full_access=True)
    agent = _ReadAgent(layout["scope"], handler)

    result = _read(agent, secret)

    assert result["status"] == "error"
    assert "TOKEN=x" not in json.dumps(result)
    assert secret.resolve() not in _granted(agent)


def test_relayed_full_access_does_not_follow_a_link_into_a_secret_folder(
    layout, monkeypatch
):
    vault = layout["other"] / "vault"
    vault.mkdir()
    (vault / "config").write_text("TOKEN=x", encoding="utf-8")
    monkeypatch.setattr("gaia.security.SECRET_DIRECTORIES", {str(vault.resolve())})
    link = layout["scope"] / "innocent"
    try:
        link.symlink_to(vault, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    handler = _relayed_handler(full_access=True)
    agent = _ReadAgent(layout["scope"], handler)

    result = _read(agent, link / "config")

    assert result["status"] == "error"
    assert "TOKEN=x" not in json.dumps(result)
    assert vault.resolve() not in _granted(agent)


def test_relayed_full_access_stays_inside_gaia_allowed_paths(layout, monkeypatch):
    monkeypatch.setenv("GAIA_ALLOWED_PATHS", str(layout["scope"]))
    handler = _relayed_handler(full_access=True)
    agent = _ReadAgent(layout["scope"], handler)

    assert _read(agent, layout["file"])["status"] == "error"
    assert handler._confirm_event is None
    assert _granted(agent) == {layout["scope"].resolve()}


def test_turning_full_access_off_makes_the_next_path_ask_again(layout):
    handler = _relayed_handler(full_access=True)
    agent = _ReadAgent(layout["scope"], handler)
    assert _read(agent, layout["file"])["status"] == "success"

    handler.auto_approve_gated_tools = False
    handler.grants_paths = False
    results = {}
    worker = threading.Thread(
        target=lambda: results.update(read=_read(agent, layout["other"] / "notes.txt"))
    )
    worker.start()
    requests = _answer_next_prompt(handler, approved=False)
    worker.join(timeout=5)

    assert [r["tool"] for r in requests] == [PATH_ACCESS_PROMPT_TOOL]
    assert results["read"]["status"] == "error"
    assert layout["other"].resolve() not in _granted(agent)


def test_an_unanswered_path_prompt_times_out_as_a_refusal(layout):
    handler = _relayed_handler(full_access=False)
    handler.confirm_timeout_seconds = 0.2
    agent = _ReadAgent(layout["scope"], handler)

    result = _read(agent, layout["file"])

    assert result["status"] == "error"
    assert _granted(agent) == {layout["scope"].resolve()}


def test_an_answer_to_an_earlier_prompt_cannot_approve_this_one(layout):
    handler = _relayed_handler(full_access=False)
    agent = _ReadAgent(layout["scope"], handler)
    results = {}
    worker = threading.Thread(
        target=lambda: results.update(read=_read(agent, layout["file"]))
    )
    worker.start()
    deadline = time.monotonic() + 5
    while handler._confirm_event is None and time.monotonic() < deadline:
        time.sleep(0.01)

    late = handler.resolve_tool_confirmation(approved=True, confirm_id="stale-id")
    still_waiting = not handler._confirm_event.is_set()
    handler.resolve_tool_confirmation(approved=False, confirm_id=handler._confirm_id)
    worker.join(timeout=5)

    assert late is False
    assert still_waiting
    assert results["read"]["status"] == "error"
    assert layout["file"].parent.resolve() not in _granted(agent)


def test_a_timeout_answer_never_approves_even_when_marked_approved(layout):
    handler = _relayed_handler(full_access=False)
    agent = _ReadAgent(layout["scope"], handler)
    results = {}
    worker = threading.Thread(
        target=lambda: results.update(read=_read(agent, layout["file"]))
    )
    worker.start()
    _answer_next_prompt(handler, approved=True, timed_out=True)
    worker.join(timeout=5)

    assert results["read"]["status"] == "error"
