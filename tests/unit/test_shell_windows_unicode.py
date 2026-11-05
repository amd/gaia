# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""run_shell_command must keep non-ASCII file names on Windows.

cmd.exe writes its built-ins' output in the console code page (437 on an
English box), so ``dir`` printed ``تقرير 📊 final.txt`` as ``????? ?? final.txt``
before GAIA ever decoded a byte, and the agent reported the file as
``final.txt``.
"""

import sys

import pytest

from gaia.agents.tools.shell_tools import ShellToolsMixin

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="cmd.exe code pages are Windows-only"
)

NAMES = ["تقرير 📊 final.txt", "日本語 café.txt"]


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


def _shell_tool():
    from gaia.agents.base.tools import get_tool_metadata

    host = _Host()
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"]


@pytest.fixture
def folder(tmp_path):
    target = tmp_path / "مجلد"
    target.mkdir()
    for name in NAMES:
        (target / name).write_text("x\n", encoding="utf-8")
    return target


@pytest.mark.parametrize("command", ["dir", "dir /b", "dir /b | sort"])
def test_cmd_builtins_list_non_ascii_names(folder, command):
    result = _shell_tool()(command=command, working_directory=str(folder))

    assert result["status"] == "success", result
    for name in NAMES:
        assert name in result["stdout"], result["stdout"]


def test_echo_keeps_non_ascii_text(folder):
    result = _shell_tool()(command="echo تقرير ✓", working_directory=str(folder))

    assert result["status"] == "success", result
    assert "تقرير ✓" in result["stdout"]


def test_exit_code_survives_the_utf8_console(folder):
    result = _shell_tool()(
        command="dir /b no-such-file.txt", working_directory=str(folder)
    )

    assert result["return_code"] != 0, result
