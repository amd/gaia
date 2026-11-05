# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's own model server can't be started at all.

The chat used to wait, then say the model "may be loading or briefly busy —
try sending the message again", which can never work while the engine can't
start. Now the turn ends at once with what failed and the in-app fix (run
setup again), and is not retried step by step.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from gaia.agents.base.agent import Agent
from gaia.daemon.errors import DaemonError, LemonadeNotAnsweringError
from gaia.llm import lemonade_client as lc
from gaia.llm.providers.lemonade import (
    LemonadeEngineStartError,
    LemonadeNetworkError,
    classify_lemonade_exception,
)
from gaia.llm.retry import RetryState, attach_retry_state
from gaia.llm.surface_hints import AGENT_UI_SETUP_HINT, TUI_SETUP_HINT, surface_wording
from gaia.logger import get_logger
from gaia.ui.sse_handler import SSEOutputHandler

#: What a daemon that could not start lemond reports; the log tail it quotes
#: is full of refused connections, which used to read as "briefly busy".
_DAEMON_SAYS = (
    "the daemon did not become healthy within 60s. Last lines:\n"
    "[WinError 10061] No connection could be made because the target machine "
    "actively refused it"
)

_RETRY_WORDING = ("briefly busy", "try sending the message again")


@pytest.fixture(autouse=True)
def _retry_env(monkeypatch):
    monkeypatch.delenv("GAIA_AGENT_MODEL_RETRIES", raising=False)
    monkeypatch.setenv("GAIA_AGENT_MODEL_RETRY_COOLDOWN", "1")


def _client() -> lc.LemonadeClient:
    client = lc.LemonadeClient.__new__(lc.LemonadeClient)
    client.log = get_logger(__name__)
    client._gaia_daemon_dir = None
    return client


def _start_failure(daemon_error: Exception) -> BaseException:
    """The error the client raises when a request finds GAIA's server gone and
    the daemon's start fails, chained to the refused request as it is live."""
    with patch("gaia.daemon.client.ensure_lemonade", side_effect=daemon_error):
        try:
            raise attach_retry_state(
                requests.exceptions.ConnectionError("refused on 60293"),
                RetryState(attempts=1, last_reason="connection error", exhausted=True),
            )
        except requests.exceptions.ConnectionError:
            try:
                _client()._start_gaia_lemonade()
            except lc.LemonadeClientError as e:
                return e
    raise AssertionError("the start did not fail")


class TestTheClientSaysTheEngineCannotStart:
    def test_a_failed_start_is_its_own_error(self):
        error = _start_failure(DaemonError(_DAEMON_SAYS))
        assert isinstance(error, lc.GaiaLemonadeStartError)
        assert "Could not start GAIA's Lemonade Server" in str(error)

    def test_a_server_that_is_up_but_not_answering_stays_a_wait(self):
        error = _start_failure(LemonadeNotAnsweringError("still loading"))
        assert isinstance(error, lc.GaiaLemonadeNotAnsweringError)
        assert not isinstance(error, lc.GaiaLemonadeStartError)

    def test_it_classifies_as_not_retryable_with_the_setup_fix(self):
        error = _start_failure(DaemonError(_DAEMON_SAYS))
        typed = classify_lemonade_exception(error)
        assert isinstance(typed, LemonadeEngineStartError)
        assert typed.retryable is False
        assert TUI_SETUP_HINT in typed.user_message
        assert not any(w in typed.user_message for w in _RETRY_WORDING)

    def test_flattened_by_a_wrapper_it_is_still_recognised(self):
        flat = RuntimeError(
            f"Error in send_messages: Could not start GAIA's Lemonade Server: "
            f"{_DAEMON_SAYS}"
        )
        assert isinstance(classify_lemonade_exception(flat), LemonadeEngineStartError)

    def test_a_plain_refused_connection_keeps_the_retry_path(self):
        typed = classify_lemonade_exception(
            RuntimeError("Connection refused while the server restarts")
        )
        assert isinstance(typed, LemonadeNetworkError)
        assert typed.retryable is True


class _PlainAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass


class _Provider:
    """GAIA's own server, stopped, as the agent loop sees it."""

    def __init__(self):
        self._backend = SimpleNamespace(model_load_listener=None)

    def gaia_lemonade_stopped(self) -> bool:
        return True


def _turn(streaming: bool, error: BaseException):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _PlainAgent(
            silent_mode=True, skip_lemonade=True, model_id=lc.DEFAULT_MODEL_NAME
        )
    agent.streaming = streaming
    agent._is_loaded_ctx_too_small = lambda: False
    agent._model_retry_sleep = lambda _s: None
    agent.console = SSEOutputHandler()
    calls = []

    def _fail(*_, **__):
        calls.append(1)
        raise error

    def _fail_stream(*a, **k):
        _fail(*a, **k)
        yield  # pragma: no cover - makes this a generator

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.llm_client = _Provider()
    chat.send_messages = MagicMock(side_effect=_fail)
    chat.send_messages_stream = MagicMock(side_effect=_fail_stream)
    agent.chat = chat
    result = agent.process_query("Check the ledger", max_steps=5)
    events = []
    while not agent.console.event_queue.empty():
        event = agent.console.event_queue.get()
        if event is not None:
            events.append(event)
    return result, events, len(calls)


@pytest.mark.parametrize("streaming", [True, False])
def test_the_turn_ends_at_once_with_the_setup_fix(streaming):
    result, events, calls = _turn(streaming, _start_failure(DaemonError(_DAEMON_SAYS)))

    final = [e for e in events if e.get("type") == "answer"][-1]
    text = final["content"]
    assert text == (
        "GAIA's AI engine couldn't start, so this request is unfinished.\n\n"
        "Nothing had run yet.\n\n"
        f"Sending the message again won't help until it is repaired. {TUI_SETUP_HINT}"
    )
    assert not any(w in text for w in _RETRY_WORDING)
    # The Agent UI shows its own in-app fix, never a command.
    assert surface_wording(text, "agent_ui").endswith(AGENT_UI_SETUP_HINT)
    assert result["status"] == "failed"
    # One attempt: retrying the step only repeats the daemon's failed start.
    assert calls == 1
    assert result["model_retries"]["step_retries"] == 0
    # Not offered "Start the model server": starting is what failed.
    assert result["model_server_down"] is False
    assert final.get("failure") is None
