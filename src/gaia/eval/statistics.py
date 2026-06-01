# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Statistical analysis primitives for GAIA eval benchmarks.

Stdlib-only (no numpy/scipy). Provides:
  * ``compute_variance`` — mean/stdev/min/max/CV% + extended percentiles,
    IQR, MAD, and skewness for a list of values.
  * ``compare_runs`` / ``compare_runs_by_model`` — run-to-run deltas and
    variance summaries across repeated benchmark runs.
  * ``mann_whitney_u`` / ``cliffs_delta`` / ``bootstrap_ci`` — non-parametric
    significance + effect-size tests for cross-model comparison.

The run-comparison helpers operate on plain ``list[dict]`` run-result records
(keys such as ``total_duration_ms``, ``total_tokens``, ``category_counts``,
``batch_results``); they never import benchmark or agent code, so they are
reusable by any eval that emits the same record shape.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class RunDelta:
    """+/- delta between two consecutive runs."""

    run_id_a: str  # earlier run
    run_id_b: str  # later run
    delta_duration_ms: int  # B - A
    delta_input_tokens: int
    delta_output_tokens: int
    delta_reasoning_tokens: int
    delta_total_tokens: int
    delta_total_emails: int
    delta_avg_ttft_ms: float = 0.0  # B - A, avg TTFT in milliseconds
    delta_avg_tps: float = 0.0  # B - A, avg tokens per second
    category_deltas: dict[str, int] = field(default_factory=dict)
    # Per-category: count in B - count in A


@dataclass
class BatchDelta:
    """+/- delta for a specific batch between two runs."""

    batch_number: int
    run_id_a: str
    run_id_b: str
    delta_duration_ms: int
    delta_input_tokens: int
    delta_output_tokens: int
    delta_reasoning_tokens: int
    delta_avg_ttft_ms: float = 0.0
    delta_avg_tps: float = 0.0
    delta_email_count: int = 0


@dataclass
class VarianceSummary:
    """Statistical summary across multiple runs."""

    metric: str  # e.g., "total_duration_ms", "total_tokens"
    mean: float
    stdev: float
    min_val: float
    max_val: float
    cv_pct: float  # coefficient of variation (%)
    values: list[float] = field(default_factory=list)
    # Extended percentiles and robust statistics (multi-model support).
    median: float = 0.0
    p25: float = 0.0
    p75: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    iqr: float = 0.0  # interquartile range (p75 - p25)
    mad: float = 0.0  # median absolute deviation
    skewness: float = 0.0
    n: int = 0  # total runs included
    filtered_n: int = 0  # runs after filtering (e.g., cold-start excluded)


@dataclass
class ComparisonReport:
    """Full comparison report across multiple benchmark runs."""

    runs_compared: int
    run_deltas: list[RunDelta] = field(default_factory=list)
    batch_deltas: list[BatchDelta] = field(default_factory=list)
    variance_summaries: list[VarianceSummary] = field(default_factory=list)
    category_stability: dict[str, dict[str, Any]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stdev(values: list[float], mean_val: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean_val) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _cv_pct(_values: list[float], mean_val: float, stdev_val: float) -> float:
    if mean_val == 0:
        return 0.0
    return abs(stdev_val / mean_val) * 100


# ---------------------------------------------------------------------------
# Extended percentile / robust statistic helpers
# ---------------------------------------------------------------------------


def _percentile(values: list[float], p: float) -> float:
    """Compute the p-th percentile (0-100) using linear interpolation."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n == 1:
        return s[0]
    # Linear interpolation (same as numpy default).
    k = (p / 100.0) * (n - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    d0 = s[f] * (c - k)
    d1 = s[c] * (k - f)
    return d0 + d1


def _median(values: list[float]) -> float:
    return _percentile(values, 50)


def _mad(values: list[float]) -> float:
    """Median Absolute Deviation."""
    if not values:
        return 0.0
    med = _median(values)
    return _median([abs(v - med) for v in values])


def _skewness(values: list[float]) -> float:
    """Fisher-Pearson skewness coefficient."""
    n = len(values)
    if n < 3:
        return 0.0
    m = _mean(values)
    s = _stdev(values, m)
    if s == 0:
        return 0.0
    m3 = sum((v - m) ** 3 for v in values) / n
    return m3 / (s**3)


def compute_variance(values: list[float], *, metric_name: str = "") -> VarianceSummary:
    """Compute mean/stdev/min/max/CV% + extended percentiles for a list of values."""
    if not values:
        return VarianceSummary(
            metric=metric_name,
            mean=0.0,
            stdev=0.0,
            min_val=0.0,
            max_val=0.0,
            cv_pct=0.0,
        )
    m = _mean(values)
    s = _stdev(values, m)
    p25 = _percentile(values, 25)
    p75 = _percentile(values, 75)
    return VarianceSummary(
        metric=metric_name,
        mean=round(m, 2),
        stdev=round(s, 2),
        min_val=min(values),
        max_val=max(values),
        cv_pct=round(_cv_pct(values, m, s), 2),
        values=values,
        median=round(_percentile(values, 50), 2),
        p25=round(p25, 2),
        p75=round(p75, 2),
        p95=round(_percentile(values, 95), 2),
        p99=round(_percentile(values, 99), 2),
        iqr=round(p75 - p25, 2),
        mad=round(_mad(values), 2),
        skewness=round(_skewness(values), 4),
        n=len(values),
        filtered_n=len(values),
    )


# ---------------------------------------------------------------------------
# Run-to-run deltas
# ---------------------------------------------------------------------------


def _compute_run_delta(run_a: dict, run_b: dict) -> RunDelta:
    """Compute +/- deltas between two run result dicts."""
    cat_a = run_a.get("category_counts", {})
    cat_b = run_b.get("category_counts", {})
    all_cats = set(cat_a.keys()) | set(cat_b.keys())

    category_deltas = {}
    for cat in sorted(all_cats):
        category_deltas[cat] = cat_b.get(cat, 0) - cat_a.get(cat, 0)

    return RunDelta(
        run_id_a=run_a.get("run_id", "unknown"),
        run_id_b=run_b.get("run_id", "unknown"),
        delta_duration_ms=run_b.get("total_duration_ms", 0)
        - run_a.get("total_duration_ms", 0),
        delta_input_tokens=run_b.get("total_input_tokens", 0)
        - run_a.get("total_input_tokens", 0),
        delta_output_tokens=run_b.get("total_output_tokens", 0)
        - run_a.get("total_output_tokens", 0),
        delta_reasoning_tokens=run_b.get("total_reasoning_tokens", 0)
        - run_a.get("total_reasoning_tokens", 0),
        delta_total_tokens=run_b.get("total_tokens", 0) - run_a.get("total_tokens", 0),
        delta_total_emails=run_b.get("total_emails", 0) - run_a.get("total_emails", 0),
        delta_avg_ttft_ms=run_b.get("avg_time_to_first_token_ms", 0)
        - run_a.get("avg_time_to_first_token_ms", 0),
        delta_avg_tps=run_b.get("avg_tokens_per_second", 0)
        - run_a.get("avg_tokens_per_second", 0),
        category_deltas=category_deltas,
    )


def _compute_batch_deltas(run_a: dict, run_b: dict) -> list[BatchDelta]:
    """Compute per-batch deltas between two runs."""
    deltas = []
    batches_a = {b["batch_number"]: b for b in run_a.get("batch_results", [])}
    batches_b = {b["batch_number"]: b for b in run_b.get("batch_results", [])}
    all_batch_nums = set(batches_a.keys()) | set(batches_b.keys())

    for batch_num in sorted(all_batch_nums):
        ba = batches_a.get(batch_num, {})
        bb = batches_b.get(batch_num, {})
        deltas.append(
            BatchDelta(
                batch_number=batch_num,
                run_id_a=run_a.get("run_id", "unknown"),
                run_id_b=run_b.get("run_id", "unknown"),
                delta_duration_ms=bb.get("duration_ms", 0) - ba.get("duration_ms", 0),
                delta_input_tokens=bb.get("total_input_tokens", 0)
                - ba.get("total_input_tokens", 0),
                delta_output_tokens=bb.get("total_output_tokens", 0)
                - ba.get("total_output_tokens", 0),
                delta_reasoning_tokens=bb.get("total_reasoning_tokens", 0)
                - ba.get("total_reasoning_tokens", 0),
                delta_avg_ttft_ms=bb.get("avg_time_to_first_token_ms", 0)
                - ba.get("avg_time_to_first_token_ms", 0),
                delta_avg_tps=bb.get("avg_tokens_per_second", 0)
                - ba.get("avg_tokens_per_second", 0),
                delta_email_count=len(bb.get("email_results", []))
                - len(ba.get("email_results", [])),
            )
        )
    return deltas


# ---------------------------------------------------------------------------
# Category stability analysis
# ---------------------------------------------------------------------------


def _compute_category_stability(runs: list[dict]) -> dict[str, dict[str, Any]]:
    """Track how category counts vary across runs."""
    all_cats: set[str] = set()
    for run in runs:
        all_cats.update(run.get("category_counts", {}).keys())

    stability = {}
    for cat in sorted(all_cats):
        counts = [run.get("category_counts", {}).get(cat, 0) for run in runs]
        m = _mean(counts)
        s = _stdev(counts, m)
        stability[cat] = {
            "mean": round(m, 2),
            "stdev": round(s, 2),
            "min": min(counts) if counts else 0,
            "max": max(counts) if counts else 0,
            "cv_pct": round(_cv_pct(counts, m, s), 2),
            "counts_per_run": counts,
        }
    return stability


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compare_runs(runs: list[dict]) -> ComparisonReport:
    """Compare multiple benchmark runs and compute variance + deltas.

    Args:
        runs: List of run result dicts (from JSON/JSONL output).

    Returns:
        ComparisonReport with run deltas, batch deltas, and variance summaries.
    """
    if len(runs) < 2:
        # Single run — compute variance summaries with single values.
        report = ComparisonReport(runs_compared=len(runs))
        if runs:
            run = runs[0]
            # Duration metrics: ms → seconds.
            for metric_key in [
                "total_duration_ms",
                "avg_duration_per_email_ms",
            ]:
                val = run.get(metric_key, 0) / 1_000
                report.variance_summaries.append(
                    VarianceSummary(
                        metric=metric_key.replace("_ms", "_s"),
                        mean=val,
                        stdev=0.0,
                        min_val=val,
                        max_val=val,
                        cv_pct=0.0,
                        values=[val],
                    )
                )
            for metric_key in [
                "total_input_tokens",
                "total_output_tokens",
                "total_reasoning_tokens",
                "total_tokens",
                "total_emails",
                "avg_input_tokens_per_email",
                "avg_output_tokens_per_email",
                "avg_total_tokens_per_email",
            ]:
                val = run.get(metric_key, 0)
                report.variance_summaries.append(
                    VarianceSummary(
                        metric=metric_key,
                        mean=val,
                        stdev=0.0,
                        min_val=val,
                        max_val=val,
                        cv_pct=0.0,
                        values=[val],
                    )
                )
            report.category_stability = _compute_category_stability(runs)
            # Single-run LLM escalation %.
            total = 0
            confident = 0
            for batch in run.get("batch_results", []):
                for email in batch.get("email_results", []):
                    total += 1
                    if email.get("confident", False):
                        confident += 1
            pct = (total - confident) / max(total, 1) * 100 if total > 0 else 0.0
            report.variance_summaries.append(
                VarianceSummary(
                    metric="llm_escalation_pct",
                    mean=pct,
                    stdev=0.0,
                    min_val=pct,
                    max_val=pct,
                    cv_pct=0.0,
                    values=[pct],
                )
            )
        return report

    # Compute run-to-run deltas.
    run_deltas = []
    batch_deltas = []
    for i in range(1, len(runs)):
        run_deltas.append(_compute_run_delta(runs[i - 1], runs[i]))
        batch_deltas.extend(_compute_batch_deltas(runs[i - 1], runs[i]))

    # Compute variance summaries across all runs.
    variance_summaries = []

    # Duration metrics: convert ms → seconds before computing variance.
    duration_keys = ["total_duration_ms", "avg_duration_per_email_ms"]
    for key in duration_keys:
        values_ms = [run.get(key, 0) for run in runs]
        values_s = [v / 1_000 for v in values_ms]
        display_key = key.replace("_ms", "_s")
        variance_summaries.append(compute_variance(values_s, metric_name=display_key))

    # Non-duration metrics: pass through as-is.
    for metric_key in [
        "total_input_tokens",
        "total_output_tokens",
        "total_reasoning_tokens",
        "total_tokens",
        "total_emails",
        "avg_input_tokens_per_email",
        "avg_output_tokens_per_email",
        "avg_total_tokens_per_email",
        "avg_time_to_first_token_ms",
        "avg_tokens_per_second",
    ]:
        values = [run.get(metric_key, 0) for run in runs]
        variance_summaries.append(compute_variance(values, metric_name=metric_key))

    # Category stability.
    category_stability = _compute_category_stability(runs)

    # LLM escalation % variance.
    escalation_pcts = []
    for run in runs:
        total = 0
        confident = 0
        for batch in run.get("batch_results", []):
            for email in batch.get("email_results", []):
                total += 1
                if email.get("confident", False):
                    confident += 1
        pct = (total - confident) / max(total, 1) * 100 if total > 0 else 0.0
        escalation_pcts.append(pct)
    if escalation_pcts:
        variance_summaries.append(
            compute_variance(escalation_pcts, metric_name="llm_escalation_pct")
        )

    return ComparisonReport(
        runs_compared=len(runs),
        run_deltas=run_deltas,
        batch_deltas=batch_deltas,
        variance_summaries=variance_summaries,
        category_stability=category_stability,
    )


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------


def print_comparison_report(report: ComparisonReport) -> None:
    """Print a human-readable comparison report to stdout."""
    print(f"\n{'='*70}")
    print("  GAIA Eval Benchmark — Variance Analysis")
    print(f"{'='*70}")
    print(f"  Runs compared: {report.runs_compared}")

    if report.run_deltas:
        print("\n  Run-to-Run Deltas (+/- values):")
        print(f"  {'─'*66}")
        for delta in report.run_deltas:
            sign_dur = "+" if delta.delta_duration_ms >= 0 else ""
            sign_tok = "+" if delta.delta_total_tokens >= 0 else ""
            print(f"  {delta.run_id_a[-14:]} → {delta.run_id_b[-14:]}")
            print(f"    Duration: {sign_dur}{delta.delta_duration_ms}ms")
            print(f"    Tokens:   {sign_tok}{delta.delta_total_tokens}")
            sign_ttft = "+" if delta.delta_avg_ttft_ms >= 0 else ""
            sign_tps = "+" if delta.delta_avg_tps >= 0 else ""
            print(f"    TTFT:     {sign_ttft}{delta.delta_avg_ttft_ms:.1f}ms")
            print(f"    TPS:      {sign_tps}{delta.delta_avg_tps:.1f}")
            if delta.category_deltas:
                for cat, d in sorted(delta.category_deltas.items()):
                    sign = "+" if d >= 0 else ""
                    print(f"    {cat}: {sign}{d}")
            print(f"  {'─'*66}")

    if report.variance_summaries:
        print("\n  Variance Summary (across all runs):")
        print(f"  {'─'*66}")
        for vs in report.variance_summaries:
            line = (
                f"  {vs.metric:<30s}: μ={vs.mean:>10.2f}  "
                f"σ={vs.stdev:>10.2f}  "
                f"min={vs.min_val:>8.2f}  "
                f"max={vs.max_val:>8.2f}  "
                f"CV={vs.cv_pct:>5.1f}%"
            )
            # Extended stats on a second line if n >= 2.
            if vs.n >= 2 and vs.median != 0:
                line += (
                    f"\n  {'':>30s}  median={vs.median:>8.2f}  "
                    f"IQR={vs.iqr:>8.2f}  "
                    f"MAD={vs.mad:>8.2f}  "
                    f"skew={vs.skewness:>6.2f}"
                )
            print(line)

    if report.category_stability:
        print("\n  Category Stability:")
        print(f"  {'─'*66}")
        for cat, stats in sorted(report.category_stability.items()):
            print(
                f"  {cat:<16s}: μ={stats['mean']:.1f}  "
                f"σ={stats['stdev']:.1f}  "
                f"range=[{stats['min']}, {stats['max']}]  "
                f"CV={stats['cv_pct']:.1f}%"
            )

    print(f"{'='*70}\n")


def to_dict(report: ComparisonReport) -> dict[str, Any]:
    """Serialize a ComparisonReport to a JSON-serializable dict."""
    return {
        "runs_compared": report.runs_compared,
        "run_deltas": [
            {
                "run_id_a": d.run_id_a,
                "run_id_b": d.run_id_b,
                "delta_duration_ms": d.delta_duration_ms,
                "delta_input_tokens": d.delta_input_tokens,
                "delta_output_tokens": d.delta_output_tokens,
                "delta_reasoning_tokens": d.delta_reasoning_tokens,
                "delta_total_tokens": d.delta_total_tokens,
                "delta_total_emails": d.delta_total_emails,
                "delta_avg_ttft_ms": round(d.delta_avg_ttft_ms, 1),
                "delta_avg_tps": round(d.delta_avg_tps, 1),
                "category_deltas": d.category_deltas,
            }
            for d in report.run_deltas
        ],
        "batch_deltas": [
            {
                "batch_number": d.batch_number,
                "run_id_a": d.run_id_a,
                "run_id_b": d.run_id_b,
                "delta_duration_ms": d.delta_duration_ms,
                "delta_input_tokens": d.delta_input_tokens,
                "delta_output_tokens": d.delta_output_tokens,
                "delta_reasoning_tokens": d.delta_reasoning_tokens,
                "delta_avg_ttft_ms": round(d.delta_avg_ttft_ms, 1),
                "delta_avg_tps": round(d.delta_avg_tps, 1),
                "delta_email_count": d.delta_email_count,
            }
            for d in report.batch_deltas
        ],
        "variance_summaries": [
            {
                "metric": vs.metric,
                "mean": vs.mean,
                "stdev": vs.stdev,
                "min": vs.min_val,
                "max": vs.max_val,
                "cv_pct": vs.cv_pct,
                "median": vs.median,
                "p25": vs.p25,
                "p75": vs.p75,
                "p95": vs.p95,
                "p99": vs.p99,
                "iqr": vs.iqr,
                "mad": vs.mad,
                "skewness": vs.skewness,
                "n": vs.n,
                "filtered_n": vs.filtered_n,
            }
            for vs in report.variance_summaries
        ],
        "category_stability": report.category_stability,
    }


# ---------------------------------------------------------------------------
# Cross-model grouping
# ---------------------------------------------------------------------------


def compare_runs_by_model(runs: list[dict]) -> dict[str, ComparisonReport]:
    """Group runs by model_id and compute per-model variance reports.

    Args:
        runs: List of run result dicts (from JSON/JSONL output).

    Returns:
        Dict mapping model_id -> ComparisonReport for that model.
    """
    groups: dict[str, list[dict]] = {}
    for run in runs:
        model_id = run.get("model", "unknown")
        groups.setdefault(model_id, []).append(run)

    return {model: compare_runs(group) for model, group in groups.items()}


# ---------------------------------------------------------------------------
# Statistical tests (stdlib-only, no scipy dependency)
# ---------------------------------------------------------------------------


def mann_whitney_u(values_a: list[float], values_b: list[float]) -> tuple[float, float]:
    """Mann-Whitney U test for cross-model comparison.

    Returns (U_statistic, approximate_p_value).
    Uses normal approximation with continuity correction and tie handling.
    Requires at least 2 samples per group.
    """
    n1 = len(values_a)
    n2 = len(values_b)
    if n1 < 2 or n2 < 2:
        return (0.0, 1.0)

    # Rank all values together.
    combined = [(v, 0) for v in values_a] + [(v, 1) for v in values_b]
    combined.sort(key=lambda x: x[0])

    # Assign ranks (handle ties with average rank).
    ranks_a: list[float] = []
    ranks_b: list[float] = []
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) and combined[j][0] == combined[i][0]:
            j += 1
        # Average rank for tied values.
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            if combined[k][1] == 0:
                ranks_a.append(avg_rank)
            else:
                ranks_b.append(avg_rank)
        i = j

    R1 = sum(ranks_a)
    U1 = R1 - n1 * (n1 + 1) / 2.0
    U2 = n1 * n2 - U1
    U = min(U1, U2)

    # Normal approximation for p-value.
    mean_u = n1 * n2 / 2.0
    std_u = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12.0)
    if std_u == 0:
        return (U, 1.0)

    # Continuity correction.
    z = (U - mean_u + 0.5) / std_u if U < mean_u else (U - mean_u - 0.5) / std_u
    # Approximate two-tailed p-value using error function approximation.
    p_value = 2.0 * (1.0 - _normal_cdf(abs(z)))
    return (U, max(0.0, min(1.0, p_value)))


def _normal_cdf(x: float) -> float:
    """Approximation of the standard normal CDF."""
    # Abramowitz and Stegun approximation (error < 7.5e-8).
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return 0.5 * (1.0 + sign * y)


def cliffs_delta(values_a: list[float], values_b: list[float]) -> float:
    """Cliff's delta effect size for cross-model comparison.

    Returns a value in [-1, 1]:
    - |d| < 0.147: negligible
    - 0.147 <= |d| < 0.33: small
    - 0.33 <= |d| < 0.474: medium
    - |d| >= 0.474: large
    """
    if not values_a or not values_b:
        return 0.0

    n1 = len(values_a)
    n2 = len(values_b)
    greater = 0
    ties = 0
    for a in values_a:
        for b in values_b:
            if a > b:
                greater += 1
            elif a == b:
                ties += 1

    return (2.0 * greater + ties) / (n1 * n2) - 1.0


def bootstrap_ci(
    values_a: list[float],
    values_b: list[float],
    *,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    statistic: str = "mean_diff",
) -> tuple[float, float]:
    """Bootstrap confidence interval for the difference between two samples.

    Args:
        values_a: First sample.
        values_b: Second sample.
        n_bootstrap: Number of bootstrap resamples.
        confidence: Confidence level (default 0.95).
        statistic: "mean_diff" or "median_diff".

    Returns:
        (lower_bound, upper_bound) of the confidence interval.
    """
    if len(values_a) < 2 or len(values_b) < 2:
        return (0.0, 0.0)

    boot_stats: list[float] = []
    rng = random.Random(42)  # Reproducible.
    for _ in range(n_bootstrap):
        resample_a = [rng.choice(values_a) for _ in range(len(values_a))]
        resample_b = [rng.choice(values_b) for _ in range(len(values_b))]
        if statistic == "median_diff":
            boot_stats.append(_median(resample_a) - _median(resample_b))
        else:
            boot_stats.append(_mean(resample_a) - _mean(resample_b))

    alpha = (1.0 - confidence) / 2.0
    lower = _percentile(boot_stats, alpha * 100)
    upper = _percentile(boot_stats, (1.0 - alpha) * 100)
    return (round(lower, 4), round(upper, 4))
