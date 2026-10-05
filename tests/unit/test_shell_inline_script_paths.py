# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An interpreter's inline script is checked word by word, never as one path.

``powershell -Command "Select-String -Path 'C:\\...\\server.log' ..."`` used to
join the whole script onto the cwd and ask the user to approve that "path".
The file the script reads must still be checked; the script text must never
reach the access prompt; and nothing that was refused before may pass now.
"""

import base64
import sys
from pathlib import Path

import pytest

from gaia.agents.tools.shell_tools import ShellToolsMixin, _parse_line


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


class _RecordingValidator:
    """Allows paths under *root* and records every path it was asked about."""

    def __init__(self, root):
        self.root = str(Path(root).resolve())
        self.asked = []

    def is_path_allowed(self, path):
        self.asked.append(str(path))
        return str(path).startswith(self.root)


def _check(command, cwd, root):
    """``(refusal, validator)`` for every step of *command* run from *cwd*."""
    host = _Host()
    host.path_validator = _RecordingValidator(root)
    steps, error = _parse_line(command, bypass_gates=True)
    assert error is None, error
    for step in steps:
        refusal = host._path_traversal_refusal(step, str(cwd), frozenset())
        if refusal:
            return refusal, host.path_validator
    return None, host.path_validator


@pytest.fixture
def layout(tmp_path):
    """``home/`` is the allowed zone; ``home/work`` the cwd; ``outside/`` is not."""
    home = tmp_path / "home"
    work = home / "work"
    log = home / "Documents" / "stress" / "server.log"
    log.parent.mkdir(parents=True)
    work.mkdir()
    log.write_text("ERROR one\n")
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("secret\n")
    return {"home": home, "work": work, "log": log, "secret": secret}


def _reported(path):
    return (
        f"powershell -Command \"Select-String -Path '{path}' -Pattern 'ERROR' "
        '-SimpleMatch | Measure-Object -Line"'
    )


def test_the_reported_command_checks_the_file_not_the_script(layout):
    refusal, validator = _check(
        _reported(layout["log"]), layout["work"], layout["home"]
    )

    assert refusal is None
    assert str(layout["log"].resolve()) in validator.asked
    assert not any("Select-String" in path for path in validator.asked)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-letter paths")
def test_the_reported_command_verbatim():
    """The exact line from the live session, with its Windows paths."""
    command = (
        'powershell -Command "Select-String -Path '
        "'C:\\Users\\Kalin\\gaia-rc-sbx\\home\\Documents\\stress\\server.log' "
        "-Pattern 'ERROR' -SimpleMatch | Measure-Object -Line\""
    )
    refusal, validator = _check(
        command,
        "C:\\Users\\Kalin\\gaia-rc-sbx\\home4509",
        "C:\\Users\\Kalin\\gaia-rc-sbx",
    )

    assert refusal is None
    assert (
        "C:\\Users\\Kalin\\gaia-rc-sbx\\home\\Documents\\stress\\server.log"
        in validator.asked
    )
    assert not any("Select-String" in path for path in validator.asked)


def test_a_script_reading_an_out_of_scope_file_is_refused_for_that_file(layout):
    refusal, validator = _check(
        _reported(layout["secret"]), layout["work"], layout["home"]
    )

    real = str(layout["secret"].resolve())
    assert refusal is not None and refusal["executed"] is False
    assert real in refusal["error"]
    assert "Select-String" not in refusal["error"]
    # The prompt is raised for what the validator is asked: the real file.
    assert validator.asked[-1] == real


@pytest.mark.parametrize(
    "command",
    [
        'bash -c "cat ../../outside/secret.txt"',
        "sh -c 'head -n 1 ../../outside/secret.txt'",
        'bash -lc "cat ../../outside/secret.txt | wc -l"',
        "python -c \"print(open('../../outside/secret.txt').read())\"",
        "python3 -Ic \"open('../../outside/secret.txt')\"",
        "node -e \"require('fs').readFileSync('../../outside/secret.txt')\"",
        'cmd /c "type ../../outside/secret.txt"',
        "cmd /c type ../../outside/secret.txt",
        'cmd.exe /C "type ../../outside/secret.txt"',
        'CMD.EXE /K "type ../../outside/secret.txt"',
        'cmd /k "dir /b ../../outside/secret.txt"',
        'cmd /s /q /c "type ../../outside/secret.txt"',
        'cmd /d /e:on /C "type ../../outside/secret.txt"',
        'cmd /r "type ../../outside/secret.txt"',
        'cmd "/ctype ../../outside/secret.txt"',
        "cmd /Ktype ../../outside/secret.txt",
        'cmd /c "echo hi & type ../../outside/secret.txt"',
        "pwsh -NoLogo -Command Get-Content -Path:../../outside/secret.txt",
        "powershell Get-Content '../../outside/secret.txt'",
        'powershell /NoProfile /Command "Get-Content ../../outside/secret.txt"',
        "pwsh.exe /nologo /c Get-Content ../../outside/secret.txt",
        "powershell /File ../../outside/secret.txt",
    ],
)
def test_traversal_inside_an_inline_script_is_refused(command, layout):
    refusal, _ = _check(command, layout["work"], layout["home"])

    assert refusal is not None, command
    assert str(layout["secret"].resolve()) in refusal["error"]


@pytest.mark.parametrize(
    "command",
    [
        'bash -c "cat ../Documents/stress/server.log"',
        "sh -c 'head -n 1 ../Documents/stress/server.log'",
        'bash -lc "cat ../Documents/stress/server.log | wc -l"',
        "python -c \"print(open('../Documents/stress/server.log').read())\"",
        "python3 -Ic \"open('../Documents/stress/server.log')\"",
        "node -e \"require('fs').readFileSync('../Documents/stress/server.log')\"",
        "perl -e \"open(F, '../Documents/stress/server.log')\"",
        "ruby -e \"File.read('../Documents/stress/server.log')\"",
        'cmd /c "type ../Documents/stress/server.log"',
        "cmd /c type ../Documents/stress/server.log",
        'cmd.exe /C "type ../Documents/stress/server.log"',
        'CMD.EXE /K "type ../Documents/stress/server.log"',
        'cmd /k "dir /b /a:-d ../Documents/stress"',
        'cmd /s /q /c "type ../Documents/stress/server.log"',
        'cmd /d /e:on /C "type ../Documents/stress/server.log"',
        'cmd /r "type ../Documents/stress/server.log"',
        'cmd "/ctype ../Documents/stress/server.log"',
        "cmd /Ktype ../Documents/stress/server.log",
        "pwsh -NoLogo -Command Get-Content -Path:../Documents/stress/server.log",
        "powershell Get-Content '../Documents/stress/server.log'",
        'powershell /NoProfile /Command "Get-Content ../Documents/stress/server.log"',
        "pwsh.exe /nologo /c Get-Content ../Documents/stress/server.log",
        "powershell /File ../Documents/stress/server.log",
    ],
)
def test_an_allowed_path_inside_an_inline_script_is_not_refused(command, layout):
    refusal, validator = _check(command, layout["work"], layout["home"])

    assert refusal is None, refusal
    # The file was really checked, not skipped.
    assert any(
        path.startswith(str(layout["log"].parent.resolve())) for path in validator.asked
    )


@pytest.mark.parametrize(
    "command",
    [
        "cmd /c type /outside/secret.txt",
        "cmd /outside/secret.txt /c dir",
        "pwsh /outside/secret.ps1",
        "powershell /NoProfile /File /outside/secret.ps1",
    ],
)
def test_only_a_switch_is_exempt_never_a_path(command, layout):
    """A ``/name`` switch is skipped; a rooted path beside it is still checked."""
    refusal, _ = _check(command, layout["work"], layout["home"])

    assert refusal is not None, command


def test_an_encoded_command_is_decoded_and_checked(layout):
    script = "Get-Content '../../outside/secret.txt'"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()

    refusal, _ = _check(
        f"powershell -EncodedCommand {encoded}", layout["work"], layout["home"]
    )

    assert refusal is not None
    assert str(layout["secret"].resolve()) in refusal["error"]


def test_plain_traversal_is_still_refused(layout):
    refusal, _ = _check("cat ../../outside/secret.txt", layout["work"], layout["home"])

    assert refusal is not None
    assert str(layout["secret"].resolve()) in refusal["error"]


def test_an_env_value_is_still_checked(layout):
    outside = layout["secret"].parent.as_posix()
    refusal, _ = _check(f"PYTHONPATH={outside} ls", layout["work"], layout["home"])

    assert refusal is not None


def test_a_script_file_operand_is_still_one_path(layout):
    """Only the script flag's operand is split; a script FILE stays a path."""
    refusal, _ = _check(
        "python ../../outside/secret.txt -c ignored", layout["work"], layout["home"]
    )

    assert refusal is not None
    assert str(layout["secret"].resolve()) in refusal["error"]


def test_a_script_with_no_paths_asks_about_nothing(layout):
    refusal, validator = _check(
        'powershell -Command "Get-Process | Sort-Object WS -Descending"',
        layout["work"],
        layout["home"],
    )

    assert refusal is None
    assert validator.asked == []


def test_run_shell_command_refuses_before_running(layout):
    """End to end: the refusal lands before anything is spawned."""
    from gaia.agents.base.tools import get_tool_metadata

    host = _Host()
    host.path_validator = _RecordingValidator(layout["home"])
    host.register_shell_tools()
    result = get_tool_metadata("run_shell_command")["function"](
        command="powershell -Command \"Get-Content -Path '../../outside/secret.txt'\"",
        working_directory=str(layout["work"]),
    )

    assert result["status"] == "error", result
    assert result["executed"] is False
    assert str(layout["secret"].resolve()) in result["error"]
    assert not any("Get-Content" in path for path in host.path_validator.asked)
