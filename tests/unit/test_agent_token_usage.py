# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for real per-turn token accounting (#2899).

The TUI's stats line used to show a client-side `len(content)/4` guess of the
final answer's length, not the tokens the model actually generated. The real
fix crosses three layers; this file covers the Python side:

1. `_sum_conversation_tokens` / `_safe_number` — pure aggregation, defensive
   against malformed per-step stats.
2. `_extract_tool_usage` — a generic, agent-agnostic convention letting a tool
   that makes its own internal LLM calls (bypassing the normal per-step
   accounting) report usage back on its own return payload. Deliberately
   narrow (requires a recognized numeric token field) so an unrelated tool's
   own "usage" key (rate-limit/quota/disk usage) is never misread as tokens.
3. `_fold_tool_usage` / `Agent._execute_tool` wiring — the single choke point
   every tool call goes through, verified end-to-end with a real Agent
   instance and a real (non-mocked) `_execute_tool` call.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import (
    Agent,
    _extract_tool_usage,
    _query_ttft_seconds,
    _safe_number,
    _sum_conversation_tokens,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save/restore the global tool registry around each test."""
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _TokenUsageAgent(Agent):
    """Minimal concrete Agent that pulls tools from the global registry."""

    def _register_tools(self):  # tools are registered per-test via @tool
        pass


def _make_agent(**kwargs) -> _TokenUsageAgent:
    """Construct a real Agent without touching Lemonade or the network."""
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _TokenUsageAgent(skip_lemonade=True, silent_mode=True, **kwargs)
    return agent


# ─────────────────────────── _safe_number ────────────────────────────────


class TestSafeNumber:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (42, 42),
            (0, 0),
            (3.7, 3),
            (None, 0),
            ("42", 0),  # untrusted string input never raises, never counts
            ({"nested": 1}, 0),
            ([1, 2], 0),
            (-5, 0),  # negative is untrusted, not a valid token count
            (True, 0),  # bool is an int subclass in Python — must not count as 1
        ],
    )
    def test_coerces_or_zeroes(self, value, expected):
        assert _safe_number(value) == expected


# ─────────────────────────── _sum_conversation_tokens ─────────────────────


class TestSumConversationTokens:
    def test_sums_per_step_stats(self):
        conversation = [
            {"role": "user", "content": "hi"},
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 1,
                    "performance_stats": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 2,
                    "performance_stats": {"input_tokens": 12, "output_tokens": 8},
                },
            },
        ]
        total_in, total_out = _sum_conversation_tokens(conversation)
        assert (total_in, total_out) == (22, 13)

    def test_ignores_non_stats_system_entries(self):
        conversation = [
            {"role": "system", "content": "some other system message"},
            {"role": "system", "content": {"type": "not_stats"}},
        ]
        assert _sum_conversation_tokens(conversation) == (0, 0)

    def test_malformed_stats_do_not_raise(self):
        conversation = [
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "performance_stats": {"input_tokens": "bad", "output_tokens": None},
                },
            },
        ]
        assert _sum_conversation_tokens(conversation) == (0, 0)

    def test_folds_tool_usage_entries(self):
        tool_usage = [
            {"prompt_tokens": 5, "completion_tokens": 20},
            {"input_tokens": 3, "output_tokens": 7},
        ]
        total_in, total_out = _sum_conversation_tokens([], tool_usage)
        assert (total_in, total_out) == (8, 27)

    def test_conversation_and_tool_usage_both_fold_in(self):
        conversation = [
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "performance_stats": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]
        tool_usage = [{"prompt_tokens": 5, "completion_tokens": 20}]
        total_in, total_out = _sum_conversation_tokens(conversation, tool_usage)
        assert (total_in, total_out) == (15, 25)


# ─────────────────────── _query_ttft_seconds (#2899 follow-up) ────────────
#
# Reads the real per-request ttft Lemonade's /stats already reports off the
# turn's FIRST step, not the last — a last-step reading drops every earlier
# step's tool-decision latency.
class TestQueryTTFTSeconds:
    def test_reads_ttft_off_the_first_stats_entry(self):
        conversation = [
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 1,
                    "performance_stats": {"time_to_first_token": 8.2},
                },
            },
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 2,
                    "performance_stats": {"time_to_first_token": 1.5},
                },
            },
        ]
        # Step 1 — the first LLM call of the turn — not the final step that
        # happened to produce the visible answer, and not an average/sum.
        assert _query_ttft_seconds(conversation) == 8.2

    def test_no_stats_entries_returns_none(self):
        assert _query_ttft_seconds([]) is None
        assert _query_ttft_seconds([{"role": "user", "content": "hi"}]) is None

    def test_missing_field_on_first_step_returns_none_not_a_later_steps_value(self):
        # Step 1 has no ttft; step 2 does. Must omit, not fall through to step 2.
        conversation = [
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 1,
                    "performance_stats": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 2,
                    "performance_stats": {"time_to_first_token": 1.5},
                },
            },
        ]
        assert _query_ttft_seconds(conversation) is None

    def test_step_1_entry_entirely_missing_returns_none_not_step_2s_value(self):
        # A failed /stats poll can skip step 1's entry entirely, leaving
        # step 2's as the earliest present — must check the step number,
        # not just take the first entry found.
        conversation = [
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 2,
                    "performance_stats": {"time_to_first_token": 1.5},
                },
            },
        ]
        assert _query_ttft_seconds(conversation) is None

    def test_zero_or_negative_reported_value_is_treated_as_unavailable(self):
        conversation = [
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 1,
                    "performance_stats": {"time_to_first_token": 0},
                },
            },
        ]
        assert _query_ttft_seconds(conversation) is None

    def test_non_finite_value_is_treated_as_unavailable(self):
        conversation = [
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 1,
                    "performance_stats": {"time_to_first_token": float("inf")},
                },
            },
        ]
        assert _query_ttft_seconds(conversation) is None

    def test_non_dict_performance_stats_does_not_raise(self):
        conversation = [
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 1,
                    "performance_stats": "not a dict",
                },
            },
        ]
        assert _query_ttft_seconds(conversation) is None

    def test_malformed_value_does_not_raise(self):
        conversation = [
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 1,
                    "performance_stats": {"time_to_first_token": "bad"},
                },
            },
        ]
        assert _query_ttft_seconds(conversation) is None


# ─────────────────────────── _extract_tool_usage ──────────────────────────


class TestExtractToolUsage:
    def test_extracts_from_dict_payload(self):
        result = {"results": [], "usage": {"prompt_tokens": 5, "completion_tokens": 20}}
        assert _extract_tool_usage(result) == {
            "prompt_tokens": 5,
            "completion_tokens": 20,
        }

    def test_extracts_from_json_string_payload(self):
        import json

        result = json.dumps({"ok": True, "usage": {"completion_tokens": 20}})
        assert _extract_tool_usage(result) == {"completion_tokens": 20}

    def test_no_usage_key_yields_none(self):
        assert _extract_tool_usage({"results": []}) is None

    def test_non_dict_payload_yields_none(self):
        assert _extract_tool_usage(["not", "a", "dict"]) is None
        assert _extract_tool_usage(None) is None
        assert _extract_tool_usage(42) is None

    def test_malformed_json_string_yields_none_not_raise(self):
        assert _extract_tool_usage("{not valid json") is None

    def test_usage_key_without_recognized_token_field_is_rejected(self):
        # An unrelated tool's own "usage" key (rate-limit/quota/disk usage,
        # not LLM tokens) must never be misread as token accounting.
        result = {"usage": {"remaining_quota": 100, "disk_usage_mb": 42}}
        assert _extract_tool_usage(result) is None

    def test_non_numeric_token_field_is_rejected(self):
        result = {"usage": {"completion_tokens": "not-a-number"}}
        assert _extract_tool_usage(result) is None

    def test_bool_token_field_is_rejected(self):
        # bool is an int subclass in Python; a stray True/False under a
        # recognized field name must not be treated as a real count.
        result = {"usage": {"completion_tokens": True}}
        assert _extract_tool_usage(result) is None


# ─────────────────────────── _execute_tool -> _fold_tool_usage wiring ─────


class TestToolUsageRollup:
    def test_tool_reported_usage_is_folded_via_execute_tool(self):
        """AC2(c): a tool that makes its own internal LLM calls (bypassing the
        normal per-step accounting, e.g. email triage's per-message
        classification fan-out) and reports usage on its own return payload
        gets that usage folded into the turn's running total."""

        @tool
        def triage_tool() -> dict:
            """Simulates a triage-style tool with its own internal LLM usage."""
            return {
                "results": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 20},
            }

        agent = _make_agent()
        agent._tool_reported_usage = []  # simulate the per-turn reset

        result = agent._execute_tool("triage_tool", {})

        assert result["usage"] == {"prompt_tokens": 5, "completion_tokens": 20}
        assert agent._tool_reported_usage == [
            {"prompt_tokens": 5, "completion_tokens": 20}
        ]
        # And it reaches the real total through the same helper the loop uses.
        _, total_output = _sum_conversation_tokens([], agent._tool_reported_usage)
        assert total_output == 20

    def test_tool_without_usage_does_not_pollute_the_total(self):
        @tool
        def plain_tool(value: str) -> dict:
            """A normal tool with no LLM usage to report."""
            return {"echo": value}

        agent = _make_agent()
        agent._tool_reported_usage = []

        agent._execute_tool("plain_tool", {"value": "hi"})

        assert agent._tool_reported_usage == []

    def test_usage_accumulates_across_multiple_tool_calls_in_one_turn(self):
        @tool
        def classify_batch(n: int) -> dict:
            """Each call reports its own slice of usage, like a fan-out loop."""
            return {"usage": {"completion_tokens": n * 10}}

        agent = _make_agent()
        agent._tool_reported_usage = []

        agent._execute_tool("classify_batch", {"n": 1})
        agent._execute_tool("classify_batch", {"n": 2})
        agent._execute_tool("classify_batch", {"n": 3})

        _, total_output = _sum_conversation_tokens([], agent._tool_reported_usage)
        assert total_output == 60  # (1 + 2 + 3) * 10

    def test_new_agent_instance_starts_with_no_stale_usage(self):
        """The __init__-level default (#2899) must not leak across instances
        or survive without the per-turn reset that _process_query_impl does."""
        agent = _make_agent()
        assert agent._tool_reported_usage == []
