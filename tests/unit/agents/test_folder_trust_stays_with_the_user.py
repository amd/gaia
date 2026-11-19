# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Trusting a folder's guidance stays a step only the user takes.

Every way an agent could reach ``gaia trust add`` or the trust store from its
shell or Python tools is refused outright, with no approval prompt, in every
mode. Classification only: process launches are stubbed to fail.
"""

from __future__ import annotations

import pytest

from gaia.agents.tools.shell_tools import TIER_REFUSE
from gaia.skills.binaries import human_only_gaia_call

from . import test_skill_commands_stay_with_the_user as _skill_gate
from .test_skill_commands_stay_with_the_user import _agent, _refusal

layout = _skill_gate.layout

MODES = ["interactive", "unattended", "full_access"]

SHELL_SPELLINGS = [
    "gaia trust add",
    "gaia trust add C:/repo",
    "gaia project trust add",
    "GAIA TRUST ADD",
    "gaia.exe trust add",
    "gaia --debug trust add",
    "python -m gaia trust add",
    "python -m gaia.cli project trust add",
    "cmd /c gaia trust add",
    'powershell -Command "gaia trust add"',
    "bash -c 'gaia trust add'",
    "env -i gaia trust add",
    "echo hi && gaia trust add",
    "gaia trust forget --all --yes",
    "gaia trust list",
]

PYTHON_SPELLINGS = [
    "from gaia.project_trust import remember_folder_trust, TRUSTED\n"
    "remember_folder_trust('.', TRUSTED)",
    "import gaia.project_trust as t\nt.remember_folder_trust('.', t.TRUSTED)",
    "import importlib\n"
    "importlib.import_module('gaia.project_trust').remember_folder_trust('.', 'trusted')",
    "from gaia import project_trust_cli",
    "import subprocess\n"
    "subprocess.run(['gaia', 'trust', 'add', '.'], env={'PATH': '/usr/bin'})",
    "import os, subprocess\nos.environ.clear()\n"
    "subprocess.run(['gaia', 'trust', 'add', '.'])",
    "import subprocess, sys\n"
    "subprocess.run([sys.executable, '-m', 'gaia', 'project', 'trust', 'add'], env={})",
    "import os\nos.system('gaia trust add .')",
]


def _mode(layout, mode):
    return _agent(
        layout, unattended=mode == "unattended", full_access=mode == "full_access"
    )


@pytest.mark.parametrize("command", SHELL_SPELLINGS)
@pytest.mark.parametrize("mode", MODES)
def test_every_shell_spelling_of_gaia_trust_is_refused(layout, command, mode):
    agent = _mode(layout, mode)

    refused = _refusal(agent, command)

    assert refused is not None and refused["tier"] == TIER_REFUSE
    assert agent.console.asked == []


@pytest.mark.parametrize("code", PYTHON_SPELLINGS)
@pytest.mark.parametrize("mode", MODES)
def test_python_that_trusts_a_folder_is_refused_before_any_prompt(layout, code, mode):
    agent = _mode(layout, mode)

    result = agent._execute_tool("run_python", {"code": code})

    assert result["executed"] is False
    assert "never ask the user to approve it" in result["hint"]
    assert agent.ran == []
    assert agent.console.asked == []


@pytest.mark.parametrize(
    "command",
    [
        'grep -r "gaia trust" docs',
        'echo "run gaia trust add yourself"',
        "gaia automation list",
    ],
)
def test_text_about_gaia_trust_is_not_a_trust_call(layout, command):
    agent = _agent(layout)

    refused = _refusal(agent, command)

    assert refused is None or refused["tier"] != TIER_REFUSE


@pytest.mark.parametrize(
    "words, found",
    [
        (["gaia", "trust", "add"], "gaia trust"),
        (["gaia", "project", "trust", "add"], "gaia project"),
        (["gaia", "skill", "trust", "x"], "gaia skill"),
        (["gaia", "email", "autonomy", "trust"], ""),
        (["git", "trust"], ""),
    ],
)
def test_human_only_gaia_call_names_the_trust_commands(words, found):
    assert human_only_gaia_call(words) == found
