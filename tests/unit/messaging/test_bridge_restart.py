# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Replacing a wedged agent child: what the user is told, what is dropped,
what is cleaned up, and how often it may happen.

Driven on a fake clock against fake children.
"""

import subprocess
import threading
import time

import pytest

from gaia.messaging import bridge
from gaia.messaging.bridge import AgentChannel, Turn

from .test_bridge_turn_idle import GRACE, LIMIT, FakeClock, FakeProc, _wait_for


class SilentDeathProc(FakeProc):
    """Its output stays open after terminate(), so its reader keeps running."""

    def terminate(self):
        self.terminated = True


def _channel(spawn, clock, seen, **kwargs):
    ch = AgentChannel(
        on_event=lambda event, turn: seen.append((event, turn)),
        spawn=spawn,
        idle_limit=LIMIT,
        cancel_grace=GRACE,
        clock=clock,
        poll_seconds=0.01,
        **kwargs,
    )
    ch.start()
    return ch


def _spawner(procs, cls=FakeProc):
    def spawn():
        proc = cls()
        procs.append(proc)
        return proc

    return spawn


def _wedge(clock, proc):
    """Let the running turn go idle and ignore the stop."""
    clock.advance(LIMIT + 1)
    assert _wait_for(
        lambda: any("cancel" in line for line in proc.stdin.lines)
    ), "no stop was sent"
    clock.advance(GRACE + 1)
    assert _wait_for(lambda: proc.terminated)


def _texts(seen, text, kind=None):
    return [
        e for e, t in seen if t.text == text and (kind is None or e["type"] == kind)
    ]


def test_closing_while_a_replacement_starts_stops_the_replacement():
    clock, seen, procs = FakeClock(), [], []
    holder = {}

    def spawn():
        if procs:  # the replacement: the user stops the bridge right now
            holder["ch"].close()
        proc = FakeProc()
        procs.append(proc)
        return proc

    ch = _channel(spawn, clock, seen)
    holder["ch"] = ch
    ch.submit(Turn(text="q1"))
    assert _wait_for(lambda: procs[0].stdin.lines)
    _wedge(clock, procs[0])
    assert _wait_for(lambda: len(procs) == 2)
    assert _wait_for(lambda: procs[1].terminated), "the new child was left running"


def test_a_replaced_child_resets_the_conversation_and_says_so():
    clock, seen, procs = FakeClock(), [], []
    ch = _channel(_spawner(procs), clock, seen)
    try:
        ch.submit(Turn(text="earlier", sender="U2"))
        assert _wait_for(lambda: procs[0].stdin.lines)
        procs[0].stdout.emit({"type": "final", "answer": "hi"})
        assert _wait_for(lambda: _texts(seen, "earlier", "final"))

        ch.submit(Turn(text="stuck", sender="U1"))
        assert _wait_for(lambda: len(procs[0].stdin.lines) == 2)
        _wedge(clock, procs[0])
        assert _wait_for(lambda: _texts(seen, "stuck", "error"))
        [told] = _texts(seen, "stuck")
        assert "restarted" in told["detail"]
        assert '"continue"' not in told["detail"]

        # U2's history went with the old child; their next answer says so.
        ch.submit(Turn(text="later", sender="U2"))
        assert _wait_for(lambda: len(procs) == 2 and procs[1].stdin.lines)
        procs[1].stdout.emit({"type": "final", "answer": "answer"})
        assert _wait_for(lambda: _texts(seen, "later", "final"))
        [answer] = _texts(seen, "later", "final")
        assert answer["answer"].startswith(bridge.RESTART_NOTICE)
        assert answer["answer"].endswith("answer")
    finally:
        ch.close()


def test_a_late_line_from_the_replaced_child_never_reaches_the_next_turn():
    """The old child's reader is still alive (its pipe stayed open) when it
    flushes a final answer meant for the stopped turn."""
    clock, seen, procs = FakeClock(), [], []
    ch = _channel(_spawner(procs, SilentDeathProc), clock, seen)
    try:
        ch.submit(Turn(text="stuck"))
        ch.submit(Turn(text="next"))
        assert _wait_for(lambda: procs[0].stdin.lines)
        _wedge(clock, procs[0])
        assert _wait_for(lambda: len(procs) == 2 and procs[1].stdin.lines)

        procs[0].stdout.emit({"type": "final", "answer": "stale"})
        time.sleep(0.2)
        procs[1].stdout.emit({"type": "final", "answer": "fresh"})
        assert _wait_for(lambda: _texts(seen, "next", "final"))
        assert [e["answer"] for e in _texts(seen, "next", "final")] == ["fresh"]
    finally:
        ch.close()


def test_an_answer_being_rendered_is_not_stopped_under_it():
    """The answer arrived just inside the window, and rendering it is slow."""
    clock, seen, procs = FakeClock(), [], []
    rendering, release = threading.Event(), threading.Event()

    def on_event(event, turn):
        seen.append((event, turn))
        if event["type"] == "final":
            rendering.set()
            release.wait(5)

    ch = AgentChannel(
        on_event=on_event,
        spawn=_spawner(procs),
        idle_limit=LIMIT,
        cancel_grace=GRACE,
        clock=clock,
        poll_seconds=0.01,
    )
    ch.start()
    try:
        ch.submit(Turn(text="q"))
        assert _wait_for(lambda: procs[0].stdin.lines)
        procs[0].stdout.emit({"type": "final", "answer": "done"})
        assert rendering.wait(5)
        clock.advance(LIMIT + GRACE + 10)
        time.sleep(0.2)
        assert not any("cancel" in line for line in procs[0].stdin.lines)
        assert [e["type"] for e, _ in seen] == ["final"]
    finally:
        release.set()
        ch.close()


def test_three_restarts_a_day_then_the_bridge_says_how_to_restart_it():
    clock, seen, procs = FakeClock(), [], []
    ch = _channel(_spawner(procs), clock, seen, restart_hint="RESTART-IT")
    try:
        for i in range(bridge.MAX_AGENT_RESTARTS + 1):
            ch.submit(Turn(text=f"wedge{i}"))
            assert _wait_for(lambda i=i: len(procs) > i and procs[i].stdin.lines)
            _wedge(clock, procs[i])
            assert _wait_for(lambda i=i: _texts(seen, f"wedge{i}", "error"))
        assert len(procs) == 1 + bridge.MAX_AGENT_RESTARTS
        assert (
            "RESTART-IT"
            in _texts(seen, f"wedge{bridge.MAX_AGENT_RESTARTS}")[0]["detail"]
        )
        ch.submit(Turn(text="after"))
        assert _wait_for(lambda: _texts(seen, "after", "error"))
        assert "RESTART-IT" in _texts(seen, "after")[0]["detail"]
    finally:
        ch.close()


def test_restarts_spread_over_days_never_use_up_the_budget():
    clock, seen, procs = FakeClock(), [], []
    ch = _channel(_spawner(procs), clock, seen, restart_hint="RESTART-IT")
    try:
        for i in range(bridge.MAX_AGENT_RESTARTS + 2):
            ch.submit(Turn(text=f"wedge{i}"))
            assert _wait_for(lambda i=i: len(procs) > i and procs[i].stdin.lines)
            _wedge(clock, procs[i])
            assert _wait_for(lambda i=i: _texts(seen, f"wedge{i}", "error"))
            clock.advance(bridge.RESTART_WINDOW_SECONDS + 1)
        assert not any("RESTART-IT" in e.get("detail", "") for e, _ in seen)
    finally:
        ch.close()


def test_a_child_that_ignores_terminate_is_killed_and_reaped():
    calls = []

    class Stubborn:
        pid = 4242

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

        def wait(self, timeout=None):
            calls.append("wait")
            if calls.count("wait") == 1:
                raise subprocess.TimeoutExpired("agent", timeout)
            return -9

    AgentChannel._stop_child(Stubborn())
    assert calls == ["terminate", "wait", "kill", "wait"]


def test_telegram_follows_gaia_stream_timeout(monkeypatch):
    from gaia.messaging.telegram import TelegramAdapter

    monkeypatch.setenv("GAIA_STREAM_TIMEOUT", "120")
    assert TelegramAdapter(token="t", allowed_users={1})._idle_limit == 120
    monkeypatch.setenv("GAIA_STREAM_TIMEOUT", "0")
    with pytest.raises(ValueError, match="out of range"):
        TelegramAdapter(token="t", allowed_users={1})


def test_slack_passes_its_restart_hint_to_the_channel():
    from gaia.messaging.slack.adapter import SlackAdapter, slack_restart_hint

    from .test_slack_adapter import FakeWeb

    made = {}

    def factory(**kwargs):
        made.update(kwargs)

        class Channel:
            def start(self):
                pass

            def close(self):
                pass

        return Channel()

    hint = slack_restart_hint(background=True)
    adapter = SlackAdapter(
        bot_token="xoxb",
        app_token="xapp",
        allowed_users={"U1"},
        channel_factory=factory,
        web_client=FakeWeb(),
        restart_hint=hint,
    )
    adapter.start(connect=False)
    assert made["restart_hint"] == hint
    assert "--background" in hint and "gaia slack stop" in hint
    assert "--allowed-users" in slack_restart_hint()


def test_slack_start_turns_a_bad_stream_timeout_into_a_clean_error(monkeypatch):
    from gaia.messaging.slack.cli import run_start

    monkeypatch.setenv("GAIA_STREAM_TIMEOUT", "soon")
    said = []
    assert run_start(allowed_users="U1", emit=said.append) == 2
    assert "GAIA_STREAM_TIMEOUT" in said[0]
