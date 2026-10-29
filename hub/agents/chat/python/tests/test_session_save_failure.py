# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Interactive commands must not discard a conversation after a failed save."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gaia_agent_chat.agent import ChatAgent
from gaia_agent_chat.app import interactive_mode
from gaia_agent_chat.session import SessionManager


@pytest.fixture
def agent():
    return SimpleNamespace(
        current_session=SimpleNamespace(session_id="original"),
        chat_history=[{"role": "user", "content": "keep this conversation"}],
        conversation_history=[{"role": "user", "content": "keep this conversation"}],
        save_current_session=Mock(return_value=True),
        session_manager=Mock(),
        tool_loader=Mock(),
    )


@pytest.mark.parametrize("command", ["/quit", "/reset"])
def test_failed_save_preserves_session_and_allows_retry(
    agent, command, monkeypatch, capsys
):
    original = agent.current_session
    history = list(agent.chat_history)
    agent.save_current_session.side_effect = [False, True]
    user_input = Mock(side_effect=[command, "/quit", EOFError])
    monkeypatch.setattr("builtins.input", user_input)

    interactive_mode(agent)

    output = capsys.readouterr().out
    assert user_input.call_count == 2
    assert agent.save_current_session.call_count == 2
    assert agent.current_session is original
    assert agent.chat_history == history
    assert agent.conversation_history == history
    agent.session_manager.create_session.assert_not_called()
    agent.tool_loader.reset_session.assert_not_called()
    assert "Failed to save session" in output
    assert "/save" in output
    assert "unsaved changes will be lost" in output
    assert "Previous session saved" not in output


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, EOFError])
def test_failed_quit_allows_explicit_unsaved_exit(
    agent, interruption, monkeypatch, capsys
):
    agent.save_current_session.return_value = False
    user_input = Mock(side_effect=["/quit", interruption])
    monkeypatch.setattr("builtins.input", user_input)

    interactive_mode(agent)

    assert user_input.call_count == 2
    assert "unsaved changes will be lost" in capsys.readouterr().out


def test_reset_retries_after_save_failure(agent, monkeypatch, capsys):
    agent.save_current_session.side_effect = [False, True, True]
    new_session = SimpleNamespace(session_id="new")
    agent.session_manager.create_session.return_value = new_session
    monkeypatch.setattr(
        "builtins.input", Mock(side_effect=["/reset", "/reset", "/quit", EOFError])
    )

    interactive_mode(agent)

    assert agent.current_session is new_session
    assert agent.chat_history == []
    agent.session_manager.create_session.assert_called_once_with()
    agent.tool_loader.reset_session.assert_called_once_with()
    output = capsys.readouterr().out
    assert output.count("Failed to save session") == 1
    assert output.count("Previous session saved") == 1


def test_quit_without_session_does_not_save(agent, monkeypatch, capsys):
    agent.current_session = None
    monkeypatch.setattr("builtins.input", Mock(return_value="/quit"))

    interactive_mode(agent)

    agent.save_current_session.assert_not_called()
    assert "Goodbye!" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["/quit", "/reset"])
def test_storage_failure_can_be_repaired_without_losing_chat(
    agent, command, tmp_path, monkeypatch, capsys
):
    manager = SessionManager(str(tmp_path / "sessions"), auto_cleanup=False)
    original = manager.create_session("session_keep")
    agent.current_session = original
    agent.session_manager = manager
    agent.indexed_files = {"report.txt"}
    agent.watch_directories = []
    agent.save_current_session = lambda: ChatAgent.save_current_session(agent)
    manager.session_dir.rmdir()
    manager.session_dir.write_text("storage is unavailable", encoding="utf-8")

    inputs = iter([command, "/quit"])
    before_retry = []

    def user_input(_prompt):
        next_command = next(inputs, None)
        if next_command is None:
            raise EOFError
        if before_retry:
            manager.session_dir.unlink()
            manager.session_dir.mkdir()
        before_retry.append((agent.current_session, list(agent.chat_history)))
        return next_command

    monkeypatch.setattr("builtins.input", user_input)

    interactive_mode(agent)

    assert before_retry[1] == (original, agent.conversation_history)
    saved = manager.load_session(original.session_id)
    assert saved.chat_history == agent.conversation_history
    assert saved.indexed_documents == ["report.txt"]
    assert "Failed to save session" in capsys.readouterr().out
