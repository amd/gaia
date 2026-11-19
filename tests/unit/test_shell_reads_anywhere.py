# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A read-only shell command runs, unasked, on a path outside the session's folders.

Run end to end through ``run_shell_command``: the check before the prompt and
the checks at execution must agree, or a line nobody was asked about is refused
when it runs. A secret, a folder the user removed and a session
``GAIA_ALLOWED_PATHS`` confines keep their refusals.
"""

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY, get_tool_metadata
from gaia.agents.tools.shell_tools import TIER_REFUSE, ShellToolsMixin
from gaia.security import PathValidator


class _Console:
    auto_approve_gated_tools = False
    full_access = False


class Host(ShellToolsMixin):
    debug = False

    def __init__(self, root):
        super().__init__()
        self.console = _Console()
        self.path_validator = PathValidator(allowed_paths=[str(root)])


@pytest.fixture
def folders(tmp_path):
    """``(allowed, outside)``: the session's folder and one beside it."""
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "notes.txt").write_text("from outside\n")
    (outside / ".env").write_text("TOKEN=abc\n")
    return allowed, outside


@pytest.fixture
def run():
    saved = dict(_TOOL_REGISTRY)

    def call(host, command, cwd):
        host.register_shell_tools()
        tool = get_tool_metadata("run_shell_command")["function"]
        return tool(command=command, working_directory=str(cwd))

    try:
        yield call
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _args(command, cwd):
    return {"command": command, "working_directory": str(cwd)}


def test_a_read_outside_the_folders_runs_with_nobody_asked(folders, run):
    allowed, outside = folders
    host = Host(allowed)
    command = f"cat {(outside / 'notes.txt').as_posix()}"

    assert host.skill_grant_covers_call("run_shell_command", _args(command, allowed))
    assert (
        host.policy_refusal_for_call("run_shell_command", _args(command, allowed))
        is None
    )
    result = run(host, command, allowed)

    assert result["status"] == "success", result
    assert "from outside" in result["stdout"]


def test_a_read_run_in_a_folder_outside_runs_too(folders, run):
    allowed, outside = folders
    host = Host(allowed)

    assert host.skill_grant_covers_call("run_shell_command", _args("ls", outside))
    result = run(host, "ls", outside)

    assert result["status"] == "success", result
    assert "notes.txt" in result["stdout"]


def test_a_change_outside_the_folders_still_asks(folders):
    allowed, outside = folders
    host = Host(allowed)
    command = f"rm {(outside / 'notes.txt').as_posix()}"

    assert not host.skill_grant_covers_call(
        "run_shell_command", _args(command, allowed)
    )


def test_one_read_does_not_open_the_folder_to_the_next_command(folders, run):
    allowed, outside = folders
    host = Host(allowed)
    target = (outside / "notes.txt").as_posix()

    assert run(host, f"cat {target}", allowed)["status"] == "success"
    verdict, _ = host._line_verdict(f"cp {target} copy.txt", str(allowed))

    assert verdict is not None
    assert not host.path_validator.is_path_allowed(str(outside), prompt_user=False)


def test_a_credential_file_outside_is_still_refused(folders, run):
    allowed, outside = folders
    host = Host(allowed)
    command = f"cat {(outside / '.env').as_posix()}"

    assert not host.skill_grant_covers_call(
        "run_shell_command", _args(command, allowed)
    )
    result = run(host, command, allowed)

    assert result["status"] == "error"
    assert "holds credentials" in result["error"]
    assert "TOKEN" not in str(result)


def test_a_confined_session_still_refuses_the_read(folders, run, monkeypatch):
    allowed, outside = folders
    monkeypatch.setenv("GAIA_ALLOWED_PATHS", str(allowed))
    host = Host(allowed)
    command = f"cat {(outside / 'notes.txt').as_posix()}"

    assert not host.skill_grant_covers_call(
        "run_shell_command", _args(command, allowed)
    )
    refusal = host.policy_refusal_for_call("run_shell_command", _args(command, allowed))

    assert refusal is not None and refusal.get("tier") == TIER_REFUSE
    assert "GAIA_ALLOWED_PATHS" in refusal["error"]
    result = run(host, command, allowed)
    assert result["status"] == "error"
    assert "from outside" not in str(result)
