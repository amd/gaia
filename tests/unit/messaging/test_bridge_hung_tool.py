# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A hung tool under the messaging bridge is ended by the agent's tool limit.

The bridge never stops a turn while a tool runs: a running tool keeps the
turn's idle clock fresh until its ``tool_result`` arrives. So the only thing
that ends a hung tool is the agent's own per-tool limit
(``GAIA_AGENT_TOOL_TIMEOUT``, 180 s by default). This drives the real
``gaia_agent.stdio`` turn loop, with a real ``Agent`` and a scripted model,
behind a real ``AgentChannel``, and checks the user still gets an answer.
"""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.messaging.bridge import CONTROL_KEY, AgentChannel, Turn

from .test_bridge_turn_idle import FakeClock, _wait_for

stdio = pytest.importorskip(
    "gaia_agent.stdio",
    reason="the flagship agent package is not installed in this environment",
)

TOOL_LIMIT = 6.0
IDLE_LIMIT = 100.0
HANG = 30.0


class _Wire:
    """The child's stdout pipe: written by the turn loop, read by the bridge."""

    def __init__(self):
        self._lines = []
        self._partial = ""
        self._cv = threading.Condition()
        self._eof = False

    def write(self, data):
        with self._cv:
            self._partial += data
            while "\n" in self._partial:
                line, self._partial = self._partial.split("\n", 1)
                self._lines.append(line + "\n")
            self._cv.notify_all()

    def flush(self):
        pass

    def eof(self):
        with self._cv:
            self._eof = True
            self._cv.notify_all()

    def readline(self):
        with self._cv:
            while not self._lines and not self._eof:
                self._cv.wait(timeout=1)
            return self._lines.pop(0) if self._lines else ""


class _ChildStdin:
    def __init__(self, on_query):
        self.lines = []
        self._on_query = on_query

    def write(self, data):
        line = data.rstrip("\n")
        self.lines.append(line)
        if CONTROL_KEY not in json.loads(line):
            self._on_query(stdio.parse_query(line))

    def flush(self):
        pass

    def close(self):
        pass


class _InProcessChild:
    """``gaia-agent`` without the process: each query runs ``stdio.run_turn``.

    Control lines (a cancel, a tool decision) are recorded but never reach
    the agent, so a test that needs one applied cannot use this double.
    """

    def __init__(self, agent):
        self.stdout = _Wire()
        self.stdin = _ChildStdin(self._start_turn)
        self._agent = agent
        self._state = stdio.PermissionState()

    def _start_turn(self, query):
        threading.Thread(
            target=stdio.run_turn,
            args=(self._agent, query, self.stdout),
            kwargs={"state": self._state},
            daemon=True,
        ).start()

    def terminate(self):
        self.stdout.eof()

    def kill(self):
        self.stdout.eof()

    def wait(self, timeout=None):
        return 0


class _HungToolAgent(Agent):
    release = threading.Event()

    def _register_tools(self):
        @tool
        def fetch_hung_feed(url: str) -> dict:
            """Fetch a news feed."""
            _HungToolAgent.release.wait(HANG)
            return {"status": "ok", "url": url}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", str(TOOL_LIMIT))
    saved = dict(_TOOL_REGISTRY)
    _HungToolAgent.release = threading.Event()
    call = {"tool": "fetch_hung_feed", "tool_args": {"url": "https://feed.test"}}
    replies = [json.dumps(call)]

    def send(*_a, **_kw):
        text = replies.pop(0) if replies else json.dumps({"answer": "No feed."})
        return MagicMock(text=text, stats={}, finish_reason="stop")

    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            built = _HungToolAgent(silent_mode=True, skip_lemonade=True)
        built.chat = MagicMock()
        built.chat.send_messages.side_effect = send
        built.chat.config.max_tokens = 4096
        built.streaming = False
        built._tool_requires_confirmation = lambda *a, **kw: False
        yield built
    finally:
        _HungToolAgent.release.set()
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_a_hung_tool_is_ended_by_the_agents_tool_limit(agent):
    clock, children, seen = FakeClock(), [], []

    def spawn():
        children.append(_InProcessChild(agent))
        return children[-1]

    ch = AgentChannel(
        on_event=lambda event, turn: seen.append(event),
        spawn=spawn,
        idle_limit=IDLE_LIMIT,
        cancel_grace=IDLE_LIMIT,
        clock=clock,
        poll_seconds=0.01,
    )
    ch.start()
    try:
        ch.submit(Turn(text="what's in my feed?"))
        assert _wait_for(lambda: any(e["type"] == "tool_call" for e in seen))
        # Far past the bridge's own limit: a running tool must not be stopped.
        clock.advance(10 * IDLE_LIMIT)
        assert not [e for e in seen if e["type"] == "tool_result"], (
            "the tool ended before the bridge's clock moved, so this run says "
            "nothing about the bridge holding a running tool open"
        )

        ended = _wait_for(
            lambda: any(e["type"] in ("final", "error") for e in seen),
            timeout=10 * TOOL_LIMIT,
        )
        assert ended, f"the hung tool was never ended; the user saw: {seen}"
        [end] = [e for e in seen if e["type"] in ("final", "error")]
        assert end == {**end, "type": "final", "answer": "No feed."}
        [result] = [e for e in seen if e["type"] == "tool_result"]
        assert result["data"]["success"] is False
        assert "did not return within" in result["data"]["summary"]

        # Ended by the tool limit, not by the bridge stopping or replacing it.
        assert len(children) == 1
        assert not [
            line for line in children[0].stdin.lines if CONTROL_KEY in json.loads(line)
        ]
    finally:
        ch.close()
