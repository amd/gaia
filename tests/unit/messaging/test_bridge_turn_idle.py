# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A wedged agent child must not hold every messaging chat forever.

``AgentChannel`` serves one turn at a time and waited for each turn's terminal
event with no deadline, so a child that stopped emitting while still alive
blocked every later message on the bridge. The writer now stops a turn that
has made no progress for ``idle_limit`` seconds, the same rule the Agent UI
uses: a running tool or a pending approval is not idle. A child that ignores
the stop is shut down, and the messages queued behind it are answered with an
error instead of waiting.

Driven on a fake clock against a fake child.
"""

import json
import threading
import time

import pytest

from gaia.messaging.bridge import (
    CONTROL_CANCEL,
    CONTROL_KEY,
    AgentChannel,
    Turn,
)

LIMIT = 100.0
GRACE = 10.0


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.now

    def advance(self, seconds):
        with self._lock:
            self.now += seconds


class FakeStdin:
    def __init__(self):
        self.lines = []

    def write(self, data):
        self.lines.append(data.rstrip("\n"))

    def flush(self):
        pass

    def close(self):
        pass


class FakeStdout:
    def __init__(self):
        self._queue = []
        self._cv = threading.Condition()
        self._eof = False

    def emit(self, event):
        with self._cv:
            self._queue.append(json.dumps(event) + "\n")
            self._cv.notify_all()

    def eof(self):
        with self._cv:
            self._eof = True
            self._cv.notify_all()

    def readline(self):
        with self._cv:
            while not self._queue and not self._eof:
                self._cv.wait(timeout=2)
            if self._queue:
                return self._queue.pop(0)
            return ""


class FakeProc:
    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.stdout.eof()


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _channel(proc, clock, seen):
    ch = AgentChannel(
        on_event=lambda event, turn: seen.append((event, turn.text)),
        spawn=lambda: proc,
        idle_limit=LIMIT,
        cancel_grace=GRACE,
        clock=clock,
        poll_seconds=0.01,
    )
    ch.start()
    return ch


def _cancels(proc):
    return [
        line
        for line in proc.stdin.lines
        if json.loads(line).get(CONTROL_KEY) == CONTROL_CANCEL
    ]


def _errors(seen, text):
    return [e for e, t in seen if t == text and e["type"] == "error"]


def test_silent_turn_is_stopped_and_the_next_message_runs():
    proc, clock, seen = FakeProc(), FakeClock(), []
    ch = _channel(proc, clock, seen)
    try:
        ch.submit(Turn(text="first"))
        ch.submit(Turn(text="second"))
        assert _wait_for(lambda: len(proc.stdin.lines) == 1)

        clock.advance(LIMIT + 1)
        assert _wait_for(lambda: _cancels(proc))
        # Late output while the stop is pending is not shown.
        proc.stdout.emit({"type": "token", "delta": "late"})
        time.sleep(0.1)
        assert not [e for e, t in seen if t == "first"]

        # The child honours the stop: the user is told, and can continue.
        proc.stdout.emit({"type": "error", "detail": "Cancelled."})
        assert _wait_for(lambda: _errors(seen, "first"))
        [stopped] = [e for e, t in seen if t == "first"]
        assert "without progress" in stopped["detail"]
        assert '"continue"' in stopped["detail"]
        assert _wait_for(lambda: any("second" in line for line in proc.stdin.lines))
        assert proc.terminated is False
    finally:
        ch.close()


def test_progress_resets_the_idle_clock():
    proc, clock, seen = FakeProc(), FakeClock(), []
    ch = _channel(proc, clock, seen)
    try:
        ch.submit(Turn(text="q"))
        assert _wait_for(lambda: proc.stdin.lines)
        for i in range(5):
            clock.advance(LIMIT - 10)
            proc.stdout.emit({"type": "token", "delta": "x"})
            assert _wait_for(lambda i=i: len(seen) == i + 1)
        time.sleep(0.1)
        assert not _errors(seen, "q")
        assert not _cancels(proc)
    finally:
        ch.close()


def test_running_tool_and_pending_approval_are_not_idle():
    proc, clock, seen = FakeProc(), FakeClock(), []
    ch = _channel(proc, clock, seen)
    try:
        ch.submit(Turn(text="q"))
        assert _wait_for(lambda: proc.stdin.lines)

        proc.stdout.emit({"type": "tool_call", "tool": "run_shell_command"})
        assert _wait_for(lambda: len(seen) == 1)
        clock.advance(LIMIT * 5)
        time.sleep(0.1)
        assert not _cancels(proc)

        proc.stdout.emit({"type": "tool_result", "tool": "run_shell_command"})
        proc.stdout.emit({"type": "needs_confirmation", "confirm_id": "c1"})
        assert _wait_for(lambda: len(seen) == 3)
        clock.advance(LIMIT * 5)
        time.sleep(0.1)
        assert not _cancels(proc)

        # Answered: from here the turn is on the clock again.
        proc.stdout.emit({"type": "status", "message": "working"})
        assert _wait_for(lambda: len(seen) == 4)
        clock.advance(LIMIT + 1)
        assert _wait_for(lambda: _cancels(proc))
    finally:
        ch.close()


def _restarting_channel(procs, clock, seen, max_restarts=3):
    made = iter(procs)
    ch = AgentChannel(
        on_event=lambda event, turn: seen.append((event, turn.text)),
        spawn=lambda: next(made),
        idle_limit=LIMIT,
        cancel_grace=GRACE,
        clock=clock,
        poll_seconds=0.01,
        max_restarts=max_restarts,
        restart_hint="Restart it with the right command.",
    )
    ch.start()
    return ch


def _wedge(proc, clock):
    assert _wait_for(lambda: proc.stdin.lines)
    clock.advance(LIMIT + 1)
    assert _wait_for(lambda: _cancels(proc))
    clock.advance(GRACE + 1)
    assert _wait_for(lambda: proc.terminated)


def test_a_child_that_ignores_the_stop_is_replaced_and_the_queue_moves_on():
    wedged, fresh = FakeProc(), FakeProc()
    clock, seen = FakeClock(), []
    ch = _restarting_channel([wedged, fresh], clock, seen)
    try:
        ch.submit(Turn(text="first"))
        ch.submit(Turn(text="second"))
        _wedge(wedged, clock)

        # The queued message goes to the new child, not to a dead channel.
        assert _wait_for(lambda: any("second" in line for line in fresh.stdin.lines))
        # Output the killed child flushed late belongs to no turn.
        wedged.stdout.emit({"type": "final", "answer": "stale"})
        fresh.stdout.emit({"type": "final", "answer": "fresh answer"})
        assert _wait_for(lambda: [e for e, t in seen if t == "second"])
        assert [e["answer"] for e, t in seen if t == "second"] == ["fresh answer"]
    finally:
        ch.close()


def test_after_its_restarts_run_out_the_channel_fails_fast_with_the_hint():
    wedged = FakeProc()
    clock, seen = FakeClock(), []
    ch = _restarting_channel([wedged], clock, seen, max_restarts=0)
    try:
        ch.submit(Turn(text="first"))
        ch.submit(Turn(text="second"))
        _wedge(wedged, clock)

        assert _wait_for(lambda: _errors(seen, "second"))
        assert (
            "Restart it with the right command." in _errors(seen, "second")[0]["detail"]
        )
        assert not any("second" in line for line in wedged.stdin.lines)
    finally:
        ch.close()


def test_gaia_stream_timeout_sets_the_window(monkeypatch):
    from gaia.messaging import bridge

    monkeypatch.setenv("GAIA_STREAM_TIMEOUT", "120")
    assert bridge.stream_timeout_from_env() == 120
    ch = AgentChannel(on_event=lambda e, t: None, spawn=FakeProc)
    assert ch._idle_limit == 120.0
    monkeypatch.setenv("GAIA_STREAM_TIMEOUT", "soon")
    with pytest.raises(ValueError, match="GAIA_STREAM_TIMEOUT"):
        AgentChannel(on_event=lambda e, t: None, spawn=FakeProc)


def test_idle_limit_must_be_positive():
    with pytest.raises(ValueError, match="idle_limit"):
        AgentChannel(on_event=lambda e, t: None, spawn=FakeProc, idle_limit=0)
