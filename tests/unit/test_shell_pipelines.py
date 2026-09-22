# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A validated ``a | b`` must run as a pipeline on macOS and Linux.

The executor runs POSIX commands as an argv list, without a shell, and the
validator has already dropped the ``|`` tokens. Joining the segments back into
one argv ran ``cat notes.txt | sort`` as ``cat notes.txt sort``: unsorted
output, a "No such file" on stderr, and ``status: success``. The guardrails
tell the model pipes are allowed, so it trusted the result.
"""

import os
import sys

import pytest

from gaia.agents.tools.shell_tools import ShellToolsMixin

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Windows pipelines run through cmd.exe"
)


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


def _run(command, cwd):
    """Run *command* through a fresh host, so the rate limiter never trips."""
    from gaia.agents.base.tools import get_tool_metadata

    host = _Host()
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"](
        command=command, working_directory=str(cwd)
    )


@pytest.fixture
def notes(tmp_path):
    (tmp_path / "notes.txt").write_text("pear\napple\nfig\napple pie\n")
    return tmp_path


def test_pipe_feeds_one_stage_into_the_next(notes):
    result = _run("cat notes.txt | sort", notes)

    assert result["status"] == "success", result
    assert result["stdout"] == "apple\napple pie\nfig\npear\n"
    assert result["stderr"] == ""
    assert result["return_code"] == 0


def test_three_stage_pipeline_counts_matches(notes):
    result = _run("cat notes.txt | grep apple | wc -l", notes)

    assert result["stdout"].strip() == "2", result
    assert result["return_code"] == 0


def test_reader_that_stops_early_is_not_a_failure(notes):
    """``| head`` closing its input kills the writer with SIGPIPE — normal."""
    (notes / "big.txt").write_text("line\n" * 200_000)

    result = _run("cat big.txt | head -1", notes)

    assert result["stdout"] == "line\n"
    assert result["return_code"] == 0, result


def test_failed_stage_is_not_masked_by_a_later_success(notes):
    """``pytest | tail`` must not report a failing suite as passing.

    The verification line reads ``return_code`` to decide "ran and passed", so
    a pipeline reports its rightmost failure, not just its last stage.
    """
    result = _run("cat missing.txt | sort", notes)

    assert result["return_code"] != 0, result
    assert result["has_errors"] is True
    assert "missing.txt" in result["stderr"]


def test_every_stage_stderr_is_kept(notes):
    result = _run("cat missing.txt | grep nothing", notes)

    assert "missing.txt" in result["stderr"], result


def test_pipeline_runs_in_the_working_directory(notes):
    (notes / "sub").mkdir()
    (notes / "sub" / "only-here.txt").write_text("x\n")

    result = _run("ls | grep only", notes / "sub")

    assert result["stdout"].strip() == "only-here.txt", result
    assert result["cwd"] == str((notes / "sub").resolve())


def test_pipeline_times_out_and_leaves_no_process_behind(notes, monkeypatch):
    from gaia.agents.tools import shell_tools

    started = []
    real_popen = shell_tools.subprocess.Popen

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(shell_tools.subprocess, "Popen", recording_popen)
    fifo = notes / "never-written"
    os.mkfifo(fifo)

    host = _Host()
    host.register_shell_tools()
    from gaia.agents.base.tools import get_tool_metadata

    result = get_tool_metadata("run_shell_command")["function"](
        command="cat never-written | sort", working_directory=str(notes), timeout=1
    )

    assert result.get("timed_out") is True, result
    assert started and all(proc.poll() is not None for proc in started)
