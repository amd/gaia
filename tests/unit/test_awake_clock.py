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
    """The interrupt time that leaves out sleep and hibernation."""
    import ctypes

    ticks = ctypes.c_ulonglong()
    assert ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(ticks))
    assert awake_clock.monotonic() == pytest.approx(ticks.value / 1e7, abs=0.1)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX clock source")
def test_elsewhere_it_is_the_monotonic_clock():
    """Linux and macOS stop ``time.monotonic`` during suspend already."""
    assert awake_clock.monotonic() == pytest.approx(time.monotonic(), abs=0.1)
