# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``find /c`` must run Windows' find.exe, not the GNU find on PATH.

Git-for-Windows puts GNU ``find`` and ``sort`` on PATH, ahead of System32 on
many machines. ``dir /s /b | find /c ".pyd"`` then ran GNU find, which read
``/c`` as the C: drive and walked all of it: 48M characters of output where the
model expected a count.
"""

import os
import shutil
import sys

import pytest

from gaia.agents.tools.shell_tools import (
    ShellToolsMixin,
    _system32_first,
    _wants_windows_tool,
)

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="cmd.exe tool resolution is Windows-only"
)


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


def _run(command, cwd):
    from gaia.agents.base.tools import get_tool_metadata

    host = _Host()
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"](
        command=command, working_directory=str(cwd)
    )


@pytest.mark.parametrize(
    "segment",
    [
        ["find", "/c", ".pyd"],
        ["find", "/V", "/C", '""'],
        ["FIND", "/i", "x", "file.txt"],
        ["sort", "/r"],
        ["sort", "/+3", "data.txt"],
    ],
)
def test_cmd_style_switches_mean_the_windows_tool(segment):
    assert _wants_windows_tool(segment)


@pytest.mark.parametrize(
    "segment",
    [
        ["find", ".", "-name", "*.py"],
        ["find", "/usr/share", "-type", "f"],
        ["sort", "-u", "data.txt"],
        ["sort"],
        ["findstr", "/c:x", "file.txt"],
        ["grep", "/c", "file.txt"],
    ],
)
def test_gnu_spellings_and_other_tools_are_left_alone(segment):
    assert not _wants_windows_tool(segment)


def test_system32_leads_path_only_when_a_segment_needs_it(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    env = {"PATH": r"C:\Program Files\Git\usr\bin"}

    assert _system32_first(env, [["dir", "/s", "/b"]]) is env

    routed = _system32_first(env, [["dir", "/s", "/b"], ["find", "/c", ".pyd"]])
    assert routed["PATH"].split(os.pathsep) == [
        os.path.join(r"C:\Windows", "System32"),
        r"C:\Program Files\Git\usr\bin",
    ]
    assert env["PATH"] == r"C:\Program Files\Git\usr\bin"


@pytest.mark.parametrize(
    "command", ["sort /o out.txt in.txt", "sort /OUTPUT out.txt in.txt"]
)
def test_windows_sort_output_switch_is_not_a_read(command):
    parts = command.split()
    error = ShellToolsMixin._validate_command(parts[0], parts, command)
    assert error is not None
    assert "writes to a file" in error["error"]


@windows_only
def test_find_count_runs_windows_find_even_with_gnu_find_first(tmp_path, monkeypatch):
    gnu_bin = r"C:\Program Files\Git\usr\bin"
    if not os.path.isfile(os.path.join(gnu_bin, "find.exe")):
        pytest.skip("Git-for-Windows GNU find is not installed")
    monkeypatch.setenv("PATH", gnu_bin + os.pathsep + os.environ["PATH"])
    assert shutil.which("find").lower().startswith(gnu_bin.lower())
    (tmp_path / "a.pyd").write_text("")
    (tmp_path / "b.pyd").write_text("")
    (tmp_path / "c.txt").write_text("")

    result = _run('dir /b | find /c ".pyd"', tmp_path)

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "2"


@windows_only
def test_gnu_find_still_runs_for_gnu_spelling(tmp_path, monkeypatch):
    gnu_bin = r"C:\Program Files\Git\usr\bin"
    if not os.path.isfile(os.path.join(gnu_bin, "find.exe")):
        pytest.skip("Git-for-Windows GNU find is not installed")
    monkeypatch.setenv("PATH", gnu_bin + os.pathsep + os.environ["PATH"])
    (tmp_path / "a.pyd").write_text("")

    result = _run('find . -name "*.pyd"', tmp_path)

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "./a.pyd"
