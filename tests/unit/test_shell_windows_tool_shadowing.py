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
from pathlib import Path

import pytest

from gaia.agents.tools.shell_tools import (
    ShellToolsMixin,
    _parse_line,
    _pin_windows_tools,
    _wants_windows_tool,
)

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="cmd.exe tool resolution is Windows-only"
)


def _git_usr_bin(tool):
    """Git-for-Windows' usr/bin, wherever Git is installed, or skip."""
    git = shutil.which("git")
    # git.exe sits in cmd\ or mingw64\bin\ under the install root.
    for root in Path(git).parents if git else ():
        gnu_bin = root / "usr" / "bin"
        if (gnu_bin / f"{tool}.exe").is_file():
            return str(gnu_bin)
    pytest.skip(f"Git-for-Windows GNU {tool} is not installed")


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
        ["find.exe", "/c", "x"],
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


@pytest.fixture
def system_root(tmp_path, monkeypatch):
    system32 = tmp_path / "System32"
    system32.mkdir()
    for name in ("find.exe", "sort.exe"):
        (system32 / name).write_text("")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    return system32


def _segments(text):
    steps, error = _parse_line(text)
    assert error is None, error
    assert len(steps) == 1
    return steps[0].segments


def test_text_is_untouched_when_no_segment_needs_the_windows_tool(system_root):
    text = 'dir /s /b | find . -name "*.py" | sort -u'
    assert _pin_windows_tools(text, _segments(text)) == text


def test_only_the_switch_spelled_segment_is_pinned_by_absolute_path(system_root):
    text = 'type log.txt | sort -u | find /c "a|b"'

    pinned = _pin_windows_tools(text, _segments(text))

    find_exe = str(system_root / "find.exe")
    assert pinned == f'type log.txt | sort -u | "{find_exe}" /c "a|b"'


def test_find_exe_spelling_is_pinned_too(system_root):
    text = "FIND.EXE /i x file.txt"
    pinned = _pin_windows_tools(text, _segments(text))
    assert pinned == f'"{system_root / "find.exe"}" /i x file.txt'


def test_an_unspaced_pipe_is_a_segment_and_pinned_too(system_root):
    text = "dir /b|find /c x | sort /r"

    pinned = _pin_windows_tools(text, _segments(text))

    find_exe, sort_exe = system_root / "find.exe", system_root / "sort.exe"
    assert pinned == f'dir /b|"{find_exe}" /c x | "{sort_exe}" /r'


def test_a_segment_that_cannot_be_located_is_refused(system_root):
    # The command word is not literally at the start of its segment.
    text = 'dir /b | "find" /c x'
    with pytest.raises(ValueError):
        _pin_windows_tools(text, _segments(text))


def test_missing_system_root_is_an_actionable_error(monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    with pytest.raises(RuntimeError, match="SystemRoot is not set"):
        _pin_windows_tools("find /c x f", [["find", "/c", "x", "f"]])


def test_missing_windows_binary_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        _pin_windows_tools("sort /r f", [["sort", "/r", "f"]])


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
    gnu_bin = _git_usr_bin("find")
    monkeypatch.setenv("PATH", gnu_bin + os.pathsep + os.environ["PATH"])
    assert shutil.which("find").lower().startswith(gnu_bin.lower())
    (tmp_path / "a.pyd").write_text("")
    (tmp_path / "b.pyd").write_text("")
    (tmp_path / "c.txt").write_text("")
    # cmd.exe resolves a bare name from the working directory before PATH.
    (tmp_path / "find.bat").write_text("@echo shadowed\n")

    result = _run('dir /b | find /c ".pyd"', tmp_path)

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "2"


@windows_only
def test_gnu_find_still_runs_for_gnu_spelling(tmp_path, monkeypatch):
    gnu_bin = _git_usr_bin("find")
    monkeypatch.setenv("PATH", gnu_bin + os.pathsep + os.environ["PATH"])
    (tmp_path / "a.pyd").write_text("")

    result = _run('find . -name "*.pyd"', tmp_path)

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "./a.pyd"


@windows_only
def test_gnu_sort_and_windows_find_share_one_pipeline(tmp_path, monkeypatch):
    gnu_bin = _git_usr_bin("sort")
    monkeypatch.setenv("PATH", gnu_bin + os.pathsep + os.environ["PATH"])
    (tmp_path / "log.txt").write_text("err a\nerr a\nerr b\nok\n")

    result = _run('cat log.txt | sort -u | find /c "err"', tmp_path)

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "2"
