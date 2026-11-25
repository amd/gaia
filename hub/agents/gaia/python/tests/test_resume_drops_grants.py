# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Resuming another conversation starts it with no "always" answers.

An "always" was given in one conversation. ``/resume`` in ``gaia chat`` swaps
the conversation under the same console, so without a reset a grant from A
would run unasked in B. Grants are not saved with a session, so resuming back
to A asks again too. A resume that fails keeps the current conversation and
its answers.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from gaiapy.chat_agent import ChatAgent
from gaiapy.interactive import interactive_mode
from gaiapy.session import SessionManager

from gaia.agents.base.console import AgentConsole

_TESTS = ("run_shell_command", {"command": "pytest -q"})
_WRITE = ("write_file", {"file_path": "notes.md", "content": "hi"})


class _Chat:
    """The parts of ChatAgent that ``/resume`` touches, on a real session store."""

    load_session = ChatAgent.load_session
    save_current_session = ChatAgent.save_current_session

    def __init__(self, sessions: SessionManager, console):
        self.session_manager = sessions
        self.console = console
        self.rag = None
        self.indexed_files = set()
        self.watch_directories = []
        self.conversation_history = []
        self.chat_history = []
        self.tool_loader = Mock()
        self.current_session = None

    def _watch_directory(self, path):  # pragma: no cover - no dirs are watched
        raise AssertionError(f"unexpected watch of {path}")


@pytest.fixture
def sessions(tmp_path):
    manager = SessionManager(session_dir=str(tmp_path / "sessions"), auto_cleanup=False)
    for sid, said in (("conv_a", "run the tests"), ("conv_b", "summarise notes")):
        session = manager.create_session(sid)
        session.chat_history = [{"role": "user", "content": said}]
        assert manager.save_session(session)
    return manager


@pytest.fixture
def console(monkeypatch):
    monkeypatch.setattr(
        "gaia.agents.base.console.terminal_is_interactive", lambda: True
    )
    return AgentConsole()


def _in_conversation(sessions, console, sid="conv_a", *granted) -> _Chat:
    agent = _Chat(sessions, console)
    assert agent.load_session(sid)
    for call in granted:
        assert console.grant_call_for_session(*call)
    return agent


def _run(monkeypatch, agent, *lines):
    monkeypatch.setattr("builtins.input", Mock(side_effect=[*lines, EOFError]))
    interactive_mode(agent)


def _asks(monkeypatch, console, call) -> bool:
    """True when *call* puts a question on the terminal (answered "no");
    False when a grant runs it unasked."""
    answer = Mock(return_value="n")
    monkeypatch.setattr("builtins.input", answer)
    approved = console.confirm_tool_execution(*call)
    assert approved is not answer.called, "asked and denied, or granted unasked"
    return answer.called


def test_a_grant_from_one_conversation_asks_again_in_the_resumed_one(
    monkeypatch, sessions, console
):
    agent = _in_conversation(sessions, console, "conv_a", _TESTS, _WRITE)
    assert not _asks(monkeypatch, console, _TESTS), "precondition: the grant holds"

    _run(monkeypatch, agent, "/resume conv_b")

    assert agent.current_session.session_id == "conv_b"
    assert console.session_grant_scopes() == {}
    assert _asks(monkeypatch, console, _TESTS)
    assert _asks(monkeypatch, console, _WRITE)


def test_resuming_back_to_the_first_conversation_still_asks(
    monkeypatch, sessions, console
):
    agent = _in_conversation(sessions, console, "conv_a", _TESTS)

    _run(monkeypatch, agent, "/resume conv_b", "/resume conv_a")

    assert agent.current_session.session_id == "conv_a"
    assert agent.conversation_history == [{"role": "user", "content": "run the tests"}]
    assert _asks(monkeypatch, console, _TESTS)


def test_a_grant_made_after_resuming_does_not_follow_back(
    monkeypatch, sessions, console
):
    agent = _in_conversation(sessions, console, "conv_a")
    _run(monkeypatch, agent, "/resume conv_b")
    assert console.grant_call_for_session(*_WRITE)

    _run(monkeypatch, agent, "/resume conv_a")

    assert _asks(monkeypatch, console, _WRITE)


def test_resuming_the_conversation_already_open_also_forgets(
    monkeypatch, sessions, console
):
    agent = _in_conversation(sessions, console, "conv_a", _TESTS)

    _run(monkeypatch, agent, "/resume conv_a")

    assert _asks(monkeypatch, console, _TESTS)


@pytest.mark.parametrize(
    "target",
    ["no_such_session", "../conv_b", "conv_corrupt"],
    ids=["unknown", "escapes-the-store", "corrupt-file"],
)
def test_a_failed_resume_keeps_the_conversation_and_its_grants(
    monkeypatch, sessions, console, target
):
    (sessions.session_dir / "conv_corrupt.json").write_text("{not json", "utf-8")
    agent = _in_conversation(sessions, console, "conv_a", _TESTS)
    before = list(agent.conversation_history)

    _run(monkeypatch, agent, f"/resume {target}")

    assert agent.current_session.session_id == "conv_a"
    assert agent.conversation_history == before
    assert console.call_is_granted(*_TESTS)


def test_grants_are_gone_before_any_of_the_other_conversation_loads(sessions, console):
    agent = _in_conversation(sessions, console, "conv_a", _TESTS)
    seen = []
    forget = console.reset_tool_approvals

    def reset():
        seen.append(
            (agent.current_session.session_id, list(agent.conversation_history))
        )
        forget()

    console.reset_tool_approvals = reset

    assert agent.load_session("conv_b")

    assert seen == [("conv_a", [{"role": "user", "content": "run the tests"}])]
    assert not console.call_is_granted(*_TESTS)


def test_a_reset_that_fails_does_not_switch_conversations(sessions, console):
    agent = _in_conversation(sessions, console, "conv_a")
    console.reset_tool_approvals = Mock(side_effect=RuntimeError("boom"))

    assert agent.load_session("conv_b") is False

    assert agent.current_session.session_id == "conv_a"
    assert agent.conversation_history == [{"role": "user", "content": "run the tests"}]


def test_typing_resume_at_a_pending_prompt_denies_it_and_switches_nothing(
    monkeypatch, sessions, console
):
    agent = _in_conversation(sessions, console, "conv_a")
    agent.load_session = Mock(wraps=agent.load_session)
    monkeypatch.setattr("builtins.input", Mock(return_value="/resume conv_b"))

    assert console.confirm_tool_execution(*_TESTS) is False

    agent.load_session.assert_not_called()
    assert agent.current_session.session_id == "conv_a"
    assert console.session_grant_scopes() == {}


def test_a_session_without_a_console_still_loads(sessions):
    agent = _Chat(sessions, None)

    assert agent.load_session("conv_b")
    assert agent.current_session.session_id == "conv_b"


def test_resume_forgets_the_calls_declined_in_the_other_conversation(sessions, console):
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.denied_effects import DeclinedCalls

    agent = _in_conversation(sessions, console, "conv_a")
    agent._declined_calls = DeclinedCalls()
    agent._declined_calls.record(*_TESTS)
    agent.forget_declined_calls = lambda: Agent.forget_declined_calls(agent)

    assert agent.load_session("conv_b")

    assert agent._declined_calls.declined(*_TESTS) is None


def test_a_failed_resume_keeps_the_calls_declined_here(sessions, console):
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.denied_effects import DeclinedCalls

    agent = _in_conversation(sessions, console, "conv_a")
    agent._declined_calls = DeclinedCalls()
    agent._declined_calls.record(*_TESTS)
    agent.forget_declined_calls = lambda: Agent.forget_declined_calls(agent)

    assert agent.load_session("no_such_conversation") is False

    assert agent._declined_calls.declined(*_TESTS) == "pytest -q"
