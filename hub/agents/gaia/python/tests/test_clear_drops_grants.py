# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Clearing a conversation forgets its "always" and "no" answers, and only its own.

An "always" was given in the context of one conversation. Once the user clears
it, the next request starts from nothing, so the same command has to ask again.
Every clear path is covered: the TUI's ``/clear`` query, the host's
``clear_history`` control (messaging bridges), and the CLI's ``/reset``.
"""

from __future__ import annotations

import io
import json
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

from gaiapy import stdio
from gaiapy.interactive import interactive_mode
from gaiapy.stdio import PermissionState

from gaia.agents.base.tool_grants import grant_scope
from gaia.ui.sse_handler import SSEOutputHandler

_TESTS = ("run_shell_command", {"command": "pytest -q"})
_WRITE = ("write_file", {"file_path": "notes.md", "content": "hi"})


class _Agent:
    def __init__(self):
        self.console = None
        self.conversation_history = [{"role": "user", "content": "run the tests"}]


def _granted_state(*calls) -> PermissionState:
    """A session whose earlier turn answered "always" to each of *calls*."""
    state = PermissionState()
    handler = SSEOutputHandler()
    state.attach(handler)
    for call in calls:
        assert handler.grant_call_for_session(*call)
    state.detach(handler)
    return state


def _next_turn(state: PermissionState) -> SSEOutputHandler:
    handler = SSEOutputHandler()
    state.attach(handler)
    return handler


def _asks(handler: SSEOutputHandler, call) -> bool:
    """True when *call* raises a prompt rather than running on a grant."""
    approved = handler.confirm_tool_execution(*call, timeout=0.05)
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    asked = any(e.get("type") == "permission_request" for e in events)
    assert not (asked and approved), "an unanswered prompt must never approve"
    return asked


def test_the_same_command_asks_again_after_tui_clear():
    state = _granted_state(_TESTS)
    first = _next_turn(state)
    assert not _asks(first, _TESTS), "precondition: the grant holds"
    state.detach(first)
    agent = _Agent()

    wire = io.StringIO()
    stdio.dispatch_query(agent, stdio.CLEAR_CONVERSATION_QUERY, wire, state=state)

    assert json.loads(wire.getvalue().splitlines()[-1])["answer"] == (
        "conversation_cleared"
    )
    assert state.grants() == []
    assert _asks(_next_turn(state), _TESTS)


def test_the_same_command_asks_again_after_host_clear_history(monkeypatch):
    state = _granted_state(_TESTS, _WRITE)
    agent = _Agent()
    monkeypatch.setattr(
        stdio.sys,
        "stdin",
        io.StringIO(
            json.dumps({stdio.CONTROL_KEY: stdio.CONTROL_CLEAR_HISTORY}) + "\n"
        ),
    )
    q: "queue.Queue" = queue.Queue()
    stdio._pump_stdin(q, state)
    sentinel = q.get_nowait()
    assert isinstance(sentinel, stdio._ClearHistory)
    assert state.grants(), "the pump only queues the clear; the turn loop does it"

    stdio._clear_from_host(agent, state, None)

    assert agent.conversation_history == []
    assert state.grants() == []
    handler = _next_turn(state)
    assert _asks(handler, _TESTS)
    assert _asks(handler, _WRITE)


def test_a_declined_command_is_asked_about_again_after_a_clear():
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.denied_effects import DeclinedCalls

    agent = _Agent()
    agent._declined_calls = DeclinedCalls()
    agent._declined_calls.record(*_TESTS)
    agent.forget_declined_calls = lambda: Agent.forget_declined_calls(agent)

    stdio.dispatch_query(
        agent, stdio.CLEAR_CONVERSATION_QUERY, io.StringIO(), state=PermissionState()
    )

    assert agent._declined_calls.declined(*_TESTS) is None


def test_clear_keeps_full_access_and_drops_only_grants():
    state = _granted_state(_TESTS)
    state.set_full_access(True)

    stdio.dispatch_query(
        _Agent(), stdio.CLEAR_CONVERSATION_QUERY, io.StringIO(), state=state
    )

    assert state.full_access
    assert state.grants() == []


def test_another_sessions_grant_survives_the_clear():
    cleared = _granted_state(_TESTS)
    other = _granted_state(_TESTS)

    stdio.dispatch_query(
        _Agent(), stdio.CLEAR_CONVERSATION_QUERY, io.StringIO(), state=cleared
    )

    assert cleared.grants() == []
    scope = grant_scope(*_TESTS)
    assert other.grants() == [{"key": scope.key, "label": scope.label}]
    assert not _asks(_next_turn(other), _TESTS)


def test_a_clear_during_a_pending_approval_does_not_approve_it():
    state = _granted_state(_WRITE)
    handler = _next_turn(state)
    handler.answers_confirmations = True
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(
            approved=handler.confirm_tool_execution(*_TESTS, timeout=5)
        ),
        daemon=True,
    )
    worker.start()
    deadline = time.monotonic() + 5
    while handler._confirm_event is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert handler._confirm_event is not None, "the prompt never opened"

    # Clearing while the prompt is open must neither answer it nor leave the
    # running turn holding the cleared grants.
    assert state.forget_grants() == 1
    assert not handler._confirm_event.is_set()
    assert not handler.call_is_granted(*_WRITE)

    state.resolve(stdio.DECISION_DENY, None)
    worker.join(timeout=5)
    assert result == {"approved": False}


def test_an_always_answered_before_the_queued_clear_is_still_dropped():
    """/clear typed mid-turn lands after the turn, grants made in it included."""
    state = PermissionState()
    handler = _next_turn(state)
    handler.grant_call_for_session(*_TESTS)
    state.detach(handler)

    stdio._clear_from_host(_Agent(), state, None)

    assert _asks(_next_turn(state), _TESTS)


def test_grants_carried_in_from_an_earlier_turn_do_not_come_back_after_clear():
    """Family keys with no listed answer are dropped too: clear keeps nothing."""
    state = _granted_state(_TESTS)
    # A family key the session holds without a matching listed answer, the way
    # grants restored into a session can arrive.
    stray = grant_scope(*_WRITE)
    state._grants.update(stray.keys)

    stdio.dispatch_query(
        _Agent(), stdio.CLEAR_CONVERSATION_QUERY, io.StringIO(), state=state
    )

    handler = _next_turn(state)
    assert handler.session_grants() == set()
    assert handler.session_grant_scopes() == {}
    assert _asks(handler, _WRITE)


def test_clear_also_empties_the_agents_own_console():
    console = SSEOutputHandler()
    console.grant_call_for_session(*_TESTS)
    agent = _Agent()
    agent.console = console

    stdio._clear_from_host(agent, PermissionState(), None)

    assert not console.call_is_granted(*_TESTS)


def test_cli_reset_forgets_always_answers(monkeypatch):
    console = SSEOutputHandler()
    console.grant_call_for_session(*_TESTS)
    agent = SimpleNamespace(
        console=console,
        current_session=SimpleNamespace(session_id="old"),
        chat_history=[],
        conversation_history=[],
        save_current_session=Mock(return_value=True),
        session_manager=Mock(),
        tool_loader=Mock(),
    )
    agent.session_manager.create_session.return_value = SimpleNamespace(
        session_id="new"
    )

    monkeypatch.setattr(
        "builtins.input", Mock(side_effect=["/reset", "/quit", EOFError])
    )

    interactive_mode(agent)

    assert not console.call_is_granted(*_TESTS)
    assert console.session_grant_scopes() == {}


def test_cli_reset_that_fails_to_save_keeps_the_conversation_and_its_grants(
    monkeypatch,
):
    console = SSEOutputHandler()
    console.grant_call_for_session(*_TESTS)
    agent = SimpleNamespace(
        console=console,
        current_session=SimpleNamespace(session_id="old"),
        chat_history=[],
        conversation_history=[],
        save_current_session=Mock(return_value=False),
        session_manager=Mock(),
        tool_loader=Mock(),
    )
    monkeypatch.setattr("builtins.input", Mock(side_effect=["/reset", EOFError]))

    interactive_mode(agent)

    assert agent.current_session.session_id == "old"
    assert console.call_is_granted(*_TESTS)


_STDIO_CHILD = r"""
import sys
import gaiapy.agent as agent_mod
from gaiapy import stdio

class Agent:
    def __init__(self, config=None):
        self.console = None
        self.conversation_history = []

    def process_query(self, query):
        approved = self.console.confirm_tool_execution(
            "run_shell_command", {"command": "pytest -q"}
        )
        return {"answer": f"approved={approved}"}

agent_mod.GaiaAgent = Agent
agent_mod.GaiaAgentConfig = lambda **kwargs: None
stdio._model_state_event = lambda agent: {"type": "status", "message": "ready"}
sys.exit(stdio.main(sys.argv[1:]))
"""


def test_real_stdio_process_asks_again_after_clear(tmp_path):
    """The terminal UI's own sequence over the real wire: always, /clear, ask."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    import gaia

    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    env["GAIA_TUI_HOME"] = str(tmp_path / "tui")
    # The child must import this checkout, not whichever one is installed.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(stdio.__file__).parents[1]), str(Path(gaia.__file__).parents[1])]
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _STDIO_CHILD,
            "--history-file",
            str(tmp_path / "h.sqlite3"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        env=env,
    )
    lines: "queue.Queue" = queue.Queue()
    threading.Thread(
        target=lambda: [lines.put(line) for line in child.stdout], daemon=True
    ).start()
    transcript = []

    def send(line):
        transcript.append(f"> {line}")
        child.stdin.write(line + "\n")
        child.stdin.flush()

    def until(kind):
        """The next prompt or terminal event; *kind* says which is expected."""
        while True:
            try:
                line = lines.get(timeout=30)
            except queue.Empty:
                raise AssertionError(
                    f"no {kind} within 30s:\n" + "\n".join(transcript)
                ) from None
            if not line.startswith("{"):
                continue
            event = json.loads(line)
            if event.get("type") in ("needs_confirmation", "final", "error"):
                transcript.append(f"< {event['type']} {event.get('answer', '')}")
                return event

    try:
        send(json.dumps({stdio.QUERY_KEY: "run the tests"}))
        ask = until("needs_confirmation")
        assert ask["type"] == "needs_confirmation"
        send(
            json.dumps(
                {
                    stdio.CONTROL_KEY: stdio.CONTROL_TOOL_DECISION,
                    "decision": stdio.DECISION_ALWAYS,
                    "confirm_id": ask.get("confirm_id"),
                }
            )
        )
        assert until("final")["answer"] == "approved=True"

        send(json.dumps({stdio.QUERY_KEY: "run them again"}))
        assert until("final")["answer"] == "approved=True", "always held"

        send(json.dumps({stdio.QUERY_KEY: stdio.CLEAR_CONVERSATION_QUERY}))
        assert until("final")["answer"] == "conversation_cleared"

        send(json.dumps({stdio.QUERY_KEY: "run the tests"}))
        again = until("needs_confirmation")
        assert again["type"] == "needs_confirmation", "\n".join(transcript)
        send(
            json.dumps(
                {
                    stdio.CONTROL_KEY: stdio.CONTROL_TOOL_DECISION,
                    "decision": stdio.DECISION_DENY,
                    "confirm_id": again.get("confirm_id"),
                }
            )
        )
        assert until("final")["answer"] == "approved=False"
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
    print("\n".join(transcript))
