# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A PC that sleeps mid-answer does not wake up to a "the model stalled" error.

On Windows the monotonic clock keeps counting while the machine sleeps. Every
silence limit measured with it (the 120 s stream idle limit, the socket read
deadline behind it, the Agent UI's no-progress limit) used to fire the moment
the PC woke from a long enough sleep, though the local model server had slept
too and was about to carry on. These tests jump a fake clock by hours between
two polls, which is what a sleep looks like to the process, and check that a
genuinely silent server is still caught.
"""

import threading
import time

import httpcore
import pytest

from gaia.llm.retry import StreamStalledError
from gaia.llm.stream_watchdog import (
    ByteActivity,
    StallLimits,
    _AbortableStream,
    watch_stream,
)

IDLE = 120.0
LIMITS = StallLimits(first_output=IDLE, idle=IDLE, remote=False)
EIGHT_HOURS = 8 * 60 * 60.0


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.now

    def advance(self, seconds):
        with self._lock:
            self.now += seconds


def _poll_until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, "condition not reached"
        time.sleep(0.01)


def test_a_stream_that_sleeps_with_the_pc_carries_on_after_waking():
    raw = FakeClock()
    activity = ByteActivity(clock=raw)
    resume = threading.Event()
    reached_silence = threading.Event()

    def produce():
        yield "Hel"
        reached_silence.set()
        assert resume.wait(10)
        yield "lo"

    stream = watch_stream(
        produce, limits=LIMITS, activity=activity, is_output=bool, clock=raw
    )
    got = [next(stream)]
    assert reached_silence.wait(5)

    def sleep_then_wake():
        time.sleep(0.3)  # the consumer is polling the silence now
        raw.advance(EIGHT_HOURS)
        time.sleep(0.6)  # it polls again: the first look after waking
        resume.set()

    waker = threading.Thread(target=sleep_then_wake)
    waker.start()
    got.extend(stream)
    waker.join()
    assert got == ["Hel", "lo"]


class SteadyClock(FakeClock):
    """Awake time: every read is one second later, so there is never a gap."""

    def __call__(self):
        with self._lock:
            self.now += 1.0
            return self.now


def test_a_server_that_goes_silent_while_awake_is_still_cut_off():
    raw = SteadyClock()
    activity = ByteActivity(clock=raw)
    never = threading.Event()

    def produce():
        yield "Hel"
        never.wait(10)
        yield "never"

    short = StallLimits(first_output=10.0, idle=10.0, remote=False)
    stream = watch_stream(
        produce, limits=short, activity=activity, is_output=bool, clock=raw
    )
    try:
        assert next(stream) == "Hel"
        with pytest.raises(StreamStalledError):
            next(stream)
    finally:
        never.set()


class _QuietSocket(httpcore.NetworkStream):
    """A connection whose reads time out until told to return data."""

    def __init__(self, on_read):
        self.on_read = on_read
        self.reads = 0

    def read(self, max_bytes, timeout=None):
        self.reads += 1
        data = self.on_read(self.reads)
        if data is None:
            raise httpcore.ReadTimeout("slice")
        return data

    def write(self, buffer, timeout=None):
        pass

    def close(self):
        pass


def test_a_socket_read_spanning_a_sleep_does_not_time_out(monkeypatch):
    import gaia.llm.stream_watchdog as watchdog

    raw = FakeClock()
    monkeypatch.setattr(watchdog.time, "monotonic", raw)

    def on_read(n):
        if n == 3:
            raw.advance(EIGHT_HOURS)  # the PC slept during this slice
        return b"data" if n == 5 else None

    stream = _AbortableStream(_QuietSocket(on_read), ByteActivity(clock=raw))
    assert stream.read(1024, timeout=150.0) == b"data"


def test_a_socket_read_still_times_out_on_a_silent_server(monkeypatch):
    import gaia.llm.stream_watchdog as watchdog

    raw = FakeClock()
    monkeypatch.setattr(watchdog.time, "monotonic", raw)

    def on_read(_n):
        raw.advance(1.0)
        return None

    stream = _AbortableStream(_QuietSocket(on_read), ByteActivity(clock=raw))
    with pytest.raises(httpcore.ReadTimeout):
        stream.read(1024, timeout=150.0)


def test_a_request_the_pc_slept_through_keeps_its_retry_budget():
    """A connection the sleep dropped is retried, not given up on as overdue."""
    import requests

    from gaia.llm.retry import ModelCallRetrier, RetryPolicy
    from gaia.utils.awake_clock import AwakeClock

    raw = FakeClock()
    attempts = []

    def call():
        attempts.append(raw.now)
        if len(attempts) == 1:
            raw.advance(EIGHT_HOURS)
            raise requests.exceptions.ConnectionError("connection reset")
        return "ok"

    retrier = ModelCallRetrier(
        RetryPolicy(deadline=120.0), sleep=lambda _s: None, clock=AwakeClock(raw)
    )
    assert retrier.call(call) == "ok"
    assert len(attempts) == 2


def test_lemonade_requests_measure_their_retry_budget_in_awake_time():
    from gaia.llm.lemonade_client import LemonadeClient
    from gaia.utils.awake_clock import monotonic

    client = LemonadeClient(base_url="http://127.0.0.1:1/api/v1")
    assert client._model_retrier()._clock is monotonic
