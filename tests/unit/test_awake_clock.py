# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The awake clock runs while the PC is awake and stops while it sleeps."""

import sys
import time

import pytest

from gaia.utils import awake_clock


def test_it_runs_with_the_process():
    start = awake_clock.monotonic()
    time.sleep(0.3)
    elapsed = awake_clock.monotonic() - start
    # Windows reads it at timer-tick resolution, about 16 ms.
    assert 0.25 <= elapsed <= 1.0


def test_it_never_goes_backwards():
    readings = [awake_clock.monotonic() for _ in range(1000)]
    assert readings == sorted(readings)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows clock source")
def test_on_windows_it_is_the_unbiased_interrupt_time():
    """The interrupt time that leaves out S3 sleep and hibernation.

    Modern Standby is not left out: it is S0, so this clock keeps counting.
    """
    import ctypes

    ticks = ctypes.c_ulonglong()
    assert ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(ticks))
    assert awake_clock.monotonic() == pytest.approx(ticks.value / 1e7, abs=0.1)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows clock source")
def test_on_windows_it_reads_the_unbiased_interrupt_time_and_nothing_else(
    monkeypatch,
):
    """A PC that never used S3 can't tell this clock from one that counts sleep."""

    def known_ticks(ticks):
        ticks._obj.value = 123_456_789_000  # 100 ns units
        return 1

    assert awake_clock._query.__name__ == "QueryUnbiasedInterruptTime"
    monkeypatch.setattr(awake_clock, "_query", known_ticks)
    assert awake_clock.monotonic() == pytest.approx(12_345.6789)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows clock source")
def test_a_clock_windows_refuses_fails_every_limit_loudly(monkeypatch):
    """No fallback clock: a limit that can't be measured says how to recover."""
    import ctypes

    def refuse(_ticks):
        ctypes.set_last_error(87)  # ERROR_INVALID_PARAMETER
        return 0

    monkeypatch.setattr(awake_clock, "_query", refuse)
    with pytest.raises(awake_clock.AwakeClockError) as excinfo:
        awake_clock.monotonic()
    message = str(excinfo.value)
    assert "QueryUnbiasedInterruptTime" in message
    assert "Restart GAIA" in message and "gaia diagnostics" in message
    assert isinstance(excinfo.value.__cause__, OSError)
    assert excinfo.value.__cause__.winerror == 87


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX clock source")
def test_elsewhere_it_is_the_monotonic_clock():
    """Linux and macOS stop ``time.monotonic`` during suspend already."""
    assert awake_clock.monotonic() == pytest.approx(time.monotonic(), abs=0.1)
