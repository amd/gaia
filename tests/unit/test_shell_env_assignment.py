# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A leading ``VAR=value`` must set that variable for that one command.

Every inline assignment refused across four models' benchmark runs was a
``PYTHONPATH`` needed to import the project under test, and the refusal's advice
— run it without the assignment — fails with ImportError, so the agent had no
route left to verify its own work.

The value never reaches a shell: it is handed to the subprocess as an
environment entry, and the command after it is allowlist-checked exactly as it
was before.

Only the tests that actually launch a process carry ``@posix_only``. The
refusal and parsing tests decide before anything reaches a shell, so they run
everywhere — a module-level skip would hide them on the platform most GAIA
contributors develop on.
"""

import os
import shutil
import sys

import pytest

from gaia.agents.tools.shell_tools import ShellToolsMixin

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows steps run through cmd.exe; TZ/date are not its commands",
)


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


class _PytestHost(ShellToolsMixin):
    """A host whose loaded skill granted ``shell:execute:pytest``."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from gaia.skills.binaries import BinaryGrants

        self._granted_binaries = BinaryGrants()
        self._granted_binaries.grant("pytest", skill_name="write-tests")


def _run(command, cwd, host_cls=_Host):
    """Run *command* through a fresh host, so the rate limiter never trips."""
    from gaia.agents.base.tools import get_tool_metadata

    host = host_cls()
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"](
        command=command, working_directory=str(cwd), timeout=120
    )


# --------------------------------------------------------------------------
# The measured case: PYTHONPATH is what makes the project importable.
# --------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    """A tiny project whose test only imports its module via PYTHONPATH."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "mymod.py").write_text("VALUE = 7\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mymod.py").write_text(
        "import mymod\n\n\ndef test_value():\n    assert mymod.VALUE == 7\n"
    )
    return tmp_path


@pytest.mark.skipif(shutil.which("pytest") is None, reason="pytest not on PATH")
@posix_only
def test_pythonpath_is_what_makes_the_suite_importable(project):
    """The same command passes with the assignment and fails without it."""
    without = _run("pytest -q tests/", project, _PytestHost)
    assert without["return_code"] != 0, without

    with_it = _run("PYTHONPATH=lib pytest -q tests/", project, _PytestHost)

    assert with_it["status"] == "success", with_it
    assert with_it["return_code"] == 0, with_it


def test_an_assignment_does_not_hide_the_binary_from_its_grant():
    """The grant reads the command after the assignment, flags and all."""
    host = _PytestHost()

    covered = host.skill_grant_covers_call(
        "run_shell_command", {"command": "PYTHONPATH=lib pytest -q tests/"}
    )
    refused = host.skill_grant_covers_call(
        "run_shell_command", {"command": "PYTHONPATH=lib pytest --pdb"}
    )

    assert covered is True
    assert refused is False


# --------------------------------------------------------------------------
# Scope: the variable reaches that one subprocess and nothing else.
# --------------------------------------------------------------------------


@pytest.fixture
def other_zone(tmp_path):
    """A timezone whose UTC offset differs from this host's."""
    local = _run("date +%z", tmp_path)["stdout"].strip()
    return "Asia/Tokyo" if local == "+0000" else "UTC"


def _offset(zone):
    return "+0900" if zone == "Asia/Tokyo" else "+0000"


@posix_only
def test_the_assignment_reaches_the_command(tmp_path, other_zone):
    result = _run(f"TZ={other_zone} date +%z", tmp_path)

    assert result["status"] == "success", result
    assert result["stdout"].strip() == _offset(other_zone)


@pytest.mark.skipif(shutil.which("pytest") is None, reason="pytest not on PATH")
@posix_only
def test_several_assignments_on_one_command_all_apply(tmp_path):
    """The command itself reports what it was given, so both must be there."""
    (tmp_path / "test_env.py").write_text(
        "import os\n\n\ndef test_both():\n"
        "    assert os.environ['GAIA_A'] == '1'\n"
        "    assert os.environ['GAIA_B'] == '2'\n"
    )

    result = _run("GAIA_A=1 GAIA_B=2 pytest -q test_env.py", tmp_path, _PytestHost)

    assert result["return_code"] == 0, result


@posix_only
def test_an_empty_value_is_allowed(tmp_path):
    result = _run("TZ= date +%z", tmp_path)

    assert result["status"] == "success", result


@posix_only
def test_it_does_not_leak_into_this_process(tmp_path, other_zone):
    before = os.environ.get("TZ")

    _run(f"TZ={other_zone} date +%z", tmp_path)

    assert os.environ.get("TZ") == before


@posix_only
def test_it_does_not_leak_into_a_later_segment(tmp_path, other_zone):
    """``date`` ignores stdin, so stage two reports its own environment."""
    local = _run("date +%z", tmp_path)["stdout"].strip()

    result = _run(f"TZ={other_zone} date +%z | date +%z", tmp_path)

    assert result["stdout"].strip() == local, result
    assert local != _offset(other_zone)


@posix_only
def test_it_does_not_leak_into_a_later_step(tmp_path, other_zone):
    local = _run("date +%z", tmp_path)["stdout"].strip()

    result = _run(f"TZ={other_zone} date +%z && date +%z", tmp_path)

    assert result["status"] == "success", result
    assert result["stdout"].split() == [_offset(other_zone), local]


@posix_only
def test_each_segment_carries_its_own(tmp_path, other_zone):
    result = _run(f"date +%z | TZ={other_zone} date +%z", tmp_path)

    assert result["stdout"].strip() == _offset(other_zone), result


@posix_only
def test_it_survives_a_stderr_redirection_on_the_same_command(tmp_path, other_zone):
    """The redirection is lifted out first, so it lands on the right segment."""
    result = _run(f"TZ={other_zone} date +%z 2>&1 | cat", tmp_path)

    assert result["stdout"].strip() == _offset(other_zone), result


def test_a_quoted_value_keeps_its_spaces():
    """shlex owns the quoting, so a value may hold a space like any argument."""
    from gaia.agents.tools.shell_tools import _parse_line

    steps, error = _parse_line('GREETING="hello there" echo hi')

    assert error is None, error
    assert steps[0].envs == ({"GREETING": "hello there"},)
    assert steps[0].segments == [["echo", "hi"]]


# --------------------------------------------------------------------------
# What is refused, and how loudly.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "PATH",
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "BASH_ENV",
        "GIT_SSH_COMMAND",
        "NODE_OPTIONS",
        "PYTEST_ADDOPTS",
        "LESSOPEN",
        "GIT_CONFIG_COUNT",
        "GH_PAGER",
        "PYTHONSTARTUP",
    ],
)
def test_a_loader_or_hook_variable_is_refused_by_name(name, tmp_path):
    result = _run(f"{name}=/tmp/x ls", tmp_path)

    assert result["status"] == "error", result
    assert result["executed"] is False
    assert name in result["error"]


def test_the_denial_is_case_insensitive(tmp_path):
    """Windows matches environment names that way, so a lowercase one is PATH."""
    result = _run("path=/tmp/x ls", tmp_path)

    assert result["status"] == "error", result
    assert result["executed"] is False


def test_the_allowlist_still_decides_what_runs(tmp_path):
    result = _run("PYTHONPATH=. rm -rf x", tmp_path)

    assert result["status"] == "error", result
    assert "'rm'" in result["error"], result
    assert result["executed"] is False


def test_an_assignment_with_no_command_is_refused(tmp_path):
    result = _run("FOO=bar", tmp_path)

    assert result["status"] == "error", result
    assert result["executed"] is False


def test_an_assignment_with_no_command_in_a_chain_is_refused(tmp_path):
    result = _run("ls && FOO=bar", tmp_path)

    assert result["status"] == "error", result
    assert result["executed"] is False


def test_cd_takes_no_assignment(tmp_path):
    (tmp_path / "sub").mkdir()

    result = _run("FOO=bar cd sub && ls", tmp_path)

    assert result["status"] == "error", result
    assert result["executed"] is False


@posix_only
def test_a_later_token_that_looks_like_one_is_an_argument(tmp_path):
    """Only a LEADING token is an assignment; ``grep a=b`` is still a pattern."""
    (tmp_path / "notes.txt").write_text("a=b\nc\n")

    result = _run("grep a=b notes.txt", tmp_path)

    assert result["status"] == "success", result
    assert result["stdout"] == "a=b\n"


def test_a_value_outside_the_allowed_paths_is_refused(tmp_path):
    """An env value is held to the same path rule as an argument."""

    class _Validator:
        @staticmethod
        def is_path_allowed(path):
            return str(path).startswith(str(tmp_path))

    from gaia.agents.base.tools import get_tool_metadata

    host = _Host()
    host.path_validator = _Validator()
    host.register_shell_tools()
    result = get_tool_metadata("run_shell_command")["function"](
        command="PYTHONPATH=/etc/ssl ls", working_directory=str(tmp_path)
    )

    assert result["status"] == "error", result
    assert result["executed"] is False
