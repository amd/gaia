# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``run_python`` given a saved script is the file tool's call to every rule.

A model that could only pass code typed a saved script out again for each run.
Running it by path must not open a second, less-checked route to the same
action: the call is rewritten to ``execute_python_file`` before any check.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.credential_reads import as_script_call
from gaia.agents.base.denied_effects import DeniedEffects, effects_of_call
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.shell_tools import _segment_env


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "project"
    root.mkdir(parents=True)
    (root / "secrets.json").write_text("{}\n")
    (root / "data.csv").write_text("a,b\n1,2\n")
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    monkeypatch.chdir(root)
    return root


class _PythonAgent(Agent):
    """A base agent whose Python tools only record that they ran."""

    ran: list = []

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        ran = self.ran

        @tool
        def run_python(code: str = "", path: str = "", args: str = "") -> dict:
            """Run a Python snippet."""
            ran.append(("snippet", code, path))
            return {"status": "success", "stdout": ""}

        @tool
        def execute_python_file(file_path: str, args: str = "") -> dict:
            """Run a Python file."""
            ran.append(("file", file_path, args))
            return {"status": "success", "stdout": ""}


@pytest.fixture
def agent(project):  # pylint: disable=unused-argument
    saved = dict(_TOOL_REGISTRY)
    _PythonAgent.ran = []
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _PythonAgent(silent_mode=True, skip_lemonade=True)
    built.console.auto_approve_gated_tools = True
    try:
        yield built
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_a_path_makes_it_the_file_tools_call():
    assert as_script_call(
        "run_python", {"path": "job.py", "args": "-v", "timeout": 60}
    ) == ("execute_python_file", {"file_path": "job.py", "args": "-v", "timeout": 60})


def test_an_empty_code_beside_the_path_is_no_code():
    assert as_script_call("run_python", {"code": " ", "path": "job.py"}) == (
        "execute_python_file",
        {"file_path": "job.py"},
    )


@pytest.mark.parametrize(
    "name, args",
    [
        ("run_python", {"code": "print(1)"}),
        ("run_python", {"code": "print(1)", "path": "job.py"}),
        ("run_python", {"path": "  "}),
        ("run_python", None),
        ("read_file", {"path": "job.py"}),
        ("execute_python_file", {"file_path": "job.py"}),
    ],
)
def test_any_other_call_is_left_as_it_is(name, args):
    assert as_script_call(name, args) == (name, args)


@pytest.mark.parametrize("entry", ["_execute_tool", "_execute_tool_timed"])
def test_a_saved_script_runs_through_the_file_tool(agent, project, entry):
    script = project / "load.py"
    script.write_text("print(open('data.csv').read())\n")

    result = getattr(agent, entry)("run_python", {"path": str(script), "args": "-v"})

    assert result.get("status") == "success", result
    assert agent.ran == [("file", str(script), "-v")]


@pytest.mark.parametrize("entry", ["_execute_tool", "_execute_tool_timed"])
def test_a_script_that_reads_a_credential_file_is_refused_by_path(
    agent, project, entry
):
    script = project / "leak.py"
    script.write_text("print(open('secrets.json').read())\n")

    result = getattr(agent, entry)("run_python", {"path": str(script)})

    assert result["executed"] is False, result
    assert "credentials" in result["error"]
    assert agent.ran == []


def test_a_snippet_still_runs_as_a_snippet(agent):
    result = agent._execute_tool("run_python", {"code": "print(6 * 7)"})

    assert result.get("status") == "success", result
    assert agent.ran == [("snippet", "print(6 * 7)", "")]


def test_code_and_path_together_are_left_for_the_tool_to_refuse(agent, project):
    script = project / "load.py"
    script.write_text("print(1)\n")

    agent._execute_tool("run_python", {"code": "print(2)", "path": str(script)})

    assert agent.ran == [("snippet", "print(2)", str(script))]


def test_where_running_files_is_withheld_a_path_is_refused_and_says_why(agent, project):
    script = project / "load.py"
    script.write_text("print(1)\n")
    agent.deny_tools(["execute_python_file"])

    for entry in ("_execute_tool", "_execute_tool_timed"):
        result = getattr(agent, entry)("run_python", {"path": str(script)})

        assert result["executed"] is False, result
        assert "execute_python_file" in result["error"]
        assert "`code`" in result["error"]
    assert agent.ran == []


class _SnippetOnlyAgent(Agent):
    """An agent that has run_python and no execute_python_file at all."""

    ran: list = []

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        ran = self.ran

        @tool
        def run_python(code: str = "", path: str = "", args: str = "") -> dict:
            """Run a Python snippet."""
            ran.append(("snippet", code, path))
            return {"status": "success", "stdout": ""}


@pytest.mark.parametrize("approve", [False, True])
@pytest.mark.parametrize("entry", ["_execute_tool", "_execute_tool_timed"])
def test_with_no_file_tool_a_path_meets_the_approval_gate(project, entry, approve):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.pop("execute_python_file", None)
    _SnippetOnlyAgent.ran = []
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            built = _SnippetOnlyAgent(silent_mode=True, skip_lemonade=True)
        built._tools_registry.pop("execute_python_file", None)
        asked = []
        built.console.auto_approve_gated_tools = False
        built.console.confirm_tool_execution = lambda name, args: (
            asked.append(name) or approve
        )
        script = project / "load.py"
        script.write_text("print(1)\n")

        result = getattr(built, entry)("run_python", {"path": str(script)})

        assert asked == ["run_python"]
        if approve:
            assert built.ran == [("snippet", "", str(script))]
        else:
            assert result.get("status") == "denied", result
            assert built.ran == []
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_a_denial_of_the_script_covers_it_by_either_route(project):
    script = project / "cleanup.py"
    script.write_text("print(1)\n")
    by_path = {"path": str(script)}
    by_file = {"file_path": str(script)}

    assert effects_of_call("run_python", by_path, str(project)) == effects_of_call(
        "execute_python_file", by_file, str(project)
    )
    for denied, retried in (
        (("run_python", by_path), ("execute_python_file", by_file)),
        (("execute_python_file", by_file), ("run_python", by_path)),
    ):
        ledger = DeniedEffects(str(project))
        ledger.record(*denied, "the user said no")
        assert ledger.conflict(*retried) is not None


def test_a_python_the_shell_starts_reads_and_prints_utf8(monkeypatch):
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    env = _segment_env({})

    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_an_encoding_the_user_or_the_command_set_wins(monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "latin-1")

    assert _segment_env({})["PYTHONIOENCODING"] == "latin-1"
    assert _segment_env({"PYTHONUTF8": "0"})["PYTHONUTF8"] == "0"
