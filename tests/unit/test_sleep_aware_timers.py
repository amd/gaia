# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A PC that sleeps mid-answer does not wake up to a "the model stalled" error.

On Windows ``time.monotonic()`` keeps counting while the machine sleeps. Every
limit measured with it (the 120 s stream idle limit, the socket read deadline
behind it, the retry budget, a tool's time limit) used to fire the moment the
PC woke from a long enough sleep, though the local model server had slept too
and was about to carry on. These tests stand in for such a PC: its
``time.monotonic`` jumps by hours during a sleep while the awake clock does
not, and the code under test runs with its default clocks.
"""

import importlib
import threading
import time

import httpcore
import pytest
import requests

from gaia.llm.retry import StreamStalledError
from gaia.utils import awake_clock

IDLE = 120.0
EIGHT_HOURS = 8 * 60 * 60.0


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.now

    def advance(self, seconds):
        with self._lock:
            self.now += seconds


class SleepingPC:
    """``time.monotonic`` as Windows has it (counts sleep), and the awake clock."""

    def __init__(self):
        self.monotonic = _Clock()
        self.awake = _Clock()

    def sleep(self, seconds):
        self.monotonic.advance(seconds)

    def run(self, seconds):
        self.monotonic.advance(seconds)
        self.awake.advance(seconds)


@pytest.fixture
def pc():
    """The PC's clocks, with the stream watchdog reloaded to read them.

    Reloaded so default clock arguments bind the stand-ins, whichever clock
    the module uses by default; that way these tests fail on any version that
    measures a limit on ``time.monotonic``. The swap lasts only this test, and
    nothing in this module starts a thread that outlives it.
    """
    import gaia.llm.stream_watchdog as watchdog

    machine = SleepingPC()
    real_monotonic, real_awake = time.monotonic, awake_clock.monotonic
    time.monotonic, awake_clock.monotonic = machine.monotonic, machine.awake
    machine.watchdog = importlib.reload(watchdog)
    try:
        yield machine
    finally:
        time.monotonic, awake_clock.monotonic = real_monotonic, real_awake
        importlib.reload(watchdog)


def _limits(watchdog, idle=IDLE):
    return watchdog.StallLimits(first_output=idle, idle=idle, remote=False)


def test_a_stream_that_sleeps_with_the_pc_carries_on_after_waking(pc):
    wd = pc.watchdog
    resume = threading.Event()
    reached_silence = threading.Event()

    def produce():
        yield "Hel"
        reached_silence.set()
        assert resume.wait(10)
        yield "lo"

    stream = wd.watch_stream(
        produce, limits=_limits(wd), activity=wd.ByteActivity(), is_output=bool
    )
    got = [next(stream)]
    assert reached_silence.wait(5)

    def sleep_then_wake():
        time.sleep(0.3)  # the consumer is polling the silence now
        pc.sleep(EIGHT_HOURS)
        time.sleep(0.6)  # it polls again after waking
        resume.set()

    waker = threading.Thread(target=sleep_then_wake)
    waker.start()
    got.extend(stream)
    waker.join()
    assert got == ["Hel", "lo"]


def test_a_server_silent_while_the_process_was_paused_is_still_cut_off(pc):
    """Awake but not running (a VM pause, a stalled thread) is not sleep: the
    silence counts, all at once."""
    wd = pc.watchdog
    never = threading.Event()

    def produce():
        yield "Hel"
        never.wait(10)
        yield "never"

    stream = wd.watch_stream(
        produce,
        limits=_limits(wd, idle=10.0),
        activity=wd.ByteActivity(),
        is_output=bool,
    )
    try:
        assert next(stream) == "Hel"
        pc.run(30.0)  # one jump, longer than the limit, with the PC awake
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


def test_a_socket_read_spanning_a_sleep_does_not_time_out(pc):
    wd = pc.watchdog

    def on_read(n):
        if n == 3:
            pc.sleep(EIGHT_HOURS)
        return b"data" if n == 5 else None

    stream = wd._AbortableStream(_QuietSocket(on_read), wd.ByteActivity())
    assert stream.read(1024, timeout=150.0) == b"data"


def test_a_socket_read_still_times_out_on_a_silent_server(pc):
    wd = pc.watchdog

    def on_read(_n):
        pc.run(1.0)
        return None

    stream = wd._AbortableStream(_QuietSocket(on_read), wd.ByteActivity())
    with pytest.raises(httpcore.ReadTimeout):
        stream.read(1024, timeout=150.0)


def test_a_request_the_pc_slept_through_keeps_its_retry_budget(pc):
    """A connection the sleep dropped is retried, not given up on as overdue."""
    from gaia.llm.lemonade_client import LemonadeClient

    client = LemonadeClient(base_url="http://127.0.0.1:1/api/v1")
    client._retry_sleep = lambda _s: None
    attempts = []

    def call():
        attempts.append(1)
        if len(attempts) == 1:
            pc.sleep(EIGHT_HOURS)
            raise requests.exceptions.ConnectionError("connection reset")
        return "ok"

    assert client._model_retrier().call(call) == "ok"
    assert len(attempts) == 2


def _busy_client(pc, statuses):
    from gaia.llm.lemonade_client import LemonadeClient, LemonadeStatus

    client = LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    client._retry_sleep = pc.run
    busy = LemonadeStatus(running=False)
    busy.busy = True
    ready = LemonadeStatus(running=True)
    answers = iter(statuses)

    def get_status(**_kwargs):
        answer = next(answers)
        if answer == "sleep":
            pc.sleep(EIGHT_HOURS)
        return ready if answer == "ready" else busy

    client.get_status = get_status
    return client, ready


def test_a_busy_server_wait_the_pc_slept_through_keeps_waiting(pc):
    """The load wait (600 s) leaves the sleep out, like the retry budget."""
    client, ready = _busy_client(pc, ["sleep", "busy", "ready"])
    assert client._wait_while_busy("m") is ready


def test_a_busy_server_wait_still_gives_up_while_awake(pc, monkeypatch):
    from gaia.llm.lemonade_client import LemonadeUnresponsiveError

    monkeypatch.setenv("GAIA_LLM_LOAD_WAIT", "10")
    client, _ready = _busy_client(pc, ["busy"] * 100)
    with pytest.raises(LemonadeUnresponsiveError):
        client._wait_while_busy("m")
    assert pc.awake() - 1000.0 <= 10.0 + 1e-9


def test_a_tool_the_pc_slept_through_is_not_timed_out(pc):
    """A tool's 180 s limit, like every other limit, leaves the sleep out."""
    from gaia.agents.base.agent import Agent

    class _Host:
        def _bounded_tool_timeout(self, _name, _args):
            return 180.0

    started, release = threading.Event(), threading.Event()

    def slow_tool():
        started.set()
        assert release.wait(10)
        return "done"

    def sleep_mid_tool():
        assert started.wait(5)
        time.sleep(0.2)
        pc.sleep(EIGHT_HOURS)
        time.sleep(0.8)  # the limit is checked after waking
        release.set()

    waker = threading.Thread(target=sleep_mid_tool)
    waker.start()
    try:
        assert Agent._call_tool_bounded(_Host(), slow_tool, {}, "slow_tool") == "done"
    finally:
        release.set()
        waker.join()


def test_a_tool_that_overruns_while_awake_is_still_timed_out(pc):
    from gaia.agents.base.agent import Agent, ToolExecutionTimeout

    class _Host:
        def _bounded_tool_timeout(self, _name, _args):
            return 5.0

    started, release = threading.Event(), threading.Event()

    def stuck_tool():
        started.set()
        release.wait(10)

    def overrun():
        assert started.wait(5)
        time.sleep(0.2)  # after the limit's clock started
        pc.run(6.0)

    threading.Thread(target=overrun).start()
    try:
        with pytest.raises(ToolExecutionTimeout):
            Agent._call_tool_bounded(_Host(), stuck_tool, {}, "stuck_tool")
    finally:
        release.set()


def test_a_tool_whose_clock_fails_is_cancelled_not_left_running(monkeypatch):
    """A broken awake clock fails the call; the tool body must be told to stop."""
    from gaia.agents.base.agent import Agent
    from gaia.tool_cancellation import tool_cancelled

    class _Host:
        def _bounded_tool_timeout(self, _name, _args):
            return 180.0

    reads = []

    def failing_clock():
        reads.append(1)
        if len(reads) > 1:
            raise awake_clock.AwakeClockError("the standby monitor gave up")
        return 1000.0

    monkeypatch.setattr(awake_clock, "monotonic", failing_clock)
    started, told_to_stop = threading.Event(), threading.Event()

    def polite_tool():
        started.set()
        for _ in range(100):
            if tool_cancelled():
                told_to_stop.set()
                return
            time.sleep(0.05)

    with pytest.raises(awake_clock.AwakeClockError):
        Agent._call_tool_bounded(_Host(), polite_tool, {}, "polite_tool")
    assert started.wait(5)
    assert told_to_stop.wait(5)
