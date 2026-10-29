# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool with no arguments must not be stopped by the bridge as idle.

The real agent loop and the real stdio turn feed a messaging bridge channel.
A no-argument tool that runs far past ``GAIA_STREAM_TIMEOUT`` must be on the
wire as a running tool, so the bridge's idle watchdog leaves it alone.

Lives with the flagship's tests because it drives ``gaia_agent.stdio``.
Driven on a fake clock; no model server is contacted.
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

from gaia_agent import stdio

from gaia.messaging.bridge import (
    CONTROL_CANCEL,
    CONTROL_KEY,
    QUERY_KEY,
    AgentChannel,
    Turn,
)

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


def _cancels(proc):
    return [
        line
        for line in proc.stdin.lines
        if json.loads(line).get(CONTROL_KEY) == CONTROL_CANCEL
    ]


class _ChildWire:
    """The child's stdout as ``stdio.run_turn`` writes it: one JSON line each."""

    def __init__(self, stdout):
        self._stdout = stdout
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._stdout.emit(json.loads(line))

    def flush(self):
        pass


class _AgentStdin(FakeStdin):
    """Runs each query line through the real stdio turn on ``agent``."""

    def __init__(self, agent, stdout):
        super().__init__()
        self._agent = agent
        self._wire = _ChildWire(stdout)

    def write(self, data):
        super().write(data)
        message = json.loads(data)
        if QUERY_KEY in message:
            threading.Thread(
                target=stdio.run_turn,
                args=(self._agent, message[QUERY_KEY], self._wire),
                daemon=True,
            ).start()


def _slow_no_arg_tool_agent(started, release):
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.tools import tool

    class _SlowProbe(Agent):
        def _get_system_prompt(self):
            return "probe"

        def load_declared_skills(self, *_, **__):
            # Not the flagship: don't pick up the gaia-agent.yaml beside this file.
            return {}

        def _register_tools(self):
            @tool
            def wait_for_build() -> dict:
                """Wait for the build to finish. Takes no arguments."""
                started.set()
                assert release.wait(10), "the test never released the tool"
                return {"status": "success", "build": "green"}

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _SlowProbe(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    replies = [
        json.dumps({"thought": "wait", "tool": "wait_for_build", "tool_args": {}}),
        json.dumps({"thought": "done", "answer": "The build is green."}),
    ]

    def _send(*_, **__):
        resp = MagicMock()
        resp.text = replies.pop(0)
        resp.stats = {}
        return resp

    agent.chat = MagicMock()
    agent.chat.config.max_tokens = 8192
    agent.chat.send_messages = MagicMock(side_effect=_send)
    # Unregistered, with a pinned window: sizing history asks no server.
    agent.model_id = "probe-model"
    return agent


def test_a_long_tool_with_no_arguments_is_not_stopped_as_idle(monkeypatch):
    """The real agent loop and stdio wire, a no-arg tool, a 1 s stream timeout."""
    monkeypatch.setenv("GAIA_STREAM_TIMEOUT", "1")
    monkeypatch.setenv("GAIA_CTX_SIZE", "32768")
    started, release = threading.Event(), threading.Event()
    proc, clock, seen = FakeProc(), FakeClock(), []
    proc.stdin = _AgentStdin(_slow_no_arg_tool_agent(started, release), proc.stdout)
    ch = AgentChannel(
        on_event=lambda event, turn: seen.append((event, turn.text)),
        spawn=lambda: proc,
        cancel_grace=GRACE,
        clock=clock,
        poll_seconds=0.01,
    )
    ch.start()
    try:
        assert ch._idle_limit == 1.0
        ch.submit(Turn(text="is the build done?"))
        assert started.wait(10)
        announced = _wait_for(lambda: any(e["type"] == "tool_call" for e, _ in seen))

        clock.advance(30)  # thirty times the stream timeout, still in the tool
        # Wait for the watchdog's verdict on the advanced clock: a stop, or the
        # running tool being counted as progress.
        assert _wait_for(
            lambda: _cancels(proc) or ch._active.last_progress >= clock.now
        )
        assert not _cancels(proc), "the bridge stopped a tool that was still running"
        # It knew a tool was running before the tool returned.
        assert announced, [e["type"] for e, _ in seen]

        release.set()
        assert _wait_for(lambda: any(e["type"] == "final" for e, _ in seen))
        [final] = [e for e, _ in seen if e["type"] == "final"]
        assert final["answer"] == "The build is green."
        assert not [e for e, _ in seen if e["type"] == "error"]
    finally:
        release.set()
        ch.close()
