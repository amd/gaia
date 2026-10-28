# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The Context & performance panel's numbers: measured, or said to be unknown.

The two promises it makes — the categories add up to the meter's total, and
model time plus GAIA time is the turn's wall time — are checked here.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from gaia.agents.base import history
from gaia.ui import _chat_helpers as helpers
from gaia.ui import turn_insights
from gaia.ui.database import ChatDatabase
from gaia.ui.routers import chat


def _stats(step, perf, tools=()):
    return {
        "role": "system",
        "content": {
            "type": "stats",
            "step": step,
            "performance_stats": perf,
            "tools": list(tools),
        },
    }


# Two local streamed steps, as Lemonade's /stats reports them.
LOCAL_RESULT = {
    "timing_summary": {"llm_seconds": 6.0, "tool_seconds": 3.5, "llm_calls": 2},
    "conversation": [
        {"role": "user", "content": "hi"},
        _stats(
            1,
            {
                "prompt_tokens": 9000,
                "cache_tokens": 8000,
                "input_tokens": 1000,
                "output_tokens": 40,
                "time_to_first_token": 0.5,
                "tokens_per_second": 40.0,
            },
            tools=[
                {"name": "read_file", "seconds": 0.5},
                {"name": "write_file", "seconds": 3.0, "approval_wait_seconds": 2.0},
            ],
        ),
        _stats(
            2,
            {
                "prompt_tokens": 9200,
                "cache_tokens": 9000,
                "input_tokens": 200,
                "output_tokens": 160,
                "time_to_first_token": 0.25,
                "tokens_per_second": 20.0,
            },
        ),
    ],
}


def test_model_time_and_gaia_time_add_up_to_the_wall_time():
    perf = turn_insights.summarize_turn(
        LOCAL_RESULT,
        wall_seconds=14.0,
        model_id="Gemma-4-E4B-it-GGUF",
        loads=[{"model": "Gemma-4-E4B-it-GGUF", "seconds": 2.0, "replaced": []}],
    )
    t = perf["time"]
    assert t["model_seconds"] == 8.0  # requests + the load before the turn
    assert t["model_seconds"] + t["gaia_seconds"] == t["wall_seconds"] == 14.0
    assert t["tool_seconds"] == 1.5  # approval wait is not tool time
    assert t["waiting_seconds"] == 2.0
    assert (
        t["tool_seconds"] + t["waiting_seconds"] + t["own_seconds"] == t["gaia_seconds"]
    )


def test_tokens_cache_and_speeds_come_from_the_server():
    perf = turn_insights.summarize_turn(
        LOCAL_RESULT, wall_seconds=10.0, model_id="Gemma-4-E4B-it-GGUF"
    )
    assert perf["tokens_in"] == 18200
    assert perf["tokens_cached"] == 17000
    assert perf["tokens_new"] == 1200
    assert perf["tokens_out"] == 200
    assert perf["last_prompt_tokens"] == 9200
    assert perf["first_token_seconds"] == 0.5
    assert perf["prefill_tokens_per_second"] == round(1200 / 0.75, 1)
    # Weighted by tokens: 200 tokens over 40/40 + 160/20 seconds.
    assert perf["generation_tokens_per_second"] == round(200 / 9.0, 1)
    assert perf["tool_calls"] == 2
    assert perf["cost"] == {"kind": "local", "usd": 0.0}


def test_a_turn_with_no_model_timing_says_unknown_not_zero():
    perf = turn_insights.summarize_turn(
        {"conversation": []}, wall_seconds=3.0, model_id="M-GGUF"
    )
    assert perf["time"]["model_seconds"] is None
    assert perf["time"]["gaia_seconds"] is None
    assert perf["tokens_in"] is None
    assert perf["tokens_cached"] is None
    assert perf["first_token_seconds"] is None
    assert perf["prefill_tokens_per_second"] is None


def test_a_load_inside_a_request_is_listed_but_not_counted_twice():
    result = {
        "timing_summary": {"llm_seconds": 30.0, "tool_seconds": 0.0},
        "conversation": [
            _stats(1, {"time_to_first_token": 1.0, "model_load_seconds": 25.0})
        ],
    }
    perf = turn_insights.summarize_turn(result, wall_seconds=31.0, model_id="M-GGUF")
    assert perf["time"]["model_seconds"] == 30.0
    assert perf["loads"] == [{"model": "M-GGUF", "seconds": 25.0, "during": "request"}]


def test_cloud_usage_is_priced_from_the_rate_card(monkeypatch):
    from gaia.eval.bench import metering

    seen = {}

    def price(model, tokens_in, cached, tokens_out):
        seen.update(model=model, args=(tokens_in, cached, tokens_out))
        return 0.0123

    monkeypatch.setattr(metering, "price", price)
    result = {
        "timing_summary": {"llm_seconds": 2.0, "tool_seconds": 0.0},
        "conversation": [
            _stats(
                1,
                {"prompt_tokens": 5000, "cached_tokens": 4000, "completion_tokens": 50},
            )
        ],
    }
    model = "fireworks.accounts/fireworks/models/kimi-k2p6"
    perf = turn_insights.summarize_turn(result, wall_seconds=2.5, model_id=model)
    assert perf["tokens_new"] == 1000
    assert perf["cost"] == {"kind": "estimate", "usd": 0.0123}
    assert seen == {"model": model, "args": (5000, 4000, 50)}


def test_chat_totals_count_unpriced_turns_apart():
    turns = [
        turn_insights.summarize_turn(
            LOCAL_RESULT, wall_seconds=10.0, model_id="Gemma-4-E4B-it-GGUF"
        ),
        {
            "time": {"wall_seconds": 4.0, "model_seconds": None, "gaia_seconds": None},
            "cost": {"kind": "unknown", "usd": None},
        },
    ]
    totals = turn_insights.chat_totals(turns)
    assert totals["turns"] == 2
    assert totals["wall_seconds"] == 14.0
    assert totals["model_seconds"] == 6.0
    assert totals["unpriced_turns"] == 1
    assert totals["usd"] == 0.0
    assert turn_insights.chat_totals([])["wall_seconds"] is None


def test_turn_token_totals_that_overflow_are_not_measured(caplog):
    result = {
        "timing_summary": {"llm_seconds": 1.0, "tool_seconds": 0.0},
        "conversation": [
            _stats(
                1,
                {
                    "prompt_tokens": 1e308,
                    "cache_tokens": 1e308,
                    "input_tokens": 1e308,
                    "output_tokens": 10,
                },
            ),
            _stats(
                2,
                {
                    "prompt_tokens": 1e308,
                    "cache_tokens": 1e308,
                    "input_tokens": 1e308,
                    "output_tokens": 20,
                },
            ),
        ],
    }

    perf = turn_insights.summarize_turn(result, wall_seconds=1.0, model_id="M-GGUF")

    assert perf["tokens_in"] is None
    assert perf["tokens_cached"] is None
    assert perf["tokens_new"] is None
    assert perf["tokens_out"] == 30
    assert "turn input tokens overflowed" in caplog.text


def test_chat_token_totals_that_overflow_are_not_measured(caplog):
    turns = [
        {
            "time": {"wall_seconds": 1.0, "model_seconds": 1.0, "gaia_seconds": 0.0},
            "tokens_in": 1e308,
            "tokens_cached": 1e308,
            "tokens_out": 10,
            "cost": {"kind": "unknown", "usd": None},
        },
        {
            "time": {"wall_seconds": 1.0, "model_seconds": 1.0, "gaia_seconds": 0.0},
            "tokens_in": 1e308,
            "tokens_cached": 1e308,
            "tokens_out": 20,
            "cost": {"kind": "unknown", "usd": None},
        },
    ]

    totals = turn_insights.chat_totals(turns)

    assert totals["tokens_in"] is None
    assert totals["tokens_cached"] is None
    assert totals["tokens_out"] == 30
    assert "chat input tokens overflowed" in caplog.text


def test_performance_endpoint_serializes_overflowed_token_totals(db, monkeypatch):
    sid = db.create_session(model="M-GGUF")["id"]
    db.add_message(
        sid,
        "assistant",
        "hello",
        inference_stats={
            "performance": {
                "time": {
                    "wall_seconds": 1.0,
                    "model_seconds": 1.0,
                    "gaia_seconds": 0.0,
                },
                "tokens_in": 1e308,
                "tokens_cached": 1e308,
                "tokens_out": 1.0,
                "cost": {"kind": "unknown", "usd": None},
            }
        },
    )
    db.add_message(
        sid,
        "assistant",
        "again",
        inference_stats={
            "performance": {
                "time": {
                    "wall_seconds": 1.0,
                    "model_seconds": 1.0,
                    "gaia_seconds": 0.0,
                },
                "tokens_in": 1e308,
                "tokens_cached": 1e308,
                "tokens_out": 2.0,
                "cost": {"kind": "unknown", "usd": None},
            }
        },
    )
    monkeypatch.setattr(turn_insights, "model_now", lambda model: {"model": model})

    result = asyncio.run(chat.get_chat_performance(sid, db))

    assert result["totals"]["tokens_in"] is None
    assert result["totals"]["tokens_cached"] is None
    assert result["totals"]["tokens_out"] == 3.0
    json.dumps(result, allow_nan=False)


# ── Context ────────────────────────────────────────────────────────────────


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(history, "context_window", lambda _agent: 32768)


def _agent(**extra):
    memory = "MEMORY: the user prefers metric units."
    skills = "SKILLS: weather — use the forecast tool."
    prompt = f"You are GAIA.\n\n{skills}\n\n{memory}"
    return SimpleNamespace(
        model_id="Gemma-4-E4B-it-GGUF",
        system_prompt=prompt,
        _mixin_prompt_origins={
            memory: "get_memory_system_prompt",
            skills: "get_skills_system_prompt",
            "a stale fragment no longer in the prompt": "get_memory_system_prompt",
        },
        _openai_tools=[{"type": "function", "function": {"name": "read_file"}}],
        _tools_registry={"read_file": 1, "write_file": 1, "web_search": 1},
        _active_tool_filter=["read_file"],
        chat=SimpleNamespace(config=SimpleNamespace(max_tokens=1024)),
        **extra,
    )


TURNS = [
    [
        {"role": "user", "content": "Summarise report.pdf"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "query_documents", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "query_documents",
            "content": "Revenue rose 4% " * 50,
        },
        {"role": "assistant", "content": "Revenue rose 4%."},
    ]
]


def test_categories_add_up_to_the_meter_total(window):
    usage = history.context_usage(_agent(), TURNS, "and costs?")
    rows = turn_insights.context_categories(usage)
    assert sum(r["tokens"] for r in rows) == usage["used"]
    by_key = {r["key"]: r["tokens"] for r in rows}
    assert by_key["skills"] > 0 and by_key["memory"] > 0
    assert by_key["system"] == usage["system"] - usage["skills"] - usage["memory"]
    assert by_key["documents"] > by_key["messages"]
    assert by_key["query"] > 0


def test_tools_loaded_only_when_needed_are_listed_apart():
    assert turn_insights.tools_by_load(_agent()) == {
        "loaded": ["read_file"],
        "on_demand": ["web_search", "write_file"],
    }
    every_tool = SimpleNamespace(_tools_registry={"a": 1}, _active_tool_filter=None)
    assert turn_insights.tools_by_load(every_tool) == {"loaded": ["a"], "on_demand": []}


class _Resp:
    def __init__(self, body):
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class _FakeClient:
    """Lemonade's /tokenize: one token per whitespace-separated word."""

    sent = []

    def __init__(self, timeout, headers):
        pass

    def post(self, url, json):
        _FakeClient.sent.append((url, json))
        return _Resp({"tokens": list(range(len(json["content"].split())))})

    def close(self):
        pass


@pytest.fixture
def lemonade(monkeypatch):
    import httpx

    _FakeClient.sent = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setattr(
        turn_insights, "_lemonade", lambda: ("http://localhost:13305/api/v1", {})
    )
    # Successive /health answers; the last one repeats.
    loaded = ["Gemma-4-E4B-it-GGUF"]

    def health(*_):
        return loaded.pop(0) if len(loaded) > 1 else loaded[0]

    monkeypatch.setattr(turn_insights, "_loaded_chat_model", health)
    return loaded


def test_counts_with_the_loaded_models_tokenizer(window, lemonade):
    usage = turn_insights.measure_context(_agent(), TURNS, "Gemma-4-E4B-it-GGUF")
    assert usage["tokenizer"] == {"exact": True, "model": "Gemma-4-E4B-it-GGUF"}
    assert usage["system"] == len(_agent().system_prompt.split())
    # Asking names no model, so Lemonade can never load or swap one for it.
    assert all(set(body) == {"content"} for _url, body in _FakeClient.sent)
    assert all(url.endswith("/tokenize") for url, _ in _FakeClient.sent)


def test_another_loaded_model_means_an_estimate_with_the_reason(window, lemonade):
    lemonade[:] = ["Qwen3-30B-A3B-Instruct-2507-GGUF"]
    usage = turn_insights.measure_context(_agent(), TURNS, "Gemma-4-E4B-it-GGUF")
    assert usage["tokenizer"]["exact"] is False
    assert "not loaded" in usage["tokenizer"]["reason"]
    assert _FakeClient.sent == []


def test_a_model_swap_while_measuring_discards_the_count(window, lemonade):
    lemonade[:] = ["Gemma-4-E4B-it-GGUF", "Qwen3-30B-A3B-Instruct-2507-GGUF"]
    usage = turn_insights.measure_context(_agent(), TURNS, "Gemma-4-E4B-it-GGUF")
    assert usage["tokenizer"] == {
        "exact": False,
        "reason": "The loaded model changed while measuring.",
    }


def test_a_cloud_model_is_an_estimate(window, lemonade):
    usage = turn_insights.measure_context(
        _agent(), TURNS, "fireworks.accounts/fireworks/models/kimi-k2p6"
    )
    assert usage["tokenizer"]["exact"] is False
    assert _FakeClient.sent == []


# ── Endpoint ───────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


def test_performance_lists_each_measured_turn_and_the_totals(db, monkeypatch):
    sid = db.create_session(model="Gemma-4-E4B-it-GGUF")["id"]
    db.add_message(sid, "user", "hi")
    perf = turn_insights.summarize_turn(
        LOCAL_RESULT, wall_seconds=10.0, model_id="Gemma-4-E4B-it-GGUF"
    )
    answer = db.add_message(
        sid,
        "assistant",
        "hello",
        inference_stats={"tokens_per_second": 40.0, "performance": perf},
    )
    db.add_message(
        sid, "assistant", "an old turn", inference_stats={"tokens_per_second": 5}
    )
    monkeypatch.setattr(turn_insights, "model_now", lambda model: {"model": model})

    result = asyncio.run(chat.get_chat_performance(sid, db))

    assert [t["message_id"] for t in result["turns"]] == [answer]
    assert result["turns"][0]["time"] == perf["time"]
    assert result["totals"]["wall_seconds"] == 10.0
    assert result["now"] == {"model": "Gemma-4-E4B-it-GGUF"}


def test_performance_for_an_unknown_chat_is_a_404(db):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat.get_chat_performance("missing", db))
    assert exc.value.status_code == 404


def test_unreadable_stats_fail_loudly(db):
    sid = db.create_session()["id"]
    msg = db.add_message(sid, "assistant", "x")
    db._conn.execute("UPDATE messages SET inference_stats = '{' WHERE id = ?", (msg,))
    with pytest.raises(ValueError, match="inference_stats"):
        db.turn_performance(sid)


def test_context_endpoint_returns_categories_and_tool_loading(db, window, lemonade):
    sid = db.create_session(model="Gemma-4-E4B-it-GGUF")["id"]
    helpers._store_agent(sid, "Gemma-4-E4B-it-GGUF", [], _agent(), "gaia")

    result = asyncio.run(chat.get_context_usage(sid, db))

    assert result["tokenizer"]["exact"] is True
    assert sum(c["tokens"] for c in result["categories"]) == result["used"]
    assert result["tool_loading"]["on_demand"] == ["web_search", "write_file"]
    assert result["model"] == "Gemma-4-E4B-it-GGUF"
    assert result["context_state"] is None


# ── What the agent put away or summarized ──────────────────────────────────


def test_context_state_is_none_when_the_agent_reports_nothing():
    assert turn_insights.context_state(SimpleNamespace()) is None


def test_context_state_passes_the_agents_report_through():
    agent = SimpleNamespace(
        context_state=lambda: {
            "put_away": {"results": 3, "tokens": 9000, "extra": "dropped"},
            "summarized": None,
        }
    )
    assert turn_insights.context_state(agent) == {
        "put_away": {"results": 3, "tokens": 9000},
        "summarized": None,
    }


@pytest.mark.parametrize(
    "report",
    [
        "not a dict",
        {"summarized": {"turns": 2, "tokens": 100}},
        {"put_away": {"results": -1, "tokens": 5}},
    ],
)
def test_a_malformed_context_state_fails_loudly(report):
    with pytest.raises((TypeError, ValueError), match="context_state"):
        turn_insights.context_state(SimpleNamespace(context_state=lambda: report))


# ── Review follow-ups: never present a partial or guessed number ───────────


def test_a_step_without_stats_makes_token_totals_unknown():
    result = {
        "timing_summary": {"llm_seconds": 3.0, "tool_seconds": 0.0},
        "conversation": [
            _stats(
                1, {"prompt_tokens": 9000, "cache_tokens": 8000, "input_tokens": 1000}
            ),
            _stats(2, {}),
        ],
    }
    perf = turn_insights.summarize_turn(result, wall_seconds=4.0, model_id="M-GGUF")
    assert perf["tokens_in"] is None
    assert perf["tokens_cached"] is None


def test_a_model_gaia_cannot_place_is_never_priced_at_zero():
    # Dotted, not a known local model, not in the cloud catalog.
    assert turn_insights.where_model_runs("acme.some-model") is None
    perf = turn_insights.summarize_turn(
        LOCAL_RESULT, wall_seconds=10.0, model_id="acme.some-model"
    )
    assert perf["cost"] == {"kind": "unknown", "usd": None}
    assert turn_insights.where_model_runs("Gemma-4-E4B-it-GGUF") == "local"


def test_no_model_means_unknown_not_unloaded():
    now = turn_insights.model_now(None)
    assert now["loaded"] is None
    assert now["provider"] == "unknown"


def test_performance_names_the_newest_answer(db, monkeypatch):
    sid = db.create_session(model="Gemma-4-E4B-it-GGUF")["id"]
    perf = turn_insights.summarize_turn(
        LOCAL_RESULT, wall_seconds=10.0, model_id="Gemma-4-E4B-it-GGUF"
    )
    db.add_message(sid, "assistant", "measured", inference_stats={"performance": perf})
    newest = db.add_message(
        sid, "assistant", "kept retry", inference_stats={"model": "x"}
    )
    db.add_message(sid, "user", "a later question")
    monkeypatch.setattr(turn_insights, "model_now", lambda model: {"model": model})

    result = asyncio.run(chat.get_chat_performance(sid, db))

    assert result["latest_message_id"] == newest
    assert result["turns"][-1]["message_id"] != newest


def test_performance_before_the_first_answer_reports_the_picked_cloud_model(
    db, monkeypatch
):
    """A new cloud chat must not show the idle local model as where it runs."""
    sid = db.create_session(model="Gemma-4-E4B-it-GGUF")["id"]
    db.set_setting("custom_model", "fireworks.deepseek-v4p1-flash")
    monkeypatch.setattr(turn_insights, "model_now", lambda model: {"model": model})

    result = asyncio.run(chat.get_chat_performance(sid, db))

    assert result["now"] == {"model": "fireworks.deepseek-v4p1-flash"}


# ── Where each speed comes from: the server's timings, else GAIA's own clock ─


def _step(step, perf, **timing):
    entry = _stats(step, perf, tools=timing.pop("tools", ()))
    entry["content"].update(timing)
    return entry


# A cloud turn as the agent records it: usage with no timings, client clock per step.
CLOUD_RESULT = {
    "timing_summary": {"llm_seconds": 7.35, "tool_seconds": 0.005, "llm_calls": 2},
    "conversation": [
        _step(
            1,
            {"prompt_tokens": 11038, "completion_tokens": 67, "cached_tokens": 0},
            llm_calls=1,
            ttft_seconds=4.66,
            output_tok_per_s=206.36,
            tools=[{"name": "browse_directory", "seconds": 0.005}],
        ),
        _step(
            2,
            {"prompt_tokens": 12568, "completion_tokens": 176, "cached_tokens": 1920},
            llm_calls=1,
            ttft_seconds=1.29,
            output_tok_per_s=162.97,
        ),
    ],
}
CLOUD = "fireworks.deepseek-v4p1-flash"


def test_a_cloud_turn_uses_gaias_own_clock_and_says_so():
    perf = turn_insights.summarize_turn(CLOUD_RESULT, wall_seconds=8.1, model_id=CLOUD)
    assert perf["first_token_seconds"] == 4.66
    assert perf["first_token_source"] == "client"
    assert perf["prefill_source"] == "client"
    new = 11038 + (12568 - 1920)
    assert perf["prefill_tokens_per_second"] == round(new / (4.66 + 1.29), 1)
    assert perf["generation_source"] == "client"
    assert perf["generation_tokens_per_second"] == round(
        243 / (67 / 206.36 + 176 / 162.97), 1
    )
    assert perf["tokens_cached"] == 1920
    assert perf["unknown"] == {}


def test_a_local_turn_prefers_the_servers_timings():
    result = {
        **LOCAL_RESULT,
        "conversation": [
            _step(
                1,
                LOCAL_RESULT["conversation"][1]["content"]["performance_stats"],
                llm_calls=1,
                ttft_seconds=0.9,
                output_tok_per_s=30.0,
            ),
        ],
    }
    perf = turn_insights.summarize_turn(result, wall_seconds=5.0, model_id="M-GGUF")
    assert perf["first_token_seconds"] == 0.5
    assert perf["first_token_source"] == "server"
    assert perf["generation_source"] == "server"
    assert perf["generation_tokens_per_second"] == 40.0


def test_unknown_values_carry_the_reason():
    result = {
        "timing_summary": {"llm_seconds": 2.0, "tool_seconds": 0.0},
        "conversation": [_step(1, {"completion_tokens": 50}, llm_calls=1)],
    }
    perf = turn_insights.summarize_turn(result, wall_seconds=3.0, model_id=CLOUD)
    assert perf["first_token_seconds"] is None
    assert "streamed" in perf["unknown"]["first_token_seconds"]
    assert "new" in perf["unknown"]["prefill_tokens_per_second"]
    assert perf["unknown"]["generation_tokens_per_second"]
    assert perf["unknown"]["tokens_cached"]


def test_tool_time_outside_a_recorded_step_is_shown_not_dropped():
    result = {
        **CLOUD_RESULT,
        "timing_summary": {**CLOUD_RESULT["timing_summary"], "tool_seconds": 3.405},
    }
    perf = turn_insights.summarize_turn(result, wall_seconds=12.0, model_id=CLOUD)
    assert perf["tool_seconds_total"] == 3.405
    assert perf["tools"][-1] == {"name": None, "seconds": 3.4}
    assert sum(t["seconds"] for t in perf["tools"]) == pytest.approx(3.405)
    assert perf["time"]["tool_seconds"] == 3.405


def test_a_cloud_models_own_window_comes_from_the_catalog(window, monkeypatch):
    from gaia.llm import lemonade_client

    monkeypatch.setattr(
        lemonade_client, "cloud_model_info", lambda m: {"ctx_size": 1048576}
    )
    usage = turn_insights.measure_context(_agent(), TURNS, CLOUD)
    assert usage["model_window"] == 1048576
    assert usage["window"] == 32768
    assert usage["tokenizer"]["exact"] is False


def test_context_says_why_when_an_answered_chat_has_no_agent_in_memory(db):
    sid = db.create_session(model="Gemma-4-E4B-it-GGUF")["id"]
    db.add_message(sid, "user", "hi")
    db.add_message(sid, "assistant", "hello")

    result = asyncio.run(chat.get_context_usage(sid, db))

    assert result["available"] is False
    assert "next message" in result["reason"]
