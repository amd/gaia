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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from gaia.eval import performance, quality_metrics

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
        out["quality"] = {
            "category_accuracy": quality_metrics.category_accuracy(
                predicted_categories, ground_truth
            ),
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
    axes = ("spam", "phishing", "needs_attention")
    totals = {axis: quality_metrics.Confusion() for axis in axes}
    accuracies: list[float] = []
    saw_quality = False

    for r in results:
        q = r.get("quality")
        if not isinstance(q, dict):
            continue
        saw_quality = True
        if isinstance(q.get("category_accuracy"), (int, float)):
            accuracies.append(float(q["category_accuracy"]))
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
    out["category_accuracy"] = (
        round(sum(accuracies) / len(accuracies), 4) if accuracies else 0.0
    )
    return out


def summarize_benchmark(
    results: list[dict],
    *,
    run_id: str,
    thresholds: "quality_metrics.QualityThresholds | None" = None,
) -> dict[str, Any]:
    """Aggregate per-run results into a scorecard + per-model variance report.

    Reuses :func:`gaia.eval.scorecard.build_scorecard` (perf section) and
    :func:`gaia.eval.statistics.compare_runs_by_model` (variance) — no new perf
    aggregation logic. When any run scored quality, an aggregate ``quality``
    block (corpus-level confusion across runs) is added. When ``thresholds`` is
    given, the configurable FP/FN gate runs against that aggregate and a
    ``quality_gate`` block is added (report mode unless the manifest sets
    ``enforce``). The gate is the machinery #1112 consumes; it never fails here.
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
            # Lazy import: keep `import gaia.eval.benchmark` free of the agent stack.
            from gaia.agents.email.agent import EmailTriageAgent
            from gaia.agents.email.config import EmailAgentConfig

            try:
                from tests.fixtures.email.fake_gmail import FakeGmailBackend
            except ImportError as exc:
                raise RuntimeError(
                    "The email throughput benchmark must run from a GAIA repo "
                    "checkout — it drives the synthetic corpus in "
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

    return results


def load_ground_truth(path: str | Path) -> dict[str, dict]:
    """Load a ground-truth JSON file (loud on missing/invalid)."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# Canonical location of the committed quality-gate thresholds manifest for the
# #1230 corpus. The single entry point #1112 (CI) and #1266 consume — flip
# 'enforce' in this file (data, not code) to make CI gate on FP/FN.
_THRESHOLDS_MANIFEST = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "email"
    / "quality_gate_thresholds.json"
)


def default_quality_thresholds_path() -> Path:
    """Path to the committed quality-gate thresholds manifest (#1230 corpus)."""
    return _THRESHOLDS_MANIFEST


def load_default_quality_thresholds() -> "quality_metrics.QualityThresholds":
    """Load the committed quality-gate thresholds (loud if absent/malformed).

    The one call CI (#1112) makes to discover the FP<5%/FN<2% bars and the
    ``enforce`` switch without hardcoding a fixture path.
    """
    return quality_metrics.load_quality_thresholds(default_quality_thresholds_path())
