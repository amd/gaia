# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An Agent UI turn is stopped for making no progress, never for running long.

Before #5177 every turn was cut off after 600 s of agent time, even while it was
working, and the user was told "The request was stopped" as if they had pressed
Stop. These tests run real turns through the SSE producer (streaming) and the
non-streaming path on a fake clock, and try to break the rule both ways: a busy
turn must not be stopped, a silent one must be, and each stop must say why.
"""

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gaia.agents.base.agent import TurnCancel
from gaia.llm.lemonade_client import _MAX_REQUEST_BUDGET, request_budget_seconds
from gaia.llm.retry import RetryPolicy, stream_idle_timeout
from gaia.llm.stream_watchdog import STREAM_READ_BACKSTOP
from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager

USER_STOP_WORDS = "stopped before it finished"


class _Clock:
    """The PC's clocks, which the agent thread moves forward by hand.

    ``monotonic`` counts sleep, as ``time.monotonic`` does on Windows;
    ``awake`` does not. :meth:`advance` is awake time, :meth:`sleep` is not.
    """

    def __init__(self):
        self._now = 1000.0
        self._awake = 1000.0
        self._lock = threading.Lock()

    def monotonic(self):
        with self._lock:
            return self._now

    def awake(self):
        with self._lock:
            return self._awake

    def advance(self, seconds):
        with self._lock:
            self._now += seconds
            self._awake += seconds

    def sleep(self, seconds):
        with self._lock:
            self._now += seconds


class _ScriptedAgent:
    """Cached agent that runs *script(agent)* as its turn."""

    def __init__(self, script):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.script = script
        self.started = threading.Event()
        self.saw_cancel_message = None

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.started.set()
        return {"result": self.script(self)}

    def wait_for_cancel(self):
        """Block like a wedged turn until cancelled; return what the loop would."""
        assert self._cancel_event is not None and self._cancel_event.wait(10)
        self.saw_cancel_message = getattr(self._cancel_event, "message", None)
        return self.saw_cancel_message


@pytest.fixture
def clock():
    fake = _Clock()
    fake_time = SimpleNamespace(monotonic=fake.monotonic, time=fake.monotonic)
    with (
        patch.object(helpers, "_time", fake_time),
        patch.object(helpers, "_awake_clock", SimpleNamespace(monotonic=fake.awake)),
    ):
        yield fake


@pytest.fixture
def runtime(clock):
    db = ChatDatabase(":memory:")
    manager = RunManager()
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield db
    helpers._agent_cache.clear()
    db.close()


def _seed(db, agent):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    return session


async def _stream(db, session, agent, *, stop=False):
    request = ChatRequest(session_id=session["id"], message="go", stream=True)
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    task = asyncio.create_task(consume())
    if stop:
        assert await asyncio.to_thread(agent.started.wait, 5)
        await chat.cancel_stream(chat.CancelStreamRequest(session_id=session["id"]))
    await asyncio.wait_for(task, timeout=20)
    return events


def _done(events):
    done = [e for e in events if e.get("type") == "done"]
    assert len(done) == 1, f"expected one done event, got {events!r}"
    return done[0]


def _all_text(events):
    return " ".join(str(e.get("content", "")) for e in events)


def _wait_until_read(handler):
    """Let the stream loop drain what the agent emitted before time moves on."""
    deadline = time.monotonic() + 5
    while not handler.event_queue.empty():
        assert time.monotonic() < deadline, "stream loop stopped reading"
        time.sleep(0.01)
    time.sleep(0.05)  # and finish handling the last one


def _pass_quietly(clock, seconds, step=240):
    """Let *seconds* pass with no events, slowly enough for the loop to look.

    Each step is well under the window and the loop polls about once a step,
    so the loop always sees the wait (a tool, a prompt) while it lasts.
    """
    for _ in range(seconds // step):
        clock.advance(step)
        time.sleep(0.25)


# ---------------------------------------------------------------------------
# Streaming (the Agent UI path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_making_steady_progress_for_25_minutes_is_not_stopped(
    runtime, clock
):
    """The repro from #5177: 22+ rounds of work, 30-60 s apart, past 600 s."""

    def script(agent):
        for minute in range(25):
            clock.advance(60)
            agent.console.print_info(f"round {minute} done")
            _wait_until_read(agent.console)
        return "All 25 rounds finished; summary.txt written."

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300):
        events = await _stream(runtime, session, agent)

    done = _done(events)
    assert done["content"] == "All 25 rounds finished; summary.txt written."
    assert "cancelled" not in done
    assert "without progress" not in _all_text(events)
    assert agent._cancel_event.message is None


@pytest.mark.asyncio
async def test_a_long_silent_tool_run_is_not_stopped(runtime, clock):
    """One tool call can run quietly far past the window; its own timeout owns it."""

    def script(agent):
        agent.console.print_tool_usage("run_python")
        _wait_until_read(agent.console)
        _pass_quietly(clock, 2400)
        agent.console.pretty_print_json({"status": "success"}, "Tool Result")
        agent.console.print_tool_complete()
        return "The script finished after 40 minutes."

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 600):
        events = await _stream(runtime, session, agent)

    assert _done(events)["content"] == "The script finished after 40 minutes."
    assert agent._cancel_event.message is None


@pytest.mark.asyncio
async def test_a_long_wait_on_the_user_is_not_stopped(runtime, clock):
    def script(agent):
        handler = agent.console
        handler._confirm_id = "pending"  # a permission prompt is on screen
        _pass_quietly(clock, 2400)
        handler._confirm_id = None
        handler.print_info("approved")
        return "Done after you approved."

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 600):
        events = await _stream(runtime, session, agent)

    assert _done(events)["content"] == "Done after you approved."


@pytest.mark.asyncio
async def test_progress_is_counted_from_the_last_event_not_the_first(runtime, clock):
    """Silence after an hour of work is measured from the last event."""

    def script(agent):
        for _ in range(15):
            clock.advance(240)
            agent.console.print_info("working")
            _wait_until_read(agent.console)
        clock.advance(301)
        return agent.wait_for_cancel()

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300):
        events = await _stream(runtime, session, agent)

    assert "without progress" in _done(events)["content"]


@pytest.mark.asyncio
async def test_a_silent_turn_is_stopped_and_told_why_at_a_step_boundary(runtime, clock):
    def script(agent):
        agent.console.print_info("started")
        _wait_until_read(agent.console)
        clock.advance(301)
        return agent.wait_for_cancel()

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300):
        events = await _stream(runtime, session, agent)

    expected = (
        "This turn ran for 5 minutes without progress, so GAIA stopped it. "
        'Anything it finished before then is kept. Send "continue" to pick up '
        "where it left off."
    )
    # The agent itself was told why, so its own answer and history say so.
    assert agent.saw_cancel_message == expected
    done = _done(events)
    assert done["content"] == expected
    assert "cancelled" not in done  # not shown as the user's Stop
    assert USER_STOP_WORDS not in _all_text(events)
    assert "GAIA_STREAM_TIMEOUT" not in _all_text(events)
    assert runtime.get_messages(session["id"])[-1]["content"] == expected


@pytest.mark.asyncio
async def test_a_silent_turn_stopped_mid_generation_is_still_told_why(runtime, clock):
    """A turn cancelled mid-generation ends with no text; the reason fills it."""

    def script(agent):
        agent.console.print_info("started")
        _wait_until_read(agent.console)
        clock.advance(301)
        agent.wait_for_cancel()
        return ""

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300):
        events = await _stream(runtime, session, agent)

    done = _done(events)
    assert done["content"].startswith("This turn ran for 5 minutes without progress")
    assert runtime.get_messages(session["id"])[-1]["content"] == done["content"]


@pytest.mark.asyncio
async def test_the_users_stop_keeps_the_stop_wording(runtime, clock):
    def script(agent):
        handler = helpers._active_sse_handlers.get(session["id"])
        assert handler.cancelled.wait(10)
        return ""

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    events = await _stream(runtime, session, agent, stop=True)

    done = _done(events)
    assert done["cancelled"] is True
    assert done["content"] == "Cancelled."
    assert "without progress" not in _all_text(events)
    # A plain stop carries no limit message for the agent to repeat.
    assert agent._cancel_event.message is None


@pytest.mark.asyncio
async def test_a_model_stall_ends_through_the_stall_check_not_this_limit(
    runtime, clock
):
    """A hosted stream that stops sending is ended and retried by the LLM layer.

    Its worst case (stall, retry, stall again) fits well inside the default
    window, so the turn reports the stall's own error, once.
    """
    stall = "I stopped at step 1 because the model stopped sending."

    def script(agent):
        worst_case = 2 * stream_idle_timeout() + RetryPolicy.from_env().deadline
        clock.advance(worst_case)
        agent.console.print_error(stall)
        return stall

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    events = await _stream(runtime, session, agent)

    assert _done(events)["content"] == stall
    assert "without progress" not in _all_text(events)
    assert agent._cancel_event.message is None


# ---------------------------------------------------------------------------
# Non-streaming (API clients)
# ---------------------------------------------------------------------------


async def _ask(db, session):
    return await helpers._get_chat_response(
        db, session, ChatRequest(session_id=session["id"], message="go", stream=False)
    )


@pytest.mark.asyncio
async def test_non_streaming_turn_with_steady_progress_is_not_stopped(runtime, clock):
    def script(agent):
        for _ in range(25):
            clock.advance(60)
            agent.console.print_info("still going")
            time.sleep(0.03)
        return "Finished after 25 minutes."

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300):
        answer = await _ask(runtime, session)

    assert answer == "Finished after 25 minutes."
    assert agent._cancel_event.message is None


@pytest.mark.asyncio
async def test_non_streaming_long_tool_is_not_stopped(runtime, clock):
    def script(agent):
        agent.console.print_tool_usage("run_python")
        for _ in range(40):
            clock.advance(60)
            time.sleep(0.03)
        agent.console.print_tool_complete()
        return "Tool done."

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300):
        assert await _ask(runtime, session) == "Tool done."


@pytest.mark.asyncio
async def test_non_streaming_silent_turn_is_stopped_and_told_why(runtime, clock):
    def script(agent):
        clock.advance(301)
        return agent.wait_for_cancel()

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300):
        answer = await _ask(runtime, session)

    assert answer.startswith("This turn ran for 5 minutes without progress")
    assert "took too long" not in answer
    # The turn's console is handed back once the turn ends.
    assert not isinstance(agent.console, helpers._ActivityConsole)


@pytest.mark.asyncio
async def test_non_streaming_silence_counts_from_the_turn_start(runtime, clock):
    """A turn that goes quiet before the watchdog's first look is still stopped."""
    advanced = threading.Event()

    def script(agent):
        clock.advance(301)
        advanced.set()
        return agent.wait_for_cancel()

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    loop = asyncio.get_running_loop()
    real_run_in_executor = loop.run_in_executor

    def worker_runs_first(executor, func, *args):
        # The worker thread wins the race: the clock moves before this returns.
        future = real_run_in_executor(executor, func, *args)
        if getattr(func, "__name__", "") == "_do_chat":
            assert advanced.wait(5)
        return future

    with (
        patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300),
        patch.object(loop, "run_in_executor", worker_runs_first),
    ):
        answer = await _ask(runtime, session)

    assert answer.startswith("This turn ran for 5 minutes without progress")


# ---------------------------------------------------------------------------
# The limit itself
# ---------------------------------------------------------------------------


def test_default_window_outlasts_the_longest_silent_model_call():
    """A slow local prefill gets up to the request budget for its first token;
    the turn limit must never cut in before the LLM layer's own check does."""
    longest_local = _MAX_REQUEST_BUDGET + STREAM_READ_BACKSTOP
    assert request_budget_seconds(10**7) <= _MAX_REQUEST_BUDGET
    assert helpers._DEFAULT_STREAM_TIMEOUT_SECONDS > longest_local


@pytest.mark.parametrize(
    "seconds, words",
    [
        (4500, "75 minutes"),
        (3600, "1 hour"),
        (7200, "2 hours"),
        (300, "5 minutes"),
        (60, "1 minute"),
        (90, "90 seconds"),
        (1, "1 second"),
    ],
)
def test_the_message_names_the_configured_window(seconds, words):
    message = helpers._turn_idle_message(seconds)
    assert f"ran for {words} without progress" in message
    assert USER_STOP_WORDS not in message
    assert "GAIA_STREAM_TIMEOUT" not in message


def test_idle_clock_resets_on_progress_and_ignores_owned_waits():
    idle = helpers._TurnIdleClock(100.0)
    assert idle.idle_for(False, 160.0) == 60.0
    idle.progressed(170.0)
    assert idle.idle_for(False, 200.0) == 30.0
    assert idle.idle_for(True, 5000.0) == 0.0  # a tool run or a user wait
    assert idle.idle_for(False, 5050.0) == 50.0
    idle.progressed(10.0)  # a stale progress stamp never moves it back
    assert idle.idle_for(False, 5060.0) == 60.0


def test_turn_cancel_without_a_reason_is_a_plain_stop():
    event = TurnCancel()
    event.set()
    assert event.is_set() and event.message is None
    event = TurnCancel()
    event.cancel("why")
    assert event.is_set() and event.message == "why"


def test_non_streaming_tool_ends_on_its_result_like_the_sse_handler():
    from gaia.agents.base.console import SilentConsole

    clock = _Clock()
    console = helpers._ActivityConsole(SilentConsole(), clock.monotonic)
    console.print_tool_usage("run_python")
    console.pretty_print_json({"code": "x"}, "Arguments")
    assert console.tool_running is True  # arguments are not the result
    clock.advance(5)
    console.pretty_print_json({"status": "success"}, title="Tool Result")
    assert console.tool_running is False
    assert console.last_progress == clock.monotonic()


# ---------------------------------------------------------------------------
# A PC that sleeps mid-turn
# ---------------------------------------------------------------------------


EIGHT_HOURS = 8 * 60 * 60


@pytest.mark.asyncio
async def test_a_turn_the_pc_slept_through_carries_on_after_waking(runtime, clock):
    def script(agent):
        agent.console.print_info("started")
        _wait_until_read(agent.console)
        clock.sleep(EIGHT_HOURS)
        time.sleep(0.5)  # the loop looks at the clock after waking
        agent.console.print_info("still going after the sleep")
        _wait_until_read(agent.console)
        return "Finished after the PC woke up."

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300):
        events = await _stream(runtime, session, agent)

    assert _done(events)["content"] == "Finished after the PC woke up."
    assert "without progress" not in _all_text(events)
    assert agent._cancel_event.message is None


@pytest.mark.asyncio
async def test_a_non_streaming_turn_the_pc_slept_through_carries_on(runtime, clock):
    def script(agent):
        agent.console.print_info("started")
        clock.sleep(EIGHT_HOURS)
        time.sleep(0.5)
        agent.console.print_info("still going after the sleep")
        return "Finished after the PC woke up."

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 300):
        assert await _ask(runtime, session) == "Finished after the PC woke up."
    assert agent._cancel_event.message is None


@pytest.mark.asyncio
async def test_silence_after_waking_still_stops_the_turn(runtime, clock):
    def script(agent):
        agent.console.print_info("started")
        _wait_until_read(agent.console)
        clock.sleep(EIGHT_HOURS)
        time.sleep(0.5)
        # Awake again, and silent past the window.
        clock.advance(30)
        return agent.wait_for_cancel()

    agent = _ScriptedAgent(script)
    session = _seed(runtime, agent)
    with patch.object(helpers, "_STREAM_TIMEOUT_SECONDS", 20):
        events = await _stream(runtime, session, agent)

    assert "without progress" in _done(events)["content"]


@pytest.mark.asyncio
async def test_a_broken_time_limit_clock_tells_the_user_to_restart_gaia(runtime):
    """Not "try again": every resend would hit the same broken clock."""
    from gaia.utils.awake_clock import AwakeClockError

    message = "GAIA's time-limit clock stopped working. Restart GAIA."

    def broken():
        raise AwakeClockError(message)

    agent = _ScriptedAgent(lambda _agent: "done")
    session = _seed(runtime, agent)
    with patch.object(helpers, "_awake_clock", SimpleNamespace(monotonic=broken)):
        events = await _stream(runtime, session, agent)
    errors = [e for e in events if e.get("type") == "error"]
    assert errors, events
    assert errors[-1]["content"] == message
