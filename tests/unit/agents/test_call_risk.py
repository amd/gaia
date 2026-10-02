# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The label a permission prompt shows is earned by the command (#4446).

Running the tests was labelled DESTRUCTIVE, the same as `rm -rf`. A label that
is wrong on the safe calls is the one people learn to ignore on the real ones.
"""

import pytest

from gaia.agents.base.call_risk import call_risk, shell_command_risk


@pytest.mark.parametrize(
    "command,risk",
    [
        # Reads
        ("ls -la", "read"),
        ("git status", "read"),
        ("git log --oneline -5", "read"),
        ("cat notes.md | head -20", "read"),
        ("gh issue list --limit 5", "read"),
        ("find . -name '*.py'", "read"),
        # Writes
        ("mkdir build", "write"),
        ("git commit -m wip", "write"),
        ("npm install left-pad", "write"),
        ("echo hi > out.txt", "write"),
        ("sed -i s/a/b/ f.txt", "write"),
        # Runs code
        ("python -m pytest -q tests/", "execute"),
        (r"cd C:\Users\me\proj && python -m pytest -q tests/ 2>&1", "execute"),
        ("python scratch.py", "execute"),
        ("npm test", "execute"),
        ("make", "execute"),
        ("some-tool-nobody-listed --flag", "execute"),
        ("echo $(whoami)", "execute"),
        # Destroys
        ("rm -rf build", "destructive"),
        ("git reset --hard HEAD~1", "destructive"),
        ("git push --force", "destructive"),
        ("git clean -fdx", "destructive"),
        ("find . -name '*.pyc' -delete", "destructive"),
        ("pytest && rm -rf .pytest_cache", "destructive"),
        ("git -C . clean -fdx", "destructive"),
        # git options and subcommands that change state
        ("git remote -v", "read"),
        ("git remote add origin url", "write"),
        ("git config --get user.name", "read"),
        ("git config --global core.hooksPath x", "write"),
        ("rg --pre ./x.sh foo", "execute"),
    ],
)
def test_the_label_follows_the_command(command, risk):
    assert shell_command_risk(command) == risk


@pytest.mark.parametrize(
    "command",
    ["env FOO=1 rm -rf x", "sudo rm -rf x", "timeout 10 rm x", "xargs rm"],
)
def test_a_wrapper_takes_the_risk_of_what_it_runs(command):
    assert shell_command_risk(command) == "destructive"


def test_tools_without_a_command_are_labelled_by_kind():
    assert call_risk("run_python", {"code": "print(1)"}) == "execute"
    assert call_risk("execute_python_file", {"file_path": "a.py"}) == "execute"
    assert call_risk("write_file", {"file_path": "a.py"}) == "write"
    assert call_risk("run_shell_command", {}) == "execute"


def test_tools_it_does_not_know_are_left_to_the_client():
    assert call_risk("send_now", {"to": "a@b.com"}) is None
