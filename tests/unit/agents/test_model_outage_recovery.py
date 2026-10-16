# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An agent turn survives a model endpoint that is down for a long time.

When the client has spent its own quick retries on a transient failure (a
429, a 5xx, a dropped connection), the agent keeps waiting — exponential
backoff with jitter, at most a minute per wait, never sooner than a
``Retry-After`` — until ``GAIA_AGENT_MODEL_OUTAGE_BUDGET`` runs out, then
asks again with the conversation untouched. Only the model call is
repeated; tools never re-run.

Every timing here runs on a fake clock: nothing really sleeps except the one
test that proves Stop is prompt in real time.
"""

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    DEFAULT_MODEL_OUTAGE_BUDGET,
    MODEL_OUTAGE_MAX_WAIT,
    Agent,
    model_outage_budget,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.verification import strip_verification_scope
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    LemonadeAuthError,
    LemonadeClientError,
)
from gaia.llm.retry import RetryState, attach_retry_state, tag_http_failure

_TOOL = "read_ledger_for_outage_test"
_ANSWER = "The ledger balances: 3 pages, total 42."
_ENDPOINT = "http://model-endpoint.test:8000/api/v1"
_TOOL_RUNS = []


class _LedgerAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def read_ledger_for_outage_test(page: int) -> dict:
            """Read one page of the ledger."""
            _TOOL_RUNS.append(page)
            return {"status": "success", "page": page, "entries": 3}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


class FakeClock:
    """Monotonic clock the agent reads and sleeps on; sleeping advances it."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_RUNS.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture(autouse=True)
def _outage_env(monkeypatch):
    for name in (
        "GAIA_AGENT_MODEL_OUTAGE_BUDGET",
        "GAIA_AGENT_MODEL_RETRIES",
        "GAIA_AGENT_MODEL_RETRY_COOLDOWN",
    ):
        monkeypatch.delenv(name, raising=False)


def _make_agent(streaming: bool, clock: FakeClock) -> _LedgerAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _LedgerAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = streaming
    agent._is_loaded_ctx_too_small = lambda: False
    agent._model_retry_clock = clock
    agent._model_retry_sleep = clock.sleep
    # Top of the jitter range, so every wait is exactly its ceiling.
    agent._model_retry_random = lambda: 1.0
    agent.waits = []
    real_cool_down = agent._cool_down_for_model

    def _record(seconds):
        agent.waits.append(seconds)
        return real_cool_down(seconds)

    agent._cool_down_for_model = _record
    agent.phases = []
    agent.console.report_phase = lambda phase, message, **_: agent.phases.append(
        (phase, message)
    )
    return agent


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture(params=[False, True], ids=["non-streaming", "streaming"])
def agent(request, clean_registry, clock):  # pylint: disable=unused-argument
    return _make_agent(request.param, clock)


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


def _transient(reason="HTTP 503", retry_after=None, attempts=5):
    """What the client raises once its own retries are spent on a transient
    failure, wrapped the way AgentSDK wraps a connection error."""
    status = int(reason.split()[1]) if reason.startswith("HTTP ") else None
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    error = tag_http_failure(
        LemonadeClientError(f"Chat completion failed ({reason})"),
        status,
        "",
        headers,
    )
    if status is None:
        setattr(error, "retry_reason", reason)
    attach_retry_state(
        error, RetryState(attempts=attempts, last_reason=reason, exhausted=True)
    )
    try:
        raise ConnectionError("Failed to connect to LLM server") from error
    except ConnectionError as wrapped:
        return wrapped


class _Outage:
    """A script entry that keeps failing until the fake clock passes *until*.
    Each failed call costs *cost* seconds — the client's own attempts."""

    def __init__(self, clock, seconds, cost=30.0, reasons=("HTTP 503",)):
        self.clock = clock
        self.seconds = seconds
        self.cost = cost
        self.reasons = reasons
        self.until = None
        self.failures = 0

    def fail_now(self) -> bool:
        if self.until is None:
            self.until = self.clock.now + self.seconds
        return self.clock.now < self.until

    def error(self):
        reason = self.reasons[self.failures % len(self.reasons)]
        self.failures += 1
        self.clock.now += self.cost
        return _transient(reason)


def _script(agent, *replies):
    """Entries are reply text, an exception to raise, or an ``_Outage`` that
    fails until it is over. Records a copy of the messages every call sent."""
    queue = list(replies)
    sent = []
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.llm_client = SimpleNamespace(base_url=_ENDPOINT)

    def _next(kwargs):
        sent.append(json.loads(json.dumps(kwargs["messages"], default=str)))
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        item = queue[0]
        if isinstance(item, _Outage):
            if item.fail_now():
                raise item.error()
            queue.pop(0)
            item = queue[0]
        queue.pop(0)
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


def _retry_lines(agent):
    return [message for phase, message in agent.phases if phase == "model_retry"]


# ── recovery ─────────────────────────────────────────────────────────────


def test_429_then_success_sends_exactly_one_request_after_the_wait(agent):
    sent = _script(agent, _tool_call(1), _transient("HTTP 429"), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)

    assert _ANSWER in result["result"]
    assert len(sent) == 3
    assert sent[2] == sent[1]
    # First wait: the backoff floor of a few seconds.
    assert agent.waits == [5.0]
    assert result["status"] != "failed"
    assert result["model_unreachable"] is False
    assert result["model_retries"]["step_retries"] == 1
    assert result["model_retries"]["waited_seconds"] == pytest.approx(5.0)
    assert _TOOL_RUNS == [1]


@pytest.mark.parametrize(
    "retry_after, wait",
    [(45, 45.0), (1, 5.0), (3600, MODEL_OUTAGE_MAX_WAIT)],
    ids=["honoured", "never-sooner-than-backoff", "capped"],
)
def test_retry_after_sets_the_wait(agent, retry_after, wait):
    _script(agent, _transient("HTTP 429", retry_after=retry_after), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=5)
    assert _ANSWER in result["result"]
    assert agent.waits == [wait]


def test_a_retry_after_under_the_client_wrapper_is_still_found(agent):
    """AgentSDK re-raises as ConnectionError; the header lives on the cause."""
    error = _transient("HTTP 503", retry_after=20)
    assert error.__cause__ is not None
    _script(agent, error, _ANSWER)
    agent.process_query("Check the ledger", max_steps=5)
    assert agent.waits == [20.0]


def test_backoff_doubles_from_a_few_seconds_to_the_cap(agent):
    _script(agent, *[_transient() for _ in range(7)], _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=5)
    assert _ANSWER in result["result"]
    assert agent.waits == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0]


def test_jitter_spreads_each_wait_between_half_and_all_of_its_ceiling(
    clean_registry, clock
):
    agent = _make_agent(False, clock)
    agent._model_retry_random = lambda: 0.0
    _script(agent, _transient(), _transient(), _ANSWER)
    agent.process_query("Check the ledger", max_steps=5)
    assert agent.waits == [2.5, 5.0]


def _twenty_minute_outage_run(streaming, clock):
    agent = _make_agent(streaming, clock)
    outage = _Outage(clock, seconds=20 * 60, cost=30.0)
    sent = _script(agent, _tool_call(1), _tool_call(2), outage, _tool_call(3), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)
    return agent, result, sent, outage


def _no_outage_run(streaming, clock):
    agent = _make_agent(streaming, clock)
    sent = _script(agent, _tool_call(1), _tool_call(2), _tool_call(3), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)
    return agent, result, sent


def _comparable(conversation):
    """The conversation without per-run timing."""
    drop = {"duration", "elapsed", "seconds", "timestamp", "latency_ms", "timing"}

    def scrub(value):
        if isinstance(value, dict):
            return {
                k: scrub(v)
                for k, v in value.items()
                if k not in drop and not k.endswith("_seconds")
            }
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return scrub(json.loads(json.dumps(conversation, default=str)))


@pytest.mark.parametrize("streaming", [False, True], ids=["non-streaming", "streaming"])
def test_twenty_minute_outage_mid_tool_loop_is_invisible_in_the_result(
    clean_registry, streaming
):
    """The user's acceptance test: a 20-minute outage between two tool steps.
    Apart from status lines, the turn is identical to one with no outage."""
    clean_agent, clean, clean_sent = _no_outage_run(streaming, FakeClock())
    clean_tools = list(_TOOL_RUNS)
    _TOOL_RUNS.clear()

    clock = FakeClock()
    started = clock.now
    agent, result, sent, outage = _twenty_minute_outage_run(streaming, clock)

    assert outage.failures > 10
    assert clock.now - started >= 20 * 60
    assert result["result"] == clean["result"]
    assert result["status"] == clean["status"]
    assert _comparable(result["conversation"]) == _comparable(clean["conversation"])
    # Each tool ran exactly once, in the same order.
    assert _TOOL_RUNS == clean_tools == [1, 2, 3]
    # Every retry sent byte-for-byte what the first failed attempt sent, and
    # the successful requests match the clean run's.
    failed = sent[2 : 2 + outage.failures + 1]
    assert all(request == failed[0] for request in failed)
    assert sent[:2] + sent[2 + outage.failures :] == clean_sent
    assert result["model_unreachable"] is False
    assert result["model_retries"]["step_retries"] == outage.failures
    assert len(_retry_lines(agent)) == outage.failures
    assert not _retry_lines(clean_agent)


def test_flapping_endpoint_alternating_503_and_429_recovers(agent, clock):
    outage = _Outage(
        clock, seconds=15 * 60, cost=10.0, reasons=("HTTP 503", "HTTP 429")
    )
    _script(agent, _tool_call(1), outage, _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)
    assert _ANSWER in result["result"]
    assert result["model_unreachable"] is False
    lines = _retry_lines(agent)
    assert any("HTTP 503" in line for line in lines)
    assert any("HTTP 429" in line for line in lines)
    assert _TOOL_RUNS == [1]


def test_a_burst_of_429s_never_waits_longer_than_the_cap(agent, clock):
    outage = _Outage(clock, seconds=50 * 60, cost=0.0, reasons=("HTTP 429",))
    _script(agent, outage, _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=5)
    assert _ANSWER in result["result"]
    assert len(agent.waits) > 40
    assert max(agent.waits) <= MODEL_OUTAGE_MAX_WAIT
    assert all(wait > 0 for wait in agent.waits)


def test_retry_after_zero_cannot_turn_the_wait_into_a_hot_loop(agent):
    _script(agent, *[_transient("HTTP 429", retry_after=0) for _ in range(3)], _ANSWER)
    agent.process_query("Check the ledger", max_steps=5)
    assert agent.waits == [5.0, 10.0, 20.0]


def test_the_backoff_survives_thousands_of_waits_without_overflow(agent):
    assert agent._outage_wait(5000, None) == MODEL_OUTAGE_MAX_WAIT


# ── what is never retried ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error",
    [
        LemonadeAuthError(
            "Lemonade returned 401 Unauthorized. Check LEMONADE_API_KEY."
        ),
        LemonadeClientError("Error in chat completions (status 400): bad request"),
        tag_http_failure(LemonadeClientError("rejected (HTTP 403)"), 403),
        tag_http_failure(LemonadeClientError("not found (HTTP 404)"), 404),
        tag_http_failure(LemonadeClientError("too large (HTTP 413)"), 413),
        tag_http_failure(LemonadeClientError("invalid (HTTP 422)"), 422),
    ],
    ids=["401", "400", "403", "404", "413", "422"],
)
def test_a_permanent_error_fails_at_once_without_waiting(agent, error):
    sent = _script(agent, _tool_call(1), error)
    result = agent.process_query("Check the ledger", max_steps=10)
    assert len(sent) == 2
    assert agent.waits == []
    assert _retry_lines(agent) == []
    assert result["model_retries"]["step_retries"] == 0
    assert result["model_retries"]["waited_seconds"] == 0


# ── giving up ────────────────────────────────────────────────────────────


def test_budget_exhaustion_ends_with_an_actionable_answer(agent, clock, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_MODEL_OUTAGE_BUDGET", "600")
    started = clock.now
    outage = _Outage(clock, seconds=10**9, cost=20.0)
    _script(agent, _tool_call(1), _tool_call(2), outage)
    result = agent.process_query("Check the ledger", max_steps=10)

    text = strip_verification_scope(result["result"])
    assert text.startswith(
        f"I stopped at step 3: the model endpoint at {_ENDPOINT} stayed "
        "unavailable for 10 min (5 attempts failed, the last with HTTP 503)."
    )
    assert f"The 2 tool calls already made ({_TOOL} 2x)" in text
    assert "Check that the endpoint is up and its API key is valid" in text
    assert "GAIA_AGENT_MODEL_OUTAGE_BUDGET (now 600 seconds)" in text
    assert 'say "continue"' in text
    # Waited the budget, and not a wait beyond it.
    assert 600 <= clock.now - started <= 600 + 2 * 20
    assert sum(agent.waits) <= 600
    assert result["status"] == "failed"
    assert result["model_unreachable"] is True
    entry = [e for e in agent.error_history if e["type"] == "llm_unreachable"][-1]
    assert entry["endpoint"] == _ENDPOINT
    assert entry["last_error"] == "HTTP 503"
    assert entry["budget_seconds"] == 600
    assert entry["waited_seconds"] >= 600
    assert _TOOL_RUNS == [1, 2]


def test_budget_zero_means_no_waiting_beyond_the_client(agent, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_MODEL_OUTAGE_BUDGET", "0")
    sent = _script(agent, _tool_call(1), _transient())
    result = agent.process_query("Check the ledger", max_steps=10)
    assert len(sent) == 2
    assert agent.waits == []
    text = strip_verification_scope(result["result"])
    assert text.startswith(
        f"I stopped at step 2: the model endpoint at {_ENDPOINT} stayed "
        "unavailable (5 attempts failed, the last with HTTP 503)."
    )
    assert "GAIA_AGENT_MODEL_OUTAGE_BUDGET is 0" in text
    assert result["status"] == "failed"


def test_unreachable_before_any_tool_says_nothing_ran(agent, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_MODEL_OUTAGE_BUDGET", "0")
    _script(agent, _transient())
    result = agent.process_query("Check the ledger", max_steps=10)
    assert "No tool had run yet" in result["result"]
    assert "send your request again" in result["result"]


# ── visible, and cancellable ─────────────────────────────────────────────


def test_each_wait_is_a_status_line_saying_why_how_long_and_how_far(agent, clock):
    _script(
        agent, _transient("HTTP 429", retry_after=45), _transient("HTTP 503"), _ANSWER
    )
    agent.process_query("Check the ledger", max_steps=5)
    assert _retry_lines(agent) == [
        "The model service is busy (HTTP 429) — retrying in 45s; waited 0s of "
        "60 min. The work so far is kept.",
        "The model service is busy (HTTP 503) — retrying in 10s; waited 45s "
        "of 60 min. The work so far is kept.",
    ]


def test_status_line_wording_for_a_dropped_connection(agent):
    _script(agent, _transient("connection error"), _ANSWER)
    agent.process_query("Check the ledger", max_steps=5)
    assert _retry_lines(agent) == [
        "The connection to the model service dropped — retrying in 5s; waited "
        "0s of 60 min. The work so far is kept."
    ]


def test_the_agent_reports_it_is_waiting_on_the_endpoint_only_while_it_is(agent, clock):
    seen = []
    real_sleep = agent._model_retry_sleep

    def _sleep(seconds):
        seen.append(agent.awaiting_model_endpoint())
        real_sleep(seconds)

    agent._model_retry_sleep = _sleep
    assert agent.awaiting_model_endpoint() is False
    _script(agent, _transient(), _ANSWER)
    agent.process_query("Check the ledger", max_steps=5)
    assert seen and all(seen)
    assert agent.awaiting_model_endpoint() is False


def test_stop_during_a_wait_cancels_the_turn(agent):
    agent.console.cancelled = threading.Event()
    slices = []

    def press_stop(seconds):
        slices.append(seconds)
        agent.console.cancelled.set()

    agent._model_retry_sleep = press_stop
    sent = _script(agent, _tool_call(1), _transient(retry_after=60), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)
    assert result["status"] == "cancelled"
    assert len(sent) == 2
    assert slices == [0.5]
    assert agent.awaiting_model_endpoint() is False


def test_a_cancel_token_during_a_wait_cancels_the_turn(agent):
    agent._cancel_event = threading.Event()
    slices = []

    def cancel(seconds):
        slices.append(seconds)
        agent._cancel_event.set()

    agent._model_retry_sleep = cancel
    sent = _script(agent, _tool_call(1), _transient(retry_after=60), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=10)
    assert len(sent) == 2
    assert slices == [0.5]
    assert _ANSWER not in (result.get("result") or "")


def test_stop_ends_a_real_one_minute_wait_within_a_second(clean_registry):
    """Real time, real sleep: the one test here that is not on a fake clock."""
    agent = _make_agent(False, FakeClock())
    agent._model_retry_clock = time.monotonic
    agent._model_retry_sleep = time.sleep
    agent.console.cancelled = threading.Event()
    _script(agent, _transient(retry_after=60), _ANSWER)

    def press_stop():
        deadline = time.monotonic() + 10
        while not agent.awaiting_model_endpoint() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)
        pressed.append(time.monotonic())
        agent.console.cancelled.set()

    pressed = []
    stopper = threading.Thread(target=press_stop, daemon=True)
    stopper.start()
    result = agent.process_query("Check the ledger", max_steps=5)
    returned = time.monotonic()
    stopper.join(timeout=5)

    assert result["status"] == "cancelled"
    assert pressed and returned - pressed[0] < 1.0


# ── streaming: no duplicated text ────────────────────────────────────────


def test_a_reply_cut_mid_stream_is_discarded_and_said_so(clean_registry, clock):
    agent = _make_agent(True, clock)
    shown = []
    agent.console.print_streaming_text = lambda text, end_of_stream=False: shown.append(
        (text, end_of_stream)
    )
    cut = attach_retry_state(
        LemonadeClientError("Streaming request failed: peer closed connection"),
        RetryState(
            attempts=1,
            last_reason="connection dropped",
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
    chat.llm_client = SimpleNamespace(base_url=_ENDPOINT)
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat

    result = agent.process_query("Check the ledger", max_steps=5)
    assert len(calls) == 2
    assert _ANSWER in result["result"]
    assert "The ledger bal" not in result["result"].replace(_ANSWER, "")
    # The cut-off line was closed before anything new streamed, and the
    # status line says the fragment is thrown away.
    cut_index = shown.index(("The ledger bal", False))
    assert shown[cut_index + 1] == ("", True)
    (line,) = _retry_lines(agent)
    assert "The cut-off reply above is discarded" in line


def test_on_the_agent_ui_wire_the_answer_carries_the_new_reply_once(
    clean_registry, clock
):
    from gaia.ui.sse_handler import SSEOutputHandler

    agent = _make_agent(True, clock)
    handler = SSEOutputHandler()
    agent.console = handler
    calls = []

    def _stream(*_, **__):
        calls.append(1)
        if len(calls) == 1:
            yield SimpleNamespace(text="The ledger bal", is_complete=False, stats=None)
            raise attach_retry_state(
                LemonadeClientError("Streaming request failed"),
                RetryState(
                    attempts=1,
                    last_reason="connection dropped",
                    output_delivered=True,
                    exhausted=True,
                ),
            )
        yield SimpleNamespace(text=_ANSWER, is_complete=False, stats=None)
        yield SimpleNamespace(text="", is_complete=True, stats={})

    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.llm_client = SimpleNamespace(base_url=_ENDPOINT)
    chat.send_messages_stream = MagicMock(side_effect=_stream)
    agent.chat = chat
    agent.process_query("Check the ledger", max_steps=5)

    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    answers = [e for e in events if e and e.get("type") == "answer"]
    assert len(answers) == 1
    assert answers[0]["content"].count("The ledger bal") == 1
    retry = [e for e in events if e and e.get("phase") == "model_retry"]
    assert retry and "discarded" in retry[0]["message"]


# ── configuration ────────────────────────────────────────────────────────


def test_the_default_budget_is_an_hour():
    assert DEFAULT_MODEL_OUTAGE_BUDGET == 3600
    assert model_outage_budget() == 3600


@pytest.mark.parametrize("value", ["-1", "an hour", "nan", "inf"])
def test_an_invalid_budget_fails_loudly(monkeypatch, value):
    monkeypatch.setenv("GAIA_AGENT_MODEL_OUTAGE_BUDGET", value)
    with pytest.raises(ValueError, match="GAIA_AGENT_MODEL_OUTAGE_BUDGET"):
        model_outage_budget()


@pytest.mark.parametrize(
    "name", ["GAIA_AGENT_MODEL_RETRIES", "GAIA_AGENT_MODEL_RETRY_COOLDOWN"]
)
def test_a_retired_setting_is_rejected_not_ignored(monkeypatch, name):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match="GAIA_AGENT_MODEL_OUTAGE_BUDGET"):
        model_outage_budget()


def test_a_bad_budget_fails_the_turn_at_its_start_not_mid_outage(agent, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_MODEL_OUTAGE_BUDGET", "soon")
    sent = _script(agent, _ANSWER)
    with pytest.raises(ValueError, match="GAIA_AGENT_MODEL_OUTAGE_BUDGET"):
        agent.process_query("Check the ledger", max_steps=5)
    assert sent == []


# ── a local server that refuses is started, not waited on ────────────────

_LOCAL = "http://127.0.0.1:13305/api/v1"


@pytest.fixture
def lemonade_manager(monkeypatch):
    """The auto-start path, scripted: each call pops the next readiness."""
    from gaia.llm import lemonade_launcher
    from gaia.llm.lemonade_manager import LemonadeManager

    calls = SimpleNamespace(ensure=[], resets=0, ready=[True])

    def ensure_ready(**kwargs):
        calls.ensure.append(kwargs)
        return calls.ready.pop(0)

    def reset():
        calls.resets += 1

    monkeypatch.setattr(LemonadeManager, "ensure_ready", ensure_ready)
    monkeypatch.setattr(LemonadeManager, "reset", reset)
    monkeypatch.setattr(
        lemonade_launcher,
        "describe_start_hint",
        lambda *a, **k: SimpleNamespace(
            instruction="Start Lemonade Server from its tray icon", command=None
        ),
    )
    return calls


def _local(agent):
    agent.chat.llm_client = SimpleNamespace(base_url=_LOCAL)


def test_local_refused_starts_the_server_and_carries_on(agent, lemonade_manager):
    sent = _script(agent, _tool_call(1), _transient("connection error"), _ANSWER)
    _local(agent)
    result = agent.process_query("Check the ledger", max_steps=10)

    assert _ANSWER in result["result"]
    assert lemonade_manager.resets == 1
    assert [c["base_url"] for c in lemonade_manager.ensure] == [_LOCAL]
    # Started, not waited for: no backoff at all.
    assert agent.waits == []
    assert sent[2] == sent[1]
    assert _TOOL_RUNS == [1]
    assert any("starting it again" in line for line in _retry_lines(agent))
    assert result["model_unreachable"] is False


def test_local_refused_and_the_start_fails_fails_fast(agent, lemonade_manager, clock):
    lemonade_manager.ready = [False]
    started = clock.now
    sent = _script(agent, _tool_call(1), _transient("connection error"))
    _local(agent)
    result = agent.process_query("Check the ledger", max_steps=10)

    text = strip_verification_scope(result["result"])
    assert text.startswith(
        f"I stopped at step 2: the local model server at {_LOCAL} is not "
        "answering (5 attempts failed, the last with connection error), and it "
        "was still down after GAIA tried to start it."
    )
    assert "Start Lemonade Server from its tray icon." in text
    assert 'say "continue"' in text
    assert len(sent) == 2
    assert agent.waits == []
    assert clock.now == started
    assert result["status"] == "failed"
    assert result["model_unreachable"] is True


def test_local_server_that_refuses_again_after_a_start_is_not_restarted_forever(
    agent, lemonade_manager
):
    sent = _script(
        agent,
        _transient("connection error"),
        _transient("connection error"),
        _ANSWER,
    )
    _local(agent)
    result = agent.process_query("Check the ledger", max_steps=5)
    assert len(lemonade_manager.ensure) == 1
    assert len(sent) == 2
    assert agent.waits == []
    assert result["model_unreachable"] is True


def test_remote_refused_waits_out_the_outage(agent, lemonade_manager):
    _script(agent, _transient("connection error"), _ANSWER)
    result = agent.process_query("Check the ledger", max_steps=5)
    assert _ANSWER in result["result"]
    assert agent.waits == [5.0]
    assert lemonade_manager.ensure == []


def test_a_busy_local_server_is_waited_for_not_restarted(agent, lemonade_manager):
    """A 503 means the server is up and busy: no restart, the normal wait."""
    _script(agent, _transient("HTTP 503"), _ANSWER)
    _local(agent)
    result = agent.process_query("Check the ledger", max_steps=5)
    assert _ANSWER in result["result"]
    assert agent.waits == [5.0]
    assert lemonade_manager.ensure == []


@pytest.mark.parametrize(
    "url, local",
    [
        ("http://localhost:13305/api/v1", True),
        ("http://127.0.0.2:8000", True),
        ("http://[::1]:13305/api/v1", True),
        ("http://localhost.example.com/api/v1", False),
        ("https://models.example.com/v1", False),
        ("http://10.0.0.5:13305", False),
        (None, False),
        ("not a url", False),
    ],
)
def test_which_endpoints_count_as_this_machine(url, local):
    from gaia.agents.base.agent import _is_local_url

    assert _is_local_url(url) is local


def test_give_up_names_the_cloud_provider_behind_lemonade(agent, monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_MODEL_OUTAGE_BUDGET", "0")
    agent.model_id = "fireworks.some-hosted-model"
    _script(agent, _transient("HTTP 503"))
    result = agent.process_query("Check the ledger", max_steps=5)
    assert (
        f"the model endpoint at {_ENDPOINT} (cloud provider fireworks) stayed "
        "unavailable"
    ) in result["result"]
