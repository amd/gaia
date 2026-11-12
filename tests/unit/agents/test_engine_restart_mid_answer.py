# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's model server stops part-way through an answer.

The chat used to freeze on the half-written text for minutes, with no status,
then a different answer silently replaced it. Now the cut-off text is taken
back with a visible note, a live line says the server is being restarted
while the step waits, and a server that never comes back ends the turn with
a message that names what happened and offers the restart (no CLI command).
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    ANSWER_RESTART_MESSAGE,
    SERVER_RESTARTING_MESSAGE,
    SERVER_STARTING_MESSAGE,
    Agent,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, LemonadeClientError
from gaia.llm.providers.lemonade import MODEL_SERVER_DOWN
from gaia.llm.retry import RetryState, attach_retry_state
from gaia.llm.surface_hints import surface_wording
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

_PARTIAL = "The ledger bal"
_ANSWER = "The ledger balances: 3 entries, total 42."


class _PlainAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _retry_env(monkeypatch):
    monkeypatch.delenv("GAIA_AGENT_MODEL_RETRIES", raising=False)
    monkeypatch.setenv("GAIA_AGENT_MODEL_RETRY_COOLDOWN", "1")


def _cut():
    """What the client raises when the connection drops after text arrived."""
    return attach_retry_state(
        LemonadeClientError("Streaming request failed: Connection error."),
        RetryState(
            attempts=1,
            last_reason="connection error",
            output_delivered=True,
            exhausted=True,
        ),
    )


class _Provider:
    """The Lemonade provider as the agent sees it: GAIA's server, stopped or not."""

    def __init__(self, stopped: bool):
        self.stopped = stopped
        self._backend = SimpleNamespace(model_load_listener=None)

    def gaia_lemonade_stopped(self) -> bool:
        return self.stopped


def _turn(stopped: bool, *calls):
    """One streaming turn on the Agent UI's console. Each entry of *calls* is a
    function ``(provider) -> iterator of chunks`` for one model call."""
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _PlainAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = True
    agent._is_loaded_ctx_too_small = lambda: False
    agent._model_retry_sleep = lambda _s: None
    agent.console = SSEOutputHandler()
    provider = _Provider(stopped)
    queue = list(calls)

    def _stream(*_, **__):
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        yield from queue.pop(0)(provider)

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.llm_client = provider
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat
    result = agent.process_query("Check the ledger", max_steps=5)
    events = []
    while not agent.console.event_queue.empty():
        event = agent.console.event_queue.get()
        if event is not None:
            events.append(event)
    return result, events


def _cut_after_partial(provider):
    yield SimpleNamespace(text=_PARTIAL, is_complete=False, stats=None)
    raise _cut()


def _answer_after_restart(provider):
    """The daemon brings the server back inside the next call, as the client does."""
    provider._backend.model_load_listener(DEFAULT_MODEL_NAME, "starting_server")
    provider.stopped = False
    yield SimpleNamespace(text=_ANSWER, is_complete=False, stats=None)
    yield SimpleNamespace(text="", is_complete=True, stats={})


def _never_back(provider):
    raise LemonadeClientError(
        "Could not start GAIA's Lemonade Server: lemond exited during start; "
        "read lemond.log"
    )
    yield  # pragma: no cover - makes this a generator


def _index(events, predicate):
    return next(i for i, e in enumerate(events) if predicate(e))


def _restart_markers(events):
    return [e for e in events if e.get("retracts_answer")]


def _final(events):
    return [e for e in events if e.get("type") == "answer"][-1]


def test_a_stopped_server_takes_the_partial_back_and_says_it_is_restarting():
    result, events = _turn(True, _cut_after_partial, _answer_after_restart)

    assert _ANSWER in result["result"]
    assert result["model_failed"] is False
    [marker] = _restart_markers(events)
    assert marker == {
        "type": "status",
        "status": "warning",
        "phase": "answer_restart",
        "message": ANSWER_RESTART_MESSAGE,
        "retracts_answer": True,
    }
    partial = _index(
        events, lambda e: e.get("type") == "chunk" and _PARTIAL in e["content"]
    )
    taken_back = events.index(marker)
    restarting = [
        i
        for i, e in enumerate(events)
        if e.get("phase") == "model_restart"
        and e.get("message") == SERVER_RESTARTING_MESSAGE
    ]
    new_text = _index(
        events, lambda e: e.get("type") == "chunk" and _ANSWER in e["content"]
    )
    # Cut-off text, then the note, then the live line through the wait (both
    # the cool-down and the daemon's start), then the new answer.
    assert partial < taken_back < restarting[0]
    assert len(restarting) == 2
    assert restarting[-1] < new_text


def test_a_dropped_connection_to_another_server_still_marks_the_restart():
    """Not GAIA's server: nothing to restart, but the swap is never silent."""
    result, events = _turn(
        False,
        _cut_after_partial,
        lambda _p: iter(
            [
                SimpleNamespace(text=_ANSWER, is_complete=False, stats=None),
                SimpleNamespace(text="", is_complete=True, stats={}),
            ]
        ),
    )

    assert _ANSWER in result["result"]
    assert len(_restart_markers(events)) == 1
    retry = [e for e in events if e.get("phase") == "model_retry"]
    assert retry and retry[0]["message"].startswith(
        "The connection to the model service dropped — retrying this step in 1s"
    )
    assert not any(e.get("phase") == "model_restart" for e in events)
    # Over the relay the live line survives the repeated warning line.
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    wire = [out for e in events for out in translator.translate(e)]
    assert any(e.get("phase") == "model_retry" for e in wire)
    assert any(e.get("retracts_answer") for e in wire)


def test_a_failure_before_any_text_needs_no_restart_marker():
    def _cut_before_text(_provider):
        raise _cut()
        yield  # pragma: no cover

    _, events = _turn(True, _cut_before_text, _answer_after_restart)

    assert _restart_markers(events) == []
    assert any(e.get("message") == SERVER_RESTARTING_MESSAGE for e in events)


def _assert_server_not_back(result, events):
    final = _final(events)
    text = final["content"]
    assert text == (
        "The model server stopped while answering, and GAIA could not restart "
        "it, so this request is unfinished.\n\nNothing had run yet.\n\n"
        "Send your message again and GAIA will try to start the model server "
        "once more."
    )
    # The Agent UI points at its own button instead.
    assert surface_wording(text, "agent_ui").endswith(
        "Press Start the model server below, then send your message again."
    )
    assert result["status"] == "failed"
    assert result["model_server_down"] is True
    # The Agent UI offers its "Start the model server" button on this failure.
    assert final["failed"] is True and final["failure"] == MODEL_SERVER_DOWN


def test_a_server_the_daemon_cannot_start_ends_the_turn_with_what_to_do():
    result, events = _turn(True, _cut_after_partial, _never_back)
    _assert_server_not_back(result, events)


def test_a_server_that_stays_down_through_every_retry_ends_the_same_way():
    def _down(_provider):
        raise _cut()
        yield  # pragma: no cover

    result, events = _turn(True, _cut_after_partial, _down, _down)
    _assert_server_not_back(result, events)
    assert result["model_retries"]["step_retries"] == 2


def test_starting_a_server_that_was_never_stopped_says_starting():
    def _cold_start(provider):
        provider._backend.model_load_listener(DEFAULT_MODEL_NAME, "starting_server")
        yield SimpleNamespace(text=_ANSWER, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    _, events = _turn(False, _cold_start)

    phases = [(e.get("phase"), e.get("message")) for e in events if e.get("phase")]
    assert ("model_restart", SERVER_STARTING_MESSAGE) in phases
    assert not any(m == SERVER_RESTARTING_MESSAGE for _, m in phases)


def test_the_answer_event_never_carries_the_cut_off_text():
    _, events = _turn(True, _cut_after_partial, _answer_after_restart)
    assert _PARTIAL not in json.dumps(_final(events)["content"]).replace(
        json.dumps(_ANSWER)[1:-1], ""
    )
