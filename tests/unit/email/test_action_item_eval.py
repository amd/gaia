# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the action-item extraction eval (#1605 / PR #1917, #1949).

Mirrors ``test_corpus_integrity.py`` for the hand-labeled corpus
``tests/fixtures/email/action_items_ground_truth.json``, and verifies the
scorer (:mod:`gaia.eval.action_item_metrics`) computes correct
precision/recall/F1 on hand-checked mini cases.

Everything here runs OFFLINE — no Lemonade, no LLM, no network. The judge
path is exercised with a stub callable only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.eval.action_item_metrics import (
    DEFAULT_MAX_JUDGE_CALLS,
    ActionItemThresholds,
    default_action_item_ground_truth_path,
    default_action_item_thresholds_path,
    evaluate_action_item_gate,
    load_action_item_ground_truth,
    load_action_item_thresholds,
    match_action_items,
    normalize_action_text,
    score_action_item_extraction,
)

_CORPUS_PATH = default_action_item_ground_truth_path()
_THRESHOLDS_PATH = default_action_item_thresholds_path()

# Required case fields and their expected python types.
_REQUIRED_CASE_FIELDS = {
    "subject": str,
    "body": str,
    "expected": list,
    "has_action_items": bool,
    "rationale": str,
}


def _reject_duplicate_keys(pairs):
    """``object_pairs_hook`` that fails loud on duplicate JSON keys —
    ``json.load`` would otherwise silently keep only the last one."""
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key in corpus JSON: {key!r}")
        seen[key] = value
    return seen


@pytest.fixture(scope="module")
def corpus() -> dict:
    raw = Path(_CORPUS_PATH).read_text(encoding="utf-8")
    return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)


def _cases(corpus: dict) -> dict:
    return {k: v for k, v in corpus.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------


def test_corpus_loads_with_no_duplicate_ids(corpus):
    """Loading with the duplicate-rejecting hook succeeds and yields cases."""
    assert len(_cases(corpus)) >= 20


def test_meta_block_present_and_counts_reconcile(corpus):
    meta = corpus["_meta"]
    assert meta["fixture"] == "action_items_ground_truth.json"
    assert isinstance(meta["schema_version"], int)
    cases = _cases(corpus)
    counts = meta["counts"]
    assert counts["cases"] == len(cases)
    assert counts["expected_items"] == sum(len(c["expected"]) for c in cases.values())
    assert counts["hard_negatives"] == sum(
        1 for c in cases.values() if not c["expected"]
    )


def test_case_schema_well_formed(corpus):
    for cid, case in _cases(corpus).items():
        for fld, typ in _REQUIRED_CASE_FIELDS.items():
            assert fld in case, f"{cid}: missing field {fld!r}"
            assert isinstance(
                case[fld], typ
            ), f"{cid}: field {fld!r} is {type(case[fld])}, want {typ}"
        assert case["body"].strip(), f"{cid}: empty body"
        # has_action_items must agree with the expected list.
        assert case["has_action_items"] == bool(
            case["expected"]
        ), f"{cid}: has_action_items disagrees with expected"


def test_expected_items_satisfy_action_item_contract(corpus):
    """Every labeled item must validate against the production ``ActionItem``
    model — the corpus can never drift from the feature's output contract."""
    # EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
    # skip when a framework-only env lacks it.
    contract = pytest.importorskip("gaia_agent_email.contract")
    for cid, case in _cases(corpus).items():
        for idx, item in enumerate(case["expected"]):
            try:
                contract.ActionItem(**item)
            except Exception as exc:  # noqa: BLE001 - re-raise with location
                raise AssertionError(
                    f"{cid}: expected item {idx} violates the ActionItem "
                    f"contract: {exc}"
                ) from exc


def test_corpus_has_hard_negatives_and_coverage(corpus):
    """The corpus must measure false positives (hard negatives), due-hint
    fidelity, link items, and recall on non-cue phrasing."""
    cases = _cases(corpus)
    negatives = [cid for cid, c in cases.items() if not c["expected"]]
    assert len(negatives) >= 6, f"too few hard negatives: {negatives}"
    all_items = [it for c in cases.values() for it in c["expected"]]
    assert sum(1 for it in all_items if it["due_hint"]) >= 5
    assert sum(1 for it in all_items if it["type"] == "link") >= 2
    multi = [cid for cid, c in cases.items() if len(c["expected"]) >= 2]
    assert multi, "need at least one multi-item email for set matching"


# ---------------------------------------------------------------------------
# Scorer — hand-checked mini cases
# ---------------------------------------------------------------------------


def test_normalize_action_text():
    assert (
        normalize_action_text("  Please, send me the Q3 report!  ")
        == "please send me the q3 report"
    )


def test_scorer_hand_checked_mini_case():
    """2 expected, 2 extracted: one exact-after-normalization match, one
    spurious extraction, one miss → tp=1 fp=1 fn=1, P=R=F1=0.5."""
    gt = {
        "m-1": {
            "body": "irrelevant",
            "expected": [
                {
                    "description": "Please send me the Q3 report by Friday.",
                    "due_hint": "by Friday",
                    "type": "text",
                    "url": None,
                },
                {
                    "description": "RSVP by Monday for the dinner.",
                    "due_hint": "by Monday",
                    "type": "text",
                    "url": None,
                },
            ],
        }
    }
    extractions = {
        "m-1": [
            # Same item, different casing/punctuation → exact normalized match.
            {
                "description": "please send me the Q3 report by Friday",
                "due_hint": "by Friday",
                "type": "text",
                "url": None,
            },
            # Spurious boilerplate → false positive.
            {
                "description": "No action required.",
                "due_hint": None,
                "type": "text",
                "url": None,
            },
        ]
    }
    q = score_action_item_extraction(gt, extractions)
    block = q["action_items"]
    assert (block["tp"], block["fp"], block["fn"]) == (1, 1, 1)
    assert block["precision"] == 0.5
    assert block["recall"] == 0.5
    assert block["f1"] == 0.5
    assert q["due_hint_accuracy"] == 1.0 and q["due_hint_pairs"] == 1


def test_fuzzy_match_absorbs_minor_rewording():
    res = match_action_items(
        ["Please send me the Q3 budget spreadsheet by Friday."],
        ["Please send the Q3 budget spreadsheet to me by Friday."],
    )
    assert len(res.matched) == 1
    assert res.matched[0][2] == "fuzzy"


def test_clearly_different_items_do_not_match():
    res = match_action_items(
        ["Please send me the Q3 budget spreadsheet by Friday."],
        ["RSVP by Monday for the team dinner."],
    )
    assert not res.matched
    assert res.unmatched_expected == [0] and res.unmatched_extracted == [0]


def test_hard_negative_extraction_counts_as_fp():
    gt = {"neg-1": {"body": "x", "expected": []}}
    q = score_action_item_extraction(
        gt, {"neg-1": [{"description": "No action required.", "type": "text"}]}
    )
    assert q["action_items"]["fp"] == 1
    assert q["negatives_total"] == 1 and q["negatives_clean"] == 0
    assert q["negative_case_fp_rate"] == 1.0


def test_missing_extraction_for_labeled_case_fails_loud():
    gt = {"m-1": {"body": "x", "expected": []}}
    with pytest.raises(ValueError, match="no extraction result"):
        score_action_item_extraction(gt, {})


# ---------------------------------------------------------------------------
# Judge path (stub callable — no network)
# ---------------------------------------------------------------------------


def test_judge_arbitrates_ambiguity_band_and_is_counted():
    expected = ["Send the signed NDA back to the auditors."]
    extracted = ["Return the signed NDA to the audit team."]
    # Sanity: this pair sits below the fuzzy bar (else the judge never runs).
    baseline = match_action_items(expected, extracted)
    assert not baseline.matched

    calls = []

    def stub_judge(a: str, b: str) -> bool:
        calls.append((a, b))
        return True

    res = match_action_items(
        expected, extracted, judge=stub_judge, judge_budget=[DEFAULT_MAX_JUDGE_CALLS]
    )
    assert len(res.matched) == 1 and res.matched[0][2] == "judge"
    assert res.judge_calls == len(calls) == 1
    assert res.judge_accepted == 1


def test_judge_budget_exhaustion_is_reported_not_silent():
    res = match_action_items(
        ["Send the signed NDA back to the auditors."],
        ["Return the signed NDA to the audit team."],
        judge=lambda a, b: True,
        judge_budget=[0],
    )
    assert not res.matched
    assert res.judge_calls == 0
    assert res.judge_skipped_over_budget == 1


# ---------------------------------------------------------------------------
# Gate + thresholds manifest
# ---------------------------------------------------------------------------


def test_committed_manifest_loads_and_ships_report_mode():
    thresholds = load_action_item_thresholds(_THRESHOLDS_PATH)
    assert thresholds.enforce is False, (
        "action_items_gate_thresholds.json must ship enforce:false (report "
        "mode) — flipping it to true is a deliberate, reviewed data change."
    )
    assert 0.0 < thresholds.precision_min <= 1.0
    assert 0.0 < thresholds.recall_min <= 1.0
    assert 0.0 < thresholds.f1_min <= 1.0


def test_gate_report_mode_never_fails_even_on_breach():
    quality = {"action_items": {"precision": 0.1, "recall": 0.1, "f1": 0.1}}
    gate = evaluate_action_item_gate(
        quality,
        ActionItemThresholds(precision_min=0.8, recall_min=0.75, f1_min=0.8),
    )
    assert gate["passed"] is False
    assert len(gate["breaches"]) == 3
    assert gate["should_fail"] is False  # enforce defaults to False


def test_gate_enforced_breach_should_fail():
    quality = {"action_items": {"precision": 0.1, "recall": 0.9, "f1": 0.9}}
    gate = evaluate_action_item_gate(
        quality,
        ActionItemThresholds(
            precision_min=0.8, recall_min=0.75, f1_min=0.8, enforce=True
        ),
    )
    assert gate["should_fail"] is True
    assert [b["metric"] for b in gate["breaches"]] == ["precision"]


def test_thresholds_loader_fails_loud_on_missing_keys(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"precision_min": 0.8}', encoding="utf-8")
    with pytest.raises(ValueError, match="missing required"):
        load_action_item_thresholds(bad)


# ---------------------------------------------------------------------------
# End-to-end offline: real extractor over the committed corpus
# ---------------------------------------------------------------------------


def test_real_extractor_scores_committed_corpus_offline():
    """The full eval path (corpus → production extractor → scorer → gate)
    runs offline and produces a coherent, non-degenerate report."""
    pytest.importorskip("gaia_agent_email")
    from gaia.eval.action_item_metrics import run_action_item_extraction

    gt = load_action_item_ground_truth(_CORPUS_PATH)
    extractions = run_action_item_extraction(gt)
    quality = score_action_item_extraction(gt, extractions)

    block = quality["action_items"]
    labeled = {k: v for k, v in gt.items() if not k.startswith("_")}
    total_expected = sum(len(c["expected"]) for c in labeled.values())
    # Confusion reconciles with the corpus: every expected item is either
    # matched or missed.
    assert block["tp"] + block["fn"] == total_expected
    assert quality["cases_scored"] == len(labeled)
    # Non-degenerate: the extractor finds most items, and the deliberate
    # hard cases keep both error channels measured.
    assert block["tp"] > 0
    assert 0.0 < block["precision"] <= 1.0
    assert 0.0 < block["recall"] <= 1.0
    assert quality["negatives_total"] >= 6
    assert quality["judge"]["enabled"] is False

    gate = evaluate_action_item_gate(
        quality, load_action_item_thresholds(_THRESHOLDS_PATH)
    )
    assert gate["should_fail"] is False  # committed manifest is report mode
