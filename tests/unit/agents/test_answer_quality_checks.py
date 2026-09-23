# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Answer-seam checks that compare the answer to the turn's tool record.

Driven through the real ``process_query`` loop with a scripted model: what
matters is what the loop did — whether it re-prompted, how often, and whether
the correction is visible in the returned conversation.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import (
    VERIFY_AFTER_CHANGE_TAG,
    has_test_run_summary,
    project_has_tests,
    unverified_change,
)


class _Reply:
    def __init__(self, text):
        self.text = text
        self.stats = {}


class _ScriptedAgent(Agent):
    """Agent whose model says exactly what the script says, in order."""

    #: Canned output per ``run_python`` / ``run_shell_command`` call, in order.
    run_outputs: list = []

    def _get_system_prompt(self) -> str:
        return "test"

    def _create_console(self):
        return SilentConsole(auto_approve_gated_tools=True)

    def _register_tools(self) -> None:
        agent = self

        @tool
        def write_file(path: str, content: str) -> dict:
            """Write *content* to *path*."""
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            return {"status": "success", "path": path}

        @tool
        def edit_file(file_path: str, old: str, new: str) -> dict:
            """Replace *old* with *new* in *file_path*."""
            with open(file_path, encoding="utf-8") as fh:
                text = fh.read()
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write(text.replace(old, new))
            return {"status": "success", "file_path": file_path}

        @tool
        def read_file(path: str) -> dict:
            """Read *path*."""
            with open(path, encoding="utf-8") as fh:
                return {"status": "success", "content": fh.read()}

        @tool
        def run_python(code: str) -> dict:
            """Run Python *code*."""
            return {
                "status": "success",
                "stdout": agent.run_outputs.pop(0),
                "stderr": "",
                "return_code": 0,
            }

        @tool
        def run_shell_command(command: str) -> dict:
            """Run a shell *command*."""
            return {
                "status": "success",
                "stdout": agent.run_outputs.pop(0),
                "stderr": "",
                "return_code": 0,
            }


def _answer(text):
    return json.dumps({"answer": text})


def _call(name, **args):
    return json.dumps({"tool": name, "tool_args": args})


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A Python project with a test suite, as the working directory."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text("def test_x():\n    pass\n")
    (tmp_path / "mod.py").write_text("def area(r):\n    return 3 * r * r\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GAIA_PROJECT_ROOT", raising=False)
    return tmp_path


@pytest.fixture
def scripted():
    def build(script, run_outputs=()):
        with patch("gaia.agents.base.agent.AgentSDK") as sdk:
            chat = MagicMock()
            chat.send_messages.side_effect = [_Reply(s) for s in script]
            chat.get_stats.return_value = {}
            sdk.return_value = chat
            agent = _ScriptedAgent(silent_mode=True, skip_lemonade=True)
            agent.chat = chat
            agent.streaming = False
            agent.run_outputs = list(run_outputs)
            return agent, chat

    return build


def _tagged(result, tag):
    return [
        entry["content"]
        for entry in result["conversation"]
        if entry.get("role") == "user"
        and isinstance(entry.get("content"), str)
        and entry["content"].startswith(tag)
    ]


_EDIT = _call("edit_file", file_path="mod.py", old="3 *", new="3.14159 *")


class TestVerifyAfterChangeFires:
    def test_edit_then_no_tests(self, project, scripted):
        agent, chat = scripted([_EDIT, _answer("Fixed."), _answer("Fixed.")])
        result = agent.process_query("Use a better value of pi in area().")
        assert chat.send_messages.call_count == 3
        [message] = _tagged(result, VERIFY_AFTER_CHANGE_TAG)
        assert "mod.py" in message and "unverified" in message

    def test_edit_tests_edit_without_rerun(self, project, scripted):
        agent, chat = scripted(
            [
                _EDIT,
                _call("run_shell_command", command="python -m pytest -q"),
                _call("edit_file", file_path="mod.py", old="r * r", new="r**2"),
                _answer("Done, tests pass."),
                _answer("Done."),
            ],
            run_outputs=["1 passed in 0.01s"],
        )
        result = agent.process_query("Tidy area().")
        assert len(_tagged(result, VERIFY_AFTER_CHANGE_TAG)) == 1

    def test_model_complies_and_turn_ends(self, project, scripted):
        agent, chat = scripted(
            [
                _call("write_file", path="helpers.py", content="X = 1\n"),
                _answer("Added helpers.py."),
                _call("run_python", code="import pytest; pytest.main(['-q'])"),
                _answer("Added helpers.py; the suite printed 1 passed."),
            ],
            run_outputs=["1 passed in 0.02s"],
        )
        result = agent.process_query("Add a helpers module.")
        assert chat.send_messages.call_count == 4
        assert len(_tagged(result, VERIFY_AFTER_CHANGE_TAG)) == 1
        assert "1 passed" in result["result"]

    def test_a_hand_rolled_check_is_not_a_test_run(self, project, scripted):
        """A benchmark run: the fix was checked with two prints, the suite never
        ran, and a test that was already failing went unreported."""
        agent, chat = scripted(
            [
                _EDIT,
                _call("run_python", code="from mod import area; print(area(1))"),
                _answer("Fixed; area(1) now prints 3.14159."),
                _answer("Fixed."),
            ],
            run_outputs=["3.14159\n"],
        )
        result = agent.process_query("Use a better value of pi in area().")
        assert len(_tagged(result, VERIFY_AFTER_CHANGE_TAG)) == 1

    def test_reprompts_at_most_once_and_terminates(self, project, scripted):
        agent, chat = scripted(
            [_EDIT] + [_answer("Done.")] * 2,
        )
        result = agent.process_query("Fix area().")
        assert chat.send_messages.call_count == 3
        assert len(_tagged(result, VERIFY_AFTER_CHANGE_TAG)) == 1
        assert result["result"].startswith("Done.")


class TestVerifyAfterChangeSilent:
    @pytest.mark.parametrize(
        "check",
        [
            (_call("run_shell_command", command="pytest -q"), "3 passed in 0.1s"),
            # No timing, so not a labelled check — recognised by its output.
            (_call("run_python", code="import pytest; pytest.main()"), "2 passed"),
            (
                _call("run_python", code="import unittest; ..."),
                "Ran 4 tests in 0.003s\n\nOK",
            ),
            (_call("run_shell_command", command="make check"), "1 failed, 2 passed"),
        ],
    )
    def test_check_after_last_edit(self, project, scripted, check):
        call, output = check
        agent, chat = scripted([_EDIT, call, _answer("Fixed.")], run_outputs=[output])
        result = agent.process_query("Fix area().")
        assert chat.send_messages.call_count == 3
        assert not _tagged(result, VERIFY_AFTER_CHANGE_TAG)

    def test_read_only_turn(self, project, scripted):
        agent, chat = scripted(
            [_call("read_file", path="mod.py"), _answer("area() returns 3*r*r.")]
        )
        result = agent.process_query("What does area() do?")
        assert chat.send_messages.call_count == 2
        assert not _tagged(result, VERIFY_AFTER_CHANGE_TAG)

    def test_project_without_tests(self, tmp_path, monkeypatch, scripted):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
        (tmp_path / "mod.py").write_text("def area(r):\n    return 3 * r * r\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GAIA_PROJECT_ROOT", raising=False)
        agent, chat = scripted([_EDIT, _answer("Fixed.")])
        result = agent.process_query("Fix area().")
        assert chat.send_messages.call_count == 2
        assert not _tagged(result, VERIFY_AFTER_CHANGE_TAG)

    def test_writing_a_report_is_not_a_code_change(self, project, scripted):
        agent, chat = scripted(
            [
                _call("write_file", path="notes.md", content="# Notes\n"),
                _call("read_file", path="notes.md"),
                _answer("Ok."),
            ]
        )
        result = agent.process_query("Jot some notes down.")
        assert chat.send_messages.call_count == 3
        assert not _tagged(result, VERIFY_AFTER_CHANGE_TAG)


class TestVerifyAfterChangeHelpers:
    @pytest.mark.parametrize(
        "text",
        [
            "3 passed",
            "1 failed, 2 passed in 0.31s",
            "===== 5 passed, 1 skipped in 2.04s =====",
            "no tests ran in 0.01s",
            "....\nRan 12 tests in 0.020s\n\nOK",
        ],
    )
    def test_test_summaries_recognised(self, text):
        assert has_test_run_summary(text)

    @pytest.mark.parametrize(
        "text",
        ["3 failed attempts to connect", "passed: 3", "All good", "Ran the tests"],
    )
    def test_non_summaries_ignored(self, text):
        assert not has_test_run_summary(text)

    def test_non_file_create_tool_is_not_a_change(self, project):
        executions = [
            {"tool": "create_event", "args": {"title": "x"}, "ran": True},
        ]
        assert unverified_change(executions, str(project)) is None

    def test_refused_write_is_not_a_change(self, project):
        executions = [
            {"tool": "write_file", "args": {"path": "a.py"}, "ran": False},
        ]
        assert unverified_change(executions, str(project)) is None

    def test_write_outside_project_is_not_a_change(self, project, tmp_path_factory):
        outside = tmp_path_factory.mktemp("elsewhere") / "scratch.py"
        executions = [{"tool": "write_file", "args": {"path": str(outside)}}]
        assert unverified_change(executions, str(project)) is None

    def test_pytest_config_counts_as_a_suite(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        assert project_has_tests(str(tmp_path))

    def test_no_root_means_no_suite(self):
        assert not project_has_tests(None)
