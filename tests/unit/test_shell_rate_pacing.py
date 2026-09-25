# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A shell command over the rate limit waits its turn instead of being refused.

A refusal handed the model "wait 5.6s" and cost it a whole step to send the same
command again after the same wait. In a benchmark run that was the recovery
command itself: `python -m pytest`, right after bare `pytest` failed to import
the project. Pacing keeps the rate exactly as capped and loses no step.
"""

import time
from types import SimpleNamespace

import pytest

from gaia.agents.tools import shell_tools
from gaia.agents.tools.shell_tools import ShellToolsMixin


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


class _Clock:
    def __init__(self):
        self.now = 1_000_000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    # Swap the module's handle, not the real `time`: patching the module itself
    # also records subprocess.Popen.wait's POSIX poll backoff as pacing sleeps.
    monkeypatch.setattr(
        shell_tools,
        "time",
        SimpleNamespace(time=clock.time, sleep=clock.sleep, monotonic=time.monotonic),
    )
    return clock


def _run(host, tmp_path):
    from gaia.agents.base.tools import get_tool_metadata

    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"](
        command="ls", working_directory=str(tmp_path)
    )


def test_a_burst_waits_for_the_window_then_runs(clock, tmp_path):
    host = _Host()
    host.shell_command_times.extend([clock.now - 1] * 3)

    result = _run(host, tmp_path)

    assert result["status"] == "success", result
    assert result.get("rate_limited") is not True
    assert result["waited_seconds"] == pytest.approx(9.0)
    # Sliced, not one long sleep, so a Stop mid-wait is seen promptly.
    assert sum(clock.slept) == pytest.approx(9.0)
    assert max(clock.slept) <= ShellToolsMixin._PACE_POLL_SECONDS


def test_the_minute_window_is_paced_too(clock, tmp_path):
    host = _Host()
    host.shell_command_times.extend(clock.now - 50 + i for i in range(10))

    result = _run(host, tmp_path)

    assert result["status"] == "success", result
    assert result["waited_seconds"] == pytest.approx(10.0)


def test_the_paced_rate_never_exceeds_the_cap(clock, tmp_path):
    host = _Host()
    for _ in range(7):
        assert _run(host, tmp_path)["status"] == "success"
    times = list(host.shell_command_times)
    for i, t in enumerate(times):
        assert sum(1 for u in times if t <= u < t + 10) <= 3, times[i:]


def test_a_wait_past_the_cap_is_still_refused(clock, tmp_path):
    host = _Host()
    host.max_rate_limit_wait_seconds = 5
    host.shell_command_times.extend([clock.now - 1] * 3)

    result = _run(host, tmp_path)

    assert result["rate_limited"] is True
    assert result["executed"] is False
    assert clock.slept == []


def test_no_wait_is_reported_when_under_the_limit(clock, tmp_path):
    result = _run(_Host(), tmp_path)

    assert result["status"] == "success"
    assert "waited_seconds" not in result
    assert clock.slept == []


def test_a_stop_during_the_wait_ends_it_instead_of_sleeping_on(
    clock, tmp_path, monkeypatch
):
    """A cancelled call must stop waiting: it runs inside a bounded window.

    `_call_tool_bounded` joins the worker with a timeout and sets the
    cancellation flag on expiry. A wait that slept straight through it kept the
    worker alive past that point and delayed a user's Stop by up to a minute.
    """
    host = _Host()
    host.shell_command_times.extend([clock.now - 1] * 3)
    monkeypatch.setattr(shell_tools, "tool_cancelled", lambda: True)

    result = _run(host, tmp_path)

    assert result["rate_limited"] is True
    assert result["executed"] is False
    assert clock.slept == []
