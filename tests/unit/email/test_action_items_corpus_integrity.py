# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the action-item extraction eval (#1605 / #1949).

Mirrors ``test_corpus_integrity.py`` for the action-item seed corpus, plus
offline coverage of the fuzzy matcher + scorer in
``gaia.eval.action_item_quality``:

- The committed corpus loads, is the declared size, carries hard negatives, and
  every case is schema-well-formed (the loader is the validator — fail-loud).
- Duplicate case ids, missing required fields, and a mislabeled hard negative
  are rejected loudly.
- The fuzzy matcher pairs reworded descriptions, counts false positives /
  negatives, and only consults the injected judge in the gray band.
- The scorer runs end-to-end on hand-fed predicted-vs-expected (no backend, no
  network): perfect extraction PASSes, a spurious extraction on a hard negative
  is a precision-denominator false positive, and errored generations stay
  ERRORED.
- The committed thresholds manifest ships the #1949 bars in report mode
  (``enforce: false``) and the gate honors the ``should_fail`` contract.

Everything here runs offline: no Lemonade, no Anthropic, no live mailbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.eval import action_item_quality as aiq
from gaia.eval.scorecard import build_scorecard

_REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "email" / "action_items_ground_truth.json"
)
THRESHOLDS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "email" / "action_items_gate_thresholds.json"
)


@pytest.fixture(scope="module")
def corpus() -> dict:
    return aiq.load_action_item_corpus(CORPUS_PATH)


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------


def test_corpus_loads_and_has_seed_size(corpus):
    """Seed corpus is committed, loadable, and in the declared 15–25 range."""
    cases = aiq.corpus_cases(corpus)
    assert 15 <= len(cases) <= 25, f"seed corpus size out of range: {len(cases)}"


def test_meta_block_present_and_well_formed(corpus):
    meta = corpus["_meta"]
    assert meta["fixture"] == "action_items_ground_truth.json"
    assert meta["fixture_kind"] == "hand-authored-seed"
    assert isinstance(meta["schema_version"], int)


def test_corpus_has_both_positives_and_hard_negatives(corpus):
    """A precision eval is only meaningful with real extract-nothing cases."""
    cases = aiq.corpus_cases(corpus)
    hard = [c for c in cases.values() if aiq.is_hard_negative(c)]
    positive = [c for c in cases.values() if not aiq.is_hard_negative(c)]
    assert len(hard) >= 3, "need several hard negatives for the FP denominator"
    assert len(positive) >= 3, "need several positive cases"


def test_every_case_is_schema_well_formed(corpus):
    """The loader already validates; assert the shape it guarantees."""
    for case_id, case in aiq.corpus_cases(corpus).items():
        assert not case_id.startswith("_")
        assert case["scenario"].strip()
        for field in ("from", "subject", "body"):
            assert case["email"][field].strip(), f"{case_id}: email.{field}"
        assert isinstance(case["expected_action_items"], list), case_id
        for item in case["expected_action_items"]:
            assert item["description"].strip(), case_id
            assert item.get("type", "text") in {"text", "link"}, case_id
            if item.get("type") == "link":
                assert item.get("url", "").strip(), case_id


def test_duplicate_case_ids_rejected(tmp_path):
    dup = tmp_path / "dup.json"
    dup.write_text('{"ai-001": {}, "ai-001": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case id"):
        aiq.load_action_item_corpus(dup)


def test_missing_required_field_rejected(tmp_path, corpus):
    cases = aiq.corpus_cases(corpus)
    case_id, case = next(iter(cases.items()))
    broken = {case_id: {k: v for k, v in case.items() if k != "email"}}
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="email"):
        aiq.load_action_item_corpus(path)


def test_mislabeled_hard_negative_rejected(tmp_path):
    """A hard-negative flag that disagrees with the labeled set is loud."""
    bad = {
        "ai-x": {
            "scenario": "mislabeled",
            "is_hard_negative": True,
            "email": {"from": "a@b.c", "subject": "s", "body": "b"},
            "expected_action_items": [{"description": "do a thing", "type": "text"}],
        }
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="is_hard_negative"):
        aiq.load_action_item_corpus(path)


def test_link_item_without_url_rejected(tmp_path):
    bad = {
        "ai-x": {
            "scenario": "link missing url",
            "email": {"from": "a@b.c", "subject": "s", "body": "b"},
            "expected_action_items": [{"description": "click", "type": "link"}],
        }
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="url"):
        aiq.load_action_item_corpus(path)


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------


def test_similarity_rewards_reworded_same_action():
    """Reordering / rewording still scores as similar (token overlap)."""
    a = "Reply to Dana confirming the 3pm Thursday design review"
    b = "Confirm the design review time of 3pm Thursday with Dana"
    assert aiq.description_similarity(a, b) >= aiq.DEFAULT_MATCH_THRESHOLD


def test_similarity_low_for_unrelated_actions():
    a = "Pay invoice #4471 for $1,240"
    b = "Schedule a follow-up demo for next week"
    assert aiq.description_similarity(a, b) < aiq.DEFAULT_JUDGE_FLOOR


def test_match_counts_tp_fp_fn():
    expected = [
        {"description": "Review the onboarding flow and send feedback"},
        {"description": "Fix the pricing page typo"},
    ]
    predicted = [
        {"description": "Send feedback on the onboarding flow"},  # matches #0
        {"description": "Book a flight to Denver"},  # spurious -> FP
    ]
    m = aiq.match_action_items(predicted, expected)
    assert m.tp == 1
    assert m.fp == 1  # the spurious prediction
    assert m.fn == 1  # the pricing typo was missed
    assert m.matched[0]["expected_index"] == 0


def test_match_pairs_each_side_at_most_once():
    """One predicted item cannot satisfy two expected items."""
    expected = [
        {"description": "Send the SLA one-pager"},
        {"description": "Send the pricing one-pager"},
    ]
    predicted = [{"description": "Send the SLA one-pager"}]
    m = aiq.match_action_items(predicted, expected)
    assert m.tp == 1
    assert m.fp == 0
    assert m.fn == 1


def test_judge_only_consulted_in_gray_band():
    """A borderline pair is resolved by the injected judge; a clearly-unrelated
    pair never reaches it (no wasted LLM call)."""
    calls = {"n": 0}

    def judge(pred: str, exp: str) -> bool:
        calls["n"] += 1
        return True

    # Borderline: partial overlap that lands in the gray band.
    expected = [{"description": "Reset your account password before it expires"}]
    predicted = [{"description": "reset password"}]
    sim = aiq.description_similarity(
        predicted[0]["description"], expected[0]["description"]
    )
    assert aiq.DEFAULT_JUDGE_FLOOR <= sim < aiq.DEFAULT_MATCH_THRESHOLD, sim
    m = aiq.match_action_items(predicted, expected, judge_fn=judge)
    assert calls["n"] == 1
    assert m.tp == 1  # judge promoted it

    # No judge → the same gray-band pair is a miss, deterministically.
    m2 = aiq.match_action_items(predicted, expected)
    assert m2.tp == 0
    assert m2.fn == 1


def test_prf_hard_negative_perfect_when_silent():
    assert aiq.prf(0, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_prf_hard_negative_false_positive_tanks_precision():
    scores = aiq.prf(0, 2, 0)  # extracted 2 items on a no-action email
    assert scores["precision"] == 0.0
    assert scores["f1"] == 0.0


# ---------------------------------------------------------------------------
# Scorer on hand-fed predictions (offline end-to-end)
# ---------------------------------------------------------------------------


def _predicted_from_expected(case):
    """A 'perfect' extraction: echo the expected descriptions back."""
    return [{"description": i["description"]} for i in case["expected_action_items"]]


def test_score_case_perfect_extraction_passes(corpus):
    case = aiq.corpus_cases(corpus)["ai-002"]
    row = aiq.score_case(
        "ai-002", case, _predicted_from_expected(case), model_id="stub-model"
    )
    assert row["status"] == "PASS"
    assert row["overall_score"] == 10.0
    assert row["action_item_match"]["f1"] == 1.0
    assert row["category"] == "stub-model"


def test_score_case_hard_negative_silent_passes(corpus):
    """Correctly extracting nothing on a hard negative is a perfect case."""
    case = aiq.corpus_cases(corpus)["ai-neg-001"]
    assert aiq.is_hard_negative(case)
    row = aiq.score_case("ai-neg-001", case, [], model_id="stub-model")
    assert row["status"] == "PASS"
    assert row["action_item_match"]["f1"] == 1.0


def test_score_case_hard_negative_spurious_extraction_fails(corpus):
    case = aiq.corpus_cases(corpus)["ai-neg-001"]
    row = aiq.score_case(
        "ai-neg-001",
        case,
        [{"description": "Unsubscribe from the newsletter"}],
        model_id="stub-model",
    )
    assert row["status"] == "FAIL"
    assert row["action_item_match"]["fp"] == 1
    assert row["action_item_match"]["precision"] == 0.0


def test_score_case_none_prediction_is_errored(corpus):
    case = aiq.corpus_cases(corpus)["ai-001"]
    row = aiq.score_case("ai-001", case, None, model_id="stub-model", error="boom")
    assert row["status"] == "ERRORED"
    assert row["error"] == "boom"
    assert "action_item_match" not in row


def test_summarize_micro_averages_and_scorecard(corpus):
    """Perfect extractions on a mix of positives + hard negatives → F1 1.0 and a
    build_scorecard-compatible summary."""
    cases = aiq.corpus_cases(corpus)
    results = []
    for case_id, case in cases.items():
        results.append(
            aiq.score_case(
                case_id,
                case,
                _predicted_from_expected(case),
                model_id="stub-model",
            )
        )
    summary = aiq.summarize_extraction(
        results,
        run_id="unit",
        thresholds=aiq.ExtractionThresholds(f1_min=0.70, recall_min=0.75),
    )
    agg = summary["extraction"]
    assert agg["cases_scored"] == len(cases)
    assert agg["precision"] == 1.0
    assert agg["recall"] == 1.0
    assert agg["f1"] == 1.0
    assert agg["hard_negatives_total"] >= 3
    assert agg["hard_negative_correct_rate"] == 1.0
    # Gate passes in report mode (perfect run).
    assert summary["extraction_gate"]["passed"] is True
    assert summary["extraction_gate"]["should_fail"] is False
    # Rows also aggregate through the shared scorecard builder unchanged.
    scorecard = build_scorecard("unit", results, {})
    assert scorecard["summary"]["total_scenarios"] == len(cases)
    assert scorecard["summary"]["passed"] == len(cases)


def test_summarize_counts_hard_negative_false_positive(corpus):
    """A spurious extraction on one hard negative shows up as an FP and drops
    the hard-negative correct rate below 1.0."""
    cases = aiq.corpus_cases(corpus)
    results = []
    for case_id, case in cases.items():
        if case_id == "ai-neg-002":
            predicted = [{"description": "Do something that was never asked"}]
        else:
            predicted = _predicted_from_expected(case)
        results.append(aiq.score_case(case_id, case, predicted, model_id="stub-model"))
    agg = aiq.summarize_extraction(results, run_id="unit")["extraction"]
    assert agg["fp"] >= 1
    assert agg["precision"] < 1.0
    assert agg["hard_negative_correct_rate"] < 1.0


def test_summarize_gate_skipped_when_nothing_scored():
    """All-errored run → gate is a loud explicit skip, never a pass."""
    results = [
        {"id": "ai-001", "category": "m", "status": "ERRORED", "error": "x"},
    ]
    summary = aiq.summarize_extraction(
        results, run_id="unit", thresholds=aiq.ExtractionThresholds(f1_min=0.70)
    )
    assert "extraction" not in summary
    assert summary["extraction_gate"]["skipped"] is True
    assert summary["extraction_gate"]["should_fail"] is False


# ---------------------------------------------------------------------------
# Equivalence-judge verdict parsing
# ---------------------------------------------------------------------------


def test_parse_equivalence_verdict_happy_path_tolerates_fences():
    assert aiq.parse_equivalence_verdict('```json\n{"same_action": true}\n```') is True
    assert aiq.parse_equivalence_verdict('{"same_action": false}') is False


@pytest.mark.parametrize(
    "bad",
    ["", "no json", '{"same_action": "yes"}', '{"foo": true}', "{}"],
)
def test_parse_equivalence_verdict_rejects_garbage(bad):
    with pytest.raises(ValueError):
        aiq.parse_equivalence_verdict(bad)


# ---------------------------------------------------------------------------
# Committed thresholds manifest + gate contract
# ---------------------------------------------------------------------------


def test_committed_thresholds_manifest_is_report_mode():
    thresholds = aiq.load_extraction_thresholds(THRESHOLDS_PATH)
    assert thresholds.f1_min == 0.70
    assert thresholds.recall_min == 0.75
    assert thresholds.enforce is False  # report mode — see the manifest comment
    assert aiq.default_extraction_thresholds_path() == THRESHOLDS_PATH


def test_malformed_thresholds_manifest_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"enforce": false}', encoding="utf-8")
    with pytest.raises(ValueError, match="f1_min"):
        aiq.load_extraction_thresholds(path)


def test_gate_report_mode_never_fails_the_build():
    gate = aiq.evaluate_extraction_gate(
        {"f1": 0.10, "recall": 0.10, "precision": 0.10},
        aiq.ExtractionThresholds(f1_min=0.70, recall_min=0.75, enforce=False),
    )
    assert gate["passed"] is False
    assert gate["breaches"]
    assert gate["should_fail"] is False


def test_gate_enforced_breach_fails():
    gate = aiq.evaluate_extraction_gate(
        {"f1": 0.10, "recall": 0.10, "precision": 0.10},
        aiq.ExtractionThresholds(f1_min=0.70, enforce=True),
    )
    assert gate["should_fail"] is True


def test_gate_only_checks_set_bars():
    """An unset recall_min/precision_min (0.0) is not gated."""
    gate = aiq.evaluate_extraction_gate(
        {"f1": 0.80, "recall": 0.10, "precision": 0.10},
        aiq.ExtractionThresholds(f1_min=0.70, enforce=True),
    )
    assert gate["passed"] is True
    assert gate["should_fail"] is False


def test_gate_missing_f1_is_loud():
    with pytest.raises(ValueError, match="f1"):
        aiq.evaluate_extraction_gate({}, aiq.ExtractionThresholds(f1_min=0.70))
