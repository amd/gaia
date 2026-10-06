# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A transient model-endpoint failure mid-run must not throw the run away.

The client retries a transient failure itself (``gaia.llm.retry``); when it
gives up, the error carries a retry record. The agent loop then waits and asks
again with the conversation untouched, a bounded number of times, and only
then ends the turn — with an answer that says plainly the model became
unreachable, never one that could pass for a result.

Everything else keeps its existing path, pinned here: a permanent error ends
the turn at once, context overflow trims and retries once, a model loaded with
too small a window re-raises for reload, and an auth error is not retried.
"""

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _CONTEXT_STILL_OVERFLOWING_MESSAGE,
    DEFAULT_MODEL_RETRY_COOLDOWN,
    DEFAULT_MODEL_STEP_RETRIES,
    Agent,
    model_retry_cooldown,
    model_step_retries,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.verification import strip_verification_scope
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    LemonadeAuthError,
    LemonadeClientError,
)
from gaia.llm.retry import RetryState, attach_retry_state

_TOOL = "read_ledger_for_retry_test"
_ANSWER = "The ledger balances: 3 entries, total 42."


class _LedgerAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def read_ledger_for_retry_test(page: int) -> dict:
            """Read one page of the ledger."""
            return {"status": "success", "page": page, "entries": 3}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture(autouse=True)
def _retry_env(monkeypatch):
    monkeypatch.delenv("GAIA_AGENT_MODEL_RETRIES", raising=False)
    monkeypatch.setenv("GAIA_AGENT_MODEL_RETRY_COOLDOWN", "1")


def _make_agent(streaming: bool) -> _LedgerAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _LedgerAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = streaming
    agent._is_loaded_ctx_too_small = lambda: False
    agent.sleeps = []
    agent._model_retry_sleep = agent.sleeps.append
    return agent


@pytest.fixture(params=[False, True], ids=["non-streaming", "streaming"])
def agent(request, clean_registry):  # pylint: disable=unused-argument
    return _make_agent(request.param)


def _tool_call(page: int) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{page}",
                    "type": "function",
                    "function": {
                        "name": _TOOL,
                        "arguments": json.dumps({"page": page}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
            "content": None,
        }
    )


def _transient(reason="HTTP 503", attempts=5, wrap_connection_error=False):
    """What the client raises after spending its retries on a transient
    failure — optionally re-wrapped the way AgentSDK wraps ConnectionError."""
    error = attach_retry_state(
        LemonadeClientError(f"Chat completion failed ({reason})"),
        RetryState(attempts=attempts, last_reason=reason, exhausted=True),
    )
    if not wrap_connection_error:
        return error
    try:
        raise ConnectionError("Failed to connect to LLM server") from error
    except ConnectionError as wrapped:
        return wrapped


def _script(agent, *replies):
    """Each entry is reply text, or an exception to raise. Records a copy of
    the messages every call was sent, so tests can compare them."""
    queue = list(replies)
    sent = []
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})

    def _next(kwargs):
        sent.append(json.loads(json.dumps(kwargs["messages"], default=str)))
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def _send(*_, **kwargs):
        return SimpleNamespace(
            text=_next(kwargs), stats={"input_tokens": 10, "output_tokens": 5}
        )

    def _stream(*_, **kwargs):
        text = _next(kwargs)
        if text.startswith('{"__tool_calls__"'):
            yield SimpleNamespace(text=text, is_complete=True, stats={})
            return
        yield SimpleNamespace(text=text, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    chat.send_messages = MagicMock(side_effect=_send)
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat
    return sent


def _types(agent):
    return [e.get("type") for e in agent.error_history]


# ── transient failures ───────────────────────────────────────────────────


@pytest.mark.parametrize("wrapped", [False, True], ids=["raw", "connection-error"])
def test_transient_failure_mid_run_does_not_end_the_run(agent, wrapped):
    sent = _script(
        agent,
        _tool_call(1),
        _tool_call(2),
        _transient(wrap_connection_error=wrapped),
        _ANSWER,
    )
    result = agent.process_query("Check the ledger", max_steps=10)

    assert _ANSWER in result["result"]
    assert "couldn't finish" not in result["result"]
    # Progress was kept: the retry sent exactly what the failed call sent,
    # both tool results included.
    assert len(sent) == 4
    assert sent[3] == sent[2]
    assert sum(1 for m in sent[3] if m.get("role") == "tool") == 2
    # One cool-down (1 s, sliced so Stop stays responsive), no failure recorded.
    assert sum(agent.sleeps) == pytest.approx(1.0)
    assert result["model_retries"]["step_retries"] == 1
    assert result["model_retries"]["request_retries"] == 4
    assert result["model_unreachable"] is False
    assert "llm_unreachable" not in _types(agent)
    assert result["status"] != "failed"


def test_cool_down_doubles_per_step_retry(agent):
    _script(agent, _tool_call(1), _transient(), _transient(), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)
    assert _ANSWER in result["result"]
    assert sum(agent.sleeps) == pytest.approx(1.0 + 2.0)
    assert result["model_retries"]["step_retries"] == 2


def test_persistent_failure_ends_with_a_plain_unreachable_answer(agent):
    sent = _script(
        agent,
        _tool_call(1),
        _tool_call(2),
        *[_transient() for _ in range(1 + DEFAULT_MODEL_STEP_RETRIES)],
    )
    result = agent.process_query("Check the ledger", max_steps=10)

    text = strip_verification_scope(result["result"])
    assert "couldn't finish this task" in text
    assert "language model stopped responding at step 3" in text
    assert "5 attempts failed, the last with HTTP 503" in text
    assert "The task is incomplete." in text
    # What had been done so far.
    assert f"{_TOOL}: 2x" in text
    # Cannot pass for success.
    assert result["status"] == "failed"
    assert result["model_unreachable"] is True
    assert "llm_unreachable" in _types(agent)
    assert "having trouble reaching" not in text
    assert len(sent) == 2 + 1 + DEFAULT_MODEL_STEP_RETRIES


def test_unreachable_before_any_tool_says_nothing_ran(agent):
    _script(agent, *[_transient() for _ in range(1 + DEFAULT_MODEL_STEP_RETRIES)])
    result = agent.process_query("Check the ledger", max_steps=10)
    assert "No tools had run yet" in result["result"]
    assert result["status"] == "failed"


def test_step_retry_can_be_turned_off(agent, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_MODEL_RETRIES", "0")
    sent = _script(agent, _tool_call(1), _transient())
    result = agent.process_query("Check the ledger", max_steps=10)
    assert len(sent) == 2
    assert agent.sleeps == []
    assert "couldn't finish this task" in result["result"]


def test_read_timeout_is_not_retried_at_the_step_level(agent):
    """The request already spent its whole time budget; #1030's handling
    applies instead of another long wait."""
    sent = _script(agent, _tool_call(1), _transient(reason="read timeout", attempts=1))
    result = agent.process_query("Check the ledger", max_steps=10)
    assert len(sent) == 2
    assert agent.sleeps == []
    assert "couldn't finish this task" not in result["result"]


def test_stop_during_the_cool_down_cancels_the_turn(agent):
    agent.console.cancelled = threading.Event()

    def press_stop(_seconds):
        agent.console.cancelled.set()

    agent._model_retry_sleep = press_stop
    sent = _script(agent, _tool_call(1), _transient(), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)
    assert result["status"] == "cancelled"
    assert len(sent) == 2


def test_stream_cut_after_partial_output_is_asked_again_cleanly(clean_registry):
    """The client never retries a stream that already produced text (that
    would duplicate it); the step is asked again and only the new reply
    counts — the cut-off text does not leak into the answer."""
    agent = _make_agent(streaming=True)
    shown = []
    agent.console.print_streaming_text = lambda text, end_of_stream=False: shown.append(
        (text, end_of_stream)
    )
    cut = attach_retry_state(
        LemonadeClientError("Streaming request failed: peer closed connection"),
        RetryState(
            attempts=1,
            last_reason="connection error",
            output_delivered=True,
            exhausted=True,
        ),
    )
    calls = []

    def _stream(*_, **__):
        calls.append(1)
        if len(calls) == 1:
            yield SimpleNamespace(text="The ledger bal", is_complete=False, stats=None)
            raise cut
        yield SimpleNamespace(text=_ANSWER, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat

    result = agent.process_query("Check the ledger", max_steps=5)
    assert len(calls) == 2
    assert _ANSWER in result["result"]
    assert "The ledger bal" not in result["result"].replace(_ANSWER, "")
    # The cut-off line was closed before the retry streamed anything new.
    cut_index = shown.index(("The ledger bal", False))
    assert shown[cut_index + 1] == ("", True)
    assert result["model_retries"]["step_retries"] == 1


# ── permanent failures and the pinned existing paths ─────────────────────


def test_permanent_error_still_ends_the_turn_promptly(agent):
    sent = _script(
        agent,
        _tool_call(1),
        LemonadeClientError("Error in chat completions (status 400): bad request"),
    )
    result = agent.process_query("Check the ledger", max_steps=10)
    assert len(sent) == 2
    assert agent.sleeps == []
    assert "couldn't finish this task" not in result["result"]
    assert result["model_retries"]["step_retries"] == 0
    assert result["status"] == "failed"


def test_untagged_connection_error_keeps_its_message(agent):
    sent = _script(agent, ConnectionError("Connection refused"))
    result = agent.process_query("Check the ledger", max_steps=10)
    assert len(sent) == 1
    assert agent.sleeps == []
    assert "having trouble reaching the language model" in result["result"]


def test_auth_error_is_not_retried(agent):
    sent = _script(
        agent,
        LemonadeAuthError(
            "Lemonade returned 401 Unauthorized for /chat/completions. "
            "Verify LEMONADE_API_KEY is correct (currently set)."
        ),
    )
    result = agent.process_query("Check the ledger", max_steps=10)
    assert len(sent) == 1
    assert agent.sleeps == []
    assert "couldn't finish this task" not in result["result"]


def _overflow(tagged: bool):
    error = RuntimeError(
        "Error in chat completions (status 400): the request exceeds the "
        "available context size, try increasing it"
    )
    if tagged:
        # Even carrying a transient record, overflow takes its own path first.
        attach_retry_state(
            error, RetryState(attempts=5, last_reason="HTTP 500", exhausted=True)
        )
    return error


@pytest.mark.parametrize("tagged", [False, True], ids=["plain", "tagged"])
def test_context_overflow_path_is_unchanged(agent, tagged):
    sent = _script(agent, _overflow(tagged), _overflow(tagged))
    result = agent.process_query("Check the ledger", max_steps=5)
    assert len(sent) == 2
    assert agent.sleeps == []
    assert strip_verification_scope(result["result"]) == (
        _CONTEXT_STILL_OVERFLOWING_MESSAGE
    )
    assert "llm_context_overflow_trimmed" in _types(agent)


@pytest.mark.parametrize("tagged", [False, True], ids=["plain", "tagged"])
def test_wrong_context_size_still_reraises_for_reload(agent, tagged):
    agent._is_loaded_ctx_too_small = lambda: True
    sent = _script(agent, _overflow(tagged))
    with pytest.raises(RuntimeError, match="exceeds the available context size"):
        agent.process_query("Check the ledger", max_steps=5)
    assert len(sent) == 1
    assert agent.sleeps == []
    assert "llm_wrong_ctx_loaded_reraise" in _types(agent)


# ── configuration ────────────────────────────────────────────────────────


def test_step_retry_defaults(monkeypatch):
    monkeypatch.delenv("GAIA_AGENT_MODEL_RETRIES", raising=False)
    monkeypatch.delenv("GAIA_AGENT_MODEL_RETRY_COOLDOWN", raising=False)
    assert model_step_retries() == DEFAULT_MODEL_STEP_RETRIES
    assert model_retry_cooldown() == DEFAULT_MODEL_RETRY_COOLDOWN


@pytest.mark.parametrize(
    "name, value",
    [
        ("GAIA_AGENT_MODEL_RETRIES", "-1"),
        ("GAIA_AGENT_MODEL_RETRIES", "lots"),
        ("GAIA_AGENT_MODEL_RETRY_COOLDOWN", "-5"),
        ("GAIA_AGENT_MODEL_RETRY_COOLDOWN", "a while"),
    ],
)
def test_step_retry_settings_reject_invalid_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        model_step_retries() if "RETRIES" in name else model_retry_cooldown()
