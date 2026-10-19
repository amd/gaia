# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn cut short by closing the TUI says so in the session transcript.

The transcript (``SessionHistory``, a SQLite file per session) used to get a
turn only once it ended. Closing the TUI mid-turn either let the turn stop and
be saved as an ordinary answer, or, when the TUI killed the agent after its
grace period, left no record of the question at all.
"""

import io
import json
import queue
import sqlite3
import sys
import threading

import pytest
from gaia_agent import stdio

from gaia.agents.base.history import SessionHistory
from gaia.ui.sse_handler import SSEOutputHandler

STOPPED = "The request was stopped before it finished."
CUT_SHORT = "was interrupted before it finished"


class _Agent:
    """Blocks in its turn until released or cancelled."""

    def __init__(self, history_path):
        self.console = None
        self.device = "gpu"
        self.history_path = history_path
        self.conversation_history = SessionHistory(history_path)
        self.started = threading.Event()
        self.release = threading.Event()
        self.crash = False
        self.script = []

    def process_query(self, query):
        self.started.set()
        for event in self.script:
            self.console.event_queue.put(dict(event))
        while not self.release.wait(0.02):
            if self.console.cancelled.is_set():
                return {"answer": STOPPED}
        if self.crash:
            raise RuntimeError("tool exploded")
        return {"answer": f"answered: {query}"}


@pytest.fixture
def agent(tmp_path):
    made = _Agent(tmp_path / "session.sqlite3")
    yield made
    made.conversation_history.close()


def _transcript(path):
    db = sqlite3.connect(str(path))
    try:
        return [json.loads(row[0]) for row in db.execute("SELECT messages FROM turns")]
    finally:
        db.close()


def _start(agent, query, state=None):
    def run():
        # The stdio run loop owns the history on its own thread, as here.
        opened_here = agent.conversation_history
        agent.conversation_history = SessionHistory(agent.history_path)
        try:
            stdio.run_turn(agent, query, io.StringIO(), state=state)
        finally:
            agent.conversation_history.close()
            agent.conversation_history = opened_here

    turn = threading.Thread(target=run)
    turn.start()
    assert agent.started.wait(5)
    return turn


def test_a_turn_killed_with_the_tui_is_in_the_transcript_as_interrupted(agent):
    turn = _start(agent, "summarize my inbox")
    try:
        # The TUI kills the agent here: nothing after this point runs.
        turns = _transcript(agent.history_path)
    finally:
        agent.release.set()
        turn.join(5)

    assert len(turns) == 1
    assert turns[0][0] == {"role": "user", "content": "summarize my inbox"}
    assert CUT_SHORT in turns[0][-1]["content"]


def test_a_turn_the_tui_closed_on_is_saved_as_interrupted(agent, monkeypatch):
    state = stdio.PermissionState()
    turn = _start(agent, "summarize my inbox", state)

    # The TUI closes: stdin reaches end-of-file and the pump cancels the turn.
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    stdio._pump_stdin(queue.Queue(), state)
    turn.join(5)
    assert not turn.is_alive()

    turns = _transcript(agent.history_path)
    assert len(turns) == 1
    assert [m["role"] for m in turns[0]] == ["user", "assistant"]
    assert turns[0][0]["content"] == "summarize my inbox"
    # What the turn said, then why it never finished, in one reply.
    assert turns[0][-1]["content"] == f"{STOPPED}\n\n{stdio.INTERRUPTED_NOTE}"


def test_turns_after_the_close_are_saved_as_they_ended(agent):
    state = stdio.PermissionState()
    # stdin closed while no turn was running; queued questions still run.
    assert state.cancel_active() is False
    agent.release.set()
    stdio.run_turn(agent, "queued question", io.StringIO(), state=state)

    turns = _transcript(agent.history_path)
    assert [m["content"] for m in turns[0]] == [
        "queued question",
        "answered: queued question",
    ]


def test_a_turn_that_already_ended_is_not_marked_by_the_close():
    state = stdio.PermissionState()
    handler = SSEOutputHandler()
    state.attach(handler)
    state.end_turn(handler)  # run_turn does this once the turn is over
    state.cancel_active()
    assert not stdio._closed_by_host(handler)


def test_a_broken_wire_keeps_the_stand_in(agent):
    class _BrokenPipe(io.StringIO):
        def write(self, _text):
            raise BrokenPipeError("the TUI is gone")

    agent.script = [{"type": "status", "status": "info", "message": "working"}]
    agent.release.set()
    with pytest.raises(OSError):
        stdio.run_turn(agent, "summarize my inbox", _BrokenPipe())

    turns = _transcript(agent.history_path)
    assert len(turns) == 1
    assert CUT_SHORT in turns[0][-1]["content"]


def test_a_finished_turn_replaces_its_stand_in(agent):
    agent.release.set()
    stdio.run_turn(agent, "hello", io.StringIO())

    turns = _transcript(agent.history_path)
    assert len(turns) == 1
    assert [m["content"] for m in turns[0]] == ["hello", "answered: hello"]


def test_a_failed_turn_still_leaves_no_trace(agent):
    agent.crash = True
    agent.release.set()
    stdio.run_turn(agent, "hello", io.StringIO())

    assert _transcript(agent.history_path) == []
    agent.conversation_history.prepare(agent, "")
    assert list(agent.conversation_history) == []


def test_a_broken_history_never_sends_a_second_terminal_event(agent, monkeypatch):
    agent.crash = True
    agent.release.set()
    monkeypatch.setattr(
        agent.conversation_history,
        "discard_pending",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")),
    )
    out = io.StringIO()
    stdio.run_turn(agent, "hello", out)

    events = [json.loads(line) for line in out.getvalue().splitlines() if line]
    assert [e["type"] for e in events if e["type"] in ("final", "error")] == [
        "error"
    ]
