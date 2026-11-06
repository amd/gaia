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
from gaia.daemon import client as daemon_client
from gaia.daemon.errors import (
    DaemonBusyError,
    DaemonLockError,
    LemonadeNotAnsweringError,
    LemonadeStartFailedError,
)
from gaia.daemon.instance import DaemonInstance
from gaia.daemon.paths import log_path
from gaia.llm import lemonade_client as lc
from gaia.llm.providers.lemonade import (
    LemonadeEngineStartError,
    LemonadeNetworkError,
    classify_lemonade_exception,
)
from gaia.llm.retry import RetryState, attach_retry_state
from gaia.llm.surface_hints import (
    AGENT_UI_SETUP_HINT,
    CLI_SETUP_HINT,
    TUI_SETUP_HINT,
    surface_wording,
)
from gaia.logger import get_logger
from gaia.ui.sse_handler import SSEOutputHandler

#: What a daemon that could not start lemond reports; the log tail it quotes
#: is full of refused connections, which used to read as "briefly busy".
_DAEMON_SAYS = (
    "the daemon did not become healthy within 60s. Last lines:\n"
    "[WinError 10061] No connection could be made because the target machine "
    "actively refused it"
)


def _start_failed() -> LemonadeStartFailedError:
    return LemonadeStartFailedError(
        f"the daemon could not start Lemonade Server: {_DAEMON_SAYS}",
        reason=_DAEMON_SAYS,
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
        error = _start_failure(_start_failed())
        assert isinstance(error, lc.GaiaLemonadeStartError)
        assert "Could not start GAIA's Lemonade Server" in str(error)

    def test_a_server_that_is_up_but_not_answering_stays_a_wait(self):
        error = _start_failure(LemonadeNotAnsweringError("still loading"))
        assert isinstance(error, lc.GaiaLemonadeNotAnsweringError)
        assert not isinstance(error, lc.GaiaLemonadeStartError)

    def test_it_classifies_as_not_retryable_with_the_setup_fix(self):
        error = _start_failure(_start_failed())
        typed = classify_lemonade_exception(error)
        assert isinstance(typed, LemonadeEngineStartError)
        assert typed.retryable is False
        assert TUI_SETUP_HINT in typed.user_message
        assert not any(w in typed.user_message for w in _RETRY_WORDING)

    def test_it_says_why_and_ends_with_the_daemon_log(self):
        typed = classify_lemonade_exception(_start_failure(_start_failed()))
        assert typed.reason == _DAEMON_SAYS
        assert _DAEMON_SAYS in typed.user_message
        assert typed.user_message.endswith(str(log_path()))

    def test_flattened_by_a_wrapper_it_is_still_recognised(self):
        flat = RuntimeError(
            f"Error in send_messages: Could not start GAIA's Lemonade Server: "
            f"the daemon could not start Lemonade Server: {_DAEMON_SAYS}"
        )
        typed = classify_lemonade_exception(flat)
        assert isinstance(typed, LemonadeEngineStartError)
        assert typed.reason == _DAEMON_SAYS

    def test_a_plain_refused_connection_keeps_the_retry_path(self):
        typed = classify_lemonade_exception(
            RuntimeError("Connection refused while the server restarts")
        )
        assert isinstance(typed, LemonadeNetworkError)
        assert typed.retryable is True


class _Reply:
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.headers = {}
        self._detail = detail

    def json(self):
        return {"detail": self._detail}


_DAEMON = DaemonInstance(pid=1, port=1, token="t", api_version="1.9")


def _ensure_fails(*, attach=None, post=None) -> BaseException:
    """The client's error when the real ``ensure_lemonade`` meets *attach*
    (attaching to the daemon) and *post* (its ``/lemonade/ensure`` reply)."""
    with (
        patch.object(
            daemon_client, "start_or_attach", side_effect=attach or (lambda: _DAEMON)
        ),
        patch("requests.post", side_effect=post),
    ):
        try:
            _client()._start_gaia_lemonade()
        except lc.LemonadeClientError as e:
            return e
    raise AssertionError("the start did not fail")


def _raise(error: BaseException):
    def _fail(*_, **__):
        raise error

    return _fail


#: Daemon failures that are not a failed start: each keeps its own remedy.
_NOT_A_FAILED_START = {
    "busy": dict(
        attach=_raise(DaemonBusyError("the GAIA daemon is busy. Retry in a moment."))
    ),
    "start lock held": dict(
        attach=_raise(DaemonLockError("could not acquire the daemon start lock"))
    ),
    "version out of date": dict(
        attach=lambda: DaemonInstance(pid=1, port=1, token="t", api_version="1.1")
    ),
    "request timed out": dict(
        post=_raise(requests.exceptions.Timeout("read timed out"))
    ),
    "409 not managed": dict(
        post=lambda *a, **k: _Reply(
            409,
            "GAIA's Lemonade Server is not installed. Run `gaia init` to install it.",
        )
    ),
}


class TestOnlyTheDaemonsFailedStartIsAnEngineFailure:
    @pytest.mark.parametrize("case", list(_NOT_A_FAILED_START))
    def test_other_daemon_failures_keep_their_old_path(self, case):
        error = _ensure_fails(**_NOT_A_FAILED_START[case])
        assert type(error) is lc.LemonadeClientError
        for seen in (error, RuntimeError(f"Error in send_messages: {error}")):
            assert not isinstance(
                classify_lemonade_exception(seen), LemonadeEngineStartError
            )

    def test_busy_keeps_its_retry_wording(self):
        error = _ensure_fails(**_NOT_A_FAILED_START["busy"])
        assert "Retry in a moment" in str(error)

    def test_an_outdated_daemon_keeps_its_restart_remedy(self):
        error = _ensure_fails(**_NOT_A_FAILED_START["version out of date"])
        assert "gaia daemon restart" in str(error)

    def test_a_failed_ensure_reply_is_an_engine_failure(self):
        error = _ensure_fails(post=lambda *a, **k: _Reply(503, "lemond exited"))
        assert isinstance(error, lc.GaiaLemonadeStartError)
        assert error.reason == "lemond exited"
        typed = classify_lemonade_exception(error)
        assert isinstance(typed, LemonadeEngineStartError)
        assert typed.user_message.startswith(
            "GAIA's AI engine couldn't start: lemond exited."
        )


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
    result, events, calls = _turn(streaming, _start_failure(_start_failed()))

    final = [e for e in events if e.get("type") == "answer"][-1]
    text = final["content"]
    assert text == (
        "GAIA's AI engine couldn't start, so this request is unfinished. "
        f"{_DAEMON_SAYS[0].upper()}{_DAEMON_SAYS[1:]}.\n\n"
        "Nothing had run yet.\n\n"
        "Sending the message again won't help until it is repaired. "
        f"{TUI_SETUP_HINT} Details are in the daemon log at {log_path()}"
    )
    assert not any(w in text for w in _RETRY_WORDING)
    # Each front-end gets its own fix: the Agent UI's in-app one, the CLI's command.
    assert AGENT_UI_SETUP_HINT in surface_wording(text, "agent_ui")
    assert CLI_SETUP_HINT in surface_wording(text, "cli")
    assert result["status"] == "failed"
    # One attempt: retrying the step only repeats the daemon's failed start.
    assert calls == 1
    assert result["model_retries"]["step_retries"] == 0
    # Not offered "Start the model server": starting is what failed.
    assert result["model_server_down"] is False
    assert final.get("failure") is None
