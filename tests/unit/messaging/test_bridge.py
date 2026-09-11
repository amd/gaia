"""AgentChannel and StreamThrottle — the transport-agnostic half of a bridge.

Driven against a fake child rather than a real ``gaia-agent``: the real one
costs ~42s to construct before it answers anything, and none of the behaviour
under test here is the agent's.
"""

import json
import threading
import time

import pytest

from gaia.messaging.bridge import (
    CONTROL_KEY,
    CONTROL_TOOL_DECISION,
    DECISION_ALLOW,
    DECISION_DENY,
    QUERY_KEY,
    AgentChannel,
    AgentChannelError,
    StreamThrottle,
    Turn,
)


class FakeStdin:
    """Captures every line the bridge writes to the child."""

    def __init__(self):
        self.lines = []
        self.closed = False

    def write(self, data):
        self.lines.append(data.rstrip("\n"))

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeStdout:
    """A blocking line iterator the test feeds events into."""

    def __init__(self):
        self._queue = []
        self._cv = threading.Condition()
        self._eof = False

    def emit(self, event):
        with self._cv:
            self._queue.append(json.dumps(event) + "\n")
            self._cv.notify_all()

    def emit_raw(self, line):
        with self._cv:
            self._queue.append(line + "\n")
            self._cv.notify_all()

    def eof(self):
        with self._cv:
            self._eof = True
            self._cv.notify_all()

    def readline(self):
        """Block for the next line; empty string means EOF, as a real pipe does."""
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


@pytest.fixture
def channel():
    """A started channel plus its fake child and a record of dispatched events."""
    proc = FakeProc()
    seen = []
    ch = AgentChannel(
        on_event=lambda event, turn: seen.append((event, turn)),
        spawn=lambda: proc,
    )
    ch.start()
    yield ch, proc, seen
    ch.close()


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ----------------------------------------------------------------------
# Wire format
# ----------------------------------------------------------------------


def test_query_is_wrapped_so_a_multiline_question_stays_one_turn(channel):
    """An unwrapped newline would become several unrelated questions."""
    ch, proc, _ = channel
    ch.submit(Turn(text="line one\nline two"))
    assert _wait_for(lambda: proc.stdin.lines)
    assert json.loads(proc.stdin.lines[0]) == {QUERY_KEY: "line one\nline two"}


def test_decision_names_the_confirmation_it_answers(channel):
    """Without confirm_id a late click resolves whatever replaced its prompt."""
    ch, proc, _ = channel
    ch.decide(DECISION_ALLOW, "confirm-7")
    assert json.loads(proc.stdin.lines[-1]) == {
        CONTROL_KEY: CONTROL_TOOL_DECISION,
        "decision": DECISION_ALLOW,
        "confirm_id": "confirm-7",
    }


def test_unknown_decision_fails_closed_to_deny(channel):
    """A garbled decision must never be read as consent."""
    ch, proc, _ = channel
    ch.decide("maybe", "c1")
    assert json.loads(proc.stdin.lines[-1])["decision"] == DECISION_DENY


def test_channel_exposes_no_way_to_enable_bypass():
    """The agent accepts a `bypass` verb; a remote surface must not reach it.

    An allowlisted user is trusted to ask for one tool, not to disarm the gate
    for every later one. Bypass stays a local decision.
    """
    public = {name for name in dir(AgentChannel) if not name.startswith("_")}
    assert not {n for n in public if "bypass" in n.lower()}
    source = AgentChannel.decide.__doc__ or ""
    assert "bypass" not in source.lower()


# ----------------------------------------------------------------------
# Turn lifecycle
# ----------------------------------------------------------------------


def test_events_reach_the_adapter_with_their_turn(channel):
    ch, proc, seen = channel
    turn = Turn(text="hi", context={"channel": "D1"})
    ch.submit(turn)
    assert _wait_for(lambda: proc.stdin.lines)
    proc.stdout.emit({"type": "token", "text": "he"})
    proc.stdout.emit({"type": "final", "answer": "hello"})
    assert _wait_for(lambda: len(seen) == 2)
    assert [e["type"] for e, _ in seen] == ["token", "final"]
    assert all(t.context == {"channel": "D1"} for _, t in seen)


def test_a_second_message_waits_for_the_running_turn(channel):
    """The agent handles one query at a time; racing them would interleave."""
    ch, proc, seen = channel
    ch.submit(Turn(text="first"))
    assert _wait_for(lambda: len(proc.stdin.lines) == 1)

    ahead = ch.submit(Turn(text="second"))
    assert ahead == 1, "the caller must learn its message is queued"
    time.sleep(0.15)
    assert len(proc.stdin.lines) == 1, "second query must not be written yet"

    proc.stdout.emit({"type": "final", "answer": "one"})
    assert _wait_for(lambda: len(proc.stdin.lines) == 2)
    assert json.loads(proc.stdin.lines[1]) == {QUERY_KEY: "second"}


def test_on_busy_fires_only_for_a_queued_turn():
    proc = FakeProc()
    busy = []
    ch = AgentChannel(
        on_event=lambda e, t: None,
        spawn=lambda: proc,
        on_busy=lambda turn, ahead: busy.append((turn.text, ahead)),
    )
    ch.start()
    try:
        ch.submit(Turn(text="first"))
        assert _wait_for(lambda: proc.stdin.lines)
        assert busy == [], "the first turn starts immediately"
        ch.submit(Turn(text="second"))
        assert busy == [("second", 1)]
    finally:
        ch.close()


def test_a_crashed_child_still_terminates_the_turn(channel):
    """A turn that just goes quiet leaves the user staring at 'Thinking…'."""
    ch, proc, seen = channel
    ch.submit(Turn(text="hi"))
    assert _wait_for(lambda: proc.stdin.lines)
    proc.stdout.emit({"type": "token", "text": "partial"})
    proc.stdout.eof()
    assert _wait_for(lambda: any(e["type"] == "error" for e, _ in seen))
    detail = [e for e, _ in seen if e["type"] == "error"][0]["detail"]
    assert "stopped responding" in detail
    assert "~/.gaia/logs" in detail, "an error must say where to look next"


def test_a_crash_does_not_double_report_after_a_terminal_event(channel):
    ch, proc, seen = channel
    ch.submit(Turn(text="hi"))
    assert _wait_for(lambda: proc.stdin.lines)
    proc.stdout.emit({"type": "final", "answer": "done"})
    assert _wait_for(lambda: len(seen) == 1)
    proc.stdout.eof()
    time.sleep(0.15)
    assert [e["type"] for e, _ in seen] == ["final"]


def test_a_raising_renderer_does_not_wedge_the_channel():
    """A renderer that throws must not stop the turn from terminating."""
    proc = FakeProc()
    calls = []

    def boom(event, turn):
        calls.append(event["type"])
        raise RuntimeError("renderer exploded")

    ch = AgentChannel(on_event=boom, spawn=lambda: proc)
    ch.start()
    try:
        ch.submit(Turn(text="first"))
        assert _wait_for(lambda: proc.stdin.lines)
        proc.stdout.emit({"type": "final", "answer": "a"})
        ch.submit(Turn(text="second"))
        assert _wait_for(lambda: len(proc.stdin.lines) == 2), "channel still serving"
    finally:
        ch.close()


def test_non_json_output_is_skipped_without_desynchronising(channel):
    """A stray print from a dependency must not be read as an event."""
    ch, proc, seen = channel
    ch.submit(Turn(text="hi"))
    assert _wait_for(lambda: proc.stdin.lines)
    proc.stdout.emit_raw("WARNING: some library said something")
    proc.stdout.emit({"type": "final", "answer": "ok"})
    assert _wait_for(lambda: len(seen) == 1)
    assert seen[0][0]["type"] == "final"


def test_submitting_before_start_is_refused_with_a_remedy():
    ch = AgentChannel(on_event=lambda e, t: None, spawn=lambda: FakeProc())
    with pytest.raises(AgentChannelError) as excinfo:
        ch.submit(Turn(text="hi"))
    assert "start()" in str(excinfo.value)


def test_starting_twice_is_refused(channel):
    ch, _, _ = channel
    with pytest.raises(AgentChannelError):
        ch.start()


def test_submitting_after_close_is_refused(channel):
    ch, _, _ = channel
    ch.close()
    with pytest.raises(AgentChannelError) as excinfo:
        ch.submit(Turn(text="hi"))
    assert "shutting down" in str(excinfo.value)


def test_missing_agent_binary_names_the_fix():
    def explode():
        raise FileNotFoundError(2, "No such file")

    ch = AgentChannel(on_event=lambda e, t: None)
    ch._spawn = lambda: ch._default_spawn()
    ch._argv = ["definitely-not-a-real-binary-xyz"]
    with pytest.raises(AgentChannelError) as excinfo:
        ch.start()
    message = str(excinfo.value)
    assert "not on PATH" in message
    assert "gaia init" in message, "an error must name what to do about it"


# ----------------------------------------------------------------------
# StreamThrottle
# ----------------------------------------------------------------------


def test_throttle_batches_edits_within_the_interval():
    clock = [100.0]
    flushed = []
    throttle = StreamThrottle(flushed.append, interval=1.0, now=lambda: clock[0])
    throttle.add("a")
    throttle.add("b")
    throttle.add("c")
    assert flushed == ["a"], "only the first add crosses the interval boundary"


def test_throttle_emits_again_once_the_interval_passes():
    clock = [100.0]
    flushed = []
    throttle = StreamThrottle(flushed.append, interval=1.0, now=lambda: clock[0])
    throttle.add("a")
    clock[0] += 1.5
    throttle.add("b")
    assert flushed == ["a", "ab"], "each flush carries everything so far"


def test_throttle_finish_flushes_the_tail():
    """The last token must never be the one the throttle swallowed."""
    clock = [100.0]
    flushed = []
    throttle = StreamThrottle(flushed.append, interval=1.0, now=lambda: clock[0])
    throttle.add("a")
    throttle.add("tail")
    throttle.finish()
    assert flushed[-1] == "atail"


def test_throttle_skips_an_edit_that_would_change_nothing():
    """An unchanged body still costs a call against the channel's rate limit."""
    clock = [100.0]
    flushed = []
    throttle = StreamThrottle(flushed.append, interval=0.0, now=lambda: clock[0])
    throttle.add("a")
    throttle.finish()
    throttle.finish()
    assert flushed == ["a"]
