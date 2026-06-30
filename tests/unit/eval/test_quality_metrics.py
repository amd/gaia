# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.eval.quality_metrics (pure — no Lemonade)."""

import json

import pytest

from gaia.eval.quality_metrics import (
    QualityThresholds,
    binary_confusion,
    categorization_export,
    category_accuracy,
    compute_cost,
    confusion_for_categories,
    confusion_for_flag,
    connection_diagnostics,
    evaluate_gate,
    load_quality_thresholds,
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


# Needs-attention axis used across export/gate tests: urgent+actionable positive.
_ATTENTION = {"urgent", "actionable"}


class TestCategorizationExport:
    def test_rows_carry_predicted_expected_and_correctness(self):
        preds = {
            "a": "urgent",  # TP on attention axis (gt urgent)
            "b": "urgent",  # FP (gt low priority → negative class flagged positive)
            "c": "informational",  # TN (gt informational)
            "d": "informational",  # FN (gt actionable → positive class missed)
        }
        export = categorization_export(preds, GT, axis_positive_categories=_ATTENTION)
        rows = {r["id"]: r for r in export["rows"]}
        assert rows["a"]["predicted"] == "urgent"
        assert rows["a"]["expected"] == "urgent"
        assert rows["a"]["category_correct"] is True
        # FP row: predicted attention, ground truth not attention
        assert rows["b"]["is_false_positive"] is True
        assert rows["b"]["is_false_negative"] is False
        # FN row: ground truth attention, predicted not attention
        assert rows["d"]["is_false_negative"] is True
        assert rows["d"]["is_false_positive"] is False
        # TN row: neither
        assert rows["c"]["is_false_positive"] is False
        assert rows["c"]["is_false_negative"] is False

    def test_false_positive_and_negative_id_lists(self):
        preds = {
            "a": "urgent",
            "b": "urgent",
            "c": "informational",
            "d": "informational",
        }
        export = categorization_export(preds, GT, axis_positive_categories=_ATTENTION)
        assert export["false_positives"] == ["b"]
        assert export["false_negatives"] == ["d"]

    def test_summary_confusion_matches_axis_helper(self):
        preds = {"a": "urgent", "b": "urgent", "c": "informational", "d": "actionable"}
        export = categorization_export(preds, GT, axis_positive_categories=_ATTENTION)
        expected = confusion_for_categories(preds, GT, _ATTENTION).to_dict()
        assert export["summary"] == expected
        assert export["axis"] == "needs_attention"

    def test_skips_metadata_and_unmatched_ids(self):
        # _meta never appears; an id present in preds but not GT is ignored
        preds = {"a": "urgent", "z": "urgent"}
        export = categorization_export(preds, GT, axis_positive_categories=_ATTENTION)
        ids = {r["id"] for r in export["rows"]}
        assert ids == {"a"}  # only the overlapping labelled id

    def test_custom_axis_label(self):
        export = categorization_export(
            {"a": "urgent"},
            GT,
            axis_positive_categories={"urgent"},
            axis_label="urgent_only",
        )
        assert export["axis"] == "urgent_only"

    def test_empty_positive_set_raises(self):
        with pytest.raises(ValueError, match="at least one positive category"):
            categorization_export({"a": "urgent"}, GT, axis_positive_categories=set())


class TestConnectionDiagnostics:
    def _conn(self, **over):
        base = {
            "provider": "google",
            "account_email": "user@example.com",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            "connected_at": 1_000.0,
        }
        base.update(over)
        return base

    def test_healthy_connection(self):
        diag = connection_diagnostics([self._conn()])
        assert diag["aggregate"]["total"] == 1
        assert diag["aggregate"]["connected"] == 1
        assert diag["aggregate"]["errored"] == 0
        row = diag["connectors"][0]
        assert row["provider"] == "google"
        assert row["connected"] is True
        assert row["errored"] is False

    def test_errored_connection_flagged(self):
        diag = connection_diagnostics([self._conn(error="configuration")])
        row = diag["connectors"][0]
        assert row["errored"] is True
        assert row["error"] == "configuration"
        assert row["connected"] is False
        assert diag["aggregate"]["errored"] == 1
        assert diag["aggregate"]["connected"] == 0

    def test_missing_scopes_reported(self):
        diag = connection_diagnostics(
            [self._conn(scopes=["a"])],
            required_scopes={"google": ["a", "b"]},
        )
        row = diag["connectors"][0]
        assert row["scope_complete"] is False
        assert row["missing_scopes"] == ["b"]
        assert diag["aggregate"]["scope_incomplete"] == 1

    def test_scope_complete_when_required_met(self):
        diag = connection_diagnostics(
            [self._conn(scopes=["a", "b", "c"])],
            required_scopes={"google": ["a", "b"]},
        )
        row = diag["connectors"][0]
        assert row["scope_complete"] is True
        assert row["missing_scopes"] == []

    def test_age_seconds_from_now(self):
        diag = connection_diagnostics([self._conn(connected_at=100.0)], now=160.0)
        assert diag["connectors"][0]["age_seconds"] == 60.0

    def test_no_connections_is_empty_not_error(self):
        diag = connection_diagnostics([])
        assert diag["aggregate"]["total"] == 0
        assert diag["connectors"] == []

    def test_non_dict_entry_raises(self):
        with pytest.raises(ValueError, match="connection entry"):
            connection_diagnostics(["not-a-dict"])

    def test_missing_provider_raises(self):
        with pytest.raises(ValueError, match="provider"):
            connection_diagnostics([{"account_email": "x@example.com"}])


def _quality_block(fp_rate, fn_rate, axis="needs_attention"):
    """A minimal benchmark-style quality block with one axis confusion dict."""
    return {
        "category_accuracy": 0.4,
        axis: {
            "false_positive_rate": fp_rate,
            "false_negative_rate": fn_rate,
            "fp": 0,
            "fn": 0,
            "tp": 0,
            "tn": 0,
        },
    }


class TestQualityThresholds:
    def test_load_from_manifest(self, tmp_path):
        p = tmp_path / "thresholds.json"
        p.write_text(
            json.dumps(
                {
                    "fp_max": 0.05,
                    "fn_max": 0.02,
                    "axis": "needs_attention",
                    "enforce": False,
                }
            )
        )
        th = load_quality_thresholds(p)
        assert isinstance(th, QualityThresholds)
        assert th.fp_max == 0.05
        assert th.fn_max == 0.02
        assert th.axis == "needs_attention"
        assert th.enforce is False

    def test_missing_key_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"fp_max": 0.05}))  # missing fn_max/axis
        with pytest.raises(ValueError, match="quality-gate thresholds"):
            load_quality_thresholds(p)

    def test_non_numeric_threshold_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(
            json.dumps({"fp_max": "lots", "fn_max": 0.02, "axis": "needs_attention"})
        )
        with pytest.raises(ValueError, match="numeric"):
            load_quality_thresholds(p)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_quality_thresholds(tmp_path / "nope.json")

    def test_enforce_defaults_false_when_absent(self, tmp_path):
        p = tmp_path / "t.json"
        p.write_text(
            json.dumps({"fp_max": 0.05, "fn_max": 0.02, "axis": "needs_attention"})
        )
        # enforce is the one safety switch — absent means report-only.
        assert load_quality_thresholds(p).enforce is False


class TestEvaluateGate:
    def test_clean_input_passes(self):
        th = QualityThresholds(fp_max=0.05, fn_max=0.02, axis="needs_attention")
        result = evaluate_gate(_quality_block(0.01, 0.0), th)
        assert result["passed"] is True
        assert result["breaches"] == []
        assert result["fp_rate"] == 0.01
        assert result["fn_rate"] == 0.0
        assert result["enforce"] is False

    def test_high_fp_breaches(self):
        th = QualityThresholds(fp_max=0.05, fn_max=0.02, axis="needs_attention")
        result = evaluate_gate(_quality_block(0.30, 0.0), th)
        assert result["passed"] is False
        assert any(b["metric"] == "false_positive_rate" for b in result["breaches"])

    def test_high_fn_breaches(self):
        th = QualityThresholds(fp_max=0.05, fn_max=0.02, axis="needs_attention")
        result = evaluate_gate(_quality_block(0.0, 0.50), th)
        assert result["passed"] is False
        assert any(b["metric"] == "false_negative_rate" for b in result["breaches"])

    def test_boundary_at_threshold_passes(self):
        # exactly at the bar is a pass (bar is "must be below-or-equal").
        th = QualityThresholds(fp_max=0.05, fn_max=0.02, axis="needs_attention")
        assert evaluate_gate(_quality_block(0.05, 0.02), th)["passed"] is True

    def test_enforce_flag_propagates(self):
        th = QualityThresholds(
            fp_max=0.05, fn_max=0.02, axis="needs_attention", enforce=True
        )
        result = evaluate_gate(_quality_block(0.30, 0.0), th)
        assert result["enforce"] is True
        assert result["should_fail"] is True  # enforce AND breached

    def test_report_mode_never_fails_even_on_breach(self):
        th = QualityThresholds(
            fp_max=0.05, fn_max=0.02, axis="needs_attention", enforce=False
        )
        result = evaluate_gate(_quality_block(0.99, 0.99), th)
        assert result["passed"] is False
        assert result["should_fail"] is False  # report mode: machinery only

    def test_missing_axis_in_quality_block_raises(self):
        th = QualityThresholds(fp_max=0.05, fn_max=0.02, axis="spam")
        with pytest.raises(ValueError, match="axis 'spam'"):
            evaluate_gate(_quality_block(0.0, 0.0, axis="needs_attention"), th)

    def test_axis_missing_rate_keys_raises(self):
        th = QualityThresholds(fp_max=0.05, fn_max=0.02, axis="needs_attention")
        bad = {"needs_attention": {"tp": 1}}  # no rate keys
        with pytest.raises(ValueError, match="false_positive_rate"):
            evaluate_gate(bad, th)


# Schema-2.0 taxonomy ground truth for the acceptance metric (#1437).
# Ordinal ladder: urgent(3) > needs_response(2) > fyi(1) > promotional(0); personal off-scale.
GT2 = {
    "_meta": {"note": "metadata row — must be skipped"},
    "u": {"category": "URGENT"},
    "n": {"category": "NEEDS_RESPONSE"},
    "f": {"category": "FYI"},
    "p": {"category": "PROMOTIONAL"},
    "x": {"category": "PERSONAL"},
}


class TestWithinOneBucketAccuracy:
    def test_exact_matches_are_credited(self):
        from gaia.eval.quality_metrics import within_one_bucket_accuracy

        preds = {"u": "urgent", "n": "needs_response", "f": "fyi", "p": "promotional"}
        assert within_one_bucket_accuracy(preds, GT2) == 1.0

    def test_adjacent_buckets_credited(self):
        from gaia.eval.quality_metrics import within_one_bucket_accuracy

        # urgent->needs_response (adj), fyi->needs_response (adj),
        # promotional->fyi (adj) — all within one. 3/3.
        preds = {"u": "needs_response", "f": "needs_response", "p": "fyi"}
        assert within_one_bucket_accuracy(preds, GT2) == 1.0

    def test_distance_two_not_credited(self):
        from gaia.eval.quality_metrics import within_one_bucket_accuracy

        # promotional(0)->needs_response(2) distance 2; urgent(3)->fyi(1) distance 2.
        preds = {"p": "needs_response", "u": "fyi"}
        assert within_one_bucket_accuracy(preds, GT2) == 0.0

    def test_mixed(self):
        from gaia.eval.quality_metrics import within_one_bucket_accuracy

        # u exact(✓), n->fyi adj(✓), f->urgent dist2(✗), p->needs_response dist2(✗)
        preds = {"u": "urgent", "n": "fyi", "f": "urgent", "p": "needs_response"}
        assert within_one_bucket_accuracy(preds, GT2) == 0.5

    def test_personal_is_exact_only(self):
        from gaia.eval.quality_metrics import within_one_bucket_accuracy

        # x is PERSONAL (off-scale): exact credited, neighbor-by-rank NOT.
        assert within_one_bucket_accuracy({"x": "personal"}, GT2) == 1.0
        # fyi has rank 1 but personal has no rank → no ordinal credit.
        assert within_one_bucket_accuracy({"x": "fyi"}, GT2) == 0.0

    def test_unknown_predicted_label_exact_only(self):
        from gaia.eval.quality_metrics import within_one_bucket_accuracy

        assert within_one_bucket_accuracy({"u": "bogus"}, GT2) == 0.0

    def test_no_overlap_is_zero(self):
        from gaia.eval.quality_metrics import within_one_bucket_accuracy

        assert within_one_bucket_accuracy({"zzz": "urgent"}, GT2) == 0.0


class TestAcceptanceMetrics:
    def test_bundle_shape_and_values(self):
        from gaia.eval.quality_metrics import acceptance_metrics

        # u exact, n->fyi adj, f exact, p->fyi adj  → within_one 4/4 = 1.0
        # exact: u,f correct; n,p wrong → category_accuracy 2/4 = 0.5
        preds = {"u": "urgent", "n": "fyi", "f": "fyi", "p": "fyi"}
        m = acceptance_metrics(preds, GT2)
        assert set(m) == {
            "within_one_bucket_accuracy",
            "urgent_vs_not_accuracy",
            "urgent_recall",
            "category_accuracy",
        }
        assert m["within_one_bucket_accuracy"] == 1.0
        assert m["category_accuracy"] == 0.5
        # needs-attention truth: u,n positive; f,p negative.
        # preds positive (urgent/needs_response): only u. So tp=1(u), fn=1(n),
        # fp=0, tn=2(f,p). recall=1/2=0.5, accuracy=3/4=0.75.
        assert m["urgent_recall"] == 0.5
        assert m["urgent_vs_not_accuracy"] == 0.75


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
