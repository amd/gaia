# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A command that needs approval must not run when nothing can approve it.

Every assertion here checks the *side effect*, not the status string. A tool
that reports an error while having already deleted the file is the failure this
guards against, and a status-only assertion cannot tell the two apart.

The host is a bare ``ShellToolsMixin`` with no ``console`` attribute: the shape
a unit test, a script, or an embedding application produces. Approval belongs to
the console, so with no console the answer is no.
"""

import os
import tempfile

import pytest

from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools.shell_tools import ShellToolsMixin


class _NoConsoleHost(ShellToolsMixin):
    """No console, so no way to ask the user anything."""


@pytest.fixture
def workdir(tmp_path):
    (tmp_path / "keep.txt").write_text("precious\n")
    return tmp_path


def _run(command, cwd):
    host = _NoConsoleHost()
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"](
        command=command, working_directory=str(cwd), timeout=30
    )


def test_a_destructive_command_leaves_the_file_alone(workdir):
    result = _run("rm keep.txt", workdir)

    assert (workdir / "keep.txt").exists(), "the file was deleted without approval"
    assert result["status"] == "error", result


def test_a_command_that_writes_creates_nothing(workdir):
    result = _run("touch made-without-asking.txt", workdir)

    assert not (workdir / "made-without-asking.txt").exists(), result
    assert result["status"] == "error", result


@pytest.mark.parametrize(
    "command",
    [
        "curl https://example.com",
        "git commit -m x",
        "pip install requests",
        "npm install left-pad",
    ],
)
def test_commands_outside_the_no_prompt_list_do_not_run(command, workdir):
    result = _run(command, workdir)

    assert result["status"] == "error", result
    assert result.get("executed") is not True, result


def test_a_read_only_command_still_runs(workdir):
    """The control: this suite must not pass by refusing everything."""
    result = _run("ls", workdir)

    assert result["status"] == "success", result
    assert "keep.txt" in result["stdout"]


def test_the_blanket_approval_env_does_not_extend_to_prompted_commands(
    workdir, monkeypatch
):
    """An unattended run pre-approves prompts; it does not widen what may run."""
    monkeypatch.setenv("GAIA_AUTO_APPROVE_TOOLS", "1")

    result = _run("rm keep.txt", workdir)

    assert (workdir / "keep.txt").exists(), "blanket approval deleted the file"
    assert result["status"] == "error", result


def test_the_system_temp_dir_is_not_writable_by_default(tmp_path):
    """A host with no path validator must not fall back to unrestricted access."""
    target = os.path.join(tempfile.gettempdir(), "gaia-should-not-exist.txt")
    if os.path.exists(target):
        os.unlink(target)

    _run(f"touch {target}", tmp_path)

    assert not os.path.exists(target), "wrote outside any allowed path"
