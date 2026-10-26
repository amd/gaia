# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A read-only shell line inside the allowed folders runs without a prompt."""

import os

import pytest

from gaia.agents.tools.shell_tools import ShellToolsMixin


class _InsideCwd:
    @staticmethod
    def is_path_allowed(path: str, prompt_user: bool = True) -> bool:
        return os.path.abspath(path).lower().startswith(os.getcwd().lower())


class _Host(ShellToolsMixin):
    path_validator = _InsideCwd()


def _unasked(command: str) -> bool:
    return _Host().skill_grant_covers_call("run_shell_command", {"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "git log --oneline -5",
        "git diff main...HEAD -- src/gaia/skills/link.py",
        "git status --short",
        "git rev-parse --show-toplevel && git branch --show-current",
        "git branch -a && git log --oneline -5 && git merge-base HEAD main",
        "git blame README.md",
        "git grep -n foo",
        "git diff main...HEAD | head -50",
        "grep -rn foo src 2>/dev/null",
        "ls -a | grep -i ruff",
        "dir /s /b x.py 2>nul || echo NOT_FOUND",
        "cat README.md",
    ],
)
def test_read_only_lines_run_unasked(command):
    assert _unasked(command)


@pytest.mark.parametrize(
    "command",
    [
        "echo hi > out.txt",
        "find . -name '*.py' -delete",
        "git diff --output=x.txt",
        "git -c core.pager=evil log",
        "git grep -O vim foo",
        "git commit -m x",
        "git push",
        "python -m pytest -q",
        "cat ../../secret.txt",
        "ls && rm notes.txt",
        "cat $(echo README.md)",
    ],
)
def test_anything_that_writes_runs_code_or_leaves_the_project_still_asks(command):
    assert not _unasked(command)


def test_only_the_shell_tool_is_exempt():
    assert not _Host().skill_grant_covers_call("run_python", {"command": "ls"})
