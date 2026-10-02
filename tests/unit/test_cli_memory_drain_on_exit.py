# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Leaving a chat must not throw away the last turn's memories.

Extraction now finishes *after* the answer, so when a chat session ends the
model call for the final turn is still in flight. Whatever tears the session
down has to drain it first, or "my name is Priya" is on screen and never on
disk.

The drain was originally wired only into the one-shot ``-q`` branch, which is
the path people use least. These tests drive the real CLI entry points and
assert the drain runs on the exits that matter: a normal return, Ctrl-C, and
an error.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def chat_agent_stubs(monkeypatch):
    """Stand in for the agent wheels and the Lemonade pre-flight.

    Returns the module namespace so a test can make ``interactive_mode`` raise.
    """
    monkeypatch.setattr(
        "gaia.cli.initialize_lemonade_for_agent",
        lambda *a, **k: (True, "http://localhost:13305/api/v1"),
    )

    agent = MagicMock()
    agent.current_session = "session-1"

    agent_module = types.ModuleType("gaia_agent.agent")
    agent_module.GaiaAgent = MagicMock(return_value=agent)
    agent_module.GaiaAgentConfig = MagicMock()

    app_module = types.ModuleType("gaia_agent_chat.app")
    app_module.interactive_mode = MagicMock()

    monkeypatch.setitem(sys.modules, "gaia_agent", types.ModuleType("gaia_agent"))
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", agent_module)
    monkeypatch.setitem(
        sys.modules, "gaia_agent_chat", types.ModuleType("gaia_agent_chat")
    )
    monkeypatch.setitem(sys.modules, "gaia_agent_chat.app", app_module)

    return types.SimpleNamespace(agent=agent, app=app_module, module=agent_module)


@pytest.fixture
def drain(monkeypatch):
    spy = MagicMock(return_value=True)
    monkeypatch.setattr("gaia.agents.base.memory.drain_memory_extraction", spy)
    return spy


def test_a_normal_exit_drains(chat_agent_stubs, drain):
    from gaia.cli import _launch_interactive_cli

    _launch_interactive_cli()

    drain.assert_called_once_with(chat_agent_stubs.agent)


def test_ctrl_c_drains(chat_agent_stubs, drain):
    """The interrupt is exactly when an unfinished extraction is likeliest."""
    chat_agent_stubs.app.interactive_mode.side_effect = KeyboardInterrupt

    from gaia.cli import _launch_interactive_cli

    _launch_interactive_cli()

    drain.assert_called_once_with(chat_agent_stubs.agent)


def test_an_error_exit_still_drains(chat_agent_stubs, drain):
    """A crash in the loop does not mean the turns before it are worthless."""
    chat_agent_stubs.app.interactive_mode.side_effect = RuntimeError("boom")

    from gaia.cli import _launch_interactive_cli

    with pytest.raises(SystemExit):
        _launch_interactive_cli()

    drain.assert_called_once_with(chat_agent_stubs.agent)


def test_a_failing_drain_does_not_take_the_exit_down(chat_agent_stubs, drain, caplog):
    """Cleanup must not turn a clean exit into a traceback."""
    drain.side_effect = RuntimeError("extraction thread wedged")

    from gaia.cli import _launch_interactive_cli

    with caplog.at_level("WARNING"):
        _launch_interactive_cli()

    assert "extraction thread wedged" in caplog.text


def test_a_failure_before_the_agent_exists_is_not_an_error(monkeypatch, drain):
    """``agent`` is unbound if the pre-flight fails; the drain must be skipped."""

    def _no_backend(*_a, **_k):
        return False, None

    monkeypatch.setattr("gaia.cli.initialize_lemonade_for_agent", _no_backend)

    from gaia.cli import _launch_interactive_cli

    with pytest.raises(SystemExit):
        _launch_interactive_cli()

    drain.assert_not_called()


def test_interactive_chat_runs_the_flagship(chat_agent_stubs, drain):
    """`gaia chat` / `gaia --cli` must build GaiaAgent, never the retired ChatAgent."""
    from gaia.cli import _launch_interactive_cli

    _launch_interactive_cli()

    chat_agent_stubs.module.GaiaAgent.assert_called_once()
    chat_agent_stubs.app.interactive_mode.assert_called_once_with(
        chat_agent_stubs.agent
    )


# ``run_cli`` goes through ``asyncio.run``, whose Windows self-pipe trips the
# unit-test socket guard; the stubs keep this test off the network.
@pytest.mark.allow_network
def test_one_shot_chat_runs_the_flagship(chat_agent_stubs, drain):
    """`gaia chat -q` must build GaiaAgent and hand it the query."""
    from gaia.cli import run_cli

    chat_agent_stubs.agent.process_query.return_value = {"status": "success"}

    rc = run_cli(
        "chat",
        query="hi",
        model="stub-model",
        device="cpu",
        base_url="http://stub.invalid/api/v1",
        no_lemonade_check=True,
    )

    assert rc == 0
    chat_agent_stubs.module.GaiaAgent.assert_called_once()
    chat_agent_stubs.agent.process_query.assert_called_once_with("hi", trace=False)
    drain.assert_called_once_with(chat_agent_stubs.agent)
