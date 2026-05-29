# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.eval.performance (perf extractor — no Lemonade)."""

import json

import pytest

from gaia.eval.performance import (
    RunResult,
    extract_from_agent_result,
    extract_from_trace_json,
    extract_step_stats,
    run_to_dict,
    to_performance_summary,
)


def _agent_result():
    """A synthetic process_query() result mimicking agent.py:3745-3753 stats."""
    return {
        "input_tokens": 1200,
        "output_tokens": 300,
        "total_tokens": 1500,
        "conversation": [
            {"role": "system", "content": "You are an email triage agent."},
            {"role": "user", "content": "Triage my inbox"},
            {
                "role": "assistant",
                "content": "<thinking>let me plan</thinking> I'll triage now.",
            },
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 1,
                    "performance_stats": {
                        "input_tokens": 800,
                        "output_tokens": 150,
                        "time_to_first_token": 0.09,  # seconds
                        "tokens_per_second": 120.0,
                    },
                },
            },
            {"role": "tool", "name": "triage_inbox", "content": "{}"},
            {"role": "assistant", "content": "Done."},
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "step": 2,
                    "performance_stats": {
                        "input_tokens": 400,
                        "output_tokens": 150,
                        "time_to_first_token": 0.11,
                        "tokens_per_second": 100.0,
                    },
                },
            },
        ],
    }


class TestExtractStepStats:
    def test_two_steps_with_field_map(self):
        steps, reasoning = extract_step_stats(_agent_result()["conversation"])
        assert len(steps) == 2
        s0 = steps[0]
        assert s0.input_tokens == 800
        assert s0.output_tokens == 150
        assert s0.time_to_first_token_ms == 90.0  # 0.09s × 1000
        assert s0.tokens_per_second == 120.0
        assert s0.total_tokens == 950  # no total_tokens key → input+output fallback
        assert s0.duration_ms == 0  # /stats has no per-step duration
        # "<thinking>let me plan</thinking>" → 11 chars // 4 = 2 reasoning tokens
        assert reasoning == 2

    def test_no_stats_messages(self):
        steps, reasoning = extract_step_stats(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]
        )
        assert steps == []
        assert reasoning == 0


class TestExtractFromAgentResult:
    def test_aggregates_and_averages(self):
        run = extract_from_agent_result(
            _agent_result(),
            run_id="r1",
            timestamp="2026-05-29T00:00:00Z",
            model_id="Gemma-4-E4B-it-GGUF",
            mode="full",
            total_duration_ms=4200,
            category_counts={"urgent": 1},
            total_emails=1,
        )
        assert isinstance(run, RunResult)
        assert len(run.step_results) == 2
        assert run.total_input_tokens == 1200  # top-level preferred
        assert run.total_output_tokens == 300
        assert run.total_tokens == 1500
        assert run.avg_time_to_first_token_ms == 100.0  # (90+110)/2
        assert run.avg_tokens_per_second == 110.0  # (120+100)/2
        assert run.total_duration_ms == 4200
        assert run.category_counts == {"urgent": 1}
        assert run.total_emails == 1
        assert run.status == "ok"

    def test_sums_from_steps_when_no_top_level_tokens(self):
        ar = _agent_result()
        del ar["input_tokens"]
        del ar["output_tokens"]
        del ar["total_tokens"]
        run = extract_from_agent_result(ar, run_id="r2", timestamp="t", model_id="m")
        assert run.total_input_tokens == 1200  # 800 + 400
        assert run.total_output_tokens == 300  # 150 + 150

    def test_empty_conversation_is_ok_with_zero_steps(self):
        run = extract_from_agent_result(
            {"conversation": []}, run_id="r3", timestamp="t", model_id="m"
        )
        assert run.step_results == []
        assert run.avg_tokens_per_second == 0.0
        assert run.status == "ok"


class TestPerformanceSummary:
    def test_scorecard_contract_keys(self):
        run = extract_from_agent_result(
            _agent_result(),
            run_id="r",
            timestamp="t",
            model_id="m",
            total_duration_ms=4200,
        )
        summary = to_performance_summary(run)
        for key in (
            "avg_tokens_per_second",
            "avg_time_to_first_token",
            "total_input_tokens",
            "total_output_tokens",
            "flags",
        ):
            assert key in summary
        assert summary["avg_time_to_first_token"] == 0.1  # 100ms → 0.1s
        assert summary["avg_tokens_per_second"] == 110.0

    def test_run_to_dict_json_serializable(self):
        run = extract_from_agent_result(
            _agent_result(), run_id="r", timestamp="t", model_id="m"
        )
        json.dumps(run_to_dict(run))  # must not raise


class TestExtractFromTraceJson:
    def test_round_trips_a_trace_file(self, tmp_path):
        trace = tmp_path / "trace.json"
        trace.write_text(json.dumps(_agent_result()))
        run = extract_from_trace_json(
            str(trace), run_id="r", timestamp="t", model_id="m"
        )
        assert len(run.step_results) == 2
        assert run.avg_tokens_per_second == 110.0

    def test_missing_file_raises_loudly(self):
        with pytest.raises(FileNotFoundError):
            extract_from_trace_json(
                "/nonexistent/trace.json", run_id="r", timestamp="t", model_id="m"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
