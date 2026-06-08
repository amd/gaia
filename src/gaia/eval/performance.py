# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Performance metric extraction for GAIA eval benchmarks.

Harvests per-step latency/throughput/token metrics from the agent's
``conversation`` trace. The base ``Agent`` appends a system message of the
shape ``{"role": "system", "content": {"type": "stats",
"performance_stats": {...}}}`` after each LLM call
(see ``gaia.agents.base.agent``). ``performance_stats`` is whatever Lemonade's
``/stats`` endpoint returns; confirmed live keys (Gemma-4):

    input_tokens, output_tokens, prompt_tokens,
    time_to_first_token (seconds), tokens_per_second, decode_token_times

``total_tokens`` and per-step ``duration`` are NOT in ``/stats`` — they are
tolerated (token total falls back to input+output; run-level wall-clock
duration is supplied by the caller). This module is **domain-free**: it knows
nothing about email triage. Per-email classification (categories, confidence)
is extracted by the domain caller (e.g. ``gaia.eval.benchmark``) and passed in
via ``category_counts`` / ``total_emails``.

Sibling module ``gaia.perf_analysis`` covers the same TTFT/TPS vocabulary via
llama.cpp *log scraping*; this module is the *structured-stats* path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass
class StepResult:
    """A single LLM call in the agent loop with its token/latency cost."""

    step_number: int
    action: str  # "llm_call", "planning", "final_answer"
    tool_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0  # tokens in <thinking> blocks (estimated)
    total_tokens: int = 0
    duration_ms: int = 0
    time_to_first_token_ms: float = 0.0  # TTFT: prompt-send → first token
    tokens_per_second: float = 0.0  # TPS: inference throughput
    peak_memory_mb: float = 0.0  # best-effort; 0.0 when /stats omits it
    status: str = "ok"


@dataclass
class NpuUtilization:
    """Best-effort NPU-utilization snapshot (#1277).

    Lemonade today exposes only *static* NPU detection (``amd_npu.available`` /
    ``name`` / ``driver_version`` / ``power_mode``) — no real-time utilization %.
    So this is captured opportunistically: ``available`` is ``True`` only when a
    utilization value is actually present in the telemetry. Off-NPU (or on a
    Lemonade build without the feed) it is gracefully "unavailable" — never an
    error unless the caller explicitly requires it.
    """

    available: bool = False
    utilization_percent: float | None = None
    source: str = ""  # where the value came from (e.g. "stats", "system-info")
    detail: str = "NPU utilization unavailable (no telemetry from Lemonade)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "utilization_percent": self.utilization_percent,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass
class RunResult:
    """Run-level performance aggregate (domain-free).

    ``category_counts`` / ``total_emails`` are generic and supplied by the
    domain caller; this module never inspects classification output.
    """

    run_id: str
    timestamp: str
    model: str
    mode: str = ""
    step_results: list[StepResult] = field(default_factory=list)
    total_emails: int = 0
    total_duration_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_reasoning_tokens: int = 0
    total_tokens: int = 0
    avg_time_to_first_token_ms: float = 0.0
    avg_tokens_per_second: float = 0.0
    peak_memory_mb: float = 0.0  # max across steps; 0.0 when /stats omits it
    npu: NpuUtilization = field(default_factory=NpuUtilization)
    category_counts: dict[str, int] = field(default_factory=dict)
    is_cold_start: bool = False
    status: str = "ok"
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Keys Lemonade might expose a utilization % under, in priority order.
_NPU_UTILIZATION_KEYS = ("npu_utilization_percent", "npu_utilization")
# Keys a /stats payload might expose peak memory under, in priority order.
_PEAK_MEMORY_KEYS = ("peak_memory_mb", "max_memory_mb", "peak_memory")


def _coerce_float(value: Any) -> float | None:
    """Return ``value`` as a float, or ``None`` if it isn't numeric."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def extract_npu_utilization(
    stats: Mapping[str, Any] | None, *, require: bool = False
) -> NpuUtilization:
    """Best-effort NPU-utilization capture from a stats / system-info dict.

    Reads a utilization % from the top-level ``npu_utilization_percent`` /
    ``npu_utilization`` keys, or from a nested ``devices.amd_npu`` block (the
    shape ``LemonadeClient.get_system_info`` returns). When no value is present
    the result is "unavailable" (``available=False``, ``utilization_percent=None``)
    — this is the expected, non-error state off-NPU or on a Lemonade build
    without the feed.

    ``require=True`` flips that to fail-loud: a missing value raises
    ``RuntimeError`` (for a hardware job that genuinely demands the telemetry).
    Off-hardware the metric is "reported, not gating", so callers leave
    ``require`` at its default.
    """
    if stats:
        for key in _NPU_UTILIZATION_KEYS:
            val = _coerce_float(stats.get(key))
            if val is not None:
                return NpuUtilization(
                    available=True,
                    utilization_percent=val,
                    source="stats",
                    detail=f"NPU utilization {val}% (from /stats key '{key}')",
                )
        devices = stats.get("devices", {})
        if isinstance(devices, Mapping):
            npu_block = devices.get("amd_npu")
            if isinstance(npu_block, Mapping):
                # Inside the already-namespaced amd_npu block the field is
                # commonly the bare ``utilization_percent``.
                for key in (*_NPU_UTILIZATION_KEYS, "utilization_percent"):
                    val = _coerce_float(npu_block.get(key))
                    if val is not None:
                        return NpuUtilization(
                            available=True,
                            utilization_percent=val,
                            source="system-info",
                            detail=(
                                f"NPU utilization {val}% "
                                f"(from system-info devices.amd_npu.{key})"
                            ),
                        )

    if require:
        raise RuntimeError(
            "NPU utilization was required but Lemonade exposed no utilization "
            "value. Checked keys "
            f"{list(_NPU_UTILIZATION_KEYS)} (top-level and devices.amd_npu). "
            "Real-time NPU telemetry needs a Lemonade build/host that emits it "
            "(Ryzen AI box); off-NPU this metric is reported, not gating."
        )
    return NpuUtilization()


def _extract_peak_memory_mb(stats: Mapping[str, Any]) -> float:
    """Best-effort peak-memory (MB) from a /stats dict; 0.0 when absent."""
    for key in _PEAK_MEMORY_KEYS:
        val = _coerce_float(stats.get(key))
        if val is not None:
            return val
    return 0.0


def _extract_reasoning_tokens(text: str) -> int:
    """Estimate reasoning tokens from ``<thinking>`` blocks in assistant text.

    Lemonade's /stats endpoint does not report reasoning tokens separately, so
    we approximate by counting characters inside ``<thinking>...</thinking>``
    blocks at a 1-token ≈ 4-character (BPE) ratio. Returns 0 if no blocks found.
    """
    thinking_blocks = re.findall(r"<thinking>(.*?)</thinking>", text, re.DOTALL)
    if not thinking_blocks:
        return 0
    total_chars = sum(len(b.strip()) for b in thinking_blocks)
    return max(1, total_chars // 4)


def _last_assistant_text(conversation: list, stats_msg: dict) -> str:
    """Return the last assistant message text before a system stats message."""
    try:
        idx = conversation.index(stats_msg)
    except ValueError:
        return ""
    for i in range(idx - 1, -1, -1):
        msg = conversation[i]
        if msg.get("role") == "assistant":
            text = msg.get("content", "")
            if isinstance(text, str):
                return text
            if isinstance(text, list):
                return "".join(b.get("text", "") for b in text if isinstance(b, dict))
    return ""


def extract_step_stats(conversation: list) -> tuple[list[StepResult], int]:
    """Extract per-step ``StepResult`` objects and total reasoning tokens.

    Walks the conversation for ``{"type": "stats"}`` system messages emitted by
    the base agent after each LLM call. Returns
    ``(step_results, total_reasoning_tokens)``.
    """
    step_results: list[StepResult] = []
    step_num = 0
    total_reasoning_tokens = 0
    last_tool_name = ""

    for msg in conversation:
        role = msg.get("role", "")

        # Track the tool name from the most recent tool result.
        if role == "tool" and msg.get("name"):
            last_tool_name = msg["name"]

        # A new assistant turn = new LLM call with no tool attributed yet.
        if role == "assistant":
            last_tool_name = ""
            assistant_text = msg.get("content", "")
            if isinstance(assistant_text, str) and assistant_text:
                reasoning = _extract_reasoning_tokens(assistant_text)
                if reasoning > 0:
                    total_reasoning_tokens += reasoning

        # Per-step stats live in system entries with a dict content.
        if role == "system" and isinstance(msg.get("content"), dict):
            content = msg["content"]
            if content.get("type") == "stats" and "performance_stats" in content:
                stats = content["performance_stats"]
                step_num += 1
                raw_ttft = stats.get("time_to_first_token")
                ttft_ms = float(raw_ttft) * 1000 if raw_ttft else 0.0
                in_tok = stats.get("input_tokens", 0) or 0
                out_tok = stats.get("output_tokens", 0) or 0
                step_results.append(
                    StepResult(
                        step_number=step_num,
                        action="llm_call",
                        tool_name=last_tool_name,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        reasoning_tokens=_extract_reasoning_tokens(
                            _last_assistant_text(conversation, msg)
                        ),
                        total_tokens=(
                            stats.get("total_tokens", 0) or (in_tok + out_tok)
                        ),
                        # /stats has no per-step "duration"; tolerated as 0.
                        duration_ms=int((stats.get("duration", 0) or 0) * 1000),
                        time_to_first_token_ms=ttft_ms,
                        tokens_per_second=float(stats.get("tokens_per_second", 0) or 0),
                        peak_memory_mb=_extract_peak_memory_mb(stats),
                    )
                )

    return step_results, total_reasoning_tokens


def _harvest_npu(conversation: list) -> NpuUtilization:
    """Return the first available NPU reading across the conversation's stats.

    Best-effort: most steps won't carry NPU telemetry, so the first step that
    does wins; if none do, the result is "unavailable" (never raises here —
    ``require`` is a caller-level concern).
    """
    for msg in conversation:
        if msg.get("role") == "system" and isinstance(msg.get("content"), dict):
            content = msg["content"]
            if content.get("type") == "stats" and "performance_stats" in content:
                npu = extract_npu_utilization(content["performance_stats"])
                if npu.available:
                    return npu
    return NpuUtilization()


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------


def extract_from_agent_result(
    agent_result: dict[str, Any],
    *,
    run_id: str,
    timestamp: str,
    model_id: str,
    mode: str = "full",
    total_duration_ms: int = 0,
    category_counts: dict[str, int] | None = None,
    total_emails: int = 0,
    is_cold_start: bool = False,
) -> RunResult:
    """Build a perf ``RunResult`` from a ``process_query()`` result dict.

    Domain metrics (``category_counts`` / ``total_emails``) are supplied by the
    caller — this function only harvests latency/throughput/token stats.

    Args:
        agent_result: dict returned by ``agent.process_query()`` (must carry a
            ``conversation`` list; may carry top-level ``input_tokens`` etc.).
        run_id / timestamp / model_id / mode: run identity.
        total_duration_ms: wall-clock duration measured by the caller.
        category_counts / total_emails: domain classification rollup (optional).
        is_cold_start: whether this was the first run of the model.
    """
    conversation = agent_result.get("conversation", [])
    step_results, total_reasoning_tokens = extract_step_stats(conversation)

    # Prefer top-level aggregates if present, else sum from steps.
    input_tokens = agent_result.get("input_tokens") or sum(
        s.input_tokens for s in step_results
    )
    output_tokens = agent_result.get("output_tokens") or sum(
        s.output_tokens for s in step_results
    )
    total_tokens = agent_result.get("total_tokens") or (input_tokens + output_tokens)

    ttft_vals = [
        s.time_to_first_token_ms for s in step_results if s.time_to_first_token_ms > 0
    ]
    tps_vals = [s.tokens_per_second for s in step_results if s.tokens_per_second > 0]
    avg_ttft = sum(ttft_vals) / len(ttft_vals) if ttft_vals else 0.0
    avg_tps = sum(tps_vals) / len(tps_vals) if tps_vals else 0.0

    # Peak memory is the high-water mark across steps (not the last reading).
    peak_memory_mb = max((s.peak_memory_mb for s in step_results), default=0.0)
    # NPU utilization is best-effort: scan each step's raw /stats for a value.
    npu = _harvest_npu(conversation)

    return RunResult(
        run_id=run_id,
        timestamp=timestamp,
        model=model_id,
        mode=mode,
        step_results=step_results,
        total_emails=total_emails,
        total_duration_ms=total_duration_ms,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_reasoning_tokens=total_reasoning_tokens,
        total_tokens=total_tokens,
        avg_time_to_first_token_ms=round(avg_ttft, 1),
        avg_tokens_per_second=round(avg_tps, 1),
        peak_memory_mb=peak_memory_mb,
        npu=npu,
        category_counts=dict(category_counts or {}),
        is_cold_start=is_cold_start,
        status="ok",
    )


def to_performance_summary(run: RunResult) -> dict[str, Any]:
    """Render a ``RunResult`` into the per-scenario ``performance_summary`` dict
    that ``gaia.eval.scorecard.build_scorecard`` aggregates.

    Throughput/TTFT come straight from the harvested steps; pipeline latency is
    the caller-measured wall clock (exposed in seconds for the <5min bar). Peak
    memory is the max across steps (exposed in GB for the <8GB bar). NPU is the
    best-effort utilization block. ``flags`` carries gating-vs-reported notes.
    """
    return {
        "avg_tokens_per_second": run.avg_tokens_per_second,
        "avg_time_to_first_token": round(run.avg_time_to_first_token_ms / 1000.0, 4),
        "avg_time_to_first_token_ms": run.avg_time_to_first_token_ms,
        "total_input_tokens": run.total_input_tokens,
        "total_output_tokens": run.total_output_tokens,
        "total_tokens": run.total_tokens,
        "total_duration_ms": run.total_duration_ms,
        "pipeline_latency_s": round(run.total_duration_ms / 1000.0, 3),
        "peak_memory_mb": run.peak_memory_mb,
        "peak_memory_gb": round(run.peak_memory_mb / 1024.0, 3),
        "npu": run.npu.to_dict(),
        "total_emails": run.total_emails,
        "steps": len(run.step_results),
        "flags": [],
    }


def run_to_dict(run: RunResult) -> dict[str, Any]:
    """Serialize a ``RunResult`` (with its steps) to a JSON-serializable dict."""
    return {
        "run_id": run.run_id,
        "timestamp": run.timestamp,
        "model": run.model,
        "mode": run.mode,
        "total_emails": run.total_emails,
        "total_duration_ms": run.total_duration_ms,
        "total_input_tokens": run.total_input_tokens,
        "total_output_tokens": run.total_output_tokens,
        "total_reasoning_tokens": run.total_reasoning_tokens,
        "total_tokens": run.total_tokens,
        "avg_time_to_first_token_ms": run.avg_time_to_first_token_ms,
        "avg_tokens_per_second": run.avg_tokens_per_second,
        "peak_memory_mb": run.peak_memory_mb,
        "npu": run.npu.to_dict(),
        "category_counts": run.category_counts,
        "is_cold_start": run.is_cold_start,
        "status": run.status,
        "error": run.error,
        "step_results": [
            {
                "step_number": s.step_number,
                "action": s.action,
                "tool_name": s.tool_name,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "reasoning_tokens": s.reasoning_tokens,
                "total_tokens": s.total_tokens,
                "duration_ms": s.duration_ms,
                "time_to_first_token_ms": s.time_to_first_token_ms,
                "tokens_per_second": s.tokens_per_second,
                "peak_memory_mb": s.peak_memory_mb,
                "status": s.status,
            }
            for s in run.step_results
        ],
    }


def extract_from_trace_json(
    trace_path: str,
    *,
    run_id: str,
    timestamp: str,
    model_id: str,
    mode: str = "full",
    total_duration_ms: int = 0,
) -> RunResult:
    """Load a ``--trace`` JSON file and extract a perf ``RunResult``.

    Convenience wrapper for post-hoc perf analysis of any saved agent run.
    Raises ``FileNotFoundError`` / ``json.JSONDecodeError`` loudly on bad input.
    """
    with open(trace_path, "r", encoding="utf-8") as f:
        agent_result = json.load(f)
    return extract_from_agent_result(
        agent_result,
        run_id=run_id,
        timestamp=timestamp,
        model_id=model_id,
        mode=mode,
        total_duration_ms=total_duration_ms,
    )


# ---------------------------------------------------------------------------
# Configurable perf-threshold gate (#1277 — report mode; #1112 flips enforce)
# ---------------------------------------------------------------------------
#
# Mirrors gaia.eval.quality_metrics' FP/FN gate exactly: the bars + the single
# ``enforce`` switch live in ONE committed manifest (data, not code), the gate
# computes pass/fail + the exact breaches, and ``should_fail`` (= enforce and
# not passed) is the only hook CI (#1112) keys off. Report mode (enforce=False,
# the committed posture until the Strix Halo bars are ratified on hardware) never
# fails the harness.


@dataclass
class PerfThresholds:
    """The Strix Halo perf bars (#1277), plus the single enforce switch.

    Three MAX bars (TTFT, pipeline latency, peak memory) and one MIN bar
    (throughput). ``enforce`` ships ``False`` (report mode): the gate computes +
    reports but never fails the harness until the bars are confirmed on hardware
    (#1112 flips it in the manifest). NPU utilization is intentionally absent —
    it has no committed bar and is *reported, not gating*.
    """

    ttft_max_s: float
    throughput_min_tps: float
    pipeline_max_s: float
    peak_memory_max_gb: float
    enforce: bool = False


_REQUIRED_PERF_KEYS = (
    "ttft_max_s",
    "throughput_min_tps",
    "pipeline_max_s",
    "peak_memory_max_gb",
)


def load_perf_thresholds(path: str | Path) -> PerfThresholds:
    """Load the perf-gate thresholds manifest (loud on missing/malformed).

    The manifest is the ONE place the bars + ``enforce`` live, so CI (#1112)
    flips enforcement by editing data, not code. Missing required keys, or
    non-numeric bars, raise ``ValueError`` — there is no silent
    default-to-permissive.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"perf-gate thresholds manifest at {path} must be a JSON object, "
            f"got {type(data).__name__}."
        )
    missing = [k for k in _REQUIRED_PERF_KEYS if k not in data]
    if missing:
        raise ValueError(
            f"perf-gate thresholds manifest at {path} is missing required "
            f"key(s) {missing}. Required: {list(_REQUIRED_PERF_KEYS)}; "
            "optional: 'enforce' (default false)."
        )
    for k in _REQUIRED_PERF_KEYS:
        if not isinstance(data[k], (int, float)) or isinstance(data[k], bool):
            raise ValueError(
                f"perf-gate threshold '{k}' in {path} must be numeric, got "
                f"{data[k]!r}."
            )
    return PerfThresholds(
        ttft_max_s=float(data["ttft_max_s"]),
        throughput_min_tps=float(data["throughput_min_tps"]),
        pipeline_max_s=float(data["pipeline_max_s"]),
        peak_memory_max_gb=float(data["peak_memory_max_gb"]),
        enforce=bool(data.get("enforce", False)),
    )


def _require_metric(perf: Mapping[str, Any], key: str) -> float:
    """Pull a numeric metric out of a perf block, failing loud if absent."""
    if key not in perf:
        raise ValueError(
            f"perf gate input is missing '{key}' (have: {sorted(perf.keys())}). "
            "The benchmark must emit this metric before the gate can score it."
        )
    val = _coerce_float(perf[key])
    if val is None:
        raise ValueError(
            f"perf gate metric '{key}' must be numeric, got {perf[key]!r}."
        )
    return val


def evaluate_perf_gate(
    perf: Mapping[str, Any], thresholds: PerfThresholds
) -> dict[str, Any]:
    """Score a perf summary block against the Strix Halo bars (#1277).

    ``perf`` is the per-run / aggregate block produced by the benchmark — the
    shape :func:`to_performance_summary` emits: ``avg_time_to_first_token`` (s),
    ``avg_tokens_per_second``, ``pipeline_latency_s``, ``peak_memory_gb``, and a
    best-effort ``npu`` dict. Returns a structured gate result:

    * ``metrics`` — every metric with its value, bar, direction, pass flag, and
      an explicit ``gating`` boolean (the four bars are gating; NPU is reported).
    * ``breaches`` — the gating metrics that missed their bar.
    * ``passed`` — no gating breach.
    * ``should_fail`` — ``enforce and not passed`` (the hook #1112 keys off).
      Report mode (``enforce=False``) is always ``should_fail=False`` even on a
      breach: the machinery runs, CI does not block.

    Fail-loud: a missing *gating* metric raises ``ValueError`` — a gate that
    can't find its inputs must not silently report a pass. The NPU metric is
    reported-only and tolerated when absent (off-NPU is the normal case).
    """
    ttft = _require_metric(perf, "avg_time_to_first_token")
    tps = _require_metric(perf, "avg_tokens_per_second")
    pipeline_s = _require_metric(perf, "pipeline_latency_s")
    peak_gb = _require_metric(perf, "peak_memory_gb")

    # (metric label, value, bar, direction, gating). direction: "max" → value
    # must be <= bar; "min" → value must be >= bar.
    specs = [
        ("time_to_first_token_s", ttft, thresholds.ttft_max_s, "max", True),
        ("tokens_per_second", tps, thresholds.throughput_min_tps, "min", True),
        ("pipeline_latency_s", pipeline_s, thresholds.pipeline_max_s, "max", True),
        ("peak_memory_gb", peak_gb, thresholds.peak_memory_max_gb, "max", True),
    ]

    metrics: list[dict[str, Any]] = []
    breaches: list[dict[str, Any]] = []
    for name, value, bar, direction, gating in specs:
        within = value <= bar if direction == "max" else value >= bar
        metrics.append(
            {
                "metric": name,
                "value": value,
                "bar": bar,
                "direction": direction,
                "gating": gating,
                "passed": within,
            }
        )
        if gating and not within:
            breaches.append(
                {"metric": name, "value": value, "bar": bar, "direction": direction}
            )

    # NPU utilization is reported, never gating — surface it without a bar.
    npu_block = perf.get("npu")
    npu_value = (
        _coerce_float(npu_block.get("utilization_percent"))
        if isinstance(npu_block, Mapping)
        else None
    )
    metrics.append(
        {
            "metric": "npu_utilization_percent",
            "value": npu_value,
            "bar": None,
            "direction": "reported",
            "gating": False,
            "passed": True,  # no bar → cannot breach
        }
    )

    passed = not breaches
    return {
        "metrics": metrics,
        "breaches": breaches,
        "passed": passed,
        "enforce": thresholds.enforce,
        # The hook CI (#1112) keys off: report mode never fails the harness.
        "should_fail": thresholds.enforce and not passed,
    }
