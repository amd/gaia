# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The three calculations in `harvest.systems` that are not simple addition.

Concurrency is measured twice and both ways are easy to get wrong. The
open-interval sweep goes negative on a zero-length session, counts two sessions
that merely touch at a boundary as overlapping, and weights a level held for a
second the same as one held for six hours. The inference measure has to collapse
the several transcript records Claude Code writes per request back down to one,
or it reports a single agent as a crowd.

The KV table is the third: it is the number a machine gets bought on, so the
`weights + N x KV` arithmetic is checked against `context.kv_bytes` directly
rather than trusted to the formatting, and it is pinned to inference
concurrency rather than to open sessions.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from gaia.factory.harvest.context import GIB, KV_MODELS, kv_bytes
from gaia.factory.harvest.systems import (
    CTX_LADDER,
    InferenceLoad,
    cache_read_share,
    collect_inference,
    concurrency,
    concurrency_stats,
    delegation,
    execution_split,
    kv_memory,
    load_traces,
    model_mix,
    overlap_segments,
    parse_ts,
    prefill_decode,
    spans,
    throughput,
    weighted_level,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _usage(inp=10, out=5, read=100, write=20):
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": read,
        "cache_write_tokens": write,
        "total": inp + out + read + write,
    }


def _trace(sid="s1", start=0.0, end=60.0, calls=3, model="claude-opus-5", **kw):
    u = kw.pop("usage", None) or _usage()
    trace = {
        "session_id": sid,
        "started_at": _at(start),
        "last_at": _at(end),
        "prompts": ["do the thing"],
        "steps": [
            {
                "tool": "Bash",
                "family": "shell",
                "ok": True,
                "result_chars": 100,
                "prompt_index": 0,
            }
        ]
        * calls,
        "subagents": [],
        "usage": u,
        "usage_by_model": {model: u},
        "models": [model],
        "total_calls": calls,
    }
    trace.update(kw)
    return trace


def _load(minutes, session_ids=("s1", "s2", "s3"), missing=0):
    """An InferenceLoad from {minute: [session index per request]}."""
    return InferenceLoad(list(session_ids), dict(minutes), missing)


def _assistant(msg_id, stamp, usage=True, rec_type="assistant"):
    """One raw transcript record, in Claude Code's shape."""
    msg = {"id": msg_id, "usage": {"input_tokens": 1} if usage else {}}
    return json.dumps({"type": rec_type, "timestamp": stamp, "message": msg})


def _subagent(sid="agent-1", calls=2, families=None, usage=None):
    u = usage or _usage(inp=1, out=2, read=50, write=0)
    return {
        "session_id": sid,
        "kind": "subagent",
        "total_calls": calls,
        "families": families or {"web": calls},
        "usage": u,
        "usage_by_model": {"claude-opus-5": u},
        "steps_detail": [
            {
                "tool": "WebFetch",
                "family": "web",
                "ok": True,
                "result_chars": 42,
                "prompt_index": 0,
            }
        ]
        * calls,
    }


# --------------------------------------------------------------------- timestamps


class TestTimestamps:
    def test_trailing_z_is_utc(self):
        assert parse_ts("2026-01-01T00:00:00.000Z", "x") == T0

    @pytest.mark.parametrize("bad", [None, "", 17, "yesterday", "2026-13-40"])
    def test_unparseable_stamps_fail_loudly(self, bad):
        with pytest.raises(ValueError, match="session 7"):
            parse_ts(bad, "session 7 started_at")

    def test_backwards_stamps_are_ordered_and_reported(self):
        t = _trace(sid="flipped", start=10, end=10)
        t["last_at"] = _at(9.5)
        ivals, inverted = spans([t])
        assert ivals[0][0] < ivals[0][1], "interval must run forwards"
        assert inverted == [("flipped", pytest.approx(30.0))]

    def test_ordered_stamps_report_no_inversion(self):
        assert spans([_trace()])[1] == []


# -------------------------------------------------------------------- the sweep


class TestOverlapSegments:
    def test_two_overlapping_sessions_raise_the_level_between_them(self):
        a = (T0, T0 + timedelta(minutes=30))
        b = (T0 + timedelta(minutes=10), T0 + timedelta(minutes=40))
        segs = overlap_segments([a, b])
        levels = [(int((s - T0).total_seconds() // 60), lvl) for s, _, lvl in segs]
        assert levels == [(0, 1), (10, 2), (30, 1)]

    def test_disjoint_sessions_leave_an_idle_gap(self):
        a = (T0, T0 + timedelta(minutes=10))
        b = (T0 + timedelta(minutes=20), T0 + timedelta(minutes=30))
        assert [lvl for _, _, lvl in overlap_segments([a, b])] == [1, 0, 1]

    def test_touching_at_a_boundary_is_not_overlap(self):
        """A boundary instant is where a naive sweep invents concurrency."""
        a = (T0, T0 + timedelta(minutes=10))
        b = (T0 + timedelta(minutes=10), T0 + timedelta(minutes=20))
        segs = overlap_segments([a, b])
        assert [lvl for _, _, lvl in segs] == [1, 1]

    def test_no_segment_has_zero_width(self):
        segs = overlap_segments(
            [
                (T0, T0 + timedelta(minutes=10)),
                (T0 + timedelta(minutes=10), T0 + timedelta(minutes=10)),
                (T0 + timedelta(minutes=10), T0 + timedelta(minutes=20)),
            ]
        )
        assert all(e > s for s, e, _ in segs)

    def test_zero_length_session_never_drives_the_level_negative(self):
        instant = T0 + timedelta(minutes=5)
        segs = overlap_segments([(T0, T0 + timedelta(minutes=10)), (instant, instant)])
        levels = [lvl for _, _, lvl in segs]
        assert min(levels) >= 0
        assert max(levels) == 1, "an instant-long session is not a second agent"

    def test_nested_session_is_counted_for_its_whole_span(self):
        outer = (T0, T0 + timedelta(minutes=60))
        inner = (T0 + timedelta(minutes=20), T0 + timedelta(minutes=30))
        segs = [(s, e, lvl) for s, e, lvl in overlap_segments([outer, inner]) if e > s]
        assert [lvl for _, _, lvl in segs] == [1, 2, 1]
        two = next(e - s for s, e, lvl in segs if lvl == 2)
        assert two == timedelta(minutes=10)

    def test_empty_input(self):
        assert overlap_segments([]) == []


class TestWeightedLevel:
    def test_duration_outweighs_event_count(self):
        """Level 2 is touched briefly; level 1 dominates the busy time."""
        minutes = {1: 600.0, 2: 1.0}
        assert weighted_level(minutes, 50) == 1
        assert weighted_level(minutes, 99) == 1
        assert weighted_level(minutes, 100) == 2

    def test_idle_time_is_excluded_from_the_percentile(self):
        assert weighted_level({0: 10_000.0, 3: 10.0}, 50) == 3

    def test_no_busy_time_fails_loudly(self):
        with pytest.raises(ValueError, match="no busy time"):
            weighted_level({0: 5.0}, 50)


class TestConcurrencyStats:
    def test_peak_and_typical_on_a_hand_computed_corpus(self):
        traces = [
            _trace(sid="a", start=0, end=100),
            _trace(sid="b", start=0, end=100),
            _trace(sid="c", start=40, end=50),
        ]
        S = concurrency_stats(traces)
        assert S.peak == 3
        assert S.peak_min == pytest.approx(10.0)
        assert S.typical == 2, "90 of 100 busy minutes ran two sessions"
        assert S.busy_min == pytest.approx(100.0)
        assert S.mean == pytest.approx((90 * 2 + 10 * 3) / 100)

    def test_all_zero_length_sessions_cannot_be_measured(self):
        with pytest.raises(ValueError, match="no two can overlap"):
            concurrency_stats([_trace(sid="a", start=5, end=5)])

    def test_p90_and_typical_are_both_exposed(self):
        """80 busy minutes at level 2, 20 at level 3: p50 is 2, p90 is 3."""
        traces = [_trace("a", 0, 100), _trace("b", 0, 100), _trace("c", 80, 100)]
        S = concurrency_stats(traces)
        assert S.minutes == {2: 80.0, 3: 20.0}
        assert S.typical == 2
        assert S.p90 == 3


# ------------------------------------------------- inference-time concurrency


class TestRequestBucketing:
    """Claude Code writes one record per content block, all sharing a usage."""

    def _write(self, tmp_path, project, sid, lines):
        d = tmp_path / project
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_repeated_records_for_one_request_count_once(self, tmp_path):
        self._write(
            tmp_path,
            "proj",
            "s1",
            [
                _assistant("msg_a", "2026-01-01T10:00:01.000Z"),
                _assistant("msg_a", "2026-01-01T10:00:02.000Z"),
                _assistant("msg_a", "2026-01-01T10:00:03.000Z"),
                _assistant("msg_b", "2026-01-01T10:00:30.000Z"),
            ],
        )
        load = collect_inference(
            tmp_path, tmp_path, [_trace("s1", project="proj")], freeze=False
        )
        assert load.requests == 2, "three blocks of msg_a are one request"
        assert load.busy_minutes == 1
        assert load.sessions_per_minute == [1]

    def test_a_request_straddling_a_minute_takes_its_earliest_stamp(self, tmp_path):
        self._write(
            tmp_path,
            "proj",
            "s1",
            [
                _assistant("msg_a", "2026-01-01T10:01:00.000Z"),
                _assistant("msg_a", "2026-01-01T10:00:59.000Z"),
            ],
        )
        load = collect_inference(
            tmp_path, tmp_path, [_trace("s1", project="proj")], freeze=False
        )
        assert list(load.minutes) == ["2026-01-01T10:00"]

    def test_non_assistant_and_usageless_records_are_ignored(self, tmp_path):
        self._write(
            tmp_path,
            "proj",
            "s1",
            [
                _assistant("msg_a", "2026-01-01T10:00:00.000Z", rec_type="user"),
                _assistant("msg_b", "2026-01-01T10:00:00.000Z", usage=False),
                "{ not json",
                _assistant("msg_c", "2026-01-01T10:00:00.000Z"),
            ],
        )
        load = collect_inference(
            tmp_path, tmp_path, [_trace("s1", project="proj")], freeze=False
        )
        assert load.requests == 1

    def test_distinct_sessions_in_one_minute_are_counted_once_each(self, tmp_path):
        for sid, stamps in (
            ("s1", ["10:00:01", "10:00:40"]),
            ("s2", ["10:00:05"]),
            ("s3", ["10:05:00"]),
        ):
            self._write(
                tmp_path,
                "proj",
                sid,
                [
                    _assistant(f"{sid}-{i}", f"2026-01-01T{s}.000Z")
                    for i, s in enumerate(stamps)
                ],
            )
        traces = [_trace(s, project="proj") for s in ("s1", "s2", "s3")]
        load = collect_inference(tmp_path, tmp_path, traces, freeze=False)
        assert load.requests == 4
        assert load.busy_minutes == 2
        # 10:00 held two sessions across three requests; 10:05 held one.
        assert load.sessions_per_minute == [1, 2]
        assert load.requests_per_minute == [1, 3]

    def test_sessions_without_a_transcript_are_counted_not_hidden(self, tmp_path):
        self._write(tmp_path, "proj", "s1", [_assistant("m", "2026-01-01T10:00:00Z")])
        traces = [_trace("s1", project="proj"), _trace("gone", project="proj")]
        load = collect_inference(tmp_path, tmp_path, traces, freeze=False)
        assert load.missing == 1

    def test_a_missing_projects_root_refuses_to_substitute_open_intervals(
        self, tmp_path
    ):
        with pytest.raises(SystemExit, match="must not stand in for it"):
            collect_inference(tmp_path, tmp_path / "nope", [_trace()], freeze=False)

    def test_transcripts_with_no_requests_fail_loudly(self, tmp_path):
        (tmp_path / "proj").mkdir()
        with pytest.raises(SystemExit, match="No timestamped assistant requests"):
            collect_inference(
                tmp_path, tmp_path, [_trace("s1", project="proj")], freeze=False
            )

    def test_the_measurement_is_frozen_and_reread(self, tmp_path):
        self._write(tmp_path, "proj", "s1", [_assistant("m", "2026-01-01T10:00:00Z")])
        traces = [_trace("s1", project="proj")]
        first = collect_inference(tmp_path, tmp_path, traces)
        assert (tmp_path / "inference_minutes.json").exists()

        # Transcript grows after the freeze; the frozen figure must not move.
        self._write(
            tmp_path,
            "proj",
            "s1",
            [
                _assistant("m", "2026-01-01T10:00:00Z"),
                _assistant("m2", "2026-01-01T11:00:00Z"),
            ],
        )
        again = collect_inference(tmp_path, tmp_path, traces)
        assert again.requests == first.requests == 1
        assert again.minutes == first.minutes


class TestConcurrencyReport:
    def test_both_measures_are_reported_side_by_side(self):
        traces = [_trace("a", 0, 100), _trace("b", 0, 100)]
        # Two sessions open for the whole span; only one ever inferring.
        load = _load({f"2026-01-01T10:{m:02d}": [0] for m in range(5)})
        out = concurrency(traces, load)
        assert "sessions open" in out and "sessions inferring" in out
        assert "The gap between those two columns is the finding" in out
        assert "upper bound** on true simultaneity" in out, "minute-bucket caveat"

    def test_the_over_provisioning_factor_is_computed_not_asserted(self):
        traces = [_trace(str(i), 0, 100) for i in range(4)]
        load = _load({"2026-01-01T10:00": [0]})
        out = concurrency(traces, load)
        assert "4.0x on average" in out, "4 open sessions against 1 inferring"


# ------------------------------------------------------------------- the KV math


#: Four sessions open the whole time, but never more than two inferring at
#: once — the shape the whole correction exists to capture.
_KV_TRACES = [_trace(str(i), 0, 100) for i in range(4)]
_KV_LOAD = _load(
    {
        "2026-01-01T10:00": [0, 1],
        "2026-01-01T10:01": [0],
        "2026-01-01T10:02": [2],
    },
    session_ids=("a", "b", "c", "d"),
)


class TestKVMemory:
    def test_sizing_is_weights_plus_n_times_kv(self):
        ctx = 100_000
        out = kv_memory(_KV_TRACES, _KV_LOAD, requests=[ctx] * 10)

        peak = _KV_LOAD.sessions_per_minute[-1]
        assert peak == 2
        spec = KV_MODELS["Qwen3-32B"]
        one = spec["weights_q4"] + kv_bytes("Qwen3-32B", ctx, "q8_0") / GIB
        many = spec["weights_q4"] + peak * kv_bytes("Qwen3-32B", ctx, "q8_0") / GIB
        assert f"{one:,.1f}" in out
        assert f"{many:,.1f}" in out
        assert f"at {ctx:,} tokens of context each" in out

    def test_it_is_priced_on_inference_concurrency_not_open_sessions(self):
        """The whole point of the correction: 2 inferring, not 4 open."""
        ctx = 100_000
        out = kv_memory(_KV_TRACES, _KV_LOAD, requests=[ctx] * 10)
        assert concurrency_stats(_KV_TRACES).peak == 4
        w = KV_MODELS["Qwen3-32B"]["weights_q4"]
        kv = kv_bytes("Qwen3-32B", ctx, "q8_0") / GIB
        assert f"{w + 2*kv:,.1f}" in out, "peak inferring is 2"
        assert f"{w + 4*kv:,.1f}" not in out, "must not size on 4 open sessions"
        assert "2 agents, q8_0 KV" in out

    def test_the_open_session_alternative_is_named_with_its_cost(self):
        out = kv_memory(_KV_TRACES, _KV_LOAD, requests=[100_000] * 10)
        assert "deployment choice, not a property of the workload" in out
        assert "back at 4 concurrent contexts" in out

    def test_weights_are_loaded_once_not_per_agent(self):
        """`weights + N x KV`, never `N x (weights + KV)` — the assumption is stated."""
        out = kv_memory(_KV_TRACES, _KV_LOAD, requests=[200_000] * 4)
        wrong = 2 * (
            KV_MODELS["Qwen3-14B"]["weights_q4"]
            + kv_bytes("Qwen3-14B", 200_000, "q8_0") / GIB
        )
        assert f"{wrong:,.1f}" not in out
        assert "weights load **once**" in out

    def test_operating_point_comes_from_the_observed_p90(self):
        reqs = list(range(1, 101))  # p90 of 1..100 is 90
        out = kv_memory(_KV_TRACES, _KV_LOAD, requests=reqs)
        assert "at 90 tokens of context each" in out

    def test_without_requests_the_assumption_is_labelled_not_hidden(self):
        out = kv_memory(_KV_TRACES, _KV_LOAD)
        assert "No measured operating point available" in out
        assert "as an assumption, not a measurement" in out

    def test_every_ladder_rung_is_priced_for_every_model(self):
        out = kv_memory(_KV_TRACES, _KV_LOAD)
        for name in KV_MODELS:
            assert f"`{name}`" in out
        for ctx in CTX_LADDER[:-1]:
            assert f"| {ctx//1024}K " in out

    def test_sliding_window_model_stays_flat_relative_to_the_others(self):
        """Gemma's cache is window-capped, so it must not scale like the rest."""
        gemma = kv_bytes("Gemma-4-E4B-it", 1_048_576) / kv_bytes(
            "Gemma-4-E4B-it", 131_072
        )
        qwen = kv_bytes("Qwen3-8B", 1_048_576) / kv_bytes("Qwen3-8B", 131_072)
        assert qwen == pytest.approx(8.0)
        assert gemma < qwen


# ------------------------------------------------------------------ token tables


class TestPrefillDecode:
    def test_prefill_counts_cache_reads_and_writes(self):
        out = prefill_decode([_trace(usage=_usage(inp=10, out=5, read=100, write=20))])
        assert "26:1" in out, "prefill is 130 tokens against 5 decoded"
        assert "compute-bound" in out

    def test_subagents_are_counted_as_their_own_context(self):
        sub = _subagent(usage=_usage(inp=0, out=1, read=9, write=0))
        out = prefill_decode([_trace(subagents=[sub])])
        assert "`subagent`" in out and "9:1" in out

    def test_a_corpus_with_no_output_tokens_fails_loudly(self):
        with pytest.raises(ValueError, match="zero output tokens"):
            prefill_decode([_trace(usage=_usage(out=0))])

    def test_missing_usage_names_the_session(self):
        t = _trace(sid="broken")
        del t["usage"]
        with pytest.raises(ValueError, match="broken"):
            prefill_decode([t])


class TestModelMix:
    def test_calls_in_a_multi_model_context_are_not_attributed(self):
        t = _trace(calls=10)
        t["usage_by_model"] = {"claude-opus-5": _usage(), "claude-sonnet-5": _usage()}
        out = model_mix([t])
        assert "10 tool calls (100.0%)" in out
        assert "cannot be attributed" in out

    def test_pseudo_models_are_flagged_not_treated_as_a_tier(self):
        t = _trace()
        t["usage_by_model"]["<synthetic>"] = _usage(inp=0, out=0, read=0, write=0)
        out = model_mix([t])
        assert "harness pseudo-model" in out
        assert "`claude-opus-5` carries" in out

    def test_a_genuine_split_is_reported_as_more_than_one_tier(self):
        """The verdict must follow the share, not the reference corpus."""
        t = _trace(sid="a")
        t["usage_by_model"] = {
            "claude-opus-5": _usage(read=100),
            "claude-haiku-4-5": _usage(read=90),
        }
        out = model_mix([t])
        assert "More than one tier" in out
        assert "One tier, not several" not in out

    def test_an_all_pseudo_model_corpus_fails_loudly(self):
        t = _trace()
        t["usage_by_model"] = {"<synthetic>": _usage()}
        with pytest.raises(ValueError, match="pseudo-model"):
            model_mix([t])

    def test_missing_usage_by_model_fails_loudly(self):
        t = _trace(sid="nomodel")
        t["usage_by_model"] = {}
        with pytest.raises(ValueError, match="nomodel"):
            model_mix([t])


class TestThroughput:
    def test_rates_use_all_three_denominators(self):
        traces = [_trace("a", 0, 100, calls=100), _trace("b", 0, 100)]
        out = throughput(traces, _load({"2026-01-01T10:00": [0]}))
        assert "per session-minute" in out
        assert "per open minute" in out
        assert "per inferring minute" in out

    def test_the_inferring_denominator_gives_the_higher_rate(self):
        """Dividing by idle time understates what a server must sustain."""
        traces = [_trace("a", 0, 100, calls=60), _trace("b", 0, 100)]
        minutes = {f"2026-01-01T10:{m:02d}": [0] for m in range(10)}
        out = throughput(traces, _load(minutes))
        assert "| 63 | 0.32 | 0.63 | 6.30 |" in out


class TestCacheReadShare:
    def test_it_is_the_cache_read_fraction_of_prefill(self):
        t = _trace(usage=_usage(inp=10, out=5, read=70, write=20))
        assert cache_read_share([t]) == pytest.approx(70.0)


class TestDelegation:
    def test_fanout_and_share_are_counted_across_subagents(self):
        traces = [
            _trace("a", 0, 100, calls=10, subagents=[_subagent(calls=4)]),
            _trace("b", 0, 100, calls=10),
        ]
        out = delegation(traces)
        assert "50.0% of sessions" in out
        assert "4 of 24" in out, "subagent share of tool calls"

    def test_a_corpus_with_no_delegation_says_so(self):
        assert "No session in this corpus delegated" in delegation([_trace()])

    def test_nested_delegation_is_reported_as_unmeasurable_depth(self):
        sub = _subagent(calls=3, families={"delegate": 1, "web": 2})
        out = delegation([_trace("a", 0, 100, subagents=[sub])])
        assert "1 subagent(s) made 1 `delegate` call(s)" in out
        assert "cannot be recovered" in out


class TestExecutionSplit:
    def test_subagent_steps_are_included_in_the_local_load(self):
        out = execution_split([_trace("a", 0, 100, calls=2, subagents=[_subagent()])])
        assert "`shell`" in out and "`web`" in out

    def test_a_corpus_with_no_tool_calls_says_so(self):
        assert "No tool calls" in execution_split([_trace(calls=0)])

    def test_it_refuses_to_quantify_the_split(self):
        out = execution_split([_trace()])
        assert "no per-step timing" in out
        assert "not computable from this data" in out


# ---------------------------------------------------------------------- loading


class TestLoadTraces:
    def test_blank_lines_are_skipped(self, tmp_path):
        (tmp_path / "traces.jsonl").write_text(
            json.dumps(_trace()) + "\n\n" + json.dumps(_trace("s2")) + "\n",
            encoding="utf-8",
        )
        assert len(load_traces(tmp_path)) == 2

    def test_a_missing_corpus_says_how_to_make_one(self, tmp_path):
        with pytest.raises(SystemExit, match="harvest.scan"):
            load_traces(tmp_path)

    def test_a_corrupt_line_names_its_line_number(self, tmp_path):
        (tmp_path / "traces.jsonl").write_text(
            json.dumps(_trace()) + "\n{not json\n", encoding="utf-8"
        )
        with pytest.raises(SystemExit, match=r"traces\.jsonl:2"):
            load_traces(tmp_path)

    def test_an_empty_corpus_fails_loudly(self, tmp_path):
        (tmp_path / "traces.jsonl").write_text("\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="empty"):
            load_traces(tmp_path)
