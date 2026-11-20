# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Clearing a conversation forgets its "always" answers, and only its own.

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

from gaia_agent import stdio
from gaia_agent.interactive import interactive_mode
from gaia_agent.stdio import PermissionState

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
