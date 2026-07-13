# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Email-triage throughput benchmark (issue #1233).

Direct-drives the ``EmailTriageAgent`` over the committed synthetic corpus via
the documented eval injection seam (``EmailAgentConfig(gmail_backend=...)``),
harvests latency/throughput via :mod:`gaia.eval.performance`, scores quality via
:mod:`gaia.eval.quality_metrics`, and emits per-run result dicts whose
``performance_summary`` matches the contract that
:func:`gaia.eval.scorecard.build_scorecard` already aggregates — so the existing
scorecard / summary / JUnit renderers are reused unchanged. Variance across
repeated runs is computed with :mod:`gaia.eval.statistics`.

This is a read-only harness over an unchanged agent: it changes no LLM-affecting
product path.

Fail-loud contract: tool-result envelopes that *look* like JSON but don't parse
raise an actionable ``ValueError`` (the upstream fork swallowed these). Genuine
non-envelope tool output is skipped — that is not an error, just "not the
message we're looking for".
"""

from __future__ import annotations

import json
import os
import statistics as _stats
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from gaia.eval import performance, quality_metrics
from gaia.eval.fixture_paths import resolve_repo_fixture

# Metrics whose run-to-run spread is recorded for trustworthiness (#1894). The
# first is the gated aggregate (within-one-bucket); the rest are reported.
_ACCEPTANCE_SERIES_KEYS = (
    "within_one_bucket_accuracy",
    "urgent_vs_not_accuracy",
    "urgent_recall",
    "personal_recall",
    "category_accuracy",
)

# Committed throughput bar for #1233 (non-gating for the demo).
THROUGHPUT_BAR_TPS = 10.0
# Snappy-UX stretch target (printed, never asserted).
THROUGHPUT_STRETCH_TPS = 30.0


# ---------------------------------------------------------------------------
# Envelope parsing (fail-loud)
# ---------------------------------------------------------------------------


def _normalize_agent_result(agent_result: object) -> dict[str, Any]:
    """Coerce a ``process_query`` return into a dict, failing loud on garbage."""
    if isinstance(agent_result, dict):
        return agent_result
    if isinstance(agent_result, str):
        try:
            parsed = json.loads(agent_result)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"agent result is a string but not valid JSON: {exc}; "
                f"snippet: {agent_result[:200]!r}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"agent result JSON decoded to {type(parsed).__name__}, expected object"
            )
        return parsed
    raise TypeError(
        f"unsupported agent_result type {type(agent_result).__name__}; "
        "expected dict or JSON string"
    )


def _maybe_parse_tool_envelope(content: object) -> dict | None:
    """Parse a tool message's content as a JSON envelope.

    Returns the parsed dict, or ``None`` when the content is plainly not an
    envelope (empty, or not starting with ``{``/``[``). Raises ``ValueError``
    when the content *looks* like JSON but fails to parse — a malformed tool
    envelope is a real defect and must surface, not be swallowed.
    """
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    elif isinstance(content, str):
        text = content
    else:
        return None

    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None  # not an envelope — skip, not an error
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"malformed tool-result JSON envelope: {exc}; "
            f"payload snippet: {stripped[:200]!r}"
        ) from exc


def _extract_triage_results(conversation: list) -> tuple[list[dict], str]:
    """Find the triage tool result in the conversation.

    Returns ``(results, error)``. Raises ``ValueError`` on a malformed envelope
    (via :func:`_maybe_parse_tool_envelope`).
    """
    for msg in conversation:
        if msg.get("role") != "tool" or not msg.get("content"):
            continue
        envelope = _maybe_parse_tool_envelope(msg["content"])
        if not isinstance(envelope, dict):
            continue
        data = envelope.get("data")
        if envelope.get("ok") and isinstance(data, dict) and "results" in data:
            return data["results"], ""
        if envelope.get("ok") is False and "error" in envelope:
            return [], str(envelope["error"])
    return [], ""


def _extract_tools_called(agent_result: dict) -> list[str]:
    """Ordered, de-duplicated list of tool names used in the conversation."""
    seen: set[str] = set()
    ordered: list[str] = []
    for msg in agent_result.get("conversation", []):
        role = msg.get("role")
        name = ""
        if role == "assistant" and isinstance(msg.get("content"), dict):
            name = msg["content"].get("tool", "")
        elif role == "tool":
            name = msg.get("name", "")
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


# ---------------------------------------------------------------------------
# Result assembly (pure — offline-testable)
# ---------------------------------------------------------------------------


def build_result(
    agent_result: object,
    *,
    run_id: str,
    timestamp: str,
    model_id: str,
    total_duration_ms: int,
    ground_truth: dict[str, dict] | None = None,
    is_cold_start: bool = False,
) -> dict[str, Any]:
    """Assemble a single benchmark result dict from a ``process_query`` return.

    Pure and offline-testable (no Lemonade): feed a canned ``agent_result``.
    The output is ``build_scorecard``-compatible (``status`` / ``category`` /
    ``performance_summary`` / ``cost_estimate``) and carries the perf RunResult
    plus an optional ``quality`` block when ``ground_truth`` is provided.
    """
    result_dict = _normalize_agent_result(agent_result)
    conversation = result_dict.get("conversation", [])

    triage_results, tool_error = _extract_triage_results(conversation)
    predicted_categories = {
        r.get("id", ""): r.get("category", "") for r in triage_results if r.get("id")
    }
    category_counts: dict[str, int] = {}
    for cat in predicted_categories.values():
        if cat:
            category_counts[cat] = category_counts.get(cat, 0) + 1

    perf_run = performance.extract_from_agent_result(
        result_dict,
        run_id=run_id,
        timestamp=timestamp,
        model_id=model_id,
        mode="full",
        total_duration_ms=total_duration_ms,
        category_counts=category_counts,
        total_emails=len(triage_results),
        is_cold_start=is_cold_start,
    )

    out = performance.run_to_dict(perf_run)
    out["tools_called"] = _extract_tools_called(result_dict)
    out["performance_summary"] = performance.to_performance_summary(perf_run)
    out["cost_estimate"] = {
        "estimated_usd": quality_metrics.compute_cost(
            perf_run.total_input_tokens,
            perf_run.total_output_tokens,
            model=model_id,
        )
    }
    out["meets_throughput_bar"] = perf_run.avg_tokens_per_second >= THROUGHPUT_BAR_TPS

    # build_scorecard-compatible fields. category groups the scorecard by model.
    out["category"] = model_id
    if tool_error:
        out["status"] = "ERRORED"
        out["error"] = tool_error
    elif triage_results:
        out["status"] = "PASS"
    else:
        out["status"] = "FAIL"
        out["error"] = "no triage results found in agent conversation"

    if ground_truth is not None:
        predicted_spam = {
            r.get("id", ""): bool(r.get("is_spam", False))
            for r in triage_results
            if r.get("id")
        }
        predicted_phishing = {
            r.get("id", ""): bool(r.get("is_phishing", False))
            for r in triage_results
            if r.get("id")
        }
        # Acceptance metrics (#1437): within-one-bucket is the gated aggregate;
        # urgent-vs-not + urgent-recall + exact category_accuracy are reported.
        # One source of truth so the scorecard adapter never recomputes these
        # a different way.
        acceptance = quality_metrics.acceptance_metrics(
            predicted_categories, ground_truth
        )
        out["quality"] = {
            "category_accuracy": acceptance["category_accuracy"],
            "within_one_bucket_accuracy": acceptance["within_one_bucket_accuracy"],
            "urgent_vs_not_accuracy": acceptance["urgent_vs_not_accuracy"],
            "urgent_recall": acceptance["urgent_recall"],
            "personal_recall": acceptance["personal_recall"],
            # PERSONAL-vs-rest axis (#1437 PERSONAL coverage) — same shape as
            # needs_attention, so a personal-mail floor can gate it later.
            "personal": quality_metrics.confusion_for_categories(
                predicted_categories,
                ground_truth,
                quality_metrics.PERSONAL_CATEGORIES,
            ).to_dict(),
            "spam": quality_metrics.confusion_for_flag(
                predicted_spam, ground_truth, "is_spam"
            ).to_dict(),
            "phishing": quality_metrics.confusion_for_flag(
                predicted_phishing, ground_truth, "is_phishing"
            ).to_dict(),
            # Needs-attention axis — the axis #1278's FP/FN gate scores.
            "needs_attention": quality_metrics.confusion_for_categories(
                predicted_categories,
                ground_truth,
                quality_metrics.NEEDS_ATTENTION_CATEGORIES,
            ).to_dict(),
            # Per-email categorization log: predicted-vs-expected + FP/FN ids
            # (#1278: "logs/exports categorization results, FP, FN").
            "categorization": quality_metrics.categorization_export(
                predicted_categories, ground_truth
            ),
        }

    return out


def _aggregate_quality(results: list[dict]) -> dict[str, Any] | None:
    """Sum per-run confusion across runs into one aggregate ``quality`` block.

    Confusion counts are additive, so the corpus-level FP/FN rate is computed by
    summing tp/fp/fn/tn over every run that scored quality, then re-deriving the
    rates via :class:`~gaia.eval.quality_metrics.Confusion`. Returns ``None`` when
    no run carried a quality block (no ground truth) — the gate keys off that.
    """
    axes = ("spam", "phishing", "needs_attention", "personal")
    totals = {axis: quality_metrics.Confusion() for axis in axes}
    # Per-run scalar series for each accuracy metric (mean across runs is the
    # aggregate; the series feeds the variance/CI block in summarize_benchmark).
    series_keys = _ACCEPTANCE_SERIES_KEYS
    series: dict[str, list[float]] = {k: [] for k in series_keys}
    saw_quality = False

    for r in results:
        q = r.get("quality")
        if not isinstance(q, dict):
            continue
        saw_quality = True
        for key in series_keys:
            if isinstance(q.get(key), (int, float)):
                series[key].append(float(q[key]))
        for axis in axes:
            block = q.get(axis)
            if isinstance(block, dict):
                c = totals[axis]
                c.tp += int(block.get("tp", 0))
                c.fp += int(block.get("fp", 0))
                c.fn += int(block.get("fn", 0))
                c.tn += int(block.get("tn", 0))

    if not saw_quality:
        return None

    out: dict[str, Any] = {axis: totals[axis].to_dict() for axis in axes}
    for key in series_keys:
        vals = series[key]
        out[key] = round(sum(vals) / len(vals), 4) if vals else 0.0
    # Keep the per-run series so the scorecard can record run-to-run variance/CI
    # (#1894). Mean-of-scalars (above) is the aggregate; this is its spread.
    out["per_run"] = {k: series[k] for k in series_keys}
    return out


def _accuracy_variance(values: list[float]) -> dict[str, Any]:
    """Run-to-run spread of one accuracy metric (#1894).

    Records mean/stdev/min/max/CV% plus a normal-approximation 95% CI on the mean
    (half-width = 1.96·stdev/√n). Computed at full float precision here rather than
    via :func:`gaia.eval.statistics.compute_variance` (which rounds to 2 d.p. for
    ms/token magnitudes — too coarse for a [0,1] accuracy the gate's ±k·stdev band
    keys off). For n<2 there is no spread: stdev/CI are 0 and ``n`` flags it. The
    normal approximation is acknowledged-coarse at small n; the honest signal is
    mean ± stdev and the observed [min, max].
    """
    n = len(values)
    if n == 0:
        return {
            "n": 0,
            "mean": 0.0,
            "stdev": 0.0,
            "min": 0.0,
            "max": 0.0,
            "cv_pct": 0.0,
            "ci95_half_width": 0.0,
            "ci95_low": 0.0,
            "ci95_high": 0.0,
        }
    mean = sum(values) / n
    stdev = _stats.stdev(values) if n >= 2 else 0.0
    half = 1.96 * stdev / (n**0.5) if n >= 2 else 0.0
    cv = (stdev / mean * 100.0) if mean else 0.0
    return {
        "n": n,
        "mean": round(mean, 4),
        "stdev": round(stdev, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "cv_pct": round(cv, 2),
        "ci95_half_width": round(half, 4),
        "ci95_low": round(mean - half, 4),
        "ci95_high": round(mean + half, 4),
    }


def _acceptance_variance(aggregate_quality: dict) -> dict[str, Any]:
    """Variance/CI block over the per-run acceptance series (#1894).

    Keys off the ``per_run`` series :func:`_aggregate_quality` attached. The
    primary metric (within-one-bucket) is the gated aggregate, so its spread is
    what the variance-aware gate consumes; the others are reported.
    """
    per_run = aggregate_quality.get("per_run", {})
    block: dict[str, Any] = {
        "n_runs": max(
            (len(per_run.get(k, [])) for k in _ACCEPTANCE_SERIES_KEYS), default=0
        )
    }
    for k in _ACCEPTANCE_SERIES_KEYS:
        block[k] = _accuracy_variance([float(v) for v in per_run.get(k, [])])
    return block


def _aggregate_perf(results: list[dict]) -> dict[str, Any] | None:
    """Roll per-run ``performance_summary`` blocks into one perf block for the gate.

    Pipeline latency and peak memory take the *worst* (max) reading across runs —
    a perf gate must catch the slowest / heaviest run, not an average that hides
    it. TTFT and throughput are averaged across runs that harvested them. NPU is
    the first available reading (best-effort; most runs carry none). Returns
    ``None`` when no run carried a perf summary — the gate keys off that.

    The output shape matches what :func:`performance.to_performance_summary`
    emits, so :func:`performance.evaluate_perf_gate` scores it unchanged.
    """
    ttft_vals: list[float] = []
    tps_vals: list[float] = []
    max_pipeline_s = 0.0
    max_peak_gb = 0.0
    npu: dict[str, Any] | None = None
    saw_perf = False

    for r in results:
        ps = r.get("performance_summary")
        if not isinstance(ps, dict):
            continue
        saw_perf = True
        ttft = ps.get("avg_time_to_first_token")
        if isinstance(ttft, (int, float)) and ttft > 0:
            ttft_vals.append(float(ttft))
        tps = ps.get("avg_tokens_per_second")
        if isinstance(tps, (int, float)) and tps > 0:
            tps_vals.append(float(tps))
        pipeline_s = ps.get("pipeline_latency_s")
        if isinstance(pipeline_s, (int, float)):
            max_pipeline_s = max(max_pipeline_s, float(pipeline_s))
        peak_gb = ps.get("peak_memory_gb")
        if isinstance(peak_gb, (int, float)):
            max_peak_gb = max(max_peak_gb, float(peak_gb))
        block = ps.get("npu")
        if npu is None and isinstance(block, dict) and block.get("available"):
            npu = block

    if not saw_perf:
        return None

    return {
        "avg_time_to_first_token": (
            round(sum(ttft_vals) / len(ttft_vals), 4) if ttft_vals else 0.0
        ),
        "avg_tokens_per_second": (
            round(sum(tps_vals) / len(tps_vals), 1) if tps_vals else 0.0
        ),
        "pipeline_latency_s": round(max_pipeline_s, 3),
        "peak_memory_gb": round(max_peak_gb, 3),
        "npu": npu or {"available": False, "utilization_percent": None},
    }


def summarize_benchmark(
    results: list[dict],
    *,
    run_id: str,
    thresholds: "quality_metrics.QualityThresholds | None" = None,
    perf_thresholds: "performance.PerfThresholds | None" = None,
) -> dict[str, Any]:
    """Aggregate per-run results into a scorecard + per-model variance report.

    Reuses :func:`gaia.eval.scorecard.build_scorecard` (perf section) and
    :func:`gaia.eval.statistics.compare_runs_by_model` (variance) — no new perf
    aggregation logic. When any run scored quality, an aggregate ``quality``
    block (corpus-level confusion across runs) is added. When ``thresholds`` is
    given, the configurable FP/FN gate (#1278) runs against that aggregate and a
    ``quality_gate`` block is added. When ``perf_thresholds`` is given, the
    Strix Halo perf gate (#1277) runs against the aggregate perf block and a
    ``perf_gate`` block is added. Both gates ship in report mode unless their
    manifest sets ``enforce``; they are the machinery #1112 consumes and never
    fail here.
    """
    from gaia.eval.scorecard import build_scorecard
    from gaia.eval.statistics import compare_runs_by_model
    from gaia.eval.statistics import to_dict as variance_to_dict

    scorecard = build_scorecard(
        run_id, results, {"benchmark": "email_triage_throughput"}
    )
    variance = {
        model: variance_to_dict(report)
        for model, report in compare_runs_by_model(results).items()
    }
    summary: dict[str, Any] = {"scorecard": scorecard, "variance": variance}

    aggregate_quality = _aggregate_quality(results)
    if aggregate_quality is not None:
        # Multi-run variance/CI on the acceptance metrics (#1894). Additive — the
        # gate keys off aggregate.value (the mean); this records its spread so a
        # noisy single draw can't masquerade as a regression.
        aggregate_quality["acceptance_variance"] = _acceptance_variance(
            aggregate_quality
        )
        summary["quality"] = aggregate_quality

    if thresholds is not None:
        if aggregate_quality is None:
            # No ground truth → no axis to score. Surface a loud skip rather
            # than silently inventing a pass.
            summary["quality_gate"] = {
                "skipped": True,
                "reason": (
                    "no quality block in any run (ground truth not provided); "
                    "FP/FN gate cannot be evaluated"
                ),
                "axis": thresholds.axis,
                "enforce": thresholds.enforce,
                "should_fail": False,
            }
        else:
            summary["quality_gate"] = quality_metrics.evaluate_gate(
                aggregate_quality, thresholds
            )

    if perf_thresholds is not None:
        aggregate_perf = _aggregate_perf(results)
        if aggregate_perf is None:
            # No perf summary in any run → nothing to gate. Surface a loud skip
            # rather than silently inventing a pass.
            summary["perf_gate"] = {
                "skipped": True,
                "reason": (
                    "no performance_summary in any run; perf bars cannot be "
                    "evaluated"
                ),
                "enforce": perf_thresholds.enforce,
                "should_fail": False,
            }
        else:
            summary["perf_gate"] = performance.evaluate_perf_gate(
                aggregate_perf, perf_thresholds
            )

    return summary


# ---------------------------------------------------------------------------
# Live driver (integration — needs Lemonade)
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_benchmark(
    model_id: str,
    *,
    mbox_path: str,
    limit: int = 50,
    experiments: int = 1,
    base_url: str | None = None,
    ground_truth: dict[str, dict] | None = None,
    db_path: str | None = None,
    agent_factory: Callable[[], Any] | None = None,
    run_id_prefix: str = "bench",
) -> list[dict[str, Any]]:
    """Run the throughput benchmark ``experiments`` times and return result dicts.

    Direct-drives ``EmailTriageAgent.process_query`` over a ``FakeGmailBackend``
    loaded from ``mbox_path``. ``agent_factory`` overrides agent construction
    (used by tests to inject a stub — keeps the unit path Lemonade-free); when
    omitted a real agent is built lazily so importing this module stays cheap.
    """
    model_slug = model_id.replace("/", "-").lower()
    # The benchmark scores a fixed labelled corpus and must cover all of it for
    # a representative, category-balanced result. The LLM-facing triage tool
    # otherwise clamps each call to its interactive default (100), silently
    # truncating a larger corpus to its first N (and skewing the category mix).
    # Raise the ceiling to ``limit`` so ``--limit`` actually governs coverage;
    # per-email decisions are batch-size-independent, so this changes only how
    # many emails are scored, never how any one is classified.
    # Scope the override: save/restore so run_benchmark never permanently
    # mutates the process env — a shared-process caller (a test or a future
    # harness interleaving benchmark + agent calls) must keep the interactive
    # default ceiling once the benchmark returns.
    _prev_ceiling = os.environ.get("GAIA_EMAIL_TRIAGE_MAX_MESSAGES")
    os.environ["GAIA_EMAIL_TRIAGE_MAX_MESSAGES"] = str(limit)
    try:
        # Steer the agent to triage_inbox (whose envelope carries per-email
        # ``results``) rather than pre_scan_inbox, so throughput AND quality are
        # both harvestable and the run is deterministic across model whims.
        prompt = (
            f"Call triage_inbox for up to {limit} messages in my inbox, "
            "then give me a short summary."
        )
        results: list[dict[str, Any]] = []

        for exp in range(1, experiments + 1):
            if agent_factory is not None:
                agent = agent_factory()
            else:
                # Lazy import: keep `import gaia.eval.benchmark` free of the
                # agent stack. EmailTriageAgent ships as the standalone
                # gaia-agent-email wheel (#1102).
                try:
                    from gaia_agent_email.agent import EmailTriageAgent
                    from gaia_agent_email.config import EmailAgentConfig
                except ImportError as exc:
                    raise RuntimeError(
                        "The email throughput benchmark needs the email agent. "
                        "Install it with `pip install gaia-agent-email` (or "
                        '`pip install "amd-gaia[agents]"`). '
                        f"Original import error: {exc}"
                    ) from exc

                try:
                    from tests.fixtures.email.fake_gmail import FakeGmailBackend
                except ImportError as exc:
                    raise RuntimeError(
                        "The email throughput benchmark must run from a GAIA "
                        "repo checkout — it drives the synthetic corpus in "
                        "tests/fixtures/email and is not available in a packaged "
                        f"install. Original import error: {exc}"
                    ) from exc

                config = EmailAgentConfig(
                    model_id=model_id,
                    base_url=base_url,
                    gmail_backend=FakeGmailBackend(mbox_path),
                    db_path=db_path,
                    show_stats=True,
                    silent_mode=True,
                )
                agent = EmailTriageAgent(config=config)

            run_id = f"{run_id_prefix}-{model_slug}-exp{exp}"
            start = time.monotonic()
            agent_result = agent.process_query(prompt)
            total_duration_ms = int((time.monotonic() - start) * 1000)

            results.append(
                build_result(
                    agent_result,
                    run_id=run_id,
                    timestamp=_utc_now_iso(),
                    model_id=model_id,
                    total_duration_ms=total_duration_ms,
                    ground_truth=ground_truth,
                    is_cold_start=(exp == 1),
                )
            )

            # Release the agent's SQLite state.db before the next experiment and
            # the caller's temp-dir cleanup — an open connection locks the file on
            # Windows. Best-effort: a stub agent_factory agent may lack close_db.
            try:
                agent.close_db()
            except Exception:
                pass

        return results
    finally:
        if _prev_ceiling is None:
            os.environ.pop("GAIA_EMAIL_TRIAGE_MAX_MESSAGES", None)
        else:
            os.environ["GAIA_EMAIL_TRIAGE_MAX_MESSAGES"] = _prev_ceiling


def load_ground_truth(path: str | Path) -> dict[str, dict]:
    """Load a ground-truth JSON file (loud on missing/invalid)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def default_quality_thresholds_path() -> Path:
    """Path to the committed quality-gate thresholds manifest (#1230 corpus).

    The single entry point #1112 (CI) and #1266 consume — flip 'enforce' in that
    file (data, not code) to make CI gate on FP/FN.
    """
    return resolve_repo_fixture("email", "quality_gate_thresholds.json")


def load_default_quality_thresholds() -> "quality_metrics.QualityThresholds":
    """Load the committed quality-gate thresholds (loud if absent/malformed).

    The one call CI (#1112) makes to discover the FP<5%/FN<2% bars and the
    ``enforce`` switch without hardcoding a fixture path.
    """
    return quality_metrics.load_quality_thresholds(default_quality_thresholds_path())


def default_perf_thresholds_path() -> Path:
    """Path to the committed perf-gate thresholds manifest (#1277).

    The single entry point #1112 (CI) consumes — flip 'enforce' in that file
    (data, not code) to make CI gate on the Strix Halo bars once confirmed on
    hardware.
    """
    return resolve_repo_fixture("email", "perf_gate_thresholds.json")


def load_default_perf_thresholds() -> "performance.PerfThresholds":
    """Load the committed perf-gate thresholds (loud if absent/malformed).

    The one call CI (#1112) makes to discover the TTFT/throughput/pipeline/peak-
    memory bars and the ``enforce`` switch without hardcoding a fixture path.
    """
    return performance.load_perf_thresholds(default_perf_thresholds_path())
