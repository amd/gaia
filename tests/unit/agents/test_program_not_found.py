# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A program that is not installed is a step to take, not a refusal.

A user asked for ``git clone`` on a PC without git. The "not installed" answer
was recorded as a refusal of github.com for the rest of the request, so after
git was installed the clone was still blocked, and nothing re-read ``PATH`` so
git stayed "not installed" anyway. Drives the real ``Agent._execute_tool`` and
the real shell policy; nothing is installed or fetched.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia import program_lookup
from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.denied_effects import PROGRAM_NOT_FOUND, DeniedEffects
from gaia.agents.base.tools import tool
from gaia.agents.tools.shell_tools import NOT_APPROVABLE, ShellToolsMixin

REPO = "https://github.com/amd/gaia.git"
CLONE = f"git clone {REPO} gaia"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "GAIA_HOME"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


@pytest.fixture(name="installed")
def _installed(monkeypatch):
    """The programs on this PC: a set the test adds to."""
    programs = set()
    monkeypatch.setattr(
        program_lookup,
        "resolve",
        lambda name: f"/usr/bin/{name}" if name in programs else None,
    )
    return programs


class _Agent(Agent, ShellToolsMixin):
    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_shell_tools()
        agent = self
        agent.ran = []

        @tool
        def fetch_page(url: str) -> dict:
            """Probe page fetcher."""
            agent.ran.append(("fetch_page", url))
            return {"status": "success"}

    def _create_console(self):
        return self._console_override


def _agent(cwd, console=None):
    with patch("gaia.agents.base.agent.AgentSDK"):
        _Agent._console_override = console or SilentConsole()
        agent = _Agent(silent_mode=True, skip_lemonade=True)
    agent.max_commands_per_minute = 1000
    agent.max_commands_per_10_seconds = 1000
    agent._denied_effects = DeniedEffects(str(cwd))
    return agent


def _shell(agent, command):
    return agent._execute_tool("run_shell_command", {"command": command})


def test_a_missing_program_does_not_block_the_host_it_would_have_reached(
    _isolated, installed
):
    agent = _agent(_isolated)

    missing = _shell(agent, CLONE)
    page = agent._execute_tool("fetch_page", {"url": "https://github.com/amd/gaia"})

    assert missing[PROGRAM_NOT_FOUND] == "git"
    assert missing["executed"] is False
    assert not agent._denied_effects
    assert page["status"] == "success"


def test_a_missing_program_is_not_called_unapprovable(_isolated, installed):
    agent = _agent(_isolated)

    missing = _shell(agent, CLONE)

    assert "not installed" in missing["error"]
    assert NOT_APPROVABLE not in missing.get("hint", "")
    assert "Do not retry" not in missing.get("hint", "")
    assert "installed" in missing["hint"] and "again" in missing["hint"]


def test_the_same_command_is_judged_normally_once_the_program_is_there(
    _isolated, installed
):
    agent = _agent(_isolated)
    assert _shell(agent, CLONE)[PROGRAM_NOT_FOUND] == "git"

    installed.add("git")
    verdict = agent.policy_refusal_for_call("run_shell_command", {"command": CLONE})

    # No refusal before the prompt: the clone goes to the user for approval.
    assert verdict is None


def test_a_refresh_that_finds_the_program_lets_the_same_call_through(
    _isolated, installed, monkeypatch
):
    """The retry right after an install must not need a GAIA restart."""
    agent = _agent(_isolated)

    def install_lands(after_install=False):
        installed.add("git")
        return ["C:\\Program Files\\Git\\cmd"]

    monkeypatch.setattr(program_lookup, "refresh_path", install_lands)

    verdict = agent.policy_refusal_for_call("run_shell_command", {"command": CLONE})

    assert verdict is None


def test_a_refresh_that_changes_nothing_still_reports_the_program_missing(
    _isolated, installed
):
    agent = _agent(_isolated)

    verdict = agent.policy_refusal_for_call("run_shell_command", {"command": CLONE})

    assert verdict[PROGRAM_NOT_FOUND] == "git"


def test_a_hard_refusal_is_still_recorded_when_the_program_is_missing(
    _isolated, installed
):
    """`git push --force` is refused for what it does, installed or not."""
    agent = _agent(_isolated)

    refused = _shell(agent, f"git push --force {REPO} main")
    page = agent._execute_tool("fetch_page", {"url": "https://github.com/amd/gaia"})

    assert PROGRAM_NOT_FOUND not in refused
    assert refused["executed"] is False
    assert page["status"] == "denied"
    assert agent.ran == []


def test_a_real_refusal_on_the_same_line_wins_over_a_missing_program(
    _isolated, installed
):
    agent = _agent(_isolated)

    refused = _shell(agent, f"{CLONE} && git push --force {REPO} main")
    page = agent._execute_tool("fetch_page", {"url": "https://github.com/amd/gaia"})

    assert PROGRAM_NOT_FOUND not in refused
    assert page["status"] == "denied"


# ---------------------------------------------------------------------------
# A program GAIA has no rules for
# ---------------------------------------------------------------------------


class _Counting(SilentConsole):
    """Counts the approval prompts shown, and says yes to each."""

    def __init__(self):
        super().__init__()
        self.asked = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.asked.append(tool_args.get("command"))
        return True


def _refusal(agent, command):
    return agent.policy_refusal_for_call("run_shell_command", {"command": command})


def test_nobody_is_asked_to_approve_a_program_that_is_not_there(_isolated, installed):
    console = _Counting()
    agent = _agent(_isolated, console)

    missing = _shell(agent, "rg --files")

    assert missing[PROGRAM_NOT_FOUND] == "rg"
    assert missing["executed"] is False
    assert console.asked == []
    assert "check_cli_setup" not in missing["hint"]
    assert "Do not pick a package" in missing["hint"]


def test_a_program_the_user_installed_themselves_is_found_on_the_next_try(
    _isolated, installed, monkeypatch
):
    agent = _agent(_isolated)
    assert _refusal(agent, "rg --files")[PROGRAM_NOT_FOUND] == "rg"

    def install_lands(after_install=False):
        installed.add("rg")
        return ["C:\\Users\\me\\AppData\\Local\\Microsoft\\WinGet\\Links"]

    monkeypatch.setattr(program_lookup, "refresh_path", install_lands)

    # Not refused before the prompt: it goes to the user as any unlisted command.
    assert _refusal(agent, "rg --files") is None


@pytest.mark.parametrize(
    "command",
    [
        "mklink link target",  # a cmd built-in
        "source env.sh",  # a sh built-in
        "Get-ChildItem .",  # a PowerShell cmdlet
        "docker-compose up",  # hyphenated: cannot tell it from a cmdlet
        "./build.cmd",  # a path, not a bare name
    ],
)
def test_what_a_shell_might_still_run_is_never_called_missing(
    _isolated, installed, command
):
    agent = _agent(_isolated)

    verdict = _refusal(agent, command)

    assert verdict is None or not verdict.get(PROGRAM_NOT_FOUND)


def test_a_delete_through_a_missing_program_keeps_its_refusal_on_record(
    _isolated, installed
):
    """`rm` is not a program on Windows. "Not installed" must not replace the
    refusal of the delete, or Python could carry it out unasked."""
    (_isolated / "victim").mkdir()
    agent = _agent(_isolated, SilentConsole(auto_approve_gated_tools=True))

    refused = _shell(agent, "rm -rf victim")

    assert PROGRAM_NOT_FOUND not in refused
    assert refused["executed"] is False
    assert agent._denied_effects


def test_a_delete_after_a_missing_policed_program_stays_on_record(_isolated, installed):
    """`git clone x && rm -rf build` without git: "git is not installed" must
    not stand for the line, or the delete it also asked for goes unrecorded."""
    (_isolated / "build").mkdir()
    agent = _agent(_isolated, SilentConsole(auto_approve_gated_tools=True))

    refused = _shell(agent, f"{CLONE} && rm -rf build")

    assert PROGRAM_NOT_FOUND not in refused
    assert refused["executed"] is False
    assert agent._denied_effects


def test_a_line_with_more_than_one_command_is_left_to_the_prompt(_isolated, installed):
    """After `cd tools`, `mytool` may be a script in that folder."""
    agent = _agent(_isolated)

    for command in ("cd tools && mytool", "mytool | findstr x"):
        verdict = _refusal(agent, command)
        assert verdict is None or not verdict.get(PROGRAM_NOT_FOUND), command


def test_an_unattended_refusal_does_not_carry_the_internal_marker(_isolated, installed):
    installed.add("rg")
    agent = _agent(_isolated, SilentConsole(auto_approve_gated_tools=True))

    refused = _shell(agent, "rg --files")

    assert refused["executed"] is False
    assert "unlisted_program" not in refused


@pytest.mark.skipif(
    __import__("os").name != "nt", reason="cmd.exe runs a script from the folder"
)
def test_a_projects_own_script_is_not_called_missing(_isolated, installed):
    (_isolated / "build.cmd").write_text("@echo off\n")
    agent = _agent(_isolated)

    verdict = _refusal(agent, "build")

    assert verdict is None or not verdict.get(PROGRAM_NOT_FOUND)


def test_path_is_re_read_after_an_approved_package_manager_command(
    _isolated, installed, monkeypatch
):
    """winget puts what it installs on PATH for new processes only."""
    installed.add("winget")
    console = _Counting()
    agent = _agent(_isolated, console)
    events = []
    monkeypatch.setattr(
        agent,
        "_run_approved_once",
        lambda *_a, **_k: events.append("ran") or {"status": "success"},
    )
    monkeypatch.setattr(
        program_lookup,
        "refresh_path",
        lambda after_install=False: events.append(("refresh", after_install)) or [],
    )
    command = "winget install --id BurntSushi.ripgrep.MSVC --exact --source winget"

    result = _shell(agent, command)

    assert console.asked == [command]
    assert result["status"] == "success"
    assert events == ["ran", ("refresh", True)]


@pytest.mark.skipif(
    __import__("os").name != "nt", reason="winget and its exit codes are Windows'"
)
def test_a_failed_winget_run_carries_what_its_exit_code_means(_isolated):
    """The shell really runs a line that exits as winget did in the field."""
    from types import SimpleNamespace

    agent = _agent(_isolated)
    agent.shell_command_times = []
    steps = [SimpleNamespace(segments=[["winget", "install", "Git.Git"]])]

    outcome = agent._run_approved_once(
        "exit /b -1978335138", str(_isolated), steps, 30, "default", 0
    )

    # Windows hands the same code back signed or unsigned, depending on the caller.
    assert outcome["return_code"] & 0xFFFFFFFF == 0x8A15005E
    assert "certificate" in outcome["exit_code_meaning"]


@pytest.mark.skipif(
    __import__("os").name != "nt", reason="winget and its exit codes are Windows'"
)
def test_winget_saying_already_installed_is_not_shown_as_a_failed_step(_isolated):
    from types import SimpleNamespace

    from gaia.agents.base.tool_outcome import tool_result_failed

    agent = _agent(_isolated)
    agent.shell_command_times = []
    steps = [SimpleNamespace(segments=[["winget", "install", "Git.Git"]])]

    outcome = agent._run_approved_once(
        "exit /b -1978335135", str(_isolated), steps, 30, "default", 0
    )

    assert "already installed" in outcome["expected_exit"]
    assert "exit_code_meaning" not in outcome
    assert not tool_result_failed(outcome)


def test_git_is_told_not_to_wait_for_a_password_nobody_can_type(monkeypatch):
    from gaia.agents.tools.shell_tools import _segment_env

    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    monkeypatch.delenv("GCM_INTERACTIVE", raising=False)

    env = _segment_env({})

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    # Git Credential Manager's window is one a person at the desktop can answer.
    assert "GCM_INTERACTIVE" not in env


@pytest.mark.parametrize("source", ["command", "environment"])
def test_a_sign_in_setting_the_user_or_the_command_made_is_kept(monkeypatch, source):
    from gaia.agents.tools.shell_tools import _segment_env

    if source == "environment":
        monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
        env = _segment_env({})
    else:
        monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
        env = _segment_env({"GIT_TERMINAL_PROMPT": "1"})

    assert env["GIT_TERMINAL_PROMPT"] == "1"


def test_a_command_that_needs_no_approval_still_looks_for_a_new_install(
    _isolated, installed, monkeypatch
):
    """`git --version` is a read nobody approves; a Git the user installed
    themselves has to be found for it too, without a GAIA restart."""
    agent = _agent(_isolated)
    refreshed = []
    monkeypatch.setattr(
        program_lookup,
        "refresh_path",
        lambda after_install=False: refreshed.append(1) or [],
    )

    agent.policy_refusal_for_call("run_shell_command", {"command": "git --version"})
    assert refreshed == [1]

    installed.add("git")
    agent.policy_refusal_for_call("run_shell_command", {"command": "git --version"})
    assert refreshed == [1]


def test_a_launcher_keeps_the_missing_program_answer(_isolated, installed):
    agent = _agent(_isolated)

    missing = _shell(agent, f"timeout 60 {CLONE}")

    assert missing[PROGRAM_NOT_FOUND] == "git"
    assert not agent._denied_effects
