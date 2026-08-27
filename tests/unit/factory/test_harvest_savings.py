# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the context-size and savings models.

The carry multiplier is the load-bearing idea in both: a result emitted early
is re-sent on every later request, so its cost is its size times how many
requests follow it. Getting that wrong silently rescales every saving figure.
"""

import json

import pytest

from gaia.factory.harvest.context import KV_MODELS, _requests, collect, kv_bytes
from gaia.factory.harvest.savings import (
    CHARS_PER_TOKEN,
    RESULT_CAP_TOKENS,
    attribute,
    bounded_table,
    carry_multiplier,
    prompt_tokens,
    segment_lengths,
    segmented_multiplier,
    units,
)


def step(family, chars, digest="", ok=True):
    return {
        "tool": "Read" if family == "read" else "Bash",
        "family": family,
        "ok": ok,
        "arg_hash": f"h{digest or chars}",
        "arg_digest": digest,
        "result_chars": chars,
        "error": "",
    }


def trace(steps, calls, subagents=None):
    return {
        "steps": steps,
        "assistant_turns": calls,
        "subagents": subagents or [],
    }


class TestCarryMultiplier:
    def test_first_step_rides_every_call(self):
        assert carry_multiplier(0, 10, 20) == 20

    def test_last_step_rides_almost_none(self):
        assert carry_multiplier(9, 10, 20) == pytest.approx(2.0)

    def test_scales_linearly_with_run_length(self):
        assert carry_multiplier(0, 10, 100) == 10 * carry_multiplier(0, 10, 10)

    def test_midpoint_rides_half_the_run(self):
        assert carry_multiplier(5, 10, 40) == pytest.approx(20.0)


class TestSegmentation:
    """Compaction resets the context, so carry must not cross a reset."""

    def test_a_flat_run_is_one_segment(self):
        assert segment_lengths([10, 20, 30, 40], 8) == [8]

    def test_a_drop_below_half_the_peak_splits_the_run(self):
        segs = segment_lengths([100, 200, 300, 40, 80], 10)
        assert len(segs) == 2
        assert sum(segs) == 10

    def test_a_dip_that_stays_above_half_is_not_a_reset(self):
        assert segment_lengths([100, 200, 180, 260], 8) == [8]

    def test_missing_series_falls_back_to_one_segment(self):
        assert segment_lengths(None, 12) == [12]
        assert segment_lengths([], 12) == [12]

    def test_carry_does_not_cross_a_reset(self):
        """Billing a pre-compaction result for the whole run overstated the
        corpus total by ~15%."""
        assert segmented_multiplier(0, 10, [20]) == 20
        assert segmented_multiplier(0, 10, [10, 10]) == 10

    def test_a_step_after_the_reset_rides_only_its_own_segment(self):
        # Step 9 of 10 maps to position 18 of 20 -> 2 calls left in segment 2,
        # not the 4 an unsegmented run would charge it.
        assert segmented_multiplier(9, 10, [10, 10]) == pytest.approx(2.0)
        assert segmented_multiplier(9, 10, [20]) == pytest.approx(2.0)
        # The saving is on the *early* steps, which no longer span the reset.
        assert segmented_multiplier(1, 10, [10, 10]) < segmented_multiplier(1, 10, [20])


class TestUnits:
    def test_subagent_is_its_own_context(self):
        """A subagent has a separate window; folding it in overstates carry."""
        sub = {"steps_detail": [step("read", 400)], "assistant_turns": 3}
        got = list(units(trace([step("shell", 100)], 5, [sub])))
        assert [calls for _, calls in got] == [5, 3]
        assert [len(s) for s, _ in got] == [1, 1]


class TestAttribute:
    def test_repeat_read_is_keyed_on_path_not_full_args(self):
        """Paging one file with offsets is a re-read a cache would serve."""
        steps = [
            step("read", 4000, digest="a.py"),
            step("read", 4000, digest="a.py"),
            step("read", 4000, digest="b.py"),
        ]
        got = attribute([trace(steps, 3)])
        assert got["read_calls"] == 3
        assert got["repeat_read_calls"] == 1

    def test_only_the_tail_above_the_cap_is_counted(self):
        big = int((RESULT_CAP_TOKENS + 1000) * CHARS_PER_TOKEN)
        got = attribute([trace([step("shell", big)], 1)])
        # One step, one call, multiplier 1 -> exactly the 1000-token overage.
        assert got["over_cap"] == pytest.approx(1000.0)

    def test_result_under_the_cap_contributes_nothing(self):
        small = int((RESULT_CAP_TOKENS - 500) * CHARS_PER_TOKEN)
        assert attribute([trace([step("shell", small)], 1)])["over_cap"] == 0

    def test_schema_scales_with_model_calls_not_tool_calls(self):
        got = attribute([trace([step("shell", 10)] * 6, 2)])
        assert got["model_calls"] == 2

    def test_empty_run_is_skipped_not_divided_by_zero(self):
        got = attribute([trace([], 4)])
        assert got["carried_results"] == 0
        # A tool-free run still ships the schema block on every call.
        assert got["model_calls"] == 4

    def test_repeat_read_is_credited_only_up_to_the_cap(self):
        """Otherwise a large repeat read is counted by the cache AND the
        budgeter, and the two mechanisms double-count."""
        big = int((RESULT_CAP_TOKENS + 5000) * CHARS_PER_TOKEN)
        got = attribute([trace([step("read", big, "a.py")] * 2, 2)])
        assert got["repeat_read_calls"] == 1
        assert got["repeat_reads"] <= RESULT_CAP_TOKENS * 2

    def test_a_read_of_a_file_edited_in_between_is_not_cacheable(self):
        steps = [
            step("read", 4000, "a.py"),
            step("edit", 50, "a.py"),
            step("read", 4000, "a.py"),
        ]
        got = attribute([trace(steps, 3)])
        assert got["stale_read_calls"] == 1
        assert got["repeat_read_calls"] == 0
        assert got["repeat_reads"] == 0


class TestCollect:
    def test_series_excludes_subagents_but_requests_include_them(self, tmp_path):
        """The regression that made every subagent start look like a compaction.

        `series` feeds reset detection. Concatenating subagent requests into it
        turned 6 real resets into 438 false ones and inflated every carried
        figure ~14%.
        """
        cache = tmp_path / "cache"
        cache.mkdir()
        proj = tmp_path / "projects" / "p"
        (proj / "sess" / "subagents").mkdir(parents=True)

        def write(path, sizes):
            with path.open("w", encoding="utf-8") as fh:
                for i, n in enumerate(sizes):
                    fh.write(
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "id": f"{path.stem}-{i}",
                                    "usage": {"input_tokens": n},
                                },
                            }
                        )
                        + "\n"
                    )

        write(proj / "sess.jsonl", [100, 200, 300])
        write(proj / "sess" / "subagents" / "agent-a.jsonl", [10, 20])
        (cache / "traces.jsonl").write_text(
            json.dumps({"session_id": "sess", "project": "p"}) + "\n",
            encoding="utf-8",
        )

        sessions, requests = collect(cache, tmp_path / "projects", freeze=False)
        assert sessions[0]["series"] == [100, 200, 300]
        assert sorted(requests) == [10, 20, 100, 200, 300]


class TestBoundedTable:
    def test_a_request_under_the_bound_is_charged_what_it_costs(self):
        """Charging it the full bound invents tokens and hides the saving."""
        out = bounded_table({"tokens": {"total": 1}}, [1000] * 10)
        assert "| 16K | 0.00 B | **0%** | 100.0% |" in out


class TestPromptTokens:
    def test_output_is_excluded(self):
        stats = {
            "tokens": {
                "input": 10,
                "cache_read": 100,
                "cache_write": 20,
                "output": 999,
                "total": 1129,
            }
        }
        assert prompt_tokens(stats) == 130


class TestKvBytes:
    def test_uniform_attention_scales_linearly(self):
        one = kv_bytes("Qwen3-8B", 1000)
        assert kv_bytes("Qwen3-8B", 2000) == pytest.approx(2 * one)

    def test_qwen3_8b_is_144_kib_per_token(self):
        assert kv_bytes("Qwen3-8B", 1) == 144 * 1024

    def test_gemma_constants_match_the_shipped_config(self):
        """Pinned factor by factor: a typo here rescales every Gemma figure.

        42 layers, num_kv_shared_layers 18 -> layers 0-23 allocate. Of those,
        full_attention sits at 5/11/17/23 = 4 global at global_head_dim 512,
        and 20 sliding at head_dim 256 -- but layer 22 is the sliding-type
        donor for the 18 shared layers, so it is full length too. That leaves
        19 window-capped layers as the flat floor.
        """
        spec = KV_MODELS["Gemma-4-E4B-it"]
        assert spec["per_token_el"] == 2 * 4 * 2 * 512 + 2 * 1 * 2 * 256
        assert spec["per_token_el"] == 9216
        assert spec["flat_el"] == 2 * 19 * 2 * 256 * 512
        assert spec["flat_el"] == 9961472

    def test_sliding_window_model_has_a_flat_floor(self):
        m = "Gemma-4-E4B-it"
        assert KV_MODELS[m]["flat_el"] > 0
        # Doubling the context must less than double the total.
        assert kv_bytes(m, 2000) < 2 * kv_bytes(m, 1000)

    def test_gemma_is_far_cheaper_than_qwen_at_long_context(self):
        ratio = kv_bytes("Qwen3-8B", 131072) / kv_bytes("Gemma-4-E4B-it", 131072)
        assert 7.5 < ratio < 8.5  # ~7.9x; a loose bound would hide real drift

    def test_quantised_cache_is_smaller_but_not_free(self):
        """q8_0 carries an fp16 scale per 32-value block — not 1 byte/element."""
        fp16 = kv_bytes("Qwen3-8B", 1000, "fp16")
        q8 = kv_bytes("Qwen3-8B", 1000, "q8_0")
        assert q8 < fp16 / 1.8
        assert q8 > fp16 / 2  # strictly worse than a naive halving


class TestRequestParsing:
    def test_usage_is_deduped_per_message_id(self, tmp_path):
        """One record per content block, each repeating the same usage."""
        p = tmp_path / "s.jsonl"

        def rec(inp):
            return {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "usage": {
                        "input_tokens": inp,
                        "cache_read_input_tokens": 1000,
                        "cache_creation_input_tokens": 100,
                    },
                },
            }

        with p.open("w", encoding="utf-8") as fh:
            # Deliberately unequal, so max / first / min are distinguishable.
            for inp in (10, 0, 10):
                fh.write(json.dumps(rec(inp)) + "\n")
        assert _requests(p) == [1110]

    def test_two_message_ids_are_two_requests(self, tmp_path):
        p = tmp_path / "s.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            for mid, inp in (("a", 5), ("b", 7)):
                fh.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {"id": mid, "usage": {"input_tokens": inp}},
                        }
                    )
                    + "\n"
                )
        assert sorted(_requests(p)) == [5, 7]

    def test_non_assistant_records_are_ignored(self, tmp_path):
        p = tmp_path / "s.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
        assert _requests(p) == []

    def test_malformed_lines_do_not_abort_the_read(self, tmp_path):
        p = tmp_path / "s.jsonl"
        with p.open("w", encoding="utf-8") as fh:
            fh.write("{not json\n")
            fh.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"id": "m", "usage": {"input_tokens": 5}},
                    }
                )
                + "\n"
            )
        assert _requests(p) == [5]
