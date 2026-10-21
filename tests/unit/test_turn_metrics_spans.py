# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The turn record splits wall time into backend compute and everything else.

These pin what makes "non-model time" trustworthy: server timings come from
the request's own ``timings`` (never a stats endpoint), waits reported from the
client's request path land on the open call only, the recorder is per-thread so
a tool's or extraction's requests are not booked as the turn's, and the
post-answer spans reach the log as their own line.
"""

from __future__ import annotations

import json
import threading
import time

from gaia.agents.base import turn_metrics
from gaia.agents.base.turn_metrics import (
    POST_SCHEMA,
    RECENT_TURNS,
    TURN_LOG_ENV,
    TurnRecorder,
    format_summary,
    pre_turn_span,
)
from gaia.utils import perf_trace

TIMINGS = {
    "cache_n": 18413,
    "prompt_n": 45,
    "prompt_ms": 971.86,
    "predicted_n": 95,
    "predicted_ms": 2401.249,
    "predicted_per_second": 39.5,
}


def _recorder(tmp_path, **kwargs):
    return TurnRecorder(
        query="hello",
        agent_name="GaiaAgent",
        model_id="Gemma-4-E4B-it-GGUF",
        system_prompt="SYSTEM",
        tool_schemas=None,
        path=tmp_path / "turns.jsonl",
        **kwargs,
    )


def _one_call(recorder, *, timings=TIMINGS, waits=None):
    recorder.start_llm_call(1, "prompt")
    for key, seconds in (waits or {}).items():
        perf_trace.note_wait(key, seconds)
    perf_trace.note_server_timings(timings)
    recorder.end_llm_call({"input_tokens": 3, "output_tokens": 2})


def test_hooks_are_free_when_no_turn_is_recording():
    perf_trace.activate(None)
    perf_trace.note_wait("lock_wait_s", 1.0)
    perf_trace.note_server_timings(TIMINGS)
    with perf_trace.span("anything"):
        pass
    assert perf_trace.current() is None


def test_server_timings_give_compute_and_non_model_time(tmp_path):
    rec = _recorder(tmp_path)
    perf_trace.activate(rec)
    try:
        _one_call(rec, waits={"lease_wait_s": 0.25, "lock_wait_s": 0.5})
        _one_call(rec, waits={"lock_wait_s": 0.5})
        record = rec.finish(answer="hi", steps=2)
    finally:
        perf_trace.activate(None)

    call = record["llm_calls"][0]
    assert call["server"]["compute_s"] == round((971.86 + 2401.249) / 1000, 4)
    # The request's own timings win over the stats dict handed to end_llm_call.
    assert call["input_tokens"] == 45 + 18413
    assert call["output_tokens"] == 95
    assert call["cache_read_input_tokens"] == 18413
    totals = record["totals"]
    assert totals["server_compute_s"] == round(2 * 3.3731, 3)
    assert abs(totals["non_model_s"] - (record["total_s"] - 2 * 3.3731)) < 0.002
    assert totals["client_waits_s"] == {"lease_wait_s": 0.25, "lock_wait_s": 1.0}
    assert "server" in format_summary(record) and "non-model" in format_summary(record)


def test_no_compute_total_when_any_call_lacks_server_timings(tmp_path):
    """A partial sum would understate compute and overstate GAIA's share."""
    rec = _recorder(tmp_path)
    perf_trace.activate(rec)
    try:
        _one_call(rec)
        _one_call(rec, timings=None)
        record = rec.finish(answer="hi", steps=2)
    finally:
        perf_trace.activate(None)
    assert "server_compute_s" not in record["totals"]
    assert "non-model" not in format_summary(record)


def test_a_wait_outside_an_open_call_is_not_booked(tmp_path):
    rec = _recorder(tmp_path)
    perf_trace.activate(rec)
    try:
        perf_trace.note_wait("lock_wait_s", 9.0)  # e.g. a status probe between calls
        _one_call(rec)
        record = rec.finish(answer="", steps=1)
    finally:
        perf_trace.activate(None)
    assert record["totals"]["client_waits_s"] == {}


def test_another_threads_requests_are_not_the_turns(tmp_path):
    """Background extraction and tool bodies run on their own threads."""
    rec = _recorder(tmp_path)
    perf_trace.activate(rec)
    try:
        rec.start_llm_call(1, "prompt")

        def side_call():
            perf_trace.note_wait("lock_wait_s", 5.0)
            perf_trace.note_server_timings(TIMINGS)

        worker = threading.Thread(target=side_call)
        worker.start()
        worker.join()
        rec.end_llm_call({})
        record = rec.finish(answer="", steps=1)
    finally:
        perf_trace.activate(None)
    call = record["llm_calls"][0]
    assert "client" not in call and "server" not in call


def test_gaps_name_the_spans_that_filled_them(tmp_path):
    rec = _recorder(tmp_path)
    perf_trace.activate(rec)
    try:
        _one_call(rec)
        with perf_trace.span("context_evict"):
            time.sleep(0.02)
        rec.record_tool(1, "web_search", 0.01)
        _one_call(rec)
        record = rec.finish(answer="", steps=2)
    finally:
        perf_trace.activate(None)
    (gap,) = record["gaps"]
    assert gap["s"] >= 0.02
    assert gap["tools"] == ["web_search"]
    assert [s["name"] for s in gap["spans"]] == ["context_evict"]


def test_pre_turn_spans_backdate_the_turn(tmp_path, monkeypatch):
    monkeypatch.setenv(TURN_LOG_ENV, str(tmp_path / "turns.jsonl"))

    class Owner:
        _pre_turn_spans = None

    owner = Owner()
    with pre_turn_span(owner, "memory_context"):
        time.sleep(0.03)
    ((name, t0, t1),) = owner._pre_turn_spans
    rec = _recorder(tmp_path, started=t0)
    rec.add_span(name, t0, t1, {})
    record = rec.finish(answer="", steps=0)
    assert record["total_s"] >= 0.03
    assert record["spans"][0]["name"] == "memory_context"
    assert record["spans"][0]["start_s"] == 0.0


def test_pre_turn_span_is_a_no_op_when_recording_is_off(monkeypatch):
    monkeypatch.delenv(TURN_LOG_ENV, raising=False)

    class Owner:
        _pre_turn_spans = None

    owner = Owner()
    with pre_turn_span(owner, "memory_context"):
        pass
    assert owner._pre_turn_spans is None


def test_post_answer_spans_are_written_as_their_own_line(tmp_path):
    rec = _recorder(tmp_path)
    perf_trace.activate(rec)
    try:
        record = rec.finish(answer="hi", steps=1)
        with perf_trace.span("after_process_query"):
            pass
        post = rec.write_post()
    finally:
        perf_trace.activate(None)
    assert "after_process_query" not in [s["name"] for s in record["spans"]]
    lines = [json.loads(x) for x in (tmp_path / "turns.jsonl").read_text().splitlines()]
    assert [x["schema"] for x in lines] == [turn_metrics.SCHEMA, POST_SCHEMA]
    assert lines[1]["turn_id"] == record["turn_id"] == post["turn_id"]
    assert RECENT_TURNS[-1] is record


def test_no_post_line_when_nothing_ran_after_the_answer(tmp_path):
    rec = _recorder(tmp_path)
    rec.finish(answer="hi", steps=1)
    assert rec.write_post() is None
    assert len((tmp_path / "turns.jsonl").read_text().splitlines()) == 1


# ── wiring: the real provider and agent paths ───────────────────────────────


def test_a_streamed_calls_own_timings_reach_the_open_call(tmp_path):
    """The usage chunk's ``timings`` are the only per-request server figures."""
    from gaia.llm.providers.lemonade import LemonadeProvider

    provider = LemonadeProvider.__new__(LemonadeProvider)
    provider._last_ttft_seconds = None
    provider.tool_call_progress = None
    provider.reasoning_progress = None
    provider._last_model = "Gemma-4-E4B-it-GGUF"
    chunks = [
        {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {
            "choices": [],
            "usage": {"prompt_tokens": 18458, "completion_tokens": 95},
            "timings": TIMINGS,
        },
    ]
    rec = _recorder(tmp_path)
    perf_trace.activate(rec)
    try:
        rec.start_llm_call(1, "prompt")
        assert "".join(provider._handle_stream(iter(chunks), time.perf_counter()))
        rec.end_llm_call({})
    finally:
        perf_trace.activate(None)
    assert rec.llm_calls[0]["server"]["cache_n"] == 18413
    assert rec.llm_calls[0]["server"]["compute_s"] == round(
        (971.86 + 2401.249) / 1000, 4
    )


def test_a_nested_turn_hands_the_thread_back_to_the_outer_one(tmp_path):
    from types import SimpleNamespace

    from gaia.agents.base.agent import Agent

    outer, inner = _recorder(tmp_path), _recorder(tmp_path)
    agent = SimpleNamespace(_sealed_turn_recorder=inner, _outer_turn_recorder=outer)
    perf_trace.activate(inner)
    try:
        Agent._flush_post_turn_record(agent)
        assert perf_trace.current() is outer
    finally:
        perf_trace.activate(None)


def test_the_clients_lock_lease_and_probe_waits_land_on_the_open_call(
    tmp_path, monkeypatch
):
    """The real request path, not numbers fed to the recorder by hand."""
    from gaia.llm.lemonade_client import LemonadeClient
    from gaia.llm.slot_gate import gate_for

    client = LemonadeClient(base_url="http://127.0.0.1:9/api/v1", verbose=False)
    monkeypatch.setattr(client, "cloud_model_provider", lambda model: None)
    monkeypatch.setattr(client, "_is_cloud_model", lambda model: False)
    monkeypatch.setattr(
        client,
        "_ensure_model_loaded_locked",
        lambda model, force=False: time.sleep(0.05),
    )
    model = "perf-trace-wiring-model"
    held, release = threading.Event(), threading.Event()

    def other_request():
        with gate_for(model).hold(0, 10, 65_536):
            held.set()
            release.wait(2)

    holder = threading.Thread(target=other_request)
    holder.start()
    assert held.wait(2)
    threading.Timer(0.1, release.set).start()
    rec = _recorder(tmp_path)
    perf_trace.activate(rec)
    try:
        rec.start_llm_call(1, "prompt")
        with client._model_slot_lease(model):
            client._ensure_model_loaded(model)
        rec.end_llm_call({})
    finally:
        perf_trace.activate(None)
        holder.join(2)

    waits = rec.llm_calls[0]["client"]
    # Exclusive (no slot named), so it queued behind the other request's slot.
    assert waits["lock_wait_s"] >= 0.08
    assert "lease_wait_s" in waits
    assert waits["ensure_loaded_s"] >= 0.04


def test_only_fragments_in_the_sent_prompt_are_sized(tmp_path, monkeypatch):
    """A subclass may drop a mixin fragment from the composed prompt."""
    from types import SimpleNamespace

    from gaia.agents.base.agent import Agent

    monkeypatch.setenv(TURN_LOG_ENV, str(tmp_path / "turns.jsonl"))
    agent = SimpleNamespace(
        _pre_turn_spans=None,
        system_prompt="kept fragment\n\nagent text",
        _openai_tools=None,
        _tools_registry={},
        _active_tool_filter=None,
        _mixin_prompt_origins={
            "kept fragment": "get_memory_system_prompt",
            "filtered fragment": "get_sd_system_prompt",
        },
        chat=None,
        console=None,
    )
    try:
        rec = Agent._begin_turn_record(agent, "hello")
    finally:
        perf_trace.activate(None)
    assert rec.prompt["fragments"] == {"get_memory_system_prompt": 13}
