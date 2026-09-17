# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A wedged task must not outlive its timeout.

``subprocess.run(timeout=...)`` kills the direct child and then blocks
draining pipes a surviving grandchild still holds open, so the timeout does
not end the wait. One task in a benchmark run sat for 92 minutes against a
900-second limit and recorded zero tokens — an infrastructure hang that read
back as an agent doing nothing.
"""

import subprocess
import sys
import time

import pytest

from gaia.factory.tasks.runner import _run_child

# A child that spawns a grandchild inheriting its pipes, then exits itself.
# This is the shape that hangs: killing the child alone leaves the pipe open.
_SPAWNS_A_SURVIVOR = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
    "time.sleep(120)"
)

_EXITS_CLEANLY = "print('done')"


def _argv(code):
    return [sys.executable, "-c", code]


def test_a_well_behaved_child_returns_its_output():
    code, out, _err = _run_child(
        _argv(_EXITS_CLEANLY), cwd=None, env=None, timeout_s=60
    )
    assert code == 0
    assert "done" in out


def test_a_child_that_outlives_the_timeout_raises():
    with pytest.raises(subprocess.TimeoutExpired):
        _run_child(
            _argv("import time; time.sleep(120)"), cwd=None, env=None, timeout_s=2
        )


def test_a_surviving_grandchild_does_not_extend_the_wait():
    """The regression: the wait must end at the timeout, not at grandchild exit.

    The grandchild sleeps for 120s holding the inherited pipe. Before the
    fix this call blocked for the full 120s despite a 3s timeout.
    """
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run_child(_argv(_SPAWNS_A_SURVIVOR), cwd=None, env=None, timeout_s=3)
    elapsed = time.monotonic() - started
    # 3s timeout + a bounded drain. Anything approaching the grandchild's
    # 120s sleep means the pipe is still holding the wait open.
    assert elapsed < 40, f"timeout did not end the wait: took {elapsed:.0f}s"
