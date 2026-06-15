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
    default_perf_thresholds_path,
    default_quality_thresholds_path,
    load_default_perf_thresholds,
    load_default_quality_thresholds,
    run_benchmark,
    summarize_benchmark,
)
from gaia.eval.performance import PerfThresholds
from gaia.eval.quality_metrics import QualityThresholds

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


class TestCategorizationExportInResult:
    def test_quality_block_carries_categorization_export(self):
        out = build_result(
            _agent_result(),
            run_id="r1",
            timestamp="t",
            model_id="Gemma-4-E4B-it-GGUF",
            total_duration_ms=2000,
            ground_truth=GT,
        )
        export = out["quality"]["categorization"]
        assert "rows" in export
        assert "false_positives" in export
        assert "false_negatives" in export
        ids = {r["id"] for r in export["rows"]}
        assert ids == {"a", "b"}  # both overlap the labelled GT


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

    def test_quality_gate_block_present_with_thresholds(self):
        results = [
            build_result(
                _agent_result(),
                run_id="r0",
                timestamp="t",
                model_id="Gemma-4-E4B-it-GGUF",
                total_duration_ms=2000,
                ground_truth=GT,
            )
        ]
        th = QualityThresholds(fp_max=0.05, fn_max=0.02, axis="needs_attention")
        summary = summarize_benchmark(results, run_id="bench-run", thresholds=th)
        gate = summary["quality_gate"]
        assert "passed" in gate
        assert "breaches" in gate
        assert gate["enforce"] is False  # report mode
        # aggregate quality across runs is surfaced too
        assert "quality" in summary
        assert "needs_attention" in summary["quality"]

    def test_no_thresholds_means_no_gate_block(self):
        results = [
            build_result(
                _agent_result(),
                run_id="r0",
                timestamp="t",
                model_id="Gemma-4-E4B-it-GGUF",
                total_duration_ms=2000,
                ground_truth=GT,
            )
        ]
        summary = summarize_benchmark(results, run_id="bench-run")
        assert "quality_gate" not in summary

    def test_committed_manifest_loads_in_report_mode(self):
        # The #1112/#1266 contract: the shipped manifest must exist, parse, and
        # default to report mode (enforce=False) until accuracy (#1266) lands.
        assert default_quality_thresholds_path().exists()
        th = load_default_quality_thresholds()
        assert th.fp_max == 0.05
        assert th.fn_max == 0.02
        assert th.axis == "needs_attention"
        assert th.enforce is False

    def test_gate_skipped_when_no_ground_truth(self):
        # No quality block (no GT) → gate can't run; surfaces a clear skip note,
        # never silently invents a pass.
        results = [
            build_result(
                _agent_result(),
                run_id="r0",
                timestamp="t",
                model_id="Gemma-4-E4B-it-GGUF",
                total_duration_ms=2000,
            )
        ]
        th = QualityThresholds(fp_max=0.05, fn_max=0.02, axis="needs_attention")
        summary = summarize_benchmark(results, run_id="bench-run", thresholds=th)
        assert summary["quality_gate"]["skipped"] is True


class TestPerfGateInBenchmark:
    """The perf gate (#1277) added alongside #1278's quality gate — report mode."""

    def _results(self, *, total_duration_ms=2000):
        return [
            build_result(
                _agent_result(),
                run_id="r0",
                timestamp="t",
                model_id="Gemma-4-E4B-it-GGUF",
                total_duration_ms=total_duration_ms,
            )
        ]

    def test_perf_gate_block_present_with_perf_thresholds(self):
        pth = PerfThresholds(
            ttft_max_s=5.0,
            throughput_min_tps=10.0,
            pipeline_max_s=300.0,
            peak_memory_max_gb=8.0,
            enforce=False,
        )
        summary = summarize_benchmark(
            self._results(), run_id="bench-run", perf_thresholds=pth
        )
        gate = summary["perf_gate"]
        assert "passed" in gate
        assert "breaches" in gate
        assert "metrics" in gate  # each metric marked gating-vs-reported
        assert gate["enforce"] is False  # report mode
        assert gate["should_fail"] is False

    def test_perf_gate_uses_run_max_pipeline_latency(self):
        # pipeline latency comes from the (max) wall-clock across runs; a >5min
        # run breaches the 300s bar but report mode does not fail.
        pth = PerfThresholds(
            ttft_max_s=5.0,
            throughput_min_tps=10.0,
            pipeline_max_s=300.0,
            peak_memory_max_gb=8.0,
            enforce=False,
        )
        summary = summarize_benchmark(
            self._results(total_duration_ms=400_000),  # 400s > 300s bar
            run_id="bench-run",
            perf_thresholds=pth,
        )
        breached = {b["metric"] for b in summary["perf_gate"]["breaches"]}
        assert "pipeline_latency_s" in breached
        assert summary["perf_gate"]["should_fail"] is False  # report mode

    def test_no_perf_thresholds_means_no_perf_gate_block(self):
        summary = summarize_benchmark(self._results(), run_id="bench-run")
        assert "perf_gate" not in summary

    def test_perf_and_quality_gates_coexist(self):
        # The two gates are independent and both surface from one summarize call.
        qth = QualityThresholds(fp_max=0.05, fn_max=0.02, axis="needs_attention")
        pth = PerfThresholds(
            ttft_max_s=5.0,
            throughput_min_tps=10.0,
            pipeline_max_s=300.0,
            peak_memory_max_gb=8.0,
            enforce=False,
        )
        results = [
            build_result(
                _agent_result(),
                run_id="r0",
                timestamp="t",
                model_id="Gemma-4-E4B-it-GGUF",
                total_duration_ms=2000,
                ground_truth=GT,
            )
        ]
        summary = summarize_benchmark(
            results, run_id="bench-run", thresholds=qth, perf_thresholds=pth
        )
        assert "quality_gate" in summary
        assert "perf_gate" in summary

    def test_committed_perf_manifest_loads_in_report_mode(self):
        # #1112 contract: the shipped perf manifest must exist, parse, and default
        # to report mode until the Strix Halo bars are ratified on hardware.
        assert default_perf_thresholds_path().exists()
        pth = load_default_perf_thresholds()
        assert pth.ttft_max_s == 5.0
        assert pth.throughput_min_tps == 10.0
        assert pth.pipeline_max_s == 300.0
        assert pth.peak_memory_max_gb == 8.0
        assert pth.enforce is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
