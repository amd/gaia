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


@pytest.mark.parametrize(
    "command",
    [
        'bash -o pipefail -c "cat ../../outside/secret.txt"',
        'bash -eo pipefail -c "cat ../../outside/secret.txt"',
        'bash -c -o pipefail "cat ../../outside/secret.txt"',
        'bash --norc -O extglob -c "cat ../../outside/secret.txt"',
        'bash -c -- "-:; cat ../../outside/secret.txt"',
        "python -X utf8 -c \"open('../../outside/secret.txt')\"",
        "python -W ignore -c \"open('../../outside/secret.txt')\"",
        "node -r ./setup.js -e \"require('fs').readFileSync('../../outside/secret.txt')\"",
        "ruby -I lib -e \"File.read('../../outside/secret.txt')\"",
        "perl -I lib -e \"open(F, '../../outside/secret.txt')\"",
    ],
)
def test_script_after_interpreter_options_is_checked(command, layout):
    refusal, _ = _check(command, layout["work"], layout["home"])

    assert refusal is not None, command
    assert str(layout["secret"].resolve()) in refusal["error"]


@pytest.mark.parametrize(
    "command",
    [
        'env bash -c "cat ../../outside/secret.txt"',
        'env FOO=1 bash -c "cat ../../outside/secret.txt"',
        'env -i -u HOME bash -c "cat ../../outside/secret.txt"',
        "env -S 'bash -c \"cat ../../outside/secret.txt\"'",
        "env --split-string='bash -c \"cat ../../outside/secret.txt\"'",
        'timeout 5 bash -c "cat ../../outside/secret.txt"',
        'timeout -s KILL 5 bash -c "cat ../../outside/secret.txt"',
        'nice -n 5 bash -c "cat ../../outside/secret.txt"',
        'nohup bash -c "cat ../../outside/secret.txt"',
        'stdbuf -oL bash -c "cat ../../outside/secret.txt"',
        'time -p bash -c "cat ../../outside/secret.txt"',
        'xargs -n 1 bash -c "cat ../../outside/secret.txt"',
        "env timeout 5 python -c \"open('../../outside/secret.txt')\"",
        '/usr/bin/env pwsh -Command "Get-Content ../../outside/secret.txt"',
    ],
)
def test_script_behind_a_command_wrapper_is_checked(command, layout):
    refusal, _ = _check(command, layout["work"], layout["home"])

    assert refusal is not None, command
    assert str(layout["secret"].resolve()) in refusal["error"]


@pytest.mark.parametrize(
    "command",
    [
        'bash -o pipefail -c "cat ../Documents/stress/server.log"',
        "python -X utf8 -c \"open('../Documents/stress/server.log')\"",
        'env FOO=1 bash -c "cat ../Documents/stress/server.log"',
        'timeout 5 bash -c "cat ../Documents/stress/server.log"',
        "env -S 'bash -c \"cat ../Documents/stress/server.log\"'",
    ],
)
def test_an_allowed_path_behind_options_or_a_wrapper_is_not_refused(command, layout):
    refusal, validator = _check(command, layout["work"], layout["home"])

    assert refusal is None, refusal
    assert any(
        path.startswith(str(layout["log"].parent.resolve())) for path in validator.asked
    )


def test_an_env_wrapper_assignment_value_is_checked(layout):
    outside = layout["secret"].parent.as_posix()
    refusal, _ = _check(
        f"env PYTHONPATH={outside} python x.py", layout["work"], layout["home"]
    )

    assert refusal is not None


@pytest.mark.parametrize(
    "template",
    [
        "sort -o{path} data.txt",
        "sort -o{path}",
        "pwsh -WorkingDirectory:{path} -Command Get-Process",
    ],
)
@pytest.mark.parametrize("relative", [True, False])
def test_an_attached_option_value_is_checked_as_a_path(template, relative, layout):
    path = (
        "../../outside/secret.txt"
        if relative
        else layout["secret"].resolve().as_posix()
    )
    refusal, _ = _check(template.format(path=path), layout["work"], layout["home"])

    assert refusal is not None, template
    assert str(layout["secret"].resolve()) in refusal["error"]


def test_an_attached_option_value_inside_the_allowed_paths_passes(layout):
    refusal, _ = _check(
        "sort -o../Documents/stress/out.txt data.txt", layout["work"], layout["home"]
    )

    assert refusal is None, refusal


def test_a_path_that_cannot_be_resolved_is_refused(layout, monkeypatch):
    real_resolve = Path.resolve

    def resolve(self, *args, **kwargs):
        if "unresolvable" in str(self):
            raise OSError("simulated resolution failure")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    refusal, _ = _check("cat sub/unresolvable.txt", layout["work"], layout["home"])

    assert refusal is not None
    assert refusal["executed"] is False
    assert "unresolvable" in refusal["error"]


@pytest.mark.parametrize(
    "command",
    [
        "timeout 5 env -S 'bash -c \"cat ../../outside/secret.txt\"'",
        "timeout 5 env PYTHONPATH={outside} python x.py",
    ],
)
def test_a_wrapper_inside_a_wrapper_is_still_checked(command, layout):
    outside = layout["secret"].parent.as_posix()
    refusal, _ = _check(command.format(outside=outside), layout["work"], layout["home"])

    assert refusal is not None, command


@pytest.fixture
def home_layout(layout, monkeypatch):
    """``layout`` with its allowed ``home/`` as the user's home directory."""
    from gaia import security

    home = layout["home"]
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(security, "SECRET_DIRECTORIES", security._secret_directories())
    return layout


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.ssh/id_rsa",
        "cat ../.ssh/id_rsa",
        "echo x > ~/.aws/config",
        'bash -c "cp x ~/.kube/config"',
        'bash -c "cat $HOME/.docker/config.json"',
        "env timeout 5 python -c \"open('../.azure/token')\"",
        "sort -o../.gnupg/out data.txt",
        "ls ~/.config/gcloud",
    ],
)
def test_a_protected_folder_is_refused_inside_the_allowed_paths(command, home_layout):
    refusal, _ = _check(command, home_layout["work"], home_layout["home"])

    assert refusal is not None, command
    assert refusal["executed"] is False


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/Documents/stress/server.log",
        "cat ../Documents/stress/server.log",
        'bash -c "cp ~/Documents/stress/server.log ~/Documents/copy.log"',
        "echo x > ~/Documents/notes.txt",
        "ls ~/.config",
    ],
)
def test_an_ordinary_home_folder_command_is_still_allowed(command, home_layout):
    refusal, _ = _check(command, home_layout["work"], home_layout["home"])

    assert refusal is None, refusal
