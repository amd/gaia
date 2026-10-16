# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A transient model-endpoint failure mid-run must not throw the run away.

The client retries a transient failure itself (``gaia.llm.retry``); when it
gives up, the error carries a retry record. The agent loop then waits and asks
again with the conversation untouched until its outage budget runs out, and
only then ends the turn — with an answer that says plainly the model became
unreachable, never one that could pass for a result. The outage wait itself is
pinned in ``test_model_outage_recovery.py``.

Everything else keeps its existing path, pinned here: a permanent error ends
the turn at once, context overflow trims and retries once, a model loaded with
too small a window re-raises for reload, and an auth error is not retried.
"""

import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import _CONTEXT_STILL_OVERFLOWING_MESSAGE, Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.verification import strip_verification_scope
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    LemonadeAuthError,
    LemonadeClientError,
)
from gaia.llm.retry import (
    ModelCallRetrier,
    RetryNotice,
    RetryPolicy,
    RetryState,
    attach_retry_state,
    tag_http_failure,
)

_TOOL = "read_ledger_for_retry_test"
_ANSWER = "The ledger balances: 3 entries, total 42."
#: Pages the tool actually read, in order — proves a step retry never re-runs one.
_TOOL_RUNS = []


class _LedgerAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def read_ledger_for_retry_test(page: int) -> dict:
            """Read one page of the ledger."""
            _TOOL_RUNS.append(page)
            return {"status": "success", "page": page, "entries": 3}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_RUNS.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture(autouse=True)
def _retry_env(monkeypatch):
    monkeypatch.delenv("GAIA_AGENT_MODEL_OUTAGE_BUDGET", raising=False)


def _make_agent(streaming: bool) -> _LedgerAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _LedgerAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = streaming
    agent._is_loaded_ctx_too_small = lambda: False
    agent.sleeps = []
    # A fake clock the outage budget is measured on; sleeping advances it.
    agent.clock = [0.0]

    def _sleep(seconds):
        agent.sleeps.append(seconds)
        agent.clock[0] += seconds

    agent._model_retry_sleep = _sleep
    agent._model_retry_clock = lambda: agent.clock[0]
    # Top of the jitter range: the first outage wait is exactly 5 s.
    agent._model_retry_random = lambda: 1.0
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
    assert "I stopped at step" not in result["result"]
    # Progress was kept: the retry sent exactly what the failed call sent,
    # both tool results included.
    assert len(sent) == 4
    assert sent[3] == sent[2]
    assert sum(1 for m in sent[3] if m.get("role") == "tool") == 2
    # One wait (5 s, sliced so Stop stays responsive), no failure recorded.
    assert sum(agent.sleeps) == pytest.approx(5.0)
    assert result["model_retries"]["step_retries"] == 1
    assert result["model_retries"]["request_retries"] == 4
    assert result["model_unreachable"] is False
    assert "llm_unreachable" not in _types(agent)
    assert result["status"] != "failed"


def test_wait_doubles_per_step_retry(agent):
    _script(agent, _tool_call(1), _transient(), _transient(), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)
    assert _ANSWER in result["result"]
    assert sum(agent.sleeps) == pytest.approx(5.0 + 10.0)
    assert result["model_retries"]["step_retries"] == 2


@pytest.mark.parametrize("reason", ["read timeout", "upstream timeout"])
def test_timeout_is_not_retried_at_the_step_level(agent, reason):
    """The request already spent its whole time budget; #1030's handling
    applies instead of another long wait."""
    sent = _script(agent, _tool_call(1), _transient(reason=reason, attempts=1))
    result = agent.process_query("Check the ledger", max_steps=10)
    assert len(sent) == 2
    assert agent.sleeps == []
    assert "I stopped at step" not in result["result"]


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


def test_step_retry_never_reruns_a_tool(agent):
    """Only the model call is repeated: each tool ran exactly once."""
    _script(agent, _tool_call(1), _transient(), _tool_call(2), _transient(), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)
    assert _ANSWER in result["result"]
    assert _TOOL_RUNS == [1, 2]
    assert result["model_retries"]["step_retries"] == 2


def _gateway_429(retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after is not None else None
    return tag_http_failure(
        LemonadeClientError("Cloud rate limit reached (HTTP 429)."),
        429,
        '{"error":{"message":"cloud (amd) request failed","status_code":429}}',
        headers,
    )


def test_client_retries_show_one_status_line_each(agent):
    """A client-level retry inside the model call surfaces as a short status
    line, not silence: the turn's listener reaches the retrier."""
    shown = []
    agent.console.print_warning = shown.append
    waits = []
    failures = [_gateway_429("8"), _gateway_429()]

    def flaky():
        if failures:
            raise failures.pop(0)
        return _ANSWER

    retrier = ModelCallRetrier(
        RetryPolicy(max_attempts=5, base_delay=2, max_delay=30, deadline=120),
        sleep=waits.append,
        clock=lambda: 0.0,
    )
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(
        side_effect=lambda *_, **__: SimpleNamespace(text=retrier.call(flaky), stats={})
    )

    def _stream(*_, **__):
        yield SimpleNamespace(text=retrier.call(flaky), is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat

    result = agent.process_query("Check the ledger", max_steps=5)
    assert _ANSWER in result["result"]
    # Retry-After honoured exactly; the second wait is jittered within 0..4s.
    assert waits[0] == 8.0
    assert 0.0 <= waits[1] <= 4.0
    assert shown[0] == "The model service is busy — retrying in 8s (1/4)"
    assert shown[1].startswith("The model service is busy — retrying ")
    assert shown[1].endswith("(2/4)")


@pytest.mark.parametrize(
    "notice, line",
    [
        (
            RetryNotice("HTTP 429", 8.4, 2, 4),
            "The model service is busy — retrying in 8s (2/4)",
        ),
        (
            RetryNotice("connection error", 0.3, 1, 4),
            "The connection to the model service dropped — retrying now (1/4)",
        ),
        (
            RetryNotice("HTTP 502", 3.0, 3, 4),
            "The model service had a problem (HTTP 502) — retrying in 3s (3/4)",
        ),
    ],
)
def test_retry_status_line_wording(agent, notice, line):
    shown = []
    agent.console.print_warning = shown.append
    agent._announce_model_retry(notice)
    assert shown == [line]


def _tui_turn(agent):
    """One turn through the stdio transport the TUI drives, as the canonical
    events it writes to the pipe."""
    run_turn = pytest.importorskip("gaia_agent.stdio").run_turn

    # History sizing needs a real model's context window; not under test here.
    agent.conversation_history = None
    out = io.StringIO()
    run_turn(agent, "Check the ledger", out)
    return [json.loads(line) for line in out.getvalue().splitlines() if line]


def test_tui_turn_survives_a_step_retry_and_ends_with_the_answer(agent):
    """On the TUI's wire a step retry is a status line, never a terminal
    error that would cut the turn off."""
    _script(agent, _tool_call(1), _transient(reason="HTTP 502"), _ANSWER)
    events = _tui_turn(agent)
    types = [e["type"] for e in events]
    assert "error" not in types
    assert types[-1] == "final"
    assert _ANSWER in events[-1]["answer"]
    assert any(
        e["type"] == "status"
        and "(HTTP 502) — retrying in 5s; waited 0s of 60 min" in e.get("message", "")
        for e in events
    )
    assert _TOOL_RUNS == [1]


def test_tui_turn_gives_up_with_the_answer_not_an_error(agent, monkeypatch):
    """When the outage budget runs out the user gets the plain answer as the
    turn's final event; an error event would end the turn without it."""
    monkeypatch.setenv("GAIA_AGENT_MODEL_OUTAGE_BUDGET", "0")
    _script(agent, _tool_call(1), _transient(reason="HTTP 429"))
    events = _tui_turn(agent)
    types = [e["type"] for e in events]
    assert "error" not in types
    assert types[-1] == "final"
    assert events[-1]["answer"].startswith(
        "I stopped at step 2: the model endpoint stayed unavailable"
    )
    # Printed by the agent itself (so the CLI shows it too), not recovered
    # from the return value by the transport's fallback.
    assert "usage" in events[-1]
    assert _TOOL_RUNS == [1]


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
    assert "I stopped at step" not in result["result"]
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
    assert "I stopped at step" not in result["result"]


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


# ── stalled stream (#5126) as the Agent UI shows it ──────────────────────


def _stalled():
    """What the client raises once a hosted stream stayed silent on every try."""
    from gaia.llm.retry import StreamStalledError

    stall = StreamStalledError(limit=120, remote=True, before_output=True)
    try:
        try:
            raise stall
        except StreamStalledError as e:
            raise attach_retry_state(
                LemonadeClientError(stall.describe(2)),
                RetryState(attempts=2, last_reason="stream stalled", exhausted=True),
            ) from e
    except LemonadeClientError as error:
        return error


def test_stalled_model_ends_with_the_stall_message_not_a_generic_one(agent):
    _script(agent, _stalled())
    result = agent.process_query("Which model are you?", max_steps=5)

    text = strip_verification_scope(result["result"])
    assert text.startswith("The hosted model accepted the request but sent nothing")
    assert "pick another model" in text
    assert "temporary issue" not in text
    # A stall already spent its retries in the client; no step retry on top.
    assert agent.sleeps == []


def test_retry_notice_also_becomes_the_live_phase(agent):
    """The Agent UI header shows phases; a retry must replace "Reading your request"."""
    warnings, phases = [], []
    agent.console.print_warning = warnings.append
    agent.console.report_phase = lambda phase, message, **_: phases.append(
        (phase, message)
    )
    agent._announce_model_retry(RetryNotice("stream stalled", 0.2, 1, 1))
    assert warnings == ["The model stopped sending — retrying now (1/1)"]
    assert phases == [("model_retry", warnings[0])]
