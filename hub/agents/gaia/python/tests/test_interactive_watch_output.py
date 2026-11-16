# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/watch`` must not reprint the agent's answer (CodeQL #1049).

The agent already streams its answer through its console, as for any other
turn. The command used to print the returned text a second time, and a denial
from the path validator names the credential folder it refused, so that path
went straight to stdout.
"""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gaia_agent.interactive import interactive_mode

SECRET_DIR = "/home/user/.ssh"
DENIAL = (
    f"Read blocked: '{SECRET_DIR}/id_rsa' is inside '{SECRET_DIR}', which holds "
    "credentials. The agent is not allowed to read from it."
)


def _agent(answer):
    return SimpleNamespace(
        current_session=None,
        conversation_history=[],
        chat_history=[],
        save_current_session=Mock(return_value=True),
        session_manager=Mock(),
        tool_loader=Mock(),
        process_query=Mock(return_value=answer),
    )


def _run(agent, monkeypatch, *lines):
    monkeypatch.setattr("builtins.input", Mock(side_effect=[*lines, "/quit", EOFError]))
    interactive_mode(agent)


@pytest.mark.parametrize(
    "answer",
    [
        {"result": DENIAL},
        {"result": DENIAL, "status": "error", "error_count": 1},
        {"result": f"Watching {SECRET_DIR} for changes."},
    ],
)
def test_watch_never_echoes_the_answer(answer, monkeypatch, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    agent = _agent(answer)

    _run(agent, monkeypatch, f"/watch {SECRET_DIR}")

    agent.process_query.assert_called_once_with(f"Watch the directory: {SECRET_DIR}")
    captured = capsys.readouterr()
    for stream in (captured.out, captured.err, caplog.text):
        assert DENIAL not in stream
        assert f"'{SECRET_DIR}'" not in stream
        assert f"Watching {SECRET_DIR}" not in stream


def test_watch_without_a_directory_asks_for_one_and_runs_nothing(monkeypatch, capsys):
    agent = _agent({"result": DENIAL})

    _run(agent, monkeypatch, "/watch")

    agent.process_query.assert_not_called()
    assert "Usage: /watch <directory_path>" in capsys.readouterr().out


def test_an_answer_without_a_result_key_does_not_crash(monkeypatch, capsys):
    """The old ``result['result']`` raised KeyError on a dict without it."""
    agent = _agent({"status": "error"})

    _run(agent, monkeypatch, f"/watch {SECRET_DIR}")

    out = capsys.readouterr().out
    assert "Error:" not in out
