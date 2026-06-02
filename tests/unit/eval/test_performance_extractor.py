# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.eval.performance (perf extractor — no Lemonade)."""

import json

import pytest

from gaia.eval.performance import (
    PerfThresholds,
    RunResult,
    evaluate_perf_gate,
    extract_from_agent_result,
    extract_from_trace_json,
    extract_npu_utilization,
    extract_step_stats,
    load_perf_thresholds,
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
                        "peak_memory_mb": 6000.0,  # best-effort, may be absent
                        "npu_utilization_percent": 72.5,  # NPU telemetry (rare)
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
                        "peak_memory_mb": 6500.0,  # higher peak on second step
                        # no npu key here — extractor must tolerate the gap
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


# ---------------------------------------------------------------------------
# Peak memory (#1277 — exported, gated on Strix Halo)
# ---------------------------------------------------------------------------


class TestPeakMemory:
    def test_peak_memory_is_max_across_steps(self):
        steps, _ = extract_step_stats(_agent_result()["conversation"])
        # step 1 → 6000, step 2 → 6500; the peak is the max, not the last.
        assert steps[0].peak_memory_mb == 6000.0
        assert steps[1].peak_memory_mb == 6500.0

    def test_run_peak_memory_rolls_up_to_max(self):
        run = extract_from_agent_result(
            _agent_result(), run_id="r", timestamp="t", model_id="m"
        )
        assert run.peak_memory_mb == 6500.0  # max across the two steps

    def test_peak_memory_zero_when_absent(self):
        ar = _agent_result()
        for msg in ar["conversation"]:
            if isinstance(msg.get("content"), dict):
                msg["content"].get("performance_stats", {}).pop("peak_memory_mb", None)
        run = extract_from_agent_result(ar, run_id="r", timestamp="t", model_id="m")
        assert run.peak_memory_mb == 0.0  # tolerated gap, not an error

    def test_peak_memory_in_summary_gb(self):
        run = extract_from_agent_result(
            _agent_result(), run_id="r", timestamp="t", model_id="m"
        )
        summary = to_performance_summary(run)
        assert "peak_memory_mb" in summary
        assert summary["peak_memory_mb"] == 6500.0
        # also exposed in GB for the 8 GB Strix Halo bar
        assert summary["peak_memory_gb"] == round(6500.0 / 1024.0, 3)


# ---------------------------------------------------------------------------
# NPU utilization (#1277 — best-effort; unavailable off-NPU, never silent fail)
# ---------------------------------------------------------------------------


class TestNpuUtilization:
    def test_unavailable_when_no_telemetry(self):
        npu = extract_npu_utilization({})
        assert npu.available is False
        assert npu.utilization_percent is None
        d = npu.to_dict()
        assert d["available"] is False
        assert d["utilization_percent"] is None
        assert "unavailable" in d["detail"].lower()

    def test_unavailable_when_stats_is_none(self):
        npu = extract_npu_utilization(None)
        assert npu.available is False
        assert npu.utilization_percent is None

    def test_populated_when_percent_present(self):
        npu = extract_npu_utilization({"npu_utilization_percent": 72.5})
        assert npu.available is True
        assert npu.utilization_percent == 72.5

    def test_accepts_alternate_key(self):
        # Lemonade may expose it under a bare ``npu_utilization`` key.
        npu = extract_npu_utilization({"npu_utilization": 40})
        assert npu.available is True
        assert npu.utilization_percent == 40.0

    def test_reads_from_system_info_devices_block(self):
        sysinfo = {
            "devices": {"amd_npu": {"available": True, "utilization_percent": 55.0}}
        }
        npu = extract_npu_utilization(sysinfo)
        assert npu.available is True
        assert npu.utilization_percent == 55.0

    def test_require_raises_when_absent(self):
        # Off-NPU it's "reported, not gating" — only loud when explicitly required
        # (e.g. a hardware job that *demands* the telemetry).
        with pytest.raises(RuntimeError, match="NPU utilization"):
            extract_npu_utilization({}, require=True)

    def test_run_carries_npu_block(self):
        run = extract_from_agent_result(
            _agent_result(), run_id="r", timestamp="t", model_id="m"
        )
        # step 1 carried npu_utilization_percent=72.5 → harvested onto the run
        assert run.npu.available is True
        assert run.npu.utilization_percent == 72.5
        # serialized into the run dict and the perf summary
        assert run_to_dict(run)["npu"]["utilization_percent"] == 72.5
        assert to_performance_summary(run)["npu"]["available"] is True


# ---------------------------------------------------------------------------
# Perf-threshold gate (#1277 — mirrors #1278's report-mode quality gate)
# ---------------------------------------------------------------------------


def _perf_block(
    *, ttft_s=2.0, tps=25.0, pipeline_s=120.0, peak_gb=6.0, npu=None
) -> dict:
    """A perf summary block as ``to_performance_summary`` would emit it."""
    return {
        "avg_time_to_first_token": ttft_s,
        "avg_tokens_per_second": tps,
        "pipeline_latency_s": pipeline_s,
        "peak_memory_gb": peak_gb,
        "npu": npu or {"available": False, "utilization_percent": None},
    }


_BARS = dict(
    ttft_max_s=5.0,
    throughput_min_tps=10.0,
    pipeline_max_s=300.0,
    peak_memory_max_gb=8.0,
)


class TestPerfGate:
    def test_all_within_bars_passes(self):
        th = PerfThresholds(**_BARS, enforce=False)
        gate = evaluate_perf_gate(_perf_block(), th)
        assert gate["passed"] is True
        assert gate["breaches"] == []
        assert gate["should_fail"] is False

    def test_each_metric_marked_gating_vs_reported(self):
        th = PerfThresholds(**_BARS, enforce=False)
        gate = evaluate_perf_gate(_perf_block(), th)
        metrics = {m["metric"]: m for m in gate["metrics"]}
        # The four bars are gating; NPU is reported-only (no bar).
        assert metrics["time_to_first_token_s"]["gating"] is True
        assert metrics["tokens_per_second"]["gating"] is True
        assert metrics["pipeline_latency_s"]["gating"] is True
        assert metrics["peak_memory_gb"]["gating"] is True
        assert metrics["npu_utilization_percent"]["gating"] is False

    def test_synthetic_breach_flagged_but_not_failing_in_report_mode(self):
        # TTFT 9s blows the 5s bar; gate marks it failed but report mode (the
        # committed posture until the bars are ratified on hardware) never fails.
        th = PerfThresholds(**_BARS, enforce=False)
        gate = evaluate_perf_gate(_perf_block(ttft_s=9.0), th)
        assert gate["passed"] is False
        breached = {b["metric"] for b in gate["breaches"]}
        assert "time_to_first_token_s" in breached
        assert gate["should_fail"] is False  # report mode

    def test_breach_fails_when_enforced(self):
        th = PerfThresholds(**_BARS, enforce=True)
        gate = evaluate_perf_gate(_perf_block(ttft_s=9.0), th)
        assert gate["passed"] is False
        assert gate["should_fail"] is True  # enforce flips it

    def test_throughput_floor_is_a_lower_bound(self):
        # throughput is a MIN bar (below = breach), unlike the MAX bars.
        th = PerfThresholds(**_BARS, enforce=True)
        gate = evaluate_perf_gate(_perf_block(tps=4.0), th)
        breached = {b["metric"] for b in gate["breaches"]}
        assert "tokens_per_second" in breached
        assert gate["should_fail"] is True

    def test_pipeline_and_memory_bars(self):
        th = PerfThresholds(**_BARS, enforce=True)
        gate = evaluate_perf_gate(_perf_block(pipeline_s=400.0, peak_gb=9.0), th)
        breached = {b["metric"] for b in gate["breaches"]}
        assert {"pipeline_latency_s", "peak_memory_gb"} <= breached

    def test_npu_never_breaches_even_when_low(self):
        # NPU is observed-only — no bar, so it can't fail the gate.
        th = PerfThresholds(**_BARS, enforce=True)
        gate = evaluate_perf_gate(
            _perf_block(npu={"available": True, "utilization_percent": 1.0}), th
        )
        assert gate["passed"] is True
        assert gate["should_fail"] is False

    def test_missing_metric_key_raises(self):
        # A perf block missing a gated metric must surface, not silently pass.
        th = PerfThresholds(**_BARS, enforce=True)
        block = _perf_block()
        del block["avg_time_to_first_token"]
        with pytest.raises(ValueError, match="time_to_first_token"):
            evaluate_perf_gate(block, th)


class TestLoadPerfThresholds:
    def test_loads_valid_manifest(self, tmp_path):
        p = tmp_path / "perf.json"
        p.write_text(
            json.dumps(
                {
                    "ttft_max_s": 5.0,
                    "throughput_min_tps": 10.0,
                    "pipeline_max_s": 300.0,
                    "peak_memory_max_gb": 8.0,
                    "enforce": False,
                }
            )
        )
        th = load_perf_thresholds(p)
        assert th.ttft_max_s == 5.0
        assert th.throughput_min_tps == 10.0
        assert th.pipeline_max_s == 300.0
        assert th.peak_memory_max_gb == 8.0
        assert th.enforce is False

    def test_missing_required_key_raises(self, tmp_path):
        p = tmp_path / "perf.json"
        p.write_text(json.dumps({"ttft_max_s": 5.0}))  # missing the rest
        with pytest.raises(ValueError, match="missing required"):
            load_perf_thresholds(p)

    def test_non_numeric_bar_raises(self, tmp_path):
        p = tmp_path / "perf.json"
        p.write_text(
            json.dumps(
                {
                    "ttft_max_s": "fast",  # not numeric
                    "throughput_min_tps": 10.0,
                    "pipeline_max_s": 300.0,
                    "peak_memory_max_gb": 8.0,
                }
            )
        )
        with pytest.raises(ValueError, match="must be numeric"):
            load_perf_thresholds(p)

    def test_non_object_manifest_raises(self, tmp_path):
        p = tmp_path / "perf.json"
        p.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ValueError, match="JSON object"):
            load_perf_thresholds(p)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
