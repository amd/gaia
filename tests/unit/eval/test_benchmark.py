# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.eval.benchmark (offline — no Lemonade)."""

import json
import os

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

    def test_run_benchmark_restores_triage_ceiling_env(self, monkeypatch):
        """The scan-ceiling override is scoped: run_benchmark must not leak the
        inflated GAIA_EMAIL_TRIAGE_MAX_MESSAGES to the rest of the process."""

        class _StubAgent:
            def process_query(self, prompt):
                return _agent_result()

        # Unset before → unset after (no key materialized).
        monkeypatch.delenv("GAIA_EMAIL_TRIAGE_MAX_MESSAGES", raising=False)
        run_benchmark(
            "Gemma-4-E4B-it-GGUF",
            mbox_path="ignored",
            limit=250,
            experiments=1,
            ground_truth=GT,
            agent_factory=_StubAgent,
        )
        assert "GAIA_EMAIL_TRIAGE_MAX_MESSAGES" not in os.environ

        # Pre-existing value → restored verbatim, not left at the benchmark limit.
        monkeypatch.setenv("GAIA_EMAIL_TRIAGE_MAX_MESSAGES", "100")
        run_benchmark(
            "Gemma-4-E4B-it-GGUF",
            mbox_path="ignored",
            limit=250,
            experiments=1,
            ground_truth=GT,
            agent_factory=_StubAgent,
        )
        assert os.environ["GAIA_EMAIL_TRIAGE_MAX_MESSAGES"] == "100"


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


class TestAcceptanceMetrics:
    """Acceptance metric (#1437) wiring in the benchmark + multi-run variance (#1894)."""

    GT2 = {
        "_meta": {"note": "skip me"},
        "a": {"category": "URGENT", "is_spam": False, "is_phishing": False},
        "b": {"category": "NEEDS_RESPONSE", "is_spam": False, "is_phishing": False},
        "c": {"category": "FYI", "is_spam": False, "is_phishing": False},
        "d": {"category": "PROMOTIONAL", "is_spam": False, "is_phishing": False},
    }

    def _result(self, preds, run_id="r0"):
        envelope = {
            "ok": True,
            "data": {
                "results": [
                    {
                        "id": k,
                        "category": v,
                        "confident": True,
                        "is_spam": False,
                        "is_phishing": False,
                    }
                    for k, v in preds.items()
                ]
            },
        }
        agent_result = {
            "input_tokens": 1000,
            "output_tokens": 200,
            "conversation": [
                {"role": "user", "content": "Triage my inbox"},
                {
                    "role": "tool",
                    "name": "triage_inbox",
                    "content": json.dumps(envelope),
                },
            ],
        }
        return build_result(
            agent_result,
            run_id=run_id,
            timestamp="t",
            model_id="Gemma-4-E4B-it-GGUF",
            total_duration_ms=2000,
            ground_truth=self.GT2,
        )

    def test_build_result_emits_acceptance_fields(self):
        # All exact → within-one 1.0, exact 1.0, urgent-vs-not 1.0, urgent recall 1.0.
        out = self._result(
            {"a": "URGENT", "b": "NEEDS_RESPONSE", "c": "FYI", "d": "PROMOTIONAL"}
        )
        q = out["quality"]
        assert q["within_one_bucket_accuracy"] == 1.0
        assert q["category_accuracy"] == 1.0
        assert q["urgent_vs_not_accuracy"] == 1.0
        assert q["urgent_recall"] == 1.0
        # PERSONAL axis is wired even when the (sub)corpus has no personal rows:
        # the keys exist (honest 0.0 / empty confusion), never silently absent.
        assert "personal_recall" in q
        assert "personal" in q and "recall" in q["personal"]

    def test_within_one_credits_adjacent_not_distance_two(self):
        # a urgent->needs_response (adj ✓), b exact ✓, c fyi->promotional (adj ✓),
        # d promotional->needs_response (dist2 ✗). within-one 3/4 = 0.75; exact 1/4.
        out = self._result(
            {
                "a": "NEEDS_RESPONSE",
                "b": "NEEDS_RESPONSE",
                "c": "PROMOTIONAL",
                "d": "NEEDS_RESPONSE",
            }
        )
        q = out["quality"]
        assert q["within_one_bucket_accuracy"] == 0.75
        assert q["category_accuracy"] == 0.25

    def test_multirun_variance_block(self):
        # within-one per run: 1.0, 0.75, 1.0 → mean 0.9167, stdev>0, n_runs 3.
        runs = [
            self._result(
                {"a": "URGENT", "b": "NEEDS_RESPONSE", "c": "FYI", "d": "PROMOTIONAL"},
                run_id="r1",
            ),
            self._result(
                {
                    "a": "NEEDS_RESPONSE",
                    "b": "NEEDS_RESPONSE",
                    "c": "PROMOTIONAL",
                    "d": "NEEDS_RESPONSE",
                },
                run_id="r2",
            ),
            self._result(
                {"a": "URGENT", "b": "NEEDS_RESPONSE", "c": "FYI", "d": "PROMOTIONAL"},
                run_id="r3",
            ),
        ]
        summary = summarize_benchmark(runs, run_id="bench-run")
        q = summary["quality"]
        assert q["within_one_bucket_accuracy"] == round((1.0 + 0.75 + 1.0) / 3, 4)
        var = q["acceptance_variance"]
        assert var["n_runs"] == 3
        w = var["within_one_bucket_accuracy"]
        assert w["n"] == 3
        assert w["mean"] == round((1.0 + 0.75 + 1.0) / 3, 4)
        assert w["stdev"] > 0.0
        assert w["min"] == 0.75 and w["max"] == 1.0
        # CI band the gate uses must bracket the mean.
        assert w["ci95_low"] <= w["mean"] <= w["ci95_high"]

    def test_single_run_variance_is_degenerate(self):
        runs = [self._result({"a": "URGENT", "b": "NEEDS_RESPONSE"}, run_id="r1")]
        summary = summarize_benchmark(runs, run_id="bench-run")
        w = summary["quality"]["acceptance_variance"]["within_one_bucket_accuracy"]
        assert w["n"] == 1
        assert w["stdev"] == 0.0
        assert w["ci95_half_width"] == 0.0


class TestPerfGateInBenchmark:
    """The perf gate (#1277) added alongside #1278's quality gate.

    The committed manifest is enforcing (#1990); the report-mode cases below
    exercise the gate logic with inline ``enforce=False`` thresholds.
    """

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

    def test_committed_perf_manifest_is_enforcing(self):
        # #1990 contract: the shipped perf manifest is a hard release gate — a
        # missed Strix Halo bar blocks the build (release_agent_email.yml runs
        # it on the stx pool). If the pool proves noisy, widen the bars in the
        # manifest (data) rather than reverting to report mode.
        # Bars calibrated from the first real stx release run (v0.4.0): the prior
        # 5s / 10tps / 300s aspirations false-failed it, so they were widened
        # above observed (TTFT 8.5s, 12.1 tok/s, ~1894s/50-email) with margin.
        assert default_perf_thresholds_path().exists()
        pth = load_default_perf_thresholds()
        assert pth.ttft_max_s == 15.0
        assert pth.throughput_min_tps == 8.0
        assert pth.pipeline_max_s == 2700.0
        assert pth.peak_memory_max_gb == 16.0
        assert pth.enforce is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
