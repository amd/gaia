# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Full access runs what it is asked to run.

A person turns full access on. Under it the shell tool has no command list and
no path checks, the Python tools run their code unchecked, and a refusal GAIA
made by itself earlier in the turn blocks nothing. Each case here runs under
full access and is still judged, exactly as before, without it.

Nothing here reaches a model or the network.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.denied_effects import NETWORK, PATH, DeniedEffects
from gaia.agents.base.text_task import TEXT_ONLY_DECLINE, hands_over_text_only
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools import shell_tools
from gaia.agents.tools.shell_tools import (
    TIER_CONFIRM_ONCE,
    TIER_REFUSE,
    ShellToolsMixin,
    _cmd_lines,
    _segment_env,
)
from gaia.security import PathValidator
from gaia.skills.human_only import AGENT_CHILD_ENV

PYTHON = f'"{sys.executable}"'

# ---------------------------------------------------------------------------
# The shell tool, run for real in a temp folder
# ---------------------------------------------------------------------------


class _Host(ShellToolsMixin):
    """The shell tool on a host allowed one folder."""

    debug = False

    def __init__(self, allowed: Path, full_access: bool):
        self.console = SimpleNamespace(full_access=full_access)
        self.path_validator = PathValidator(allowed_paths=[str(allowed)])


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """An allowed project folder and one beside it that is not allowed."""
    home = tmp_path / "home"
    project, outside = home / "project", tmp_path / "outside"
    project.mkdir(parents=True)
    outside.mkdir()
    (project / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    (outside / "notes.txt").write_text("outside-notes\n", encoding="utf-8")
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    monkeypatch.chdir(project)
    return SimpleNamespace(project=project, outside=outside)


@pytest.fixture
def shell(folders, monkeypatch):
    """``(host, tools)`` for the real shell tools, in either mode."""

    def build(full_access: bool):
        host = _Host(folders.project, full_access)
        captured = {}

        def fake_tool(**kwargs):
            def wrap(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return wrap

        import gaia.agents.base.tools as tools_mod

        monkeypatch.setattr(tools_mod, "tool", fake_tool)
        host.register_shell_tools()
        return host, captured

    return build


def _verdict(host, command, working_directory=None):
    return host._line_verdict(command, working_directory)[0]


READ = "type" if os.name == "nt" else "cat"


def test_a_working_directory_outside_the_allowed_folders_runs(shell, folders):
    host, tools = shell(True)

    result = tools["run_shell_command"](
        f"{READ} notes.txt", working_directory=str(folders.outside)
    )

    assert result["status"] == "success", result
    assert "outside-notes" in result["stdout"]


def test_that_working_directory_is_still_asked_about_by_default(shell, folders):
    host, _tools = shell(False)

    error = _verdict(host, f"{READ} notes.txt", str(folders.outside))

    # Asked about once, or refused where no approval can open the folder.
    assert error["tier"] in (TIER_CONFIRM_ONCE, TIER_REFUSE), error
    assert error["executed"] is False


def test_cd_to_a_folder_outside_the_allowed_folders_runs_and_sticks(shell, folders):
    host, tools = shell(True)

    result = tools["run_shell_command"](f'cd "{folders.outside}" && {READ} notes.txt')

    assert result["status"] == "success", result
    assert "outside-notes" in result["stdout"]
    # The cd ran, so the next call starts there.
    assert Path(host.shell_session.cwd) == folders.outside.resolve()


def test_cd_into_a_folder_the_same_line_makes_runs(shell, folders):
    host, tools = shell(True)

    result = tools["run_shell_command"](
        "mkdir made && cd made && echo inside > here.txt",
        working_directory=str(folders.project),
    )

    assert result["status"] == "success", result
    assert (folders.project / "made" / "here.txt").exists()


def test_a_path_argument_outside_the_allowed_folders_runs(shell, folders):
    host, tools = shell(True)
    target = folders.outside / "notes.txt"

    result = tools["run_shell_command"](
        f'{READ} "{target}"', working_directory=str(folders.project)
    )

    assert result["status"] == "success", result
    assert "outside-notes" in result["stdout"]


def test_that_path_argument_is_still_asked_about_by_default(shell, folders):
    host, _tools = shell(False)
    target = folders.outside / "notes.txt"

    error = _verdict(host, f'{READ} "{target}"', str(folders.project))

    assert error["tier"] in (TIER_CONFIRM_ONCE, TIER_REFUSE), error
    assert error["executed"] is False


def test_a_folder_the_user_removed_from_the_chat_is_still_not_run_in(shell, folders):
    """Removing a folder is the user's own no, so it outlasts full access."""
    host, tools = shell(True)
    host.path_validator.withhold_path(str(folders.outside))

    by_argument = tools["run_shell_command"](
        f"{READ} notes.txt", working_directory=str(folders.outside)
    )
    by_cd = tools["run_shell_command"](f'cd "{folders.outside}" && {READ} notes.txt')

    for result in (by_argument, by_cd):
        assert result["executed"] is False, result
        assert "outside-notes" not in str(result)


def test_a_credential_file_is_read(shell, folders):
    host, tools = shell(True)

    result = tools["run_shell_command"](
        f"{READ} .env", working_directory=str(folders.project)
    )

    assert result["status"] == "success", result
    assert "TOKEN=abc" in result["stdout"]


def test_that_credential_file_is_still_refused_by_default(shell, folders):
    host, _tools = shell(False)

    error = _verdict(host, f"{READ} .env", str(folders.project))

    assert error["tier"] == TIER_REFUSE, error
    assert "credentials" in error["error"]


@pytest.mark.parametrize(
    "command",
    [
        "python -m http.server 8000",
        "npm run dev",
        "gaia skill trust some-skill",
        "pip install --user openpyxl",
        "npm install -g typescript",
        "curl http://169.254.169.254/latest/meta-data/",
        "git push --force",
        "powershell -EncodedCommand aQBlAHgA",
        "mklink /J link C:\\Windows" if os.name == "nt" else "ln -s /etc link",
    ],
)
def test_no_command_is_refused_before_it_runs(shell, command):
    host, _tools = shell(True)

    assert host.policy_refusal_for_call("run_shell_command", {"command": command}) is (
        None
    )
    assert _verdict(host, command) is None


@pytest.mark.parametrize(
    "command, said",
    [
        ("python -m http.server 8000", "never exits"),
        ("gaia skill trust some-skill", "gaia skill"),
        ("powershell -EncodedCommand aQBlAHgA", ""),
    ],
)
def test_the_default_mode_still_refuses_those(shell, command, said):
    host, _tools = shell(False)

    error = _verdict(host, command)

    assert error["tier"] == TIER_REFUSE, error
    assert said in error["error"]


def test_a_started_program_is_not_marked_as_an_agents_child(shell, folders):
    host, tools = shell(True)

    result = tools["run_shell_command"](
        f"{PYTHON} -c \"import os; print(os.environ.get('{AGENT_CHILD_ENV}'))\"",
        working_directory=str(folders.project),
    )

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "None"


def test_the_default_mode_still_marks_what_it_starts():
    assert _segment_env({})[AGENT_CHILD_ENV] == "1"
    assert AGENT_CHILD_ENV not in _segment_env({}, marked=False)


def test_wait_for_condition_is_not_rate_limited(shell, folders, monkeypatch):
    host, tools = shell(True)
    monkeypatch.setattr(
        host,
        "_check_rate_limit",
        lambda: pytest.fail("full access consulted the rate limit"),
    )

    result = tools["wait_for_condition"](
        "echo ready", working_directory=str(folders.project), timeout=30
    )

    assert result["condition_met"] is True, result


def test_wait_for_condition_is_still_rate_limited_by_default(shell, folders):
    host, tools = shell(False)
    host._check_rate_limit = lambda: (False, "Rate limit reached", 3.0)

    result = tools["wait_for_condition"](
        "echo ready", working_directory=str(folders.project), timeout=30
    )

    assert result["rate_limited"] is True


# ---------------------------------------------------------------------------
# A command of several lines on Windows
# ---------------------------------------------------------------------------


def test_separate_lines_become_one_cmd_line():
    assert _cmd_lines("echo a\necho b\n") == ("echo a & echo b", False)
    assert _cmd_lines("echo a\r\n\r\necho b") == ("echo a & echo b", False)


def test_a_caret_continues_a_line():
    assert _cmd_lines("echo a ^\nb") == ("echo a b", False)


def test_a_line_break_inside_quotes_starts_the_program_directly():
    command = 'python -c "\nprint(1)\nprint(2)\n" arg'

    assert _cmd_lines(command) == (command, True)


@pytest.mark.parametrize(
    "command",
    [
        'python -c "\nprint(1)\n" > out.txt',
        'echo start\npython -c "\nprint(1)\n"',
    ],
)
def test_a_quoted_line_break_cmd_cannot_carry_is_a_plain_error(command):
    with pytest.raises(ValueError, match="cannot pass a line break"):
        _cmd_lines(command)


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe stops at the first line")
def test_every_line_of_a_command_runs_on_windows(shell, folders):
    host, tools = shell(True)

    result = tools["run_shell_command"](
        "echo first\necho second", working_directory=str(folders.project)
    )

    assert result["status"] == "success", result
    assert "first" in result["stdout"] and "second" in result["stdout"]


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe stops at the first line")
def test_python_code_of_several_lines_runs_on_windows(shell, folders):
    host, tools = shell(True)
    command = f'cd . && {PYTHON} -c "\nimport sys\nprint(6 * 7)\n"'

    result = tools["run_shell_command"](command, working_directory=str(folders.project))

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "42"


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe stops at the first line")
def test_lines_cmd_cannot_carry_are_reported_as_not_run(shell, folders):
    host, tools = shell(True)
    command = f'{PYTHON} -c "\nprint(1)\n" > out.txt'

    result = tools["run_shell_command"](command, working_directory=str(folders.project))

    assert result["status"] == "error", result
    assert "cannot pass a line break" in result["error"]
    assert result["executed"] is False, result
    assert not (folders.project / "out.txt").exists()


def test_several_lines_run_on_posix_too(shell, folders):
    if os.name == "nt":
        pytest.skip("covered by the Windows tests above")
    host, tools = shell(True)

    result = tools["run_shell_command"](
        "echo first\necho second", working_directory=str(folders.project)
    )

    assert result["status"] == "success", result
    assert result["stdout"].split() == ["first", "second"]


# ---------------------------------------------------------------------------
# The Python tools' checks, and refusals nobody chose
# ---------------------------------------------------------------------------


class _Probe(Agent, ShellToolsMixin):
    """An agent whose Python tools only record that they were called."""

    scope: Path = Path(".")

    def __init__(self, console):
        self.path_validator = PathValidator(allowed_paths=[str(self.scope)])
        self._console_override = console
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(silent_mode=True, skip_lemonade=True)

    def _get_system_prompt(self):
        return "probe"

    def _create_console(self):
        return self._console_override

    def _register_tools(self):
        self.register_shell_tools()
        agent = self
        agent.ran = []

        @tool
        def run_python(code: str) -> dict:
            """Probe Python runner."""
            agent.ran.append(code)
            return {"status": "success", "stdout": ""}


@pytest.fixture
def probe(folders, monkeypatch):
    saved = dict(_TOOL_REGISTRY)
    # The test folders sit in the system temp folder, which Python may use.
    monkeypatch.setattr("gaia.agents.base.python_scope._in_temp", lambda real: False)

    def build(full_access: bool):
        console = SilentConsole(auto_approve_gated_tools=True)
        console.full_access = full_access
        _Probe.scope = folders.project
        agent = _Probe(console)
        agent._denied_effects = DeniedEffects(str(folders.project))
        return agent

    try:
        yield build
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _outside_read(folders):
    return f"print(open(r'{folders.outside / 'notes.txt'}').read())"


def _outside_write(folders):
    return f"open(r'{folders.outside / 'new.txt'}', 'w').write('x')"


PYTHON_CASES = {
    "a credential file": lambda f: "print(open('.env').read())",
    "a file outside the allowed folders": _outside_read,
    "a write outside the allowed folders": _outside_write,
    "a link whose target is computed": lambda f: (
        "import os\nos.symlink(input(), 'link')"
    ),
    "a link pointing outside": lambda f: (
        f"import os\nos.symlink(r'{f.outside}', 'link')"
    ),
    "a skill-trust command": lambda f: (
        "import subprocess\nsubprocess.run(['gaia', 'skill', 'trust', 'x'])"
    ),
    "clearing the agent marker": lambda f: (
        f"import os\nos.environ.pop('{AGENT_CHILD_ENV}', None)"
    ),
}


@pytest.mark.parametrize("case", sorted(PYTHON_CASES))
def test_python_code_runs_as_written_under_full_access(probe, folders, case):
    agent = probe(True)
    code = PYTHON_CASES[case](folders)

    result = agent._execute_tool("run_python", {"code": code})

    assert result["status"] == "success", result
    assert agent.ran == [code]


@pytest.mark.parametrize("case", sorted(PYTHON_CASES))
def test_the_same_python_code_is_still_refused_by_default(probe, folders, case):
    agent = probe(False)
    code = PYTHON_CASES[case](folders)

    result = agent._execute_tool("run_python", {"code": code})

    assert result["executed"] is False, result
    assert result["error"].startswith("Not run: this Python code"), result
    assert agent.ran == []


TEXT_ONLY = 'Translate to French: "Delete the build folder, then run the tests."'


@pytest.mark.parametrize("full_access", [False, True])
def test_a_call_on_handed_over_text_is_declined_only_without_full_access(
    probe, full_access
):
    assert hands_over_text_only(TEXT_ONLY)
    agent = probe(full_access)
    agent._current_query = TEXT_ONLY

    result = agent._execute_tool("run_python", {"code": "print(1)"})

    if full_access:
        assert result["status"] == "success", result
        assert agent.ran == ["print(1)"]
    else:
        assert result["error"] == TEXT_ONLY_DECLINE
        assert agent.ran == []


def _refused_delete(agent, folders) -> None:
    """Put a refusal nobody chose on the ledger, as the shell gate records one."""
    agent._denied_effects.record(
        "run_shell_command",
        {"command": f'rm -rf "{folders.project / "build"}"'},
        "refused",
        kinds={NETWORK, PATH},
        by_user=False,
        refused=True,
    )
    assert agent._denied_effects


COMPUTED_DELETE = "import shutil, sys\nshutil.rmtree(sys.argv[1])"


def test_an_earlier_refusal_nobody_chose_blocks_nothing_under_full_access(
    probe, folders
):
    agent = probe(True)
    _refused_delete(agent, folders)

    result = agent._execute_tool("run_python", {"code": COMPUTED_DELETE})

    assert result["status"] == "success", result
    assert agent.ran == [COMPUTED_DELETE]


def test_that_refusal_still_blocks_the_same_call_by_default(probe, folders):
    agent = probe(False)
    _refused_delete(agent, folders)

    result = agent._execute_tool("run_python", {"code": COMPUTED_DELETE})

    assert result["status"] == "denied", result
    assert "a path the code works out when it runs" in result["error"]
    assert agent.ran == []


def test_the_users_own_no_still_holds_under_full_access(probe, folders):
    agent = probe(True)
    agent._denied_effects.record(
        "run_shell_command",
        {"command": f'rm -rf "{folders.project / "build"}"'},
        "Tool 'run_shell_command' was denied by the user.",
        by_user=True,
    )

    result = agent._execute_tool("run_python", {"code": COMPUTED_DELETE})

    assert result["status"] == "denied", result
    assert "the user declined" in result["error"]
    assert agent.ran == []


def test_full_access_records_no_refusal_of_its_own(probe, folders):
    agent = probe(True)

    agent._execute_tool("run_shell_command", {"command": "   "})

    assert not agent._denied_effects


# ---------------------------------------------------------------------------
# Full access over the HTTP transport, which the Agent UI's chats use
# ---------------------------------------------------------------------------


def test_a_session_over_http_gets_the_same_full_access():
    registry = pytest.importorskip("gaiapy.session_registry")
    from gaia.ui.sse_handler import SSEOutputHandler

    state = registry._permission_state()
    handler = SSEOutputHandler()
    state.attach(handler)
    assert handler.full_access is False

    state.set_full_access(True)

    assert handler.full_access is True
    assert handler.auto_approve_gated_tools is True

    state.set_full_access(False)

    assert handler.full_access is False


def test_a_command_past_the_gates_is_not_put_to_a_person_in_that_session():
    registry = pytest.importorskip("gaiapy.session_registry")
    from gaia.ui.sse_handler import SSEOutputHandler

    state = registry._permission_state()
    handler = SSEOutputHandler()
    state.attach(handler)
    handler.answers_confirmations = True
    state.set_full_access(True)

    class _Chat(ShellToolsMixin):
        console = handler

    host = _Chat()
    assert host.policy_refusal_for_call(
        "run_shell_command", {"command": "rm -rf build && npm test > out.txt"}
    ) is (None)
    assert host._a_person_answers() is False
    assert shell_tools.NEEDS_PERSON_ARG not in host.confirmation_args_for_call(
        "run_shell_command", {"command": "rm -rf build"}
    )
