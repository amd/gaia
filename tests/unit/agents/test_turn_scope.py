# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Once a turn has answered, off-request work does not start; failing tools stop."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.turn_scope import TurnScopeGuard
from gaia.agents.base.verification import check_was_executed

QUERY = "toybox/dates.py mishandles a lowercase z. Fix it and add a regression test."


@pytest.fixture
def guard(tmp_path):
    scope = TurnScopeGuard(failure_limit=4)
    scope.begin_turn(QUERY, str(tmp_path))
    return scope


def ok(path=None):
    return {"status": "success", **({"file_path": path} if path else {})}


def test_nothing_is_refused_before_the_answer(guard):
    assert guard.check("read_file", {"file_path": "toybox/cli.py"}) is None
    assert guard.check("web_search", {"query": "strptime"}) is None


def test_after_the_answer_only_the_requests_files_and_checks_run(guard):
    guard.record("read_file", {"file_path": "tests/test_dates.py"}, ok())
    guard.mark_answered()
    assert guard.check("read_file", {"file_path": "toybox/dates.py"}) is None
    assert guard.check("edit_file", {"file_path": "tests/test_dates.py"}) is None
    assert guard.check("run_shell_command", {"command": "python -m pytest -q"}) is None
    assert guard.check("run_shell_command", {"command": "git diff"}) is None
    assert guard.check("run_shell_command", {"command": "cat toybox/dates.py"}) is None
    assert guard.check("read_tool_output", {"handle": "a1"}) is None
    assert not guard.turn_should_end


def test_loading_the_skill_a_requested_check_needs_is_allowed(guard):
    """Asked to run the tests after answering, the agent may load what grants pytest."""
    guard.mark_answered()
    assert guard.check("load_skill", {"name": "coding"}) is None
    assert guard.check("list_skills", {}) is None
    assert not guard.turn_should_end


def test_browsing_the_named_folder_after_the_answer_is_in_scope(tmp_path):
    scope = TurnScopeGuard(failure_limit=4)
    scope.begin_turn(
        f"You are working in {tmp_path}. List the open issues.", str(tmp_path)
    )
    scope.mark_answered()
    assert scope.check("browse_directory", {"directory_path": str(tmp_path)}) is None


def test_off_request_work_after_the_answer_is_refused_then_ends_the_turn(guard):
    guard.mark_answered()
    first = guard.check("extract_document_items", {"file_path": "toybox/cli.py"})
    assert first is not None and not check_was_executed(first)
    assert "already answered" in first["error"]
    assert "toybox" in first["error"] and "cli.py" in first["error"]
    assert not guard.turn_should_end
    assert guard.check("list_directory", {"path": "toybox"}) is not None
    assert guard.turn_should_end and guard.end_reason == "drift"


def test_a_refused_call_does_not_widen_the_scope(guard):
    guard.mark_answered()
    guard.record("read_file", {"file_path": "README.md"}, ok())
    assert guard.check("read_file", {"file_path": "README.md"}) is not None


def test_the_same_tool_failing_with_different_arguments_stops(guard):
    rejected = {"status": "error", "error": "Extract every source first"}
    for n in range(4):
        assert guard.check("save_extracted_items", {"file_path": f"inv{n}.md"}) is None
        guard.record("save_extracted_items", {"file_path": f"inv{n}.md"}, rejected)
    refused = guard.check("save_extracted_items", {"file_path": "inv9.md"})
    assert refused is not None and not check_was_executed(refused)
    assert "failed 4 times" in refused["error"]
    assert "Extract every source first" in refused["error"]
    assert not guard.turn_should_end
    assert guard.check("save_extracted_items", {"file_path": "inv10.md"}) is not None
    assert guard.turn_should_end and guard.end_reason == "failures"


@pytest.mark.parametrize(
    "between",
    [
        ("save_extracted_items", {"file_path": "x.md"}, {"status": "success"}),
        ("write_file", {"file_path": "notes.md"}, {"status": "success"}),
    ],
)
def test_a_success_or_a_file_change_resets_the_failure_count(guard, between):
    rejected = {"status": "error", "error": "no"}
    for n in range(3):
        guard.record("save_extracted_items", {"file_path": f"{n}.md"}, rejected)
    guard.record(*between)
    guard.record("save_extracted_items", {"file_path": "3.md"}, rejected)
    assert guard.check("save_extracted_items", {"file_path": "4.md"}) is None


@pytest.mark.parametrize(
    "result",
    [
        # A failing test run is how tests report, not a broken tool.
        {"status": "error", "return_code": 1, "stderr": "1 failed"},
        {"status": "error", "rate_limited": True, "wait_time_seconds": 1},
    ],
)
def test_a_program_exit_code_or_a_throttle_is_not_a_tool_failure(guard, result):
    for n in range(6):
        guard.record("run_shell_command", {"command": f"pytest -k t{n}"}, result)
    assert guard.check("run_shell_command", {"command": "pytest"}) is None


class ScopeAgent(Agent):
    def _get_system_prompt(self):
        return "Use the file tools."

    def _register_tools(self):
        @tool
        def read_file(file_path: str) -> dict:
            """Read a file."""
            return {
                "status": "success",
                "file_path": file_path,
                "content": Path(file_path).read_text(),
            }

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            Path(file_path).write_text(content)
            return {"status": "success", "file_path": file_path}

        @tool
        def save_inventory(file_path: str) -> dict:
            """Save an inventory; always rejected here."""
            self.save_attempts.append(file_path)
            return {"status": "error", "error": "Extract every source first"}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = ScopeAgent(silent_mode=True, skip_lemonade=True)
    agent.save_attempts = []
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def call(name, **args):
    return {"tool": name, "tool_args": args}


def script(agent, *turns):
    sent = []

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert turns_left, "unexpected model call"
        return MagicMock(text=json.dumps(turns_left.pop(0)), stats={})

    turns_left = list(turns)
    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return sent


def tool_results(sent):
    return [m["content"] for m in sent[-1] if m.get("role") == "tool"]


def test_real_loop_stops_off_request_work_after_the_answer(agent, tmp_path):
    (tmp_path / "other.txt").write_text("SENTINEL-OTHER")
    (tmp_path / "third.txt").write_text("SENTINEL-THIRD")
    sent = script(
        agent,
        # Answers without the requested save, so the completion check re-prompts.
        {"answer": "The fix is done."},
        call("read_file", file_path="other.txt"),
        call("read_file", file_path="third.txt"),
        {"answer": "Fixed. I did not save summary.md."},
    )
    result = agent.process_query("Save a summary of the fix to `summary.md`")
    assert "[check:completion]" in sent[1][-1]["content"]
    refusals = [r for r in tool_results(sent) if "already answered" in str(r)]
    assert len(refusals) == 2
    assert not any("SENTINEL" in str(r) for r in tool_results(sent))
    # The closing call withholds tools and asks for the original answer.
    assert "started work it did not ask for" in sent[-1][-1]["content"]
    assert result["result"].startswith("Fixed. I did not save summary.md.")
    assert len(sent) == 4


def test_real_loop_ends_when_one_tool_keeps_failing_with_new_arguments(agent):
    script(
        agent,
        *[call("save_inventory", file_path=f"inv{n}.md") for n in range(6)],
    )
    result = agent.process_query("Make an inventory of the notes")
    assert agent.save_attempts == [f"inv{n}.md" for n in range(4)]
    assert "save_inventory" in result["result"]
    assert "Extract every source first" in result["result"]


def test_different_programs_through_one_shell_are_different_walls(guard):
    """`python` refused, `ls` denied, `pip` refused: exploring, not a loop."""
    refused = {"status": "error", "error": "not allowed"}
    for command in (
        'python -c "import x"',
        "cd repo && ls -la /elsewhere",
        "pip show x 2>&1 | head",
        r"cd /d C:\work && sed -n 1,5p f",
    ):
        assert guard.check("run_shell_command", {"command": command}) is None
        guard.record("run_shell_command", {"command": command}, refused)
    assert guard.check("run_shell_command", {"command": "git status"}) is None
    assert not guard.turn_should_end


def test_one_program_refused_with_new_arguments_still_stops(guard):
    refused = {"status": "error", "error": "'python -c' is not allowed"}
    for n in range(4):
        command = rf'cd C:\repo && python -c "print({n})"'
        assert guard.check("run_shell_command", {"command": command}) is None
        guard.record("run_shell_command", {"command": command}, refused)
    blocked = guard.check("run_shell_command", {"command": "python -c 'x'"})
    assert blocked is not None and "run_shell_command (python)" in blocked["error"]
    # Another program through the same shell is not held up by python's streak.
    assert guard.check("run_shell_command", {"command": "git status"}) is None
    guard.check("run_shell_command", {"command": r'C:\Py\python.exe -c "y"'})
    assert guard.turn_should_end and guard.end_key == "run_shell_command (python)"


def test_work_a_check_asks_for_after_the_answer_is_not_refused(agent, tmp_path):
    """verify-after-change asks for a test run; finding the tests is that work."""
    (tmp_path / ".git").mkdir()  # a repository, so the check finds the project
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_dates.py").write_text("def test_x():\n    pass\n")
    (tmp_path / "tests" / "conftest.py").write_text("SENTINEL-CONFTEST")
    sent = script(
        agent,
        call("write_file", file_path="dates.py", content="X = 1\n"),
        {"answer": "Changed dates.py."},
        call("read_file", file_path=str(tmp_path / "tests" / "conftest.py")),
        {"answer": "Changed dates.py; the tests could not run here."},
    )
    agent.process_query("Fix the lowercase z bug in dates.py.")
    checks = [
        m["content"]
        for m in sent[-1]
        if m.get("role") == "user" and str(m["content"]).startswith("[check:")
    ]
    assert checks and checks[0].startswith("[check:verify-after-change]")
    assert "SENTINEL-CONFTEST" in str(tool_results(sent)[-1])


def test_a_check_naming_a_file_adds_only_that_file(guard, tmp_path):
    guard.mark_answered()
    report = str(tmp_path / "out" / "weekly.csv")
    assert guard.check("write_file", {"file_path": report}) is not None
    guard.widen(
        f"[check:completion] The request asked for `{report}`; it was not saved."
    )
    assert guard.check("write_file", {"file_path": report}) is None
    assert guard.check("read_file", {"file_path": "toybox/cli.py"}) is not None
