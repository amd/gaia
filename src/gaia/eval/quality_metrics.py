# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Quality metrics for GAIA classification-style eval benchmarks.

Provides corpus-agnostic scoring primitives:
  * ``category_accuracy`` — exact-match accuracy of predicted vs ground-truth
    category (the lifted ``_compute_quality`` semantics).
  * ``binary_confusion`` + axis helpers — true/false positive/negative counts
    with FP-rate, FN-rate, precision, recall, F1 for any binary axis.
  * ``categorization_export`` — per-email predicted-vs-expected log with the
    false-positive / false-negative ids on the attention axis (#1278).
  * ``connection_diagnostics`` — pure transform from the connector
    connection-status shape into an exportable per-connector + aggregate
    diagnostics block (#1278).
  * ``QualityThresholds`` / ``load_quality_thresholds`` / ``evaluate_gate`` —
    the configurable FP/FN CI gate (#1278), report mode by default.
  * ``compute_cost`` — token cost via ``gaia.eval.config.MODEL_PRICING``.

**FP/FN axis.** Email triage has two independent binary axes — a spam/phishing
axis (the corpus tracks ``is_spam`` / ``is_phishing`` booleans) and a
"needs-attention" axis (category ∈ ``NEEDS_ATTENTION_CATEGORIES`` = {urgent,
needs_response}). #1278's bars (FP<5% / FN<2%) are coherent on the attention axis
(missing an important mail is worse than a false alarm), so the gate defaults to
it — but the axis is a *manifest* value, so flipping the gate to the spam axis is
a data edit. This module still exposes neutral primitives for *all* axes. Ground
truth is always passed in as an argument, so swapping the #1230 corpus is a
drop-in.

**Gate ships in report mode.** ``evaluate_gate`` computes pass/fail and the
exact breaches, but the ``enforce`` switch (read from the thresholds manifest,
default ``False``) decides whether the harness should actually fail. Enforcement
of FP<5%/FN<2% is gated on categorization accuracy (#1266; current corpus
accuracy ~0.40) — #1112 (CI) / #1266 flip ``enforce`` to ``True`` in the manifest
when accuracy lands. Until then the machinery runs and logs, CI does not block.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from gaia.eval.config import MODEL_PRICING

# Categories whose ground-truth entries are real labels (skip metadata rows).
_METADATA_KEYS = {"_meta", "_comment", "_metadata"}

# Default attention axis: which categories count as "needs attention" (the axis
# #1278's FP/FN bars are coherent on). Missing one of these is worse than a false
# alarm — the asymmetry the bars encode.
NEEDS_ATTENTION_CATEGORIES = {"urgent", "needs_response"}


@dataclass
class Confusion:
    """2x2 confusion counts for a single binary axis, with derived rates."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def false_positive_rate(self) -> float:
        """FP / (FP + TN) — fraction of true negatives wrongly flagged."""
        denom = self.fp + self.tn
        return round(self.fp / denom, 4) if denom else 0.0

    @property
    def false_negative_rate(self) -> float:
        """FN / (FN + TP) — fraction of true positives missed (= 1 - recall)."""
        denom = self.fn + self.tp
        return round(self.fn / denom, 4) if denom else 0.0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return round(self.tp / denom, 4) if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return round(self.tp / denom, 4) if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return round(2 * p * r / (p + r), 4) if (p + r) else 0.0

    @property
    def accuracy(self) -> float:
        return round((self.tp + self.tn) / self.total, 4) if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "false_positive_rate": self.false_positive_rate,
            "false_negative_rate": self.false_negative_rate,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "accuracy": self.accuracy,
        }


def _labelled_ids(ground_truth: dict[str, dict]) -> list[str]:
    """Ground-truth ids that carry a real ``category`` label (skip metadata)."""
    return [
        gid
        for gid, entry in ground_truth.items()
        if gid not in _METADATA_KEYS
        and isinstance(entry, dict)
        and entry.get("category") is not None
    ]


def category_accuracy(
    predictions: dict[str, str], ground_truth: dict[str, dict]
) -> float:
    """Exact-match category accuracy over ids present in both inputs.

    Case-insensitive comparison. Returns 0.0 if no ids overlap. Only counts
    ground-truth rows that carry a ``category`` (metadata rows are skipped).
    """
    matched = 0
    correct = 0
    for gid in _labelled_ids(ground_truth):
        if gid not in predictions:
            continue
        matched += 1
        expected = str(ground_truth[gid]["category"]).strip().lower()
        actual = str(predictions[gid]).strip().lower()
        if actual == expected:
            correct += 1
    return round(correct / matched, 4) if matched else 0.0


def binary_confusion(predictions: dict[str, bool], truth: dict[str, bool]) -> Confusion:
    """Confusion counts over ids present in both ``predictions`` and ``truth``."""
    c = Confusion()
    for gid, pred_pos in predictions.items():
        if gid not in truth:
            continue
        true_pos = bool(truth[gid])
        pred_pos = bool(pred_pos)
        if pred_pos and true_pos:
            c.tp += 1
        elif pred_pos and not true_pos:
            c.fp += 1
        elif not pred_pos and true_pos:
            c.fn += 1
        else:
            c.tn += 1
    return c


def confusion_for_flag(
    predictions: dict[str, bool], ground_truth: dict[str, dict], flag: str
) -> Confusion:
    """Binary confusion for a boolean ground-truth flag (e.g. ``is_spam``).

    ``predictions`` maps email-id → the agent's predicted boolean for ``flag``.
    """
    truth = {
        gid: bool(entry.get(flag, False))
        for gid, entry in ground_truth.items()
        if gid not in _METADATA_KEYS and isinstance(entry, dict)
    }
    return binary_confusion(predictions, truth)


def confusion_for_categories(
    predicted_categories: dict[str, str],
    ground_truth: dict[str, dict],
    positive_categories: set[str],
) -> Confusion:
    """One-vs-rest confusion treating ``positive_categories`` as the positive
    class (e.g. ``{"urgent", "needs_response"}`` for a needs-attention axis)."""
    positives = {c.strip().lower() for c in positive_categories}
    predictions = {
        gid: cat.strip().lower() in positives
        for gid, cat in predicted_categories.items()
    }
    truth = {
        gid: str(entry.get("category", "")).strip().lower() in positives
        for gid, entry in ground_truth.items()
        if gid not in _METADATA_KEYS
        and isinstance(entry, dict)
        and entry.get("category") is not None
    }
    return binary_confusion(predictions, truth)


def per_category_confusion(
    predicted_categories: dict[str, str], ground_truth: dict[str, dict]
) -> dict[str, Confusion]:
    """One-vs-rest confusion for every category present in the ground truth."""
    cats = {
        str(entry["category"]).strip().lower()
        for gid, entry in ground_truth.items()
        if gid not in _METADATA_KEYS
        and isinstance(entry, dict)
        and entry.get("category") is not None
    }
    return {
        cat: confusion_for_categories(predicted_categories, ground_truth, {cat})
        for cat in sorted(cats)
    }


def compute_cost(
    total_input_tokens: int,
    total_output_tokens: int,
    *,
    model: str | None = None,
    cost_per_1m_input: float | None = None,
    cost_per_1m_output: float | None = None,
) -> float:
    """Token cost in USD.

    Explicit ``cost_per_1m_*`` overrides win. Otherwise the model is looked up
    by exact id in ``MODEL_PRICING`` (cloud judge models). Local models
    (Lemonade-served Gemma/Qwen/etc.) are absent from the table and therefore
    cost ``0.0`` — local inference has no per-token API cost. This is defined
    behavior, not a fallback: the ``"default"`` pricing row is intentionally
    NOT applied to unrecognized models so local runs never get mis-billed.
    """
    if cost_per_1m_input is None or cost_per_1m_output is None:
        pricing = MODEL_PRICING.get(model or "")
        in_rate = (
            cost_per_1m_input
            if cost_per_1m_input is not None
            else (pricing["input_per_mtok"] if pricing else 0.0)
        )
        out_rate = (
            cost_per_1m_output
            if cost_per_1m_output is not None
            else (pricing["output_per_mtok"] if pricing else 0.0)
        )
    else:
        in_rate, out_rate = cost_per_1m_input, cost_per_1m_output

    input_cost = total_input_tokens * in_rate / 1_000_000
    output_cost = total_output_tokens * out_rate / 1_000_000
    return round(input_cost + output_cost, 6)


# ---------------------------------------------------------------------------
# Categorization-results export (#1278: log/export categories + FP + FN)
# ---------------------------------------------------------------------------


def categorization_export(
    predicted_categories: dict[str, str],
    ground_truth: dict[str, dict],
    *,
    axis_positive_categories: Optional[set[str]] = None,
    axis_label: str = "needs_attention",
) -> dict[str, Any]:
    """Per-email categorization export with FP/FN flags on a binary axis.

    Produces the exportable record #1278 calls for: every overlapping labelled
    email as a row carrying ``predicted`` vs ``expected`` category, whether the
    exact category matched, and whether it is a false positive / false negative
    on the *attention* axis (predicted-in-``axis_positive_categories`` vs the
    ground-truth label). Also returns the FP/FN id lists and the aggregate
    confusion (identical to :func:`confusion_for_categories` for the same axis).

    Only ids present in both inputs AND carrying a real ``category`` label are
    included (metadata rows are skipped). Raises ``ValueError`` if the positive
    set is empty — an axis with no positive class can't have FP/FN semantics.
    """
    if axis_positive_categories is None:
        axis_positive_categories = NEEDS_ATTENTION_CATEGORIES
    positives = {c.strip().lower() for c in axis_positive_categories}
    if not positives:
        raise ValueError(
            "categorization_export needs at least one positive category to "
            "define the attention axis (got an empty set)."
        )

    rows: list[dict[str, Any]] = []
    false_positives: list[str] = []
    false_negatives: list[str] = []

    for gid in _labelled_ids(ground_truth):
        if gid not in predicted_categories:
            continue
        expected = str(ground_truth[gid]["category"]).strip().lower()
        predicted = str(predicted_categories[gid]).strip().lower()
        pred_pos = predicted in positives
        true_pos = expected in positives
        is_fp = pred_pos and not true_pos
        is_fn = true_pos and not pred_pos
        if is_fp:
            false_positives.append(gid)
        if is_fn:
            false_negatives.append(gid)
        rows.append(
            {
                "id": gid,
                "predicted": predicted,
                "expected": expected,
                "category_correct": predicted == expected,
                "predicted_positive": pred_pos,
                "expected_positive": true_pos,
                "is_false_positive": is_fp,
                "is_false_negative": is_fn,
            }
        )

    summary = confusion_for_categories(
        predicted_categories, ground_truth, positives
    ).to_dict()
    return {
        "axis": axis_label,
        "rows": rows,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Connector / connection diagnostics export (#1278)
# ---------------------------------------------------------------------------


def connection_diagnostics(
    connections: Sequence[Mapping[str, Any]],
    *,
    required_scopes: Mapping[str, Sequence[str]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Normalize connection-status rows into an exportable diagnostics block.

    Pure transform over the shape that ``gaia.connectors.api.list_connections``
    returns — ``[{provider, account_email, scopes, connected_at, error?}]`` — so
    it is deterministic and unit-testable without touching connector state or
    live OAuth. Per-connector it reports reachability (``connected``), whether an
    ``error`` flag is set (``errored``), scope completeness against
    ``required_scopes[provider]`` (``scope_complete`` + ``missing_scopes``), and
    connection age in seconds when ``now`` is supplied.

    Fail-loud: a non-dict entry, or one missing ``provider``, raises
    ``ValueError`` rather than being silently dropped — a malformed status row is
    a real defect in whatever produced it.
    """
    required = {k: list(v) for k, v in (required_scopes or {}).items()}
    rows: list[dict[str, Any]] = []
    connected = 0
    errored = 0
    scope_incomplete = 0

    for idx, entry in enumerate(connections):
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"connection entry at index {idx} is "
                f"{type(entry).__name__}, expected a mapping like "
                "gaia.connectors.api.list_connections() returns."
            )
        provider = entry.get("provider")
        if not provider:
            raise ValueError(
                f"connection entry at index {idx} is missing a 'provider' key: "
                f"{dict(entry)!r}"
            )

        error = entry.get("error")
        is_errored = bool(error)
        # "connected" means a stored, non-errored credential row.
        is_connected = not is_errored

        have_scopes = {str(s) for s in entry.get("scopes", [])}
        need_scopes = required.get(provider, [])
        missing = [s for s in need_scopes if s not in have_scopes]
        scope_complete = not missing

        connected_at = entry.get("connected_at")
        age_seconds: float | None = None
        if now is not None and isinstance(connected_at, (int, float)):
            age_seconds = round(float(now) - float(connected_at), 3)

        if is_connected:
            connected += 1
        if is_errored:
            errored += 1
        if not scope_complete:
            scope_incomplete += 1

        rows.append(
            {
                "provider": provider,
                "account_email": entry.get("account_email") or "",
                "connected": is_connected,
                "errored": is_errored,
                "error": error,
                "scope_complete": scope_complete,
                "missing_scopes": missing,
                "scopes": sorted(have_scopes),
                "connected_at": connected_at,
                "age_seconds": age_seconds,
            }
        )

    return {
        "connectors": rows,
        "aggregate": {
            "total": len(rows),
            "connected": connected,
            "errored": errored,
            "scope_incomplete": scope_incomplete,
        },
    }


# ---------------------------------------------------------------------------
# Configurable-threshold CI gate (#1278 — report mode; #1112/#1266 flip enforce)
# ---------------------------------------------------------------------------


@dataclass
class QualityThresholds:
    """FP/FN bars for the quality gate, plus the axis they apply to.

    ``enforce`` is the single safety switch: ``False`` (the default and the
    committed manifest value) means the gate computes + reports but never fails
    the harness — because the FP<5%/FN<2% bars only become meaningful once
    categorization accuracy lands (#1266). #1112 / #1266 flip ``enforce`` to
    ``True`` in the manifest to make CI gate on it.
    """

    fp_max: float
    fn_max: float
    axis: str = "needs_attention"
    enforce: bool = False


_REQUIRED_THRESHOLD_KEYS = ("fp_max", "fn_max", "axis")


def load_quality_thresholds(path: str | Path) -> QualityThresholds:
    """Load the quality-gate thresholds manifest (loud on missing/malformed).

    The manifest is the ONE place the bars + the ``enforce`` switch live, so CI
    (#1112) and the accuracy work (#1266) flip enforcement by editing data, not
    code. Missing required keys, or non-numeric bars, raise ``ValueError`` —
    there is no silent default-to-permissive.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"quality-gate thresholds manifest at {path} must be a JSON object, "
            f"got {type(data).__name__}."
        )
    missing = [k for k in _REQUIRED_THRESHOLD_KEYS if k not in data]
    if missing:
        raise ValueError(
            f"quality-gate thresholds manifest at {path} is missing required "
            f"key(s) {missing}. Required: {list(_REQUIRED_THRESHOLD_KEYS)}; "
            "optional: 'enforce' (default false)."
        )
    for k in ("fp_max", "fn_max"):
        if not isinstance(data[k], (int, float)) or isinstance(data[k], bool):
            raise ValueError(
                f"quality-gate threshold '{k}' in {path} must be numeric, got "
                f"{data[k]!r}."
            )
    return QualityThresholds(
        fp_max=float(data["fp_max"]),
        fn_max=float(data["fn_max"]),
        axis=str(data["axis"]),
        enforce=bool(data.get("enforce", False)),
    )


def evaluate_gate(
    quality: Mapping[str, Any], thresholds: QualityThresholds
) -> dict[str, Any]:
    """Compare a benchmark ``quality`` block's FP/FN rates to the thresholds.

    ``quality`` is the per-run / aggregate block produced by the benchmark — it
    must contain ``thresholds.axis`` mapping to a confusion dict with
    ``false_positive_rate`` and ``false_negative_rate`` (as
    :meth:`Confusion.to_dict` emits). Returns a structured gate result:
    ``passed`` (both rates at-or-below their bars), ``breaches`` (which bars were
    exceeded), and ``should_fail`` (``enforce and not passed`` — the hook #1112
    keys CI off). In report mode (``enforce=False``) ``should_fail`` is always
    ``False`` even on a breach: the machinery runs, CI does not block.

    Fail-loud: a missing axis or missing rate keys raise ``ValueError`` — a gate
    that can't find its inputs must not silently report a pass.
    """
    axis_block = quality.get(thresholds.axis)
    if not isinstance(axis_block, Mapping):
        raise ValueError(
            f"quality gate axis '{thresholds.axis}' not found in the quality "
            f"block (keys: {sorted(quality.keys())}). The benchmark must emit a "
            f"confusion dict for this axis before the gate can score it."
        )
    for rate_key in ("false_positive_rate", "false_negative_rate"):
        if rate_key not in axis_block:
            raise ValueError(
                f"quality gate axis '{thresholds.axis}' block is missing "
                f"'{rate_key}' (have: {sorted(axis_block.keys())})."
            )

    fp_rate = float(axis_block["false_positive_rate"])
    fn_rate = float(axis_block["false_negative_rate"])

    breaches: list[dict[str, Any]] = []
    if fp_rate > thresholds.fp_max:
        breaches.append(
            {
                "metric": "false_positive_rate",
                "value": fp_rate,
                "max": thresholds.fp_max,
            }
        )
    if fn_rate > thresholds.fn_max:
        breaches.append(
            {
                "metric": "false_negative_rate",
                "value": fn_rate,
                "max": thresholds.fn_max,
            }
        )

    passed = not breaches
    return {
        "axis": thresholds.axis,
        "fp_rate": fp_rate,
        "fn_rate": fn_rate,
        "fp_max": thresholds.fp_max,
        "fn_max": thresholds.fn_max,
        "passed": passed,
        "breaches": breaches,
        "enforce": thresholds.enforce,
        # The hook CI (#1112) keys off: report mode never fails the harness.
        "should_fail": thresholds.enforce and not passed,
    }
