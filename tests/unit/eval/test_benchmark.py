# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.eval.benchmark (offline — no Lemonade)."""

import copy
import json
import os

import pytest

from gaia.eval.benchmark import (
    _extract_tools_called,
    _extract_triage_results,
    _extract_triage_usage,
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
from gaia.eval.quality_metrics import QualityThresholds, compute_cost

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


def _agent_result_with_usage(
    usage=None,
    llm_classified_count=4,
):
    """Deep-copied variant of ``_agent_result()`` whose triage envelope's
    ``data`` also carries ``usage`` + ``llm_classified_count`` (Increment 1
    shape). Never mutates the shared ``TRIAGE_ENVELOPE`` constant."""
    if usage is None:
        usage = {
            "prompt_tokens": 5000,
            "completion_tokens": 800,
            "total_tokens": 5800,
            "tokens_per_second": 40.0,
        }
    ar = _agent_result()
    envelope = copy.deepcopy(TRIAGE_ENVELOPE)
    envelope["data"]["usage"] = usage
    envelope["data"]["llm_classified_count"] = llm_classified_count
    ar["conversation"][2]["content"] = json.dumps(envelope)
    return ar


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


class TestExtractTriageUsage:
    """``_extract_triage_usage`` (Increment 2): sibling walk of
    ``_extract_triage_results`` that reads ``data.usage`` /
    ``data.llm_classified_count`` off the first ok triage envelope."""

    def test_extracts_usage_and_count_from_envelope(self):
        ar = _agent_result_with_usage(
            usage={
                "prompt_tokens": 5000,
                "completion_tokens": 800,
                "total_tokens": 5800,
                "tokens_per_second": 40.0,
            },
            llm_classified_count=4,
        )
        usage, count = _extract_triage_usage(ar["conversation"])
        assert usage == {
            "prompt_tokens": 5000,
            "completion_tokens": 800,
            "total_tokens": 5800,
            "tokens_per_second": 40.0,
        }
        assert count == 4

    def test_plain_envelope_without_usage_returns_none_zero(self):
        # TRIAGE_ENVELOPE (the existing/absence-tolerant shape) carries no
        # usage or llm_classified_count at all.
        ar = _agent_result()
        usage, count = _extract_triage_usage(ar["conversation"])
        assert usage is None
        assert count == 0

    def test_no_triage_envelope_returns_none_zero(self):
        convo = [{"role": "assistant", "content": "I refuse."}]
        usage, count = _extract_triage_usage(convo)
        assert usage is None
        assert count == 0


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


class TestBuildResultUsageMerge:
    """Increment 2: ``build_result`` merges the triage-classify ``usage`` block
    into the run's token totals + adds ``tokens_per_triage`` to
    ``performance_summary``. Absence-tolerant — a plain (no-usage) envelope
    must leave every existing number exactly as today."""

    _USAGE = {
        "prompt_tokens": 5000,
        "completion_tokens": 800,
        "total_tokens": 5800,
        "tokens_per_second": 40.0,
    }

    def _build_with_usage(self, model_id="Gemma-4-E4B-it-GGUF"):
        return build_result(
            _agent_result_with_usage(usage=self._USAGE, llm_classified_count=4),
            run_id="r1",
            timestamp="t",
            model_id=model_id,
            total_duration_ms=2000,
            ground_truth=GT,
        )

    def test_merged_totals_in_performance_summary_and_top_level(self):
        # Fixture note: _agent_result()'s top-level aggregates are
        # input=1000/output=200/total=1200 (preferred over step sums by
        # performance.extract_from_agent_result). Merged with the usage block
        # (prompt=5000/completion=800/total=5800):
        #   total_input_tokens  = 1000 + 5000 = 6000
        #   total_output_tokens =  200 +  800 = 1000
        #   total_tokens        = 1200 + 5800 = 7000... but per the spec the
        #   merge adds (prompt + completion) to total_tokens, i.e. 1200 + 5800
        #   = 7000. However total input+output alone would be 7000 too
        #   (6000+1000); both derivations agree.
        out = self._build_with_usage()
        ps = out["performance_summary"]
        assert ps["total_input_tokens"] == 6000
        assert ps["total_output_tokens"] == 1000
        assert ps["total_tokens"] == 7000
        # run_to_dict output (top-level keys) reflects the same merge.
        assert out["total_input_tokens"] == 6000
        assert out["total_output_tokens"] == 1000
        assert out["total_tokens"] == 7000

    def test_new_performance_summary_fields_exact_values(self):
        out = self._build_with_usage()
        ps = out["performance_summary"]
        assert ps["triage_llm_tokens"] == 5800
        assert ps["llm_classified_count"] == 4
        assert ps["tokens_per_triage"] == 1450.0

    def test_avg_tps_and_ttft_unchanged_by_merge(self):
        # avg_tokens_per_second / avg_time_to_first_token stay outer-turn
        # derived (same values as the no-usage baseline).
        out = self._build_with_usage()
        ps = out["performance_summary"]
        assert ps["avg_tokens_per_second"] == 50.0
        assert ps["avg_time_to_first_token"] == 0.1

    def test_cost_estimate_rises_with_merged_totals(self):
        # Use a priced model so compute_cost isn't 0.0-clamped for local ids.
        out_no_usage = build_result(
            _agent_result(),
            run_id="r0",
            timestamp="t",
            model_id="claude-sonnet-4",
            total_duration_ms=2000,
            ground_truth=GT,
        )
        out_with_usage = self._build_with_usage(model_id="claude-sonnet-4")

        baseline_cost = compute_cost(1000, 200, model="claude-sonnet-4")
        merged_cost = compute_cost(6000, 1000, model="claude-sonnet-4")

        assert out_no_usage["cost_estimate"]["estimated_usd"] == baseline_cost
        assert out_with_usage["cost_estimate"]["estimated_usd"] == merged_cost
        assert merged_cost > baseline_cost

    def test_no_usage_envelope_leaves_new_keys_absent_and_totals_unchanged(self):
        # Plain TRIAGE_ENVELOPE (today's shape, no usage/llm_classified_count).
        out = build_result(
            _agent_result(),
            run_id="r1",
            timestamp="t",
            model_id="Gemma-4-E4B-it-GGUF",
            total_duration_ms=2000,
            ground_truth=GT,
        )
        ps = out["performance_summary"]
        assert "triage_llm_tokens" not in ps
        assert "llm_classified_count" not in ps
        assert "tokens_per_triage" not in ps
        assert ps["total_input_tokens"] == 1000
        assert ps["total_output_tokens"] == 200
        assert ps["total_tokens"] == 1200
        assert out["total_input_tokens"] == 1000
        assert out["total_output_tokens"] == 200
        assert out["total_tokens"] == 1200


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

    def test_errored_row_still_closes_agent_db(self):
        """A ``process_query`` failure must still release the agent's DB —
        the ERRORED-row path (``_close_agent_db``) must run before the
        experiment loop continues (#1892 regression pin)."""

        class _StubAgentWithDb:
            def __init__(self):
                self.close_db_called = False

            def process_query(self, prompt):
                raise RuntimeError("boom")

            def close_db(self):
                self.close_db_called = True

        created = {}

        def _factory():
            agent = _StubAgentWithDb()
            created["agent"] = agent
            return agent

        results = run_benchmark(
            "Gemma-4-E4B-it-GGUF",
            mbox_path="ignored-when-factory-injected",
            limit=2,
            experiments=1,
            ground_truth=GT,
            agent_factory=_factory,
        )

        assert len(results) == 1
        assert results[0]["status"] == "ERRORED"
        assert created["agent"].close_db_called is True

    def test_errored_row_tolerates_agent_with_no_close_db(self):
        """A stub agent with NO ``close_db`` attribute at all must not make
        ``run_benchmark`` raise — the ERRORED row is still returned normally
        (#1892 regression pin)."""

        class _StubAgentNoDb:
            def process_query(self, prompt):
                raise RuntimeError("boom")

        assert not hasattr(_StubAgentNoDb(), "close_db")

        results = run_benchmark(
            "Gemma-4-E4B-it-GGUF",
            mbox_path="ignored-when-factory-injected",
            limit=2,
            experiments=1,
            ground_truth=GT,
            agent_factory=_StubAgentNoDb,
        )

        assert len(results) == 1
        assert results[0]["status"] == "ERRORED"


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

    def test_scorecard_perf_section_carries_triage_usage(self):
        """#1891 gap: build_scorecard's generic performance block has no notion
        of email's triage-usage fields — they must be merged in from each run's
        performance_summary, not silently dropped (live-shape regression pin;
        was found None on real, fully-passing hardware runs)."""
        results = [
            build_result(
                _agent_result_with_usage(),
                run_id=f"r{i}",
                timestamp="t",
                model_id="Gemma-4-E4B-it-GGUF",
                total_duration_ms=2000,
            )
            for i in range(3)
        ]
        # Precondition: the per-run performance_summary DOES carry the fields
        # (build_result's own extraction, tested separately) — this test is
        # about the AGGREGATE scorecard block, not the per-run one.
        assert results[0]["performance_summary"]["triage_llm_tokens"] == 5800
        assert results[0]["performance_summary"]["llm_classified_count"] == 4

        summary = summarize_benchmark(results, run_id="bench-run")
        perf = summary["scorecard"]["performance"]
        assert perf["triage_llm_tokens"] == 5800
        assert perf["llm_classified_count"] == 4
        assert perf["tokens_per_triage"] == 1450.0

    def test_scorecard_perf_section_omits_triage_usage_when_absent(self):
        # Plain TRIAGE_ENVELOPE (no usage/llm_classified_count) must not
        # fabricate the keys — absence is the normal, non-error shape.
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
        assert "triage_llm_tokens" not in perf
        assert "llm_classified_count" not in perf
        assert "tokens_per_triage" not in perf

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

    def test_committed_perf_manifest_is_report_mode(self):
        # Temporarily report mode: the bars aren't validated across runs and TTFT
        # is cold-start-dominated (8.5s then 51s across runs), so an enforcing
        # TTFT bar keeps false-failing the release without a real regression. Same
        # 'ship now, harden later' posture as the drafting/briefing judge gates;
        # re-enforce once the bars are calibrated over several runs. Values are
        # kept as current best-estimate targets.
        assert default_perf_thresholds_path().exists()
        pth = load_default_perf_thresholds()
        assert pth.ttft_max_s == 15.0
        assert pth.throughput_min_tps == 8.0
        assert pth.pipeline_max_s == 2700.0
        assert pth.peak_memory_max_gb == 16.0
        assert pth.enforce is False


class TestCtxSizeEnvelope:
    """16K-target/32K-max ctx-window envelope for the email benchmark (#1892).

    RED-first: none of these kwargs/behaviors exist yet in benchmark.py.
    """

    def test_build_result_stamps_ctx_size(self):
        # TARGET: build_result accepts a ctx_size kwarg and stamps it at the top
        # level of the returned dict. Fails RED today with TypeError (ctx_size
        # isn't a build_result param yet); goes green when the kwarg lands.
        result = build_result(
            _agent_result(),
            run_id="x",
            timestamp="t",
            model_id="m",
            total_duration_ms=100,
            ctx_size=16384,
        )
        assert result["ctx_size"] == 16384

    def test_build_result_omits_ctx_size_when_not_given(self):
        # TARGET: when ctx_size is omitted the key is simply absent (cleanest for
        # downstream JSON — never a null to special-case). Passes today already,
        # and must keep passing once the kwarg is added with a None default.
        result = build_result(
            _agent_result(),
            run_id="x",
            timestamp="t",
            model_id="m",
            total_duration_ms=100,
        )
        assert "ctx_size" not in result

    def test_run_benchmark_accepts_ctx_size_kwarg(self):
        # TARGET: run_benchmark accepts a ctx_size kwarg and stamps the resolved
        # ctx into every returned result (including the agent_factory path, which
        # bypasses EmailAgentConfig construction). Fails RED today with TypeError
        # (no such kwarg); goes green when run_benchmark threads ctx_size into
        # build_result for each experiment.
        class _StubAgent:
            def process_query(self, prompt):
                return _agent_result()

        results = run_benchmark(
            "m",
            mbox_path="ignored",
            ctx_size=16384,
            experiments=1,
            agent_factory=_StubAgent,
        )
        assert results
        assert all(r["ctx_size"] == 16384 for r in results)

    def test_run_benchmark_records_overflow_as_errored_row(self):
        # TARGET (fixed) behavior: a single experiment's process_query raising
        # (e.g. a context-overflow error) must be caught and recorded as one
        # ERRORED row, not abort the whole run. Today there is no try/except
        # around `agent.process_query(prompt)` in run_benchmark, so this test
        # currently fails RED — not with a clean assertion failure, but with the
        # injected RuntimeError propagating out of run_benchmark uncaught. That
        # is the correct (if noisy) red state per the TDD brief for this test.
        calls = {"n": 0}

        class _StubAgent:
            def process_query(self, prompt):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("context size (4096")
                return _agent_result()

        results = run_benchmark(
            "m",
            mbox_path="ignored",
            experiments=3,
            agent_factory=_StubAgent,
        )
        assert len(results) == 3
        assert results[1]["status"] == "ERRORED"
        assert "error" in results[1]
        assert results[0]["status"] != "ERRORED"
        assert results[2]["status"] != "ERRORED"

    def test_run_benchmark_propagates_ctx_readback_errors(self):
        # ASSUMED SEAM: real ctx readback-verification is performed inside
        # EmailTriageAgent.__init__ (owned by a different file/agent in this
        # parallel work split). This test only pins that run_benchmark must NOT
        # swallow that exception — it must propagate it loudly, never silently
        # continue with a mismatched ctx. We simulate the real wiring's failure
        # mode by having the injected agent_factory itself raise, since we
        # cannot drive real EmailTriageAgent construction from this offline
        # slice.
        # Today this fails RED with TypeError (ctx_size isn't a run_benchmark
        # kwarg yet) rather than the intended RuntimeError match — still the
        # correct red state; once ctx_size is wired this assertion pins that
        # the readback RuntimeError propagates through run_benchmark unchanged.
        def _agent_factory():
            raise RuntimeError("ctx readback mismatch: requested=4096 actual=16384")

        with pytest.raises(RuntimeError, match="ctx readback mismatch"):
            run_benchmark(
                "m",
                mbox_path="ignored",
                ctx_size=4096,
                agent_factory=_agent_factory,
            )


class TestCtxSizeOutputStamping:
    """quality.json / scorecard.json ctx_size stamping contract (#1892)."""

    def test_benchmark_outputs_stamp_ctx(self):
        # DESIGN CHOICE (flagged for the implementer): asserting top-level
        # placement of ctx_size on both summary["quality"] and
        # summary["scorecard"] — not nested under "performance". If a nested
        # placement (summary["scorecard"]["performance"]["ctx_size"]) is chosen
        # instead, this assertion needs updating; top-level is preferred because
        # it's the simplest, most-discoverable place for a comparison tool to
        # read the run's ctx envelope from.
        results = [
            build_result(
                _agent_result(),
                run_id=f"r{i}",
                timestamp="t",
                model_id="Gemma-4-E4B-it-GGUF",
                total_duration_ms=2000,
                ground_truth=GT,
            )
            for i in range(2)
        ]
        # Stamp ctx_size onto the canned result dicts directly (build_result
        # doesn't support ctx_size yet — see TestCtxSizeEnvelope above), so this
        # test isolates the summarize_benchmark stamping contract on its own.
        for r in results:
            r["ctx_size"] = 16384

        summary = summarize_benchmark(results, run_id="x")
        assert summary["quality"]["ctx_size"] == 16384
        assert summary["scorecard"]["ctx_size"] == 16384


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
