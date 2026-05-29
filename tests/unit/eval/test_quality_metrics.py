# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.eval.quality_metrics (pure — no Lemonade)."""

import pytest

from gaia.eval.quality_metrics import (
    binary_confusion,
    category_accuracy,
    compute_cost,
    confusion_for_categories,
    confusion_for_flag,
    per_category_confusion,
)

GT = {
    "_meta": {"note": "metadata row — must be skipped"},
    "a": {"category": "urgent", "is_spam": False, "is_phishing": False},
    "b": {"category": "low priority", "is_spam": True, "is_phishing": False},
    "c": {"category": "informational", "is_spam": False, "is_phishing": False},
    "d": {"category": "actionable", "is_spam": False, "is_phishing": True},
}


class TestCategoryAccuracy:
    def test_partial_match(self):
        preds = {"a": "urgent", "b": "low priority", "c": "urgent", "d": "actionable"}
        assert category_accuracy(preds, GT) == 0.75  # c wrong, 3/4

    def test_case_insensitive(self):
        assert category_accuracy({"a": "URGENT"}, GT) == 1.0

    def test_skips_metadata_and_unmatched(self):
        # only "a" overlaps; _meta never counts
        assert category_accuracy({"a": "urgent", "z": "urgent"}, GT) == 1.0

    def test_no_overlap_is_zero(self):
        assert category_accuracy({"z": "urgent"}, GT) == 0.0


class TestBinaryConfusion:
    def test_one_each_quadrant(self):
        pred = {"a": True, "b": False, "c": True, "d": False}
        truth = {"a": True, "b": True, "c": False, "d": False}
        c = binary_confusion(pred, truth)
        assert (c.tp, c.fp, c.fn, c.tn) == (1, 1, 1, 1)
        assert c.false_positive_rate == 0.5
        assert c.false_negative_rate == 0.5
        assert c.precision == 0.5
        assert c.recall == 0.5
        assert c.f1 == 0.5
        assert c.accuracy == 0.5

    def test_zero_denominator_rates_are_zero(self):
        # all true-negative → no positives anywhere
        c = binary_confusion({"a": False}, {"a": False})
        assert c.false_positive_rate == 0.0
        assert c.false_negative_rate == 0.0


class TestSpamAxis:
    def test_perfect_spam_detection(self):
        pred = {"a": False, "b": True, "c": False, "d": False}  # only b is spam
        c = confusion_for_flag(pred, GT, "is_spam")
        assert (c.tp, c.fp, c.fn, c.tn) == (1, 0, 0, 3)
        assert c.false_negative_rate == 0.0

    def test_missed_spam_is_false_negative(self):
        pred = {"a": False, "b": False, "c": False, "d": False}  # missed b
        c = confusion_for_flag(pred, GT, "is_spam")
        assert c.fn == 1
        assert c.false_negative_rate == 1.0  # 1 of 1 actual spam missed


class TestAttentionAxis:
    def test_needs_attention_one_vs_rest(self):
        preds = {
            "a": "urgent",
            "b": "low priority",
            "c": "informational",
            "d": "actionable",
        }
        c = confusion_for_categories(preds, GT, {"urgent", "actionable"})
        assert (c.tp, c.fp, c.fn, c.tn) == (2, 0, 0, 2)

    def test_false_positive_when_junk_flagged_important(self):
        preds = {"a": "urgent", "b": "urgent", "c": "informational", "d": "actionable"}
        c = confusion_for_categories(preds, GT, {"urgent", "actionable"})
        assert c.fp == 1  # b (low priority) wrongly treated as needs-attention


class TestPerCategoryConfusion:
    def test_one_entry_per_category(self):
        preds = {
            "a": "urgent",
            "b": "low priority",
            "c": "informational",
            "d": "actionable",
        }
        by_cat = per_category_confusion(preds, GT)
        assert set(by_cat.keys()) == {
            "urgent",
            "low priority",
            "informational",
            "actionable",
        }
        assert by_cat["urgent"].tp == 1


class TestComputeCost:
    def test_local_model_is_free(self):
        assert compute_cost(1_000_000, 1_000_000, model="Gemma-4-E4B-it-GGUF") == 0.0

    def test_unknown_model_no_default_billing(self):
        # the MODEL_PRICING "default" row must NOT apply to local models
        assert compute_cost(5_000_000, 5_000_000, model=None) == 0.0

    def test_cloud_model_is_priced(self):
        # sonnet: $3/Mtok in, $15/Mtok out
        assert compute_cost(1_000_000, 1_000_000, model="claude-sonnet-4-6") == 18.0

    def test_explicit_override_wins(self):
        assert (
            compute_cost(1_000_000, 0, cost_per_1m_input=2.0, cost_per_1m_output=8.0)
            == 2.0
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
