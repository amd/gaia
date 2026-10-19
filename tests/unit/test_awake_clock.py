# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AwakeClock counts the time a poll loop was running, not the time it slept."""

import pytest

from gaia.utils.awake_clock import AwakeClock

EIGHT_HOURS = 8 * 60 * 60.0


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_jump_is_skipped_and_steady_time_is_counted():
    raw = FakeClock()
    clock = AwakeClock(raw)
    start = clock()
    for _ in range(10):
        raw.now += 1.0
        clock()
    raw.now += EIGHT_HOURS
    assert clock() - start == pytest.approx(10.0)
    raw.now += 1.0
    assert clock() - start == pytest.approx(11.0)
    assert clock.slept == pytest.approx(EIGHT_HOURS)


def test_steps_just_under_the_gap_all_count():
    raw = FakeClock()
    clock = AwakeClock(raw, gap=5.0)
    start = clock()
    for _ in range(100):
        raw.now += 4.9
        clock()
    assert clock() - start == pytest.approx(490.0)
    assert clock.slept == 0.0
