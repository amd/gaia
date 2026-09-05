# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the persistent shell session (issue #3380).

These run real subprocesses on purpose. A mocked session would prove only that
we called ``subprocess`` — it could not prove that ``cd`` in one call is
visible to the next, which is the entire feature.
"""

import os
import sys
import threading
import time

import pytest

from gaia.agents.tools.shell_session import (
    ShellSession,
    ShellSessionBusy,
    ShellSessionClosed,
)

#: True when the session runs commands as a cmd.exe batch file. Setting
#: GAIA_SHELL to a POSIX shell exercises the other script flavour on the same
#: box — worth doing before touching the script generator.
IS_WINDOWS = os.name == "nt" and not os.environ.get("GAIA_SHELL", "").strip()


def export_command(name: str, value: str) -> str:
    """The platform's way of exporting a variable for later commands."""
    return f"set {name}={value}" if IS_WINDOWS else f"export {name}={value}"


def echo_var_command(name: str) -> str:
    return f"echo %{name}%" if IS_WINDOWS else f'echo "${name}"'


def sleep_command(seconds: float, marker: str = "") -> str:
    """A command whose *child process* blocks, then optionally leaves a marker.

    A grandchild rather than the shell itself: killing only the shell is the
    failure mode the process-group kill exists to prevent, and a marker written
    after the sleep is how a survivor announces itself.
    """
    body = f"import time; time.sleep({seconds})"
    if marker:
        body += f"; open(r'{marker}', 'w').write('survived')"
    return f'"{sys.executable}" -c "{body}"'


@pytest.fixture
def session(tmp_path):
    shell = ShellSession(start_cwd=str(tmp_path))
    yield shell
    shell.close()


class TestWorkingDirectoryPersists:
    def test_cd_survives_to_the_next_command(self, session, tmp_path):
        (tmp_path / "sub").mkdir()

        session.run("cd sub")

        assert session.cwd.replace("\\", "/").endswith("/sub")
        result = session.run("cd" if IS_WINDOWS else "pwd")
        assert "sub" in result.stdout

    def test_working_directory_argument_is_one_shot(self, session, tmp_path):
        """An explicit per-call directory scopes that call, as it always has."""
        (tmp_path / "elsewhere").mkdir()
        before = session.cwd

        session.run(
            "cd" if IS_WINDOWS else "pwd", working_directory=str(tmp_path / "elsewhere")
        )

        assert session.cwd == before

    def test_set_cwd_rejects_a_non_directory(self, session, tmp_path):
        before = session.cwd

        assert session.set_cwd(str(tmp_path / "does-not-exist")) is False
        assert session.cwd == before

    def test_cwd_guard_refuses_a_directory_change(self, tmp_path):
        (tmp_path / "off-limits").mkdir()
        allowed = str(tmp_path)
        shell = ShellSession(
            start_cwd=allowed,
            cwd_guard=lambda path: "off-limits" not in path.replace("\\", "/"),
        )
        try:
            result = shell.run("cd off-limits")

            assert shell.cwd == allowed
            assert result.cwd_change_rejected is not None
        finally:
            shell.close()


class TestEnvironmentPersists:
    def test_exported_variable_survives_to_the_next_command(self, session):
        session.run(export_command("GAIA_TEST_VAR", "persisted"))

        assert session.environment().get("GAIA_TEST_VAR") == "persisted"
        result = session.run(echo_var_command("GAIA_TEST_VAR"))
        assert "persisted" in result.stdout

    def test_inherited_variables_are_not_reported_as_changes(self, session):
        """Only what the session actually diverged, or Windows folds every name."""
        session.run("echo hello")

        # The bug this pins: Windows folds variable names and os.environ
        # upper-cases them, so comparing raw made every inherited variable both
        # an override and a removal — the session would replay the whole
        # environment and unset it at the same time.
        diverged = session.environment()
        assert len(diverged) < len(os.environ) / 4, diverged
        assert session.removed_environment() == []

    def test_set_env_applies_to_the_next_command(self, session):
        session.set_env("GAIA_TEST_PRESET", "from-api")

        result = session.run(echo_var_command("GAIA_TEST_PRESET"))
        assert "from-api" in result.stdout

    def test_a_virtualenv_style_activation_persists(self, session, tmp_path):
        """What venv activation actually is: PATH plus a marker variable."""
        session.run(export_command("VIRTUAL_ENV", str(tmp_path / "venv")))

        assert "VIRTUAL_ENV" in session.environment()
        result = session.run(echo_var_command("VIRTUAL_ENV"))
        assert "venv" in result.stdout

    def test_the_parent_process_is_never_mutated(self, session, tmp_path):
        before_cwd = os.getcwd()
        session.run(export_command("GAIA_TEST_LEAK", "leaked"))
        session.run("cd .")

        assert "GAIA_TEST_LEAK" not in os.environ
        assert os.getcwd() == before_cwd


class TestReset:
    def test_reset_restores_the_starting_state(self, session, tmp_path):
        (tmp_path / "sub").mkdir()
        session.run("cd sub")
        session.run(export_command("GAIA_TEST_RESET", "x"))

        session.reset()

        assert session.cwd == str(tmp_path.resolve())
        assert session.environment() == {}
        result = session.run(echo_var_command("GAIA_TEST_RESET"))
        assert result.stdout.strip() != "x"


class TestTeardown:
    def test_close_makes_further_commands_an_error(self, tmp_path):
        shell = ShellSession(start_cwd=str(tmp_path))
        shell.run("echo hello")

        shell.close()

        assert shell.closed is True
        with pytest.raises(ShellSessionClosed):
            shell.run("echo hello")

    def test_close_removes_the_session_temp_directory(self, tmp_path):
        shell = ShellSession(start_cwd=str(tmp_path))
        shell.run("echo hello")
        temp_dir = shell._temp_dir  # pylint: disable=protected-access
        assert temp_dir and os.path.isdir(temp_dir)

        shell.close()

        assert not os.path.isdir(temp_dir)

    def test_a_timed_out_command_leaves_no_running_child(self, session, tmp_path):
        """The command's children are killed too, not just the shell."""
        marker = tmp_path / "survivor.txt"

        result = session.run(sleep_command(4, str(marker)), timeout=1)

        assert result.timed_out is True
        time.sleep(6)
        assert not marker.exists(), "a child outlived the timeout and kept running"


class TestSerialisation:
    def test_a_second_command_waits_for_the_first(self, session):
        """Two threads against one session must not interleave."""
        order = []
        barrier = threading.Barrier(2)

        def slow():
            barrier.wait()
            session.run(sleep_command(2), timeout=20)
            order.append("slow")

        def quick():
            barrier.wait()
            time.sleep(0.3)
            session.run("echo quick", timeout=20)
            order.append("quick")

        threads = [threading.Thread(target=slow), threading.Thread(target=quick)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert order == ["slow", "quick"]

    def test_a_busy_session_says_so_instead_of_hanging(self, session):
        """#2600: a command past its tool timeout is still inside the session."""
        started = threading.Event()

        def hog():
            started.set()
            session.run(sleep_command(6), timeout=30)

        thread = threading.Thread(target=hog)
        thread.start()
        started.wait(timeout=5)
        time.sleep(0.5)
        try:
            with pytest.raises(ShellSessionBusy):
                session.run("echo blocked", timeout=1)
        finally:
            thread.join(timeout=60)


class TestExecution:
    def test_exit_code_is_reported(self, session):
        result = session.run("exit 3")

        assert result.return_code == 3

    def test_stdout_is_captured(self, session):
        result = session.run("echo captured")

        assert "captured" in result.stdout
        assert result.return_code == 0

    def test_run_argv_applies_the_session_environment(self, session):
        session.set_env("GAIA_TEST_ARGV", "argv-value")

        result = session.run_argv(
            [sys.executable, "-c", "import os; print(os.environ['GAIA_TEST_ARGV'])"]
        )

        assert "argv-value" in result.stdout

    def test_empty_command_is_refused(self, session):
        with pytest.raises(ValueError):
            session.run("   ")
