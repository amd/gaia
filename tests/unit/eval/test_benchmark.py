# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.eval.benchmark (offline — no Lemonade)."""

import json

import pytest

from gaia.eval.benchmark import (
    _extract_tools_called,
    _extract_triage_results,
    _maybe_parse_tool_envelope,
    _normalize_agent_result,
    build_result,
    run_benchmark,
    summarize_benchmark,
)

GT = {
    "_meta": {"note": "skip me"},
    "a": {"category": "urgent", "is_spam": False, "is_phishing": False},
    "b": {"category": "low priority", "is_spam": True, "is_phishing": False},
}

TRIAGE_ENVELOPE = {
    "ok": True,
    "data": {
        "results": [
            {
                "id": "a",
                "category": "urgent",
                "confident": True,
                "is_spam": False,
                "is_phishing": False,
            },
            {
                "id": "b",
                "category": "low priority",
                "confident": True,
                "is_spam": True,
                "is_phishing": False,
            },
        ]
    },
}


def _agent_result():
    return {
        "input_tokens": 1000,
        "output_tokens": 200,
        "total_tokens": 1200,
        "conversation": [
            {"role": "user", "content": "Triage my inbox (50 emails)"},
            {
                "role": "assistant",
                "content": {"tool": "triage_inbox", "tool_input": {}},
            },
            {
                "role": "tool",
                "name": "triage_inbox",
                "content": json.dumps(TRIAGE_ENVELOPE),
            },
            {"role": "assistant", "content": "Done."},
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "performance_stats": {
                        "input_tokens": 1000,
                        "output_tokens": 200,
                        "time_to_first_token": 0.1,
                        "tokens_per_second": 50.0,
                    },
                },
            },
        ],
    }


class TestFailLoud:
    """The upstream fork swallowed malformed tool JSON; we must raise."""

    def test_malformed_envelope_raises(self):
        with pytest.raises(ValueError, match="malformed tool-result JSON envelope"):
            _maybe_parse_tool_envelope('{"ok": true, "data": {')  # truncated JSON

    def test_extract_triage_raises_on_malformed(self):
        convo = [
            {"role": "tool", "name": "triage_inbox", "content": '{"ok": true, "data'}
        ]
        with pytest.raises(ValueError):
            _extract_triage_results(convo)

    def test_build_result_propagates_raise(self):
        ar = _agent_result()
        ar["conversation"][2]["content"] = '{"ok": true, "data": ['  # corrupt
        with pytest.raises(ValueError):
            build_result(
                ar, run_id="r", timestamp="t", model_id="m", total_duration_ms=1
            )

    def test_non_envelope_tool_output_is_skipped_not_raised(self):
        # plain-text tool output is "not the message we want", not an error
        assert _maybe_parse_tool_envelope("done, archived 3 messages") is None
        assert _maybe_parse_tool_envelope("") is None


class TestNormalizeAgentResult:
    def test_dict_passthrough(self):
        d = {"conversation": []}
        assert _normalize_agent_result(d) is d

    def test_json_string_parsed(self):
        assert _normalize_agent_result('{"x": 1}') == {"x": 1}

    def test_bad_string_raises(self):
        with pytest.raises(ValueError):
            _normalize_agent_result("not json {")

    def test_wrong_type_raises(self):
        with pytest.raises(TypeError):
            _normalize_agent_result(42)


class TestExtractToolsCalled:
    def test_dedupes_in_order(self):
        assert _extract_tools_called(_agent_result()) == ["triage_inbox"]


class TestBuildResult:
    def test_scorecard_compatible_and_perf(self):
        out = build_result(
            _agent_result(),
            run_id="r1",
            timestamp="t",
            model_id="Gemma-4-E4B-it-GGUF",
            total_duration_ms=2000,
            ground_truth=GT,
        )
        assert out["status"] == "PASS"
        assert out["category"] == "Gemma-4-E4B-it-GGUF"
        assert out["performance_summary"]["avg_tokens_per_second"] == 50.0
        assert out["performance_summary"]["avg_time_to_first_token"] == 0.1
        assert out["cost_estimate"]["estimated_usd"] == 0.0  # local model
        assert out["meets_throughput_bar"] is True  # 50 >= 10
        assert out["category_counts"] == {"urgent": 1, "low priority": 1}
        assert out["total_emails"] == 2
        # quality block computed against ground truth
        assert out["quality"]["category_accuracy"] == 1.0
        assert "spam" in out["quality"] and "needs_attention" in out["quality"]

    def test_no_triage_results_is_fail(self):
        ar = {"conversation": [{"role": "assistant", "content": "I refuse."}]}
        out = build_result(
            ar, run_id="r", timestamp="t", model_id="m", total_duration_ms=1
        )
        assert out["status"] == "FAIL"
        assert "error" in out

    def test_tool_error_envelope_is_errored(self):
        ar = {
            "conversation": [
                {
                    "role": "tool",
                    "name": "triage_inbox",
                    "content": json.dumps({"ok": False, "error": "backend down"}),
                }
            ]
        }
        out = build_result(
            ar, run_id="r", timestamp="t", model_id="m", total_duration_ms=1
        )
        assert out["status"] == "ERRORED"
        assert out["error"] == "backend down"


class TestRunBenchmarkOffline:
    def test_injected_agent_factory_runs_without_lemonade(self):
        class _StubAgent:
            def process_query(self, prompt):
                return _agent_result()

        results = run_benchmark(
            "Gemma-4-E4B-it-GGUF",
            mbox_path="ignored-when-factory-injected",
            limit=2,
            experiments=2,
            ground_truth=GT,
            agent_factory=_StubAgent,
        )
        assert len(results) == 2
        assert all(r["status"] == "PASS" for r in results)
        assert results[0]["is_cold_start"] is True
        assert results[1]["is_cold_start"] is False


class TestSummarizeBenchmark:
    def test_scorecard_perf_section_populated(self):
        results = [
            build_result(
                _agent_result(),
                run_id=f"r{i}",
                timestamp="t",
                model_id="Gemma-4-E4B-it-GGUF",
                total_duration_ms=2000,
            )
            for i in range(3)
        ]
        summary = summarize_benchmark(results, run_id="bench-run")
        perf = summary["scorecard"]["performance"]
        assert perf["avg_tokens_per_second"] == 50.0
        assert perf["scenarios_with_data"] == 3
        assert "Gemma-4-E4B-it-GGUF" in summary["variance"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
