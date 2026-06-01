# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Quality metrics for GAIA classification-style eval benchmarks.

Provides corpus-agnostic scoring primitives:
  * ``category_accuracy`` — exact-match accuracy of predicted vs ground-truth
    category (the lifted ``_compute_quality`` semantics).
  * ``binary_confusion`` + axis helpers — true/false positive/negative counts
    with FP-rate, FN-rate, precision, recall, F1 for any binary axis.
  * ``compute_cost`` — token cost via ``gaia.eval.config.MODEL_PRICING``.

**FP/FN axis is a policy choice, intentionally not baked in.** Email triage has
two independent binary axes — a spam/phishing axis (the corpus tracks
``is_spam`` / ``is_phishing`` booleans) and a "needs-attention" axis (category
∈ {urgent, actionable}). #1278's bars (FP<5% / FN<2%) are coherest on the
attention axis (missing an important mail is worse than a false alarm), but the
corpus natively supports the spam axis too. This module exposes neutral
primitives for *all* axes; which axis (and threshold) gates CI is decided when
#1278 is closed. Ground truth is always passed in as an argument, so #1230's
future corpus is a drop-in swap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gaia.eval.config import MODEL_PRICING

# Categories whose ground-truth entries are real labels (skip metadata rows).
_METADATA_KEYS = {"_meta", "_comment", "_metadata"}


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
    class (e.g. ``{"urgent", "actionable"}`` for a needs-attention axis)."""
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
