# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A killed shell command keeps what it printed, and its hint scopes the retry.

A repo-wide lint or test run that blows its timeout used to come back with an
empty ``stdout`` (a Python child block-buffers into a pipe, and the kill threw
the buffer away) and a hint to re-run it with a larger timeout. Now the child
runs unbuffered, a killed pipeline still hands back its last stage's output,
and the hint names the files this turn changed so the retry covers only them.
"""

import os
import subprocess
import sys

import pytest

from gaia.agents.tools import shell_tools
from gaia.agents.tools.command_timeouts import (
    MAX_COMMAND_TIMEOUT,
    TIMEOUT_CLASSES,
    classify_command,
)
from gaia.agents.tools.shell_tools import ShellToolsMixin, _segment_env


class _Host(ShellToolsMixin):
    """A shell host whose turn has written *changed*."""

    def __init__(self, changed=None):
        super().__init__()
        self._changed = list(changed or [])

    def _files_written_this_turn(self):
        return list(self._changed)


def _run_shell(host):
    captured = {}
    import gaia.agents.base.tools as tools_module

    original = tools_module.tool

    def spy(**kwargs):
        def decorate(fn):
            captured[fn.__name__] = fn
            return original(**kwargs)(fn)

        return decorate

    tools_module.tool = spy
    try:
        host.register_shell_tools()
    finally:
        tools_module.tool = original
    return captured["run_shell_command"]


@pytest.fixture
def unrestricted(monkeypatch):
    """The allowlist stood down: python is not a read-only command."""
    monkeypatch.setattr(
        ShellToolsMixin, "_validate_command", staticmethod(lambda *a, **k: None)
    )
    monkeypatch.setattr(ShellToolsMixin, "_system_install_block", lambda *a, **k: None)


def _py(code: str) -> str:
    """A ``python -c`` line the shell tool tokenizes back into *code*."""
    exe = sys.executable.replace("\\", "/")
    return f'"{exe}" -c "{code}"'


# ---------------------------------------------------------------------------
# Unbuffered children
# ---------------------------------------------------------------------------


def test_a_python_child_runs_unbuffered(monkeypatch):
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    assert _segment_env({})["PYTHONUNBUFFERED"] == "1"


def test_the_commands_own_setting_wins(monkeypatch):
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    assert _segment_env({"PYTHONUNBUFFERED": ""})["PYTHONUNBUFFERED"] == ""


def test_the_users_environment_wins(monkeypatch):
    monkeypatch.setenv("PYTHONUNBUFFERED", "0")
    assert _segment_env({})["PYTHONUNBUFFERED"] == "0"


def test_os_environ_is_never_touched(monkeypatch):
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    _segment_env({})
    assert "PYTHONUNBUFFERED" not in os.environ


def test_a_killed_python_command_returns_what_it_printed(unrestricted, tmp_path):
    """Real process: printed, then hung, then killed at the deadline."""
    run = _run_shell(_Host())
    code = "import time; print('ran 3 of 900 checks'); time.sleep(60)"

    result = run(_py(code), timeout=4, working_directory=str(tmp_path))

    assert result["timed_out"] is True
    assert "ran 3 of 900 checks" in result["stdout"]


def test_a_killed_pipeline_returns_its_last_stages_output(
    monkeypatch, unrestricted, tmp_path
):
    """The chained-process path loses no output either."""
    run = _run_shell(_Host())
    producer = _py("import time; print('lint: 2 files checked'); time.sleep(60)")
    relay = _py("import sys; [print(line, end='') for line in sys.stdin]")
    # The assignment keeps the line off cmd.exe, so it runs as chained processes
    # on every OS.
    line = f"GAIA_TEST_STAGE=1 {producer} | {relay}"
    chained = []
    real = shell_tools._run_pipeline
    monkeypatch.setattr(
        shell_tools,
        "_run_pipeline",
        lambda *a, **k: chained.append(1) or real(*a, **k),
    )

    result = run(line, timeout=4, working_directory=str(tmp_path))

    assert chained, "the line did not take the chained-process path"

    assert result["timed_out"] is True
    assert "lint: 2 files checked" in result["stdout"]


# ---------------------------------------------------------------------------
# The hint scopes the retry to this turn's changes
# ---------------------------------------------------------------------------


def _hangs(monkeypatch):
    class _Clock:
        now = 1_000.0

        def monotonic(self):
            return self.now

        def time(self):
            return self.now

    clock = _Clock()

    class _Proc:
        pid = 4242
        args = "hangs"
        returncode = None

        def communicate(self, timeout=None):
            clock.now += timeout or 0
            raise subprocess.TimeoutExpired(cmd="hangs", timeout=timeout)

        def kill(self):
            pass

    monkeypatch.setattr(shell_tools, "time", clock)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(
        shell_tools, "terminate_process_tree", lambda process: ("partial", "")
    )


def _no_bigger_timeout(hint: str) -> None:
    assert "larger timeout" not in hint
    assert str(MAX_COMMAND_TIMEOUT) not in hint


def test_the_hint_names_the_files_changed_this_turn(
    monkeypatch, unrestricted, tmp_path
):
    changed = [str(tmp_path / "src" / "pkg" / "a.py"), str(tmp_path / "ui" / "B.tsx")]
    run = _run_shell(_Host(changed))
    _hangs(monkeypatch)

    result = run("python util/lint.py --all", working_directory=str(tmp_path))

    hint = result["hint"]
    assert f"`{os.path.join('src', 'pkg', 'a.py')}`" in hint
    assert f"`{os.path.join('ui', 'B.tsx')}`" in hint
    assert "instead of the whole repository" in hint
    _no_bigger_timeout(hint)
    assert result["stdout"] == "partial"


def test_with_nothing_changed_the_hint_still_narrows(
    monkeypatch, unrestricted, tmp_path
):
    run = _run_shell(_Host())
    _hangs(monkeypatch)

    result = run("pytest tests/unit", working_directory=str(tmp_path))

    assert "one test file rather than the whole suite" in result["hint"]
    assert "Files changed this turn" not in result["hint"]
    _no_bigger_timeout(result["hint"])


def test_a_long_change_list_is_capped(monkeypatch, unrestricted, tmp_path):
    changed = [str(tmp_path / f"m{n}.py") for n in range(20)]
    run = _run_shell(_Host(changed))
    _hangs(monkeypatch)

    hint = run("pytest", working_directory=str(tmp_path))["hint"]

    assert "`m0.py`" in hint and "`m7.py`" in hint
    assert "`m8.py`" not in hint
    assert "and 12 more" in hint


def test_a_file_outside_the_folder_keeps_its_full_path(
    monkeypatch, unrestricted, tmp_path
):
    outside = str(tmp_path.parent / "elsewhere.py")
    work = tmp_path / "repo"
    work.mkdir()
    run = _run_shell(_Host([outside, outside]))
    _hangs(monkeypatch)

    hint = run("pytest", working_directory=str(work))["hint"]

    # Named once, and not as a ``..`` path the model would have to resolve.
    assert hint.count(f"`{outside}`") == 1
    assert "`.." not in hint


@pytest.mark.parametrize("command", ["pip install -e .", "git clone --help", "ls -la"])
def test_a_build_or_download_is_pointed_at_a_background_run(
    monkeypatch, unrestricted, tmp_path, command
):
    """Scoping to changed files means nothing for an install or a clone."""
    run = _run_shell(_Host([str(tmp_path / "a.py")]))
    _hangs(monkeypatch)

    hint = run(command, working_directory=str(tmp_path))["hint"]

    assert "wait_for_condition" in hint
    assert "Files changed this turn" not in hint
    _no_bigger_timeout(hint)


def test_a_host_without_a_turn_ledger_gets_the_generic_hint(tmp_path):
    """The mixin is usable on its own: no ``_files_written_this_turn`` at all."""

    class _Bare(ShellToolsMixin):
        pass

    hint = _Bare()._timeout_hint("test", str(tmp_path))

    assert "one test file rather than the whole suite" in hint
    _no_bigger_timeout(hint)


# ---------------------------------------------------------------------------
# Repo-wide forms keep their class defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected",
    [
        ("python util/lint.py --all", "script"),
        ("go test ./...", "test"),
        ("pytest tests/unit/agents", "test"),
        ("npm test", "test"),
    ],
)
def test_repo_wide_forms_keep_their_class_default(command, expected):
    assert classify_command(command) is TIMEOUT_CLASSES[expected]
