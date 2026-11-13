# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``final`` carries what fills the model's context (contract 2.21)."""

from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

from gaia.ui import turn_insights  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _Agent:
    def __init__(self, result=None, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.model_id = "Qwen3.6-35B-A3B-GGUF"
        self.result = result if result is not None else {"answer": "Paris."}

    def process_query(self, query, max_steps=None):
        return self.result

    def close(self):
        pass


@pytest.fixture
def built(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    state = {"result": None}

    def build(**kw):
        return _Agent(result=state["result"], **kw)

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL), state
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


@pytest.fixture
def measured(monkeypatch):
    """Record what the panel measurement was asked to count."""
    calls = []

    def fake(agent, turns, model_id, deadline=None):
        calls.append({"turns": turns, "model_id": model_id})
        return {"window": 65536, "used": 1234, "model": model_id}

    monkeypatch.setattr(turn_insights, "panel_context", fake)
    return calls


def _events(client, **fields):
    body = {"query": "Capital of France?", "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    r = client.post("/v1/gaia/query", json=body)
    assert r.status_code == 200, r.text
    return [
        json.loads(line[len("data: ") :])
        for line in r.text.splitlines()
        if line.startswith("data: ")
    ]


def _final(client, **fields):
    events = _events(client, **fields)
    finals = [e for e in events if e.get("type") in ("final", "error")]
    assert len(finals) == 1, events
    return finals[0]


def test_the_contract_advertises_final_context():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 21)


def test_the_contract_advertises_live_context():
    """Contract 2.28 streams the measurement before the answer, not only on final."""
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 28)


def test_a_live_measurement_reaches_the_stream_before_the_answer(built):
    client, _ = built
    earlier = [
        [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
    ]

    events = _events(client, turns=earlier)

    types = [e["type"] for e in events]
    assert types.count("context_usage") == 1, events
    assert types.index("context_usage") < types.index("final")
    live = events[types.index("context_usage")]
    assert live["window"] > 0
    assert live["used"] > 0
    assert live["turns_in_view"] == 1


def test_final_carries_the_next_calls_context(built, measured):
    client, _ = built
    context = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]

    final = _final(client, context=context)

    assert final["type"] == "final"
    assert final["context"] == {
        "window": 65536,
        "used": 1234,
        "model": "Qwen3.6-35B-A3B-GGUF",
    }
    [call] = measured
    # The pushed turn, then this turn with its answer: what the next push carries.
    assert call["turns"] == [
        [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}],
        [
            {"role": "user", "content": "Capital of France?"},
            {"role": "assistant", "content": "Paris."},
        ],
    ]


def test_a_stopped_turn_sends_no_measurement(built, measured):
    client, state = built
    state["result"] = {"status": "cancelled"}

    final = _final(client)

    assert final["type"] == "final"
    assert "context" not in final and "context_error" not in final
    assert measured == []


def test_a_failed_measurement_says_why_and_keeps_the_answer(built, monkeypatch):
    client, _ = built

    def broken(agent, turns, model_id, deadline=None):
        raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(turn_insights, "panel_context", broken)

    final = _final(client)

    assert final["answer"] == "Paris."
    assert "context" not in final
    assert "tokenizer exploded" in final["context_error"]


def test_a_slow_tokenizer_never_holds_up_the_answer(built, monkeypatch):
    """A busy model server: the estimate stands in, labelled, within the budget."""
    import threading
    import time

    client, _ = built
    release = threading.Event()

    def slow_exact(agent, turns, model_id, estimate_reason=None, deadline=None):
        if estimate_reason is None:
            release.wait(10)  # the tokenizer is stuck behind another generation
            return {"used": 1, "tokenizer": {"exact": True}}
        return {"used": 2, "tokenizer": {"exact": False, "reason": estimate_reason}}

    monkeypatch.setattr(turn_insights, "panel_context", slow_exact)
    monkeypatch.setattr(server_mod, "_EXACT_COUNT_SECONDS", 0.2)
    started = time.monotonic()
    try:
        final = _final(client)
    finally:
        release.set()

    assert time.monotonic() - started < 3
    assert final["answer"] == "Paris."
    assert final["context"]["used"] == 2
    assert final["context"]["tokenizer"]["exact"] is False
    assert "estimates" in final["context"]["tokenizer"]["reason"]


def test_final_carries_the_turns_timings(built, measured):
    """The Agent UI's Under the hood panel measures a relayed turn from these."""
    client, state = built
    stats = {
        "type": "stats",
        "step": 1,
        "performance_stats": {"prompt_tokens": 1200, "completion_tokens": 40},
    }
    timing = {"llm_seconds": 2.5, "tool_seconds": 0.0, "llm_calls": 1}
    state["result"] = {
        "answer": "Paris.",
        "timing_summary": timing,
        "conversation": [
            {"role": "user", "content": "Capital of France?"},
            {"role": "system", "content": stats},
        ],
    }

    final = _final(client)

    assert final["performance"] == {
        "timing_summary": timing,
        "conversation": [{"role": "system", "content": stats}],
    }


def _timed_result():
    stats = {
        "type": "stats",
        "step": 1,
        "performance_stats": {"prompt_tokens": 900, "completion_tokens": 12},
    }
    return {
        "answer": "Paris.",
        "timing_summary": {"llm_seconds": 1.5, "tool_seconds": 0.0, "llm_calls": 1},
        "conversation": [{"role": "system", "content": stats}],
    }


def test_a_measurement_that_overruns_is_named_and_timings_still_ship(
    built, monkeypatch
):
    """A measurement that cannot finish says so; the answer keeps its timings."""
    import threading

    client, state = built
    state["result"] = _timed_result()
    release = threading.Event()

    def stuck(*args, **kwargs):
        release.wait(10)
        return {"used": 1}

    def answers_live(self, query, max_steps=None):
        # The real agent seals the record and prints ``final`` inside
        # process_query, before its measurement runs.
        record = [{"role": "assistant", "content": self.result["answer"]}]
        self.console.stage_final_model_messages(self.result["answer"], record)
        self.console.print_final_answer(self.result["answer"])
        return self.result

    monkeypatch.setattr(_Agent, "process_query", answers_live)
    monkeypatch.setattr(server_mod, "_measure_turn_context", stuck)
    monkeypatch.setattr(server_mod, "_CONTEXT_WAIT_SECONDS", 0.2)
    try:
        final = _final(client, turns=[])
    finally:
        release.set()

    assert final["answer"] == "Paris."
    assert "context" not in final
    assert "did not finish within" in final["context_error"]
    assert final["performance"]["timing_summary"]["llm_seconds"] == 1.5
    assert len(final["performance"]["conversation"]) == 1


def test_a_failed_turn_sends_no_measurement_even_with_a_sealed_record(
    built, measured, monkeypatch
):
    """A failure notice is not a turn the next push carries."""
    client, state = built
    notice = "The model server went away."
    state["result"] = {"answer": notice, "model_failed": True}

    def answers_failed(self, query, max_steps=None):
        self.console.mark_turn_failed()
        record = [{"role": "assistant", "content": notice}]
        self.console.stage_final_model_messages(notice, record)
        self.console.print_final_answer(notice)
        return self.result

    monkeypatch.setattr(_Agent, "process_query", answers_failed)

    final = _final(client)

    assert final["failed"] is True
    assert "context" not in final and "context_error" not in final
    assert measured == []


def test_a_slow_tail_cannot_take_the_record_or_measurement_with_it(
    built, measured, monkeypatch
):
    """rc18: a turn still storing memory past the wait lost its meter and record.

    The agent seals both before the answer, so ``final`` reports them even
    though the loop has not returned when the stream reaches it. Timings are
    the one thing that still needs the loop, and they are the only thing lost.
    """
    import threading

    client, state = built
    record = [
        {"role": "user", "content": "Capital of France?"},
        {"role": "assistant", "content": "Paris."},
    ]
    state["result"] = {"answer": "Paris.", "model_messages": record}
    storing = threading.Event()

    def answers_then_stores(self, query, max_steps=None):
        self.console.stage_final_model_messages("Paris.", record)
        self.console.print_final_answer("Paris.")
        storing.wait(0.6)  # the memory store outlives the wait for the loop
        return self.result

    monkeypatch.setattr(_Agent, "process_query", answers_then_stores)
    monkeypatch.setattr(server_mod, "_RUN_RESULT_WAIT_SECONDS", 0.1)
    try:
        final = _final(client, turns=[])
    finally:
        storing.set()

    assert final["answer"] == "Paris."
    assert final["model_messages"] == record
    assert final["context"] == {
        "window": 65536,
        "used": 1234,
        "model": "Qwen3.6-35B-A3B-GGUF",
    }
    assert "context_error" not in final
    [call] = measured
    assert call["turns"] == [record]
    assert "performance" not in final


def test_a_turns_final_still_waits_for_the_loops_timings(built, measured, monkeypatch):
    """The asymmetry is deliberate: a caller that sent ``turns`` is owed timings."""
    import threading

    client, state = built
    state["result"] = _timed_result()
    storing = threading.Event()

    def answers_then_stores(self, query, max_steps=None):
        self.console.stage_final_model_messages("Paris.", [])
        self.console.print_final_answer("Paris.")
        storing.wait(0.3)
        return self.result

    monkeypatch.setattr(_Agent, "process_query", answers_then_stores)
    monkeypatch.setattr(server_mod, "_RUN_RESULT_WAIT_SECONDS", 5.0)
    try:
        final = _final(client, turns=[])
    finally:
        storing.set()

    assert final["performance"]["timing_summary"]["llm_seconds"] == 1.5


def test_a_no_turns_final_does_not_wait_for_the_run_tail(built, measured, monkeypatch):
    """The TUI sends no ``turns``; its final must keep its earlier bound.

    Only a caller that sent ``turns`` is owed timings from the loop. Here a
    slow tail costs ``performance`` and nothing else, so the final is not held
    behind a wait whose old form was already at the bound this path had.
    """
    import threading

    client, state = built
    record = [{"role": "assistant", "content": "Paris."}]
    state["result"] = {"answer": "Paris.", "model_messages": record}
    storing = threading.Event()

    def answers_then_stores(self, query, max_steps=None):
        self.console.stage_final_model_messages("Paris.", record)
        self.console.print_final_answer("Paris.")
        storing.wait(0.6)
        return self.result

    monkeypatch.setattr(_Agent, "process_query", answers_then_stores)
    # A join would unblock when the tail finishes and attach its timings; the
    # assertion below pins that this request shape never waited for that.
    monkeypatch.setattr(server_mod, "_RUN_RESULT_WAIT_SECONDS", 5.0)
    try:
        final = _final(client)
    finally:
        storing.set()

    assert final["answer"] == "Paris."
    assert final["context"] == {
        "window": 65536,
        "used": 1234,
        "model": "Qwen3.6-35B-A3B-GGUF",
    }
    assert "model_messages" not in final
    assert "performance" not in final


def test_a_turn_that_timed_nothing_sends_no_performance(built, measured):
    client, state = built
    state["result"] = {"answer": "Paris.", "conversation": [{"role": "user"}]}

    final = _final(client)

    assert final["answer"] == "Paris."
    assert "performance" not in final


def test_performance_carries_only_the_stats_entries(built, measured):
    """User text and tool output stay in the agent; only the counts travel."""
    client, state = built
    result = _timed_result()
    result["conversation"] = [
        {"role": "user", "content": "secret question"},
        {"role": "assistant", "content": "secret answer"},
        "not-a-dict",
        *result["conversation"],
    ]
    state["result"] = result

    final = _final(client)

    sent = json.dumps(final["performance"])
    assert "secret" not in sent
    assert [e["content"]["type"] for e in final["performance"]["conversation"]] == [
        "stats"
    ]


def test_a_relayed_chat_is_measured_in_whole_turns_with_its_tool_results(
    built, measured, monkeypatch
):
    """The meter counts what the next turn carries, one entry per real turn.

    In v0.26.0-rc18 a text-tool model's recorded results were split into
    "turns" of their own, and the newest turn counted only its question and
    answer, so a 20 KB file read pushed history over budget with no warning.
    """
    from gaia.agents.base import history as history_mod

    monkeypatch.setattr(history_mod, "history_budget", lambda agent, query: 10**6)
    client, state = built
    earlier = [
        [
            {"role": "user", "content": "Sum 1..9 with Python."},
            {"role": "assistant", "content": '{"tool": "run_python"}'},
            {"role": "user", "content": "[Recorded tool result: run_python] 45"},
            {"role": "assistant", "content": "45"},
        ]
    ]
    newest = [
        {"role": "user", "content": "Capital of France?"},
        {"role": "assistant", "content": '{"tool": "read_file"}'},
        {"role": "user", "content": "[Recorded tool result: read_file] " + "x" * 20000},
        {"role": "assistant", "content": "Paris."},
    ]
    state["result"] = {"answer": "Paris.", "model_messages": newest}

    final = _final(client, context=[], turns=earlier)

    assert final["type"] == "final"
    [call] = measured
    assert call["turns"] == earlier + [newest]
