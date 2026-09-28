# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A command outside the read-only allowlist must not run, and must not half-run.

Every assertion here checks the *side effect*, not the status string. A tool
that reports an error while having already deleted the file is the failure this
guards against, and a status-only assertion cannot tell the two apart.

The host is a bare ``ShellToolsMixin``: the shape a unit test, a script, or an
embedding application produces. The allowlist refusal is enforced inside the
tool itself, so it holds with no console and with blanket approval enabled.

The agent-level confirmation gate is a separate tier, covered in
``tests/unit/agents/test_console_tool_confirmation.py``.
"""

import os
import tempfile

import pytest

import gaia
from gaia.agents.base.console import auto_approve_env_enabled
from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools.shell_tools import ShellToolsMixin


class _NoConsoleHost(ShellToolsMixin):
    """No console, so nothing in the call path could prompt or approve."""


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

    assert (workdir / "keep.txt").exists(), "the file was deleted despite the refusal"
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
def test_commands_outside_the_allowlist_do_not_run(command, workdir):
    result = _run(command, workdir)

    assert result["status"] == "error", result
    assert result.get("executed") is not True, result


def test_a_read_only_command_still_runs(workdir):
    """The control: this suite must not pass by refusing everything."""
    result = _run("ls", workdir)

    assert result["status"] == "success", result
    assert "keep.txt" in result["stdout"]


def test_blanket_approval_does_not_widen_the_allowlist(workdir, monkeypatch):
    """An unattended run pre-approves prompts; it does not widen what may run.

    The opt-in is read from the startup environment snapshot, so
    ``monkeypatch.setenv`` would be a no-op here — patch the reader instead, and
    assert it took, or the rest of the test cannot fail.
    """
    monkeypatch.setattr(gaia, "pre_dotenv_env", lambda name: "1")
    assert auto_approve_env_enabled(), "the opt-in never took; the test is vacuous"

    result = _run("rm keep.txt", workdir)

    assert (workdir / "keep.txt").exists(), "blanket approval deleted the file"
    assert result["status"] == "error", result


def test_the_system_temp_dir_is_not_written_to(tmp_path):
    """An absolute path outside the working directory is not a way around this."""
    target = os.path.join(tempfile.gettempdir(), f"gaia-should-not-exist-{os.getpid()}")

    result = _run(f"touch {target}", tmp_path)

    assert result["status"] == "error", result
    assert not os.path.exists(target), "wrote outside the working directory"
