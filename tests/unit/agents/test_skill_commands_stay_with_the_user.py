# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia skill`` stays a command only the user runs, and refusals keep their routes.

The agent may not promote, install or trust a skill: every spelling of
``gaia skill`` through the shell tool is refused outright, with no approval
prompt, whoever is watching and whatever mode the session is in. GAIA's other
``gaia`` commands are unchanged. A pre-approved run that refuses a command
reading files inside the allowed folders still offers ``run_python``; one that
reaches outside them, or changes files, does not. Classification only: every
process launch is stubbed to fail, so nothing here can run a real command.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.denied_effects import UNATTENDED_REFUSAL, DeniedEffects
from gaia.agents.base.tools import tool
from gaia.agents.tools.shell_tools import (
    TIER_REFUSE,
    ShellToolsMixin,
    _human_only_command,
)
from gaia.security import PathValidator
from gaia.skills.binaries import (
    BINARY_POLICIES,
    REFUSE,
    classify_invocation,
    human_only_gaia_call,
)

SKILL_SPELLINGS = [
    "gaia skill promote rss-digest",
    "gaia skill install rss-digest",
    "gaia skill import ./captured --allow-experimental",
    "gaia skill list",
    "GAIA SKILL PROMOTE rss-digest",
    "gaia.exe skill promote rss-digest",
    "GAIA.EXE skill promote rss-digest",
    "gaia-cli skill promote rss-digest",
    "C:/Tools/gaia.exe skill promote rss-digest",
    r'"C:\Program Files\GAIA\gaia.exe" skill promote rss-digest',
    "gaia --debug skill promote rss-digest",
    "python -m gaia skill promote rss-digest",
    "python -m gaia.cli skill promote rss-digest",
    "python3 -m gaia skill promote rss-digest",
    "py -m gaia skill promote rss-digest",
    "python -I -m gaia skill promote rss-digest",
    "cmd /c gaia skill promote rss-digest",
    "cmd.exe /c gaia skill promote rss-digest",
    'cmd /c "gaia skill promote rss-digest"',
    'powershell -Command "gaia skill promote rss-digest"',
    'powershell -NoProfile -Command "& gaia skill promote rss-digest"',
    'pwsh -c "gaia skill promote rss-digest"',
    "bash -c 'gaia skill promote rss-digest'",
    "sh -c 'gaia skill promote rss-digest'",
    "python -c \"import subprocess; subprocess.run(['gaia','skill','promote','x'])\"",
    "python -c \"import subprocess; subprocess.run('gaia skill promote x'.split())\"",
    "python -c \"import subprocess as s; s.call('gaia skill promote x', shell=True)\"",
    "python -c \"import os; os.system('gaia skill promote x')\"",
    "echo hi && gaia skill promote rss-digest",
    "echo y | gaia skill promote rss-digest",
    "start gaia skill promote rss-digest",
    'gaia skill promote "unterminated',
]

NOT_SKILL_COMMANDS = [
    'grep -r "gaia skill" docs',
    'git log --grep "gaia skill promote"',
    'echo "run gaia skill promote yourself"',
    "gaia automation list",
    "gaia discord watches",
    "gaia automation add --name skill --prompt hi --every 1h",
    "dir gaia\\skill",
    "ls gaia/skill",
    "",
    "   ",
    "&&",
]


@pytest.fixture
def layout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "GAIA_HOME"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("GAIA_AUTO_APPROVE_TOOLS", raising=False)
    systemp = tmp_path / "systemp"
    systemp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systemp))
    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: {str(systemp)})
    project = tmp_path / "project"
    project.mkdir()
    (project / "make_report.py").write_text("print(1)\n")
    (project / "data.csv").write_text("a,b\n1,2\n")
    monkeypatch.chdir(project)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "report.py").write_text("print(2)\n")

    def no_process(*args, **kwargs):
        raise AssertionError(f"a process was started: {args!r}")

    monkeypatch.setattr("gaia.agents.tools.shell_tools.subprocess.Popen", no_process)
    return {"project": project, "elsewhere": elsewhere}


class _Willing(SilentConsole):
    """A person who would approve anything they were asked about."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.asked = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.asked.append((tool_name, tool_args))
        return True


class _Agent(Agent, ShellToolsMixin):
    scope = None

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
            agent.ran.append(("run_python", code))
            return {"status": "success"}

        @tool
        def read_file(file_path: str) -> dict:
            """Probe reader."""
            agent.ran.append(("read_file", file_path))
            return {"status": "success"}

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Probe writer."""
            agent.ran.append(("write_file", file_path))
            return {"status": "success"}


def _agent(layout, *, unattended=False, full_access=False):
    console = _Willing(auto_approve_gated_tools=unattended)
    if full_access:
        console.full_access = True
    _Agent.scope = layout["project"]
    agent = _Agent(console)
    agent.max_commands_per_minute = 1000
    agent.max_commands_per_10_seconds = 1000
    agent._denied_effects = DeniedEffects(str(layout["project"]))
    return agent


def _refusal(agent, command):
    return agent.policy_refusal_for_call("run_shell_command", {"command": command})


# ---------------------------------------------------------------------------
# gaia skill is refused outright, in every spelling and every mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", SKILL_SPELLINGS)
@pytest.mark.parametrize("mode", ["interactive", "unattended", "full_access"])
def test_every_spelling_of_gaia_skill_is_refused_before_any_prompt(
    layout, command, mode
):
    agent = _agent(
        layout,
        unattended=mode == "unattended",
        full_access=mode == "full_access",
    )

    refused = _refusal(agent, command)

    assert refused is not None, command
    assert refused["tier"] == TIER_REFUSE
    # Final: not a "nobody could approve it" refusal another route may answer.
    assert UNATTENDED_REFUSAL not in refused
    assert "gaia skill" in refused["error"]
    for offer in ("interactively", "full access", "approve it", "Agent UI"):
        assert offer not in refused["error"], refused["error"]
    assert "never ask the user to approve it" in refused["hint"]


@pytest.mark.parametrize("command", SKILL_SPELLINGS[:8] + SKILL_SPELLINGS[-6:])
def test_a_willing_person_is_never_asked_and_nothing_runs(layout, command):
    agent = _agent(layout)

    result = agent._execute_tool("run_shell_command", {"command": command})

    assert result["executed"] is False
    assert agent.console.asked == []


@pytest.mark.parametrize("command", SKILL_SPELLINGS)
def test_the_execution_path_refuses_it_too(layout, command):
    agent = _agent(layout)

    error, steps = agent._line_verdict(command)

    assert error["tier"] == TIER_REFUSE
    assert steps == []


@pytest.mark.parametrize("command", NOT_SKILL_COMMANDS)
def test_text_about_gaia_skill_is_not_a_gaia_skill_call(command):
    assert _human_only_command(command) == ""


@pytest.mark.parametrize(
    "command",
    [
        "gaia automation list",
        "gaia automation list --all",
        "gaia discord watches",
        "gaia discord always-allowed",
    ],
)
@pytest.mark.parametrize("unattended", [False, True])
def test_gaia_automation_and_discord_reads_still_run_unasked(
    layout, command, unattended
):
    agent = _agent(layout, unattended=unattended)

    assert _refusal(agent, command) is None


@pytest.mark.parametrize(
    "command",
    [
        "gaia automation add --name skill --prompt hi --every 1h",
        "gaia discord watch --repo amd/gaia",
    ],
)
def test_gaia_automation_and_discord_writes_are_not_hard_refused(layout, command):
    agent = _agent(layout)

    refused = _refusal(agent, command)

    assert refused is None or refused["tier"] != TIER_REFUSE


@pytest.mark.parametrize(
    "argv",
    [
        ["gaia", "skill", "promote", "x"],
        ["gaia", "skill"],
        ["gaia", "--help", "skill", "list"],
        ["gaia", "SKILL", "trust", "x"],
    ],
)
def test_the_gaia_policy_never_offers_skill(argv):
    decision = classify_invocation(BINARY_POLICIES["gaia"], argv)

    assert decision.outcome == REFUSE
    assert decision.never_offered is True


def test_a_gaia_grant_does_not_open_skill(layout):
    command = "gaia skill promote rss-digest"

    error = ShellToolsMixin._validate_command(
        "gaia", shlex.split(command), command, granted_binaries=frozenset({"gaia"})
    )

    assert error is not None
    assert error["tier"] == TIER_REFUSE


@pytest.mark.parametrize(
    "words,found",
    [
        (["gaia", "skill", "promote"], "gaia skill"),
        (["/usr/local/bin/gaia", "skill"], "gaia skill"),
        (["python", "-m", "gaia.cli", "-v", "skill"], "gaia skill"),
        (["gaia", "automation", "add", "--name", "skill"], ""),
        (["gaia"], ""),
        (["gaiax", "skill"], ""),
        (["notgaia.exe", "skill"], ""),
    ],
)
def test_human_only_gaia_call_reads_the_program_and_its_subcommand(words, found):
    assert human_only_gaia_call(words) == found


# ---------------------------------------------------------------------------
# An unattended read refusal still offers run_python inside the allowed folders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command", ["python make_report.py", "type data.csv", "python .\\make_report.py"]
)
def test_a_read_inside_the_allowed_folders_still_offers_run_python(layout, command):
    agent = _agent(layout, unattended=True)

    refused = _refusal(agent, command)

    assert refused[UNATTENDED_REFUSAL] is True
    assert "run_python to compute" in refused["error"]
    assert "read_file" in refused["error"]


def test_a_read_outside_the_allowed_folders_does_not_offer_run_python(layout):
    agent = _agent(layout, unattended=True)
    outside = layout["elsewhere"] / "report.py"

    refused = _refusal(agent, f'python "{outside}"')

    assert "run_python" not in refused["error"]


def test_a_relative_read_that_climbs_out_does_not_offer_run_python(layout):
    agent = _agent(layout, unattended=True)

    refused = _refusal(agent, f"python ..{os.sep}elsewhere{os.sep}report.py")

    assert "run_python" not in refused["error"]


def test_a_read_through_a_link_that_leads_out_does_not_offer_run_python(
    layout, monkeypatch
):
    agent = _agent(layout, unattended=True)
    link = layout["project"] / "linked.py"
    target = layout["elsewhere"] / "report.py"
    real = os.path.realpath

    def follows_link(path, *args, **kwargs):
        if os.path.normcase(os.path.abspath(os.fspath(path))) == os.path.normcase(
            str(link)
        ):
            return str(target)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", follows_link)

    refused = _refusal(agent, "python linked.py")

    assert "run_python" not in refused["error"]


def test_a_read_of_a_credential_file_does_not_offer_run_python(layout):
    agent = _agent(layout, unattended=True)
    (layout["project"] / ".env").write_text("TOKEN=x\n")

    refused = _refusal(agent, "type .env")

    assert "run_python" not in refused["error"]


@pytest.mark.parametrize(
    "command",
    [
        "python make_report.py > out.txt",
        "copy make_report.py copy.py",
        "Move-Item make_report.py moved.py",
    ],
)
def test_a_refused_write_still_does_not_offer_run_python(layout, command):
    agent = _agent(layout, unattended=True)

    refused = _refusal(agent, command)

    assert "run_python" not in refused["error"]
    assert "write_file" in refused["error"]


def test_a_host_with_no_allowed_folders_does_not_offer_run_python(layout):
    agent = _agent(layout, unattended=True)
    del agent.path_validator

    assert agent._reads_stay_inside("python make_report.py") is False


# ---------------------------------------------------------------------------
# Python tools can't launch gaia skill either
# ---------------------------------------------------------------------------

PYTHON_SKILL_LAUNCHES = [
    "import subprocess\nsubprocess.run(['gaia', 'skill', 'promote', 'x'])",
    "import subprocess\nsubprocess.run('gaia skill promote x', shell=True)",
    "import subprocess, sys\n"
    "subprocess.run([sys.executable, '-m', 'gaia', 'skill', 'promote', 'x'])",
    "import os\nos.system('gaia skill promote x')",
    "import os\nprint(os.popen('gaia skill list').read())",
    "import pty\npty.spawn(['gaia', 'skill', 'promote', 'x'])",
    "from pty import spawn\nspawn('gaia skill trust x')",
    "import subprocess as sp\nsp.check_output(['gaia.exe', 'skill', 'trust', 'x'])",
    "from subprocess import Popen\nPopen(['gaia', 'skill', 'install', 'x']).wait()",
    "import subprocess\ncmd = ['gaia', 'skill', 'promote', 'x']\nsubprocess.run(cmd)",
    "import subprocess\nsubprocess.run('gaia skill promote x'.split())",
    "import subprocess\nsubprocess.run(['cmd', '/c', 'gaia skill promote x'])",
    "import subprocess\nsubprocess.run(['powershell', '-Command', 'gaia skill promote x'])",
    "import subprocess\nprog, sub = 'gaia', 'skill'\nsubprocess.run([prog, sub, 'promote', 'x'])",
    "import os\nos.execvp('gaia', ['gaia', 'skill', 'promote', 'x'])",
    "import os\nos.spawnlp(os.P_WAIT, 'gaia', 'gaia', 'skill', 'promote', 'x')",
    "import subprocess, sys\n"
    "subprocess.run([sys.executable, '-c', 'import os; os.system(\"gaia skill promote x\")'])",
]


@pytest.mark.parametrize("code", PYTHON_SKILL_LAUNCHES)
@pytest.mark.parametrize("mode", ["interactive", "unattended", "full_access"])
def test_python_that_launches_gaia_skill_is_refused_before_any_prompt(
    layout, code, mode
):
    agent = _agent(
        layout,
        unattended=mode == "unattended",
        full_access=mode == "full_access",
    )

    result = agent._execute_tool("run_python", {"code": code})

    assert result["executed"] is False
    assert "gaia skill" in result["error"]
    assert "never ask the user to approve it" in result["hint"]
    assert agent.ran == []
    assert agent.console.asked == []


def test_a_script_file_that_launches_gaia_skill_is_refused(layout):
    agent = _agent(layout)
    (layout["project"] / "promote.py").write_text(
        "import subprocess\nsubprocess.run(['gaia', 'skill', 'promote', 'x'])\n"
    )

    refusal = agent._policy_refusal("execute_python_file", {"file_path": "promote.py"})

    assert refusal is not None
    assert refusal["executed"] is False
    assert "gaia skill" in refusal["error"]


@pytest.mark.parametrize(
    "code",
    [
        "import subprocess\nsubprocess.run(['git', 'status'])",
        "import subprocess\nsubprocess.run(['gaia', 'automation', 'list'])",
        "import os\nos.system('git log --oneline -5')",
        "print('run gaia skill promote x in your terminal')",
        "import json\nprint(json.dumps({'skill': 'gaia'}))",
    ],
)
def test_python_that_does_not_launch_gaia_skill_still_runs(layout, code):
    agent = _agent(layout)

    assert agent._policy_refusal("run_python", {"code": code}) is None
    result = agent._execute_tool("run_python", {"code": code})

    assert result["status"] == "success"
    assert agent.ran == [("run_python", code)]
