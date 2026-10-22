# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Which shell deletes GAIA reads exactly enough to copy the files first."""

import pytest

from gaia.agents.tools.shell_deletes import delete_operands, split_exact


def _windows(text):
    argv = split_exact(text, windows=True)
    return delete_operands(argv) if argv else None


def _posix(text):
    argv = split_exact(text, windows=False)
    return delete_operands(argv) if argv else None


@pytest.mark.parametrize(
    "command, operands",
    [
        ("del notes.txt", ["notes.txt"]),
        ("erase /q /f a.txt b.txt", ["a.txt", "b.txt"]),
        ('del "my notes.txt"', ["my notes.txt"]),
        (r"del sub\notes.txt", [r"sub\notes.txt"]),
        ("rm -f notes.txt", ["notes.txt"]),
        ("powershell Remove-Item notes.txt", ["notes.txt"]),
        (
            "powershell -NoProfile -Command \"Remove-Item -LiteralPath 'my notes.txt' -Force\"",
            ["my notes.txt"],
        ),
        ('pwsh -c "ri -Path:a.txt -Confirm:$false"', ["a.txt"]),
    ],
)
def test_windows_deletes_that_are_read(command, operands):
    assert _windows(command) == operands


@pytest.mark.parametrize(
    "command",
    [
        "del *.txt",
        "del %TEMP%\\notes.txt",
        "del /s notes.txt",
        "del /p notes.txt",
        "del a.txt,b.txt",
        "del 'my notes.txt'",
        "del notes.txt > out.log",
        "rmdir /s /q build",
        "rm -rf build",
        "powershell Remove-Item -Recurse build",
        'powershell "Remove-Item $env:TEMP\\x.txt"',
        'powershell "Remove-Item a.txt; Remove-Item b.txt"',
        'powershell "Get-ChildItem *.txt | Remove-Item"',
        "powershell -File cleanup.ps1",
        "powershell Remove-Item ~\\notes.txt",
        "python -c \"import os; os.remove('notes.txt')\"",
        "del",
        'del "a""b"',
        'del "a"""',
    ],
)
def test_windows_deletes_with_unknown_effects(command):
    assert _windows(command) is None


@pytest.mark.parametrize(
    "command, operands",
    [
        ("rm notes.txt", ["notes.txt"]),
        ("rm -fv -- a.txt -b.txt", ["a.txt", "-b.txt"]),
        ("unlink notes.txt", ["notes.txt"]),
        ("pwsh -Command Remove-Item notes.txt", ["notes.txt"]),
    ],
)
def test_posix_deletes_that_are_read(command, operands):
    assert _posix(command) == operands


@pytest.mark.parametrize(
    "command",
    [
        "rm *.txt",
        "rm -r build",
        "rm -i notes.txt",
        "rm --recursive build",
        "rm $HOME/notes.txt",
        "rm ~/notes.txt",
        "rm 'my notes.txt'",
        "rm a\\ b.txt",
        "unlink a.txt b.txt",
        "find . -name '*.txt' -delete",
        "rm -f",
        "rm a.txt #b.txt",
    ],
)
def test_posix_deletes_with_unknown_effects(command):
    assert _posix(command) is None
