# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the per-turn performance recorder (dev mode, opt-in).

The recorder exists to answer "where did the 34 seconds go?", so these tests pin
the two properties that make its answer trustworthy: the cached/new input split
is computed against the previous call's prompt, and server-reported token counts
are never added to locally-counted ones.
"""

from __future__ import annotations

import json

import pytest

from gaia.agents.base.turn_metrics import (
    SCHEMA,
    TURN_LOG_ENV,
    TurnRecorder,
    _common_prefix_len,
    format_summary,
    turn_log_path,
)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {"name": f"tool_{i}", "description": "d" * 200, "parameters": {}},
    }
    for i in range(8)
]


def _recorder(**kwargs):
    defaults = dict(
        query="hello",
        agent_name="GaiaAgent",
        model_id="Gemma-4-E4B-it-GGUF",
        system_prompt="SYSTEM PROMPT " * 200,
        tool_schemas=TOOL_SCHEMAS,
        tool_names=[f"tool_{i}" for i in range(8)],
        skills_active=["gaia-voice"],
        history_messages=4,
    )
    defaults.update(kwargs)
    return TurnRecorder(**defaults)


# ── the off switch ─────────────────────────────────────────────────────────


def test_recording_is_off_unless_the_env_var_is_set(monkeypatch):
    monkeypatch.delenv(TURN_LOG_ENV, raising=False)
    assert turn_log_path() is None


def test_an_empty_env_var_is_off_not_a_relative_path(monkeypatch):
    """``GAIA_TURN_LOG=`` must not resolve to the current directory."""
    monkeypatch.setenv(TURN_LOG_ENV, "   ")
    assert turn_log_path() is None


def test_writes_nothing_when_no_path_is_configured(monkeypatch, tmp_path):
    monkeypatch.delenv(TURN_LOG_ENV, raising=False)
    rec = _recorder()
    rec.finish(
        answer="hi",
        steps=1,
    )
    assert list(tmp_path.iterdir()) == []


# ── prompt accounting ──────────────────────────────────────────────────────


def test_fixed_prefill_is_the_system_prompt_plus_the_tool_schemas():
    rec = _recorder()
    p = rec.prompt
    assert p["fixed_prefill_tokens"] == p["system_tokens"] + p["tool_schema_tokens"]
    assert p["tools_sent"] == len(TOOL_SCHEMAS)
    assert p["skills_active"] == ["gaia-voice"]


def test_tool_schemas_dominate_when_the_registry_is_large():
    """The finding this whole module exists to make visible."""
    rec = _recorder(system_prompt="short")
    assert rec.prompt["tool_schema_tokens"] > rec.prompt["system_tokens"]


# ── the cached / new split ─────────────────────────────────────────────────


def test_first_call_of_a_turn_has_nothing_cached():
    rec = _recorder()
    rec.start_llm_call(1, json.dumps([{"role": "user", "content": "x" * 4000}]))
    rec.end_llm_call({"input_tokens": 1200, "output_tokens": 10})
    call = rec.llm_calls[0]
    assert call["input_tokens_cached"] == 0
    assert call["input_tokens_new"] == call["input_tokens_local"]
    assert call["cache_hit_ratio"] == 0.0


def test_a_second_call_sharing_the_whole_prefix_is_almost_entirely_cached():
    """A ReAct step appends a tool result; everything before it is reusable."""
    rec = _recorder()
    base = json.dumps([{"role": "system", "content": "S" * 8000}])
    rec.start_llm_call(1, base)
    rec.end_llm_call({"input_tokens": 2000})
    rec.start_llm_call(2, base + json.dumps([{"role": "user", "content": "ok"}]))
    rec.end_llm_call({"input_tokens": 2010})

    second = rec.llm_calls[1]
    assert second["cache_hit_ratio"] > 0.95
    assert second["input_tokens_new"] < 20
    assert (
        second["input_tokens_cached"] + second["input_tokens_new"]
        == second["input_tokens_local"]
    )


def test_a_changed_prefix_busts_the_whole_cache():
    """Finding 3: volatile text ABOVE static text invalidates all of it."""
    rec = _recorder()
    static = "S" * 8000
    rec.start_llm_call(1, "memory-v1" + static)
    rec.end_llm_call({})
    rec.start_llm_call(2, "memory-v2" + static)  # one edit at the very front
    rec.end_llm_call({})

    assert rec.llm_calls[1]["cache_hit_ratio"] < 0.01


def test_volatile_text_placed_last_keeps_the_static_prefix_warm():
    """The same edit, moved to the tail, costs only the tail."""
    rec = _recorder()
    static = "S" * 8000
    rec.start_llm_call(1, static + "memory-v1")
    rec.end_llm_call({})
    rec.start_llm_call(2, static + "memory-v2")
    rec.end_llm_call({})

    assert rec.llm_calls[1]["cache_hit_ratio"] > 0.95


# ── backend stats ──────────────────────────────────────────────────────────


def test_backend_stats_are_kept_verbatim():
    """Curated subsets never anticipate the next latency question."""
    rec = _recorder()
    stats = {
        "input_tokens": 22919,
        "output_tokens": 150,
        "time_to_first_token": 59.193109,
        "tokens_per_second": 34.037,
        "request_count_total": 48,
        "some_future_field": "kept",
    }
    rec.start_llm_call(1, "prompt")
    rec.end_llm_call(stats)
    assert rec.llm_calls[0]["stats_raw"] == stats
    assert rec.llm_calls[0]["ttft_s"] == pytest.approx(59.193109)


def test_prefill_rate_is_derived_from_the_uncached_tokens():
    rec = _recorder()
    rec.start_llm_call(1, "x" * 40000)
    rec.end_llm_call({"time_to_first_token": 10.0})
    call = rec.llm_calls[0]
    assert call["prefill_tok_per_s"] == pytest.approx(
        call["input_tokens_new"] / 10.0, rel=1e-3
    )


def test_missing_stats_do_not_invent_numbers():
    """No silent fallback: absent is None, never a fake zero."""
    rec = _recorder()
    rec.start_llm_call(1, "prompt")
    rec.end_llm_call({})
    call = rec.llm_calls[0]
    assert call["ttft_s"] is None
    assert call["input_tokens"] is None
    assert "prefill_tok_per_s" not in call


def test_end_without_a_matching_start_is_ignored():
    rec = _recorder()
    rec.end_llm_call({"input_tokens": 5})
    assert rec.llm_calls == []


# ── totals ─────────────────────────────────────────────────────────────────


def test_server_and_local_token_totals_are_never_mixed():
    """Different tokenizers — adding one to the other is meaningless."""
    rec = _recorder()
    rec.start_llm_call(1, "x" * 4000)
    rec.end_llm_call({"input_tokens": 99999, "output_tokens": 10})
    record = rec.finish(answer="a", steps=1)
    totals = record["totals"]

    assert totals["input_tokens_server"] == 99999
    assert totals["input_tokens_local"] != 99999
    assert (
        totals["input_tokens_cached_local"] + totals["input_tokens_new_local"]
        == totals["input_tokens_local"]
    )


def test_tool_time_is_tracked_separately_from_model_time():
    rec = _recorder()
    rec.record_tool(1, "run_shell_command", 2.5, ok=True)
    rec.record_tool(2, "find_files", 0.5, ok=False)
    record = rec.finish(answer="a", steps=2)

    assert record["totals"]["tool_s"] == pytest.approx(3.0)
    assert [c["name"] for c in record["tool_calls"]] == [
        "run_shell_command",
        "find_files",
    ]
    assert record["tool_calls"][1]["ok"] is False


def test_a_refused_tool_still_contributes_its_time():
    """Otherwise a refusal's latency is misattributed to agent overhead."""
    rec = _recorder()
    rec.record_tool(1, "gh_write", 1.25, ok=False)
    record = rec.finish(answer="a", steps=1)
    assert record["totals"]["tool_s"] == pytest.approx(1.25)


# ── the written record ─────────────────────────────────────────────────────


def test_record_is_one_json_line_per_turn(tmp_path):
    path = tmp_path / "turns.jsonl"
    for i in range(3):
        # The query rides on the recorder now, not on finish() — it is fixed
        # when the turn opens, so each turn needs its own recorder to differ.
        rec = _recorder(path=path, query=f"q{i}")
        rec.start_llm_call(1, f"prompt {i}")
        rec.end_llm_call({"input_tokens": 10, "output_tokens": 2})
        rec.finish(answer="a", steps=1)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    records = [json.loads(line) for line in lines]
    assert all(r["schema"] == SCHEMA for r in records)
    assert [r["query"] for r in records] == ["q0", "q1", "q2"]


def test_record_carries_timestamps_and_total_turn_time(tmp_path):
    path = tmp_path / "turns.jsonl"
    rec = _recorder(path=path)
    rec.start_llm_call(1, "p")
    rec.end_llm_call({})
    record = rec.finish(answer="a", steps=1)

    assert record["started_at"].endswith("+00:00")
    assert record["ended_at"].endswith("+00:00")
    assert record["total_s"] >= 0
    assert record["llm_calls"][0]["at"].endswith("+00:00")


def test_an_unwritable_path_does_not_fail_the_turn(tmp_path):
    """A diagnostic that can break a user's turn is worse than no diagnostic."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    rec = _recorder(path=blocker / "nested" / "turns.jsonl")
    record = rec.finish(answer="a", steps=1)
    assert record["schema"] == SCHEMA


def test_summary_line_reports_every_number_the_user_asked_for():
    rec = _recorder()
    rec.start_llm_call(1, "x" * 4000)
    rec.end_llm_call({"input_tokens": 1000, "output_tokens": 42})
    record = rec.finish(answer="a", steps=1)
    line = format_summary(record)

    for expected in ("total", "steps", "prefill", "tools", "cached", "out", "model"):
        assert expected in line


# ── backend-reported cache counters ────────────────────────────────────────
#
# A remote prefix cache (Anthropic) knows exactly how much of the prompt it
# reused. The local prefix estimate cannot see across turns — it resets with
# each record — so on a one-step turn it always reads 0% no matter what the
# server actually reused. When the backend reports, its numbers win.


def test_backend_cache_counters_reach_the_record():
    rec = _recorder()
    rec.start_llm_call(1, "x" * 4000)
    rec.end_llm_call(
        {
            "prompt_tokens": 12380,
            "output_tokens": 28,
            "cache_read_input_tokens": 12200,
            "cache_creation_input_tokens": 0,
        }
    )
    record = rec.finish(answer="a", steps=1)

    (call,) = record["llm_calls"]
    assert call["cache_read_input_tokens"] == 12200
    assert call["cache_creation_input_tokens"] == 0
    assert record["totals"]["input_tokens_cached_server"] == 12200
    assert record["totals"]["input_tokens_server"] == 12380


def test_a_backend_that_reports_no_cache_counters_leaves_them_absent():
    """Absent means unmeasured; a 0 would read as a measured cache miss."""
    rec = _recorder()
    rec.start_llm_call(1, "x" * 4000)
    rec.end_llm_call({"input_tokens": 1000, "output_tokens": 42})
    record = rec.finish(answer="a", steps=1)

    (call,) = record["llm_calls"]
    assert "cache_read_input_tokens" not in call
    assert record["totals"]["input_tokens_cached_server"] == 0


def test_summary_prefers_the_backends_own_cache_numbers():
    rec = _recorder()
    rec.start_llm_call(1, "x" * 4000)
    rec.end_llm_call(
        {
            "prompt_tokens": 12380,
            "output_tokens": 28,
            "cache_read_input_tokens": 12200,
        }
    )
    record = rec.finish(answer="a", steps=1)

    # The local estimate for this same turn is 0% — one call, nothing before it.
    assert record["totals"]["input_tokens_cached_local"] == 0
    assert "in 12,380 (99% cached)" in format_summary(record)


def test_a_cold_turn_that_only_wrote_the_cache_still_uses_server_totals():
    """Otherwise turn 1 reads from the local estimator and turn 2 from the
    server, and the two lines a user compares are on different scales."""
    rec = _recorder()
    rec.start_llm_call(1, "x" * 4000)
    rec.end_llm_call(
        {
            "prompt_tokens": 14034,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 13696,
        }
    )
    record = rec.finish(answer="a", steps=1)
    assert "in 14,034 (0% cached)" in format_summary(record)


# The cached/new split is only as trustworthy as this helper. It is a binary
# search over slice equality, so an off-by-one lands as a wrong cache figure
# rather than a crash — which is why it is checked against the obvious
# implementation rather than against hand-picked expectations.
def _naive_prefix_len(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return i


@pytest.mark.parametrize(
    "a,b",
    [
        ("", ""),
        ("", "a"),
        ("a", ""),
        ("a", "a"),
        ("abc", "abd"),
        ("aaa", "aa"),
        ("abc", "xyz"),
        ("x" * 5000, "x" * 5000),
        ("x" * 5000 + "a", "x" * 5000 + "b"),
        ("héllo", "héllx"),
        ("日本語", "日本"),
    ],
)
def test_common_prefix_matches_the_obvious_implementation(a, b):
    assert _common_prefix_len(a, b) == _naive_prefix_len(a, b)


def test_common_prefix_is_symmetric_and_bounded():
    a, b = "shared-head" + "L" * 900, "shared-head" + "R" * 900
    n = _common_prefix_len(a, b)
    assert n == _common_prefix_len(b, a) == len("shared-head")
    assert 0 <= n <= min(len(a), len(b))
