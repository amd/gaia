# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The prompt to run a Python file never opens a file the tool would refuse.

``execute_python_file`` pointed at a credential file must be refused before any
prompt, and nothing that builds a prompt (its arguments, the event a front-end
receives, the terminal summary) may hold a byte of that file, whichever way the
path is spelled.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from gaia.agents.base import script_preview
from gaia.agents.base.call_risk import SCRIPT_ARG
from gaia.agents.base.code_path_screen import python_file_preflight
from gaia.agents.base.script_preview import script_prompt_args
from gaia.security import PathValidator
from gaia.ui.sse_translation import CanonicalTranslator, render_command_summary
from tests.unit.test_path_spellings import (  # noqa: F401  (home is a fixture)
    ENV,
    KEY,
    PROTECTED,
    _spellings,
    home,
    windows_only,
)

TOOL = "execute_python_file"


class _Host:
    def __init__(self, home_dir: Path):
        self.working_directory = str(home_dir / "Documents" / "w")
        # The credential files' own folder is in scope: only their names protect them.
        self.path_validator = PathValidator(
            allowed_paths=[str(home_dir / "Documents"), str(home_dir / "scope")]
        )
        self.path_validator.set_access_prompt(lambda path: True)


def _never_read(monkeypatch):
    opened = []
    monkeypatch.setattr(
        script_preview,
        "_digest_and_head",
        lambda path: opened.append(path) or ("", b"", False),
    )
    return opened


def _assert_nothing_shown(host, spelled, monkeypatch, says="No approval can grant it"):
    opened = _never_read(monkeypatch)

    refusal = python_file_preflight(host)({"file_path": spelled})
    shown = script_prompt_args(host, TOOL, {"file_path": spelled})

    assert refusal is not None, "no refusal before the prompt"
    assert refusal["status"] == "error" and refusal["executed"] is False
    assert refusal["tier"] == "refuse"
    assert says in refusal["error"]
    assert opened == [], "the protected file was opened for its prompt"
    assert shown[SCRIPT_ARG] == {"unreadable": "unreadable"}
    monkeypatch.undo()


def _everything_a_prompt_carries(host, spelled) -> str:
    """The prompt arguments, the front-end event and the terminal summary, as text."""
    shown = script_prompt_args(host, TOOL, {"file_path": spelled})
    (event,) = CanonicalTranslator("r1", agent_id="gaia").translate(
        {"type": "permission_request", "tool": TOOL, "args": shown}
    )
    summary = render_command_summary(TOOL, shown, workspace=host.working_directory)
    refusal = python_file_preflight(host)({"file_path": spelled})
    return json.dumps([shown, event, summary, refusal], default=str)


@pytest.mark.parametrize("rel", PROTECTED)
def test_a_protected_file_is_refused_and_never_read(home, rel, monkeypatch):
    host = _Host(home)
    spelled = str(home / rel)

    carried = _everything_a_prompt_carries(host, spelled)
    assert KEY not in carried and ENV not in carried
    _assert_nothing_shown(host, spelled, monkeypatch)


@windows_only
@pytest.mark.parametrize("rel", [".ssh/id_test", "Documents/w/.env"])
@pytest.mark.parametrize("name", list(_spellings(Path("C:\\h\\.ssh\\id_test"))))
def test_every_spelling_of_a_protected_file_shows_nothing(home, rel, name, monkeypatch):
    host = _Host(home)
    spelled = _spellings(home / rel).get(name)
    if spelled is None:
        pytest.skip("this folder has no short name")

    carried = _everything_a_prompt_carries(host, spelled)
    assert KEY not in carried and ENV not in carried
    _assert_nothing_shown(host, spelled, monkeypatch)


def test_a_relative_path_to_a_protected_file_shows_nothing(home, monkeypatch):
    host = _Host(home)

    for spelled in (".env", os.path.join("..", "..", ".ssh", "id_test")):
        carried = _everything_a_prompt_carries(host, spelled)
        assert KEY not in carried and ENV not in carried
        _assert_nothing_shown(host, spelled, monkeypatch)


def _junction(link: Path, target: Path) -> None:
    done = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip(f"cannot create a junction here: {done.stderr!r}")


@windows_only
def test_a_junction_into_ssh_shows_nothing(home, monkeypatch):
    host = _Host(home)
    link = home / "Documents" / "w" / "jl"
    _junction(link, home / ".ssh")

    spelled = str(link / "id_test")
    assert KEY not in _everything_a_prompt_carries(host, spelled)
    _assert_nothing_shown(host, spelled, monkeypatch)


@pytest.mark.parametrize("to_file", [True, False], ids=["file link", "folder link"])
def test_a_symlink_into_ssh_shows_nothing(home, to_file, monkeypatch):
    host = _Host(home)
    link = home / "Documents" / "w" / ("job.py" if to_file else "sl")
    target = home / ".ssh" / "id_test" if to_file else home / ".ssh"
    try:
        os.symlink(target, link, target_is_directory=not to_file)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create a symlink here: {e}")

    spelled = str(link if to_file else link / "id_test")
    assert KEY not in _everything_a_prompt_carries(host, spelled)
    _assert_nothing_shown(host, spelled, monkeypatch)


def test_a_path_outside_scope_that_no_approval_grants_shows_nothing(home, monkeypatch):
    # Out of scope and withheld by the user: the tool refuses it after any answer.
    host = _Host(home)
    private = home / "private"
    private.mkdir()
    (private / "job.py").write_text(f"x = '{KEY}'\n", encoding="utf-8")
    host.path_validator.withhold_path(str(private))

    spelled = str(private / "job.py")
    assert KEY not in _everything_a_prompt_carries(host, spelled)
    _assert_nothing_shown(host, spelled, monkeypatch, says="removed from this chat")


def test_a_host_with_no_path_validator_still_shows_no_credential_file(home):
    class _Bare:
        working_directory = str(home / "Documents" / "w")

    shown = script_prompt_args(_Bare(), TOOL, {"file_path": ".env"})

    assert shown[SCRIPT_ARG] == {"unreadable": "unreadable"}


def test_an_ordinary_script_still_shows(home):
    host = _Host(home)
    script = home / "Documents" / "w" / "calc.py"
    script.write_text("print(6 * 7)\n", encoding="utf-8")

    assert python_file_preflight(host)({"file_path": "calc.py"}) is None
    shown = script_prompt_args(host, TOOL, {"file_path": "calc.py"})
    assert shown[SCRIPT_ARG] == {"text": "print(6 * 7)", "hidden_lines": 0}
    assert "print(6 * 7)" in render_command_summary(
        TOOL, shown, workspace=host.working_directory
    )


def test_a_script_the_user_could_still_approve_shows(home):
    # Out of scope but grantable: the prompt shows it, and the tool then asks.
    host = _Host(home)
    other = home / "elsewhere"
    other.mkdir()
    (other / "job.py").write_text("print('hi')\n", encoding="utf-8")

    assert python_file_preflight(host)({"file_path": str(other / "job.py")}) is None
    shown = script_prompt_args(host, TOOL, {"file_path": str(other / "job.py")})
    assert shown[SCRIPT_ARG]["text"] == "print('hi')"
