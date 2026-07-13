# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the follow-up detection eval (#1606 / #1950).

Mirrors ``test_action_items_corpus_integrity.py`` for the follow-up seed corpus,
plus offline coverage of the scorer + aggregation in
``gaia.eval.followup_quality``:

- The committed corpus loads, is the declared size, carries both positives and
  hard negatives, and every case is schema-well-formed (the loader is the
  validator — fail-loud).
- Duplicate case ids, missing required fields, a mislabeled hard negative, a
  thread with no outbound message, and a malformed message are rejected loudly.
- The scorer runs end-to-end on hand-fed predicted-vs-expected flags (no
  backend, no network): a correct flag PASSes, a spurious flag on a hard
  negative is a precision-denominator false positive, a missed genuine follow-up
  is a false negative, and errored generations stay ERRORED.
- Generation is exercised with an injected stub detector (no email package) and,
  when the email agent is installed, against the REAL ``check_followups_impl``
  over the whole corpus — locking in that the corpus actually drives both
  failure modes (false positives on courtesy notes, a false negative on an
  auto-reply).
- The committed thresholds manifest ships the #1950 bars in report mode
  (``enforce: false``) and the gate honors the ``should_fail`` contract.

Everything here runs offline: no Lemonade, no Anthropic, no live mailbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.eval import followup_quality as fq
from gaia.eval.scorecard import build_scorecard

_REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "email" / "followups_ground_truth.json"
)
THRESHOLDS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "email" / "followups_gate_thresholds.json"
)


@pytest.fixture(scope="module")
def corpus() -> dict:
    return fq.load_followup_corpus(CORPUS_PATH)


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------


def test_corpus_loads_and_has_seed_size(corpus):
    """Seed corpus is committed, loadable, and in the declared 14–25 range."""
    cases = fq.corpus_cases(corpus)
    assert 14 <= len(cases) <= 25, f"seed corpus size out of range: {len(cases)}"


def test_meta_block_present_and_well_formed(corpus):
    meta = corpus["_meta"]
    assert meta["fixture"] == "followups_ground_truth.json"
    assert meta["fixture_kind"] == "hand-authored-seed"
    assert isinstance(meta["schema_version"], int)
    # The detection window the scorer runs at is documented in the fixture.
    assert meta["detection_params"]["window_days"] == fq.DEFAULT_FOLLOWUP_WINDOW_DAYS


def test_corpus_has_both_positives_and_hard_negatives(corpus):
    """A precision/recall eval is only meaningful with both classes present."""
    cases = fq.corpus_cases(corpus)
    hard = [c for c in cases.values() if fq.is_hard_negative(c)]
    positive = [c for c in cases.values() if not fq.is_hard_negative(c)]
    assert len(hard) >= 3, "need several hard negatives for the FP denominator"
    assert len(positive) >= 3, "need several awaits-reply positives"


def test_every_case_is_schema_well_formed(corpus):
    """The loader already validates; assert the shape it guarantees."""
    for case_id, case in fq.corpus_cases(corpus).items():
        assert not case_id.startswith("_")
        assert case["scenario"].strip()
        assert isinstance(case["awaits_reply"], bool)
        assert isinstance(case["thread"], list) and case["thread"], case_id
        has_outbound = False
        for msg in case["thread"]:
            assert msg["direction"] in {"outbound", "inbound"}, case_id
            assert msg["subject"].strip(), case_id
            assert msg["body"].strip(), case_id
            assert isinstance(msg["age_days"], (int, float)), case_id
            if msg["direction"] == "outbound":
                has_outbound = True
                assert msg["to"].strip(), case_id
            else:
                assert msg["from"].strip(), case_id
        assert has_outbound, f"{case_id}: sent thread needs an outbound message"


def test_duplicate_case_ids_rejected(tmp_path):
    dup = tmp_path / "dup.json"
    dup.write_text('{"fu-001": {}, "fu-001": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case id"):
        fq.load_followup_corpus(dup)


def test_missing_required_field_rejected(tmp_path, corpus):
    cases = fq.corpus_cases(corpus)
    case_id, case = next(iter(cases.items()))
    broken = {case_id: {k: v for k, v in case.items() if k != "thread"}}
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="thread"):
        fq.load_followup_corpus(path)


def test_mislabeled_hard_negative_rejected(tmp_path):
    """A hard-negative flag that disagrees with the label is loud."""
    bad = {
        "fu-x": {
            "scenario": "mislabeled",
            "awaits_reply": True,
            "is_hard_negative": True,
            "thread": [
                {
                    "direction": "outbound",
                    "to": "a@b.c",
                    "subject": "s",
                    "body": "b",
                    "age_days": 5,
                }
            ],
        }
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="is_hard_negative"):
        fq.load_followup_corpus(path)


def test_thread_without_outbound_rejected(tmp_path):
    """A sent-thread case must contain at least one outbound message."""
    bad = {
        "fu-x": {
            "scenario": "no outbound",
            "awaits_reply": False,
            "thread": [
                {
                    "direction": "inbound",
                    "from": "a@b.c",
                    "subject": "s",
                    "body": "b",
                    "age_days": 5,
                }
            ],
        }
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="outbound"):
        fq.load_followup_corpus(path)


def test_outbound_without_recipient_rejected(tmp_path):
    bad = {
        "fu-x": {
            "scenario": "no recipient",
            "awaits_reply": True,
            "thread": [
                {"direction": "outbound", "subject": "s", "body": "b", "age_days": 5}
            ],
        }
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="to"):
        fq.load_followup_corpus(path)


# ---------------------------------------------------------------------------
# Scorer on hand-fed predictions (offline)
# ---------------------------------------------------------------------------


def test_score_case_correct_positive_passes(corpus):
    case = fq.corpus_cases(corpus)["fu-001"]
    assert case["awaits_reply"] is True
    row = fq.score_case("fu-001", case, True, model_id="stub-detector")
    assert row["status"] == "PASS"
    assert row["overall_score"] == 10.0
    assert row["followup_match"]["tp"] == 1
    assert row["category"] == "stub-detector"


def test_score_case_correct_hard_negative_passes(corpus):
    """Correctly NOT flagging a no-reply-needed thread is a perfect case."""
    case = fq.corpus_cases(corpus)["fu-neg-001"]
    assert fq.is_hard_negative(case)
    row = fq.score_case("fu-neg-001", case, False, model_id="stub-detector")
    assert row["status"] == "PASS"
    assert row["followup_match"]["tn"] == 1


def test_score_case_false_positive_on_hard_negative_fails(corpus):
    case = fq.corpus_cases(corpus)["fu-neg-001"]
    row = fq.score_case("fu-neg-001", case, True, model_id="stub-detector")
    assert row["status"] == "FAIL"
    assert row["followup_match"]["fp"] == 1
    assert row["overall_score"] == 0.0


def test_score_case_false_negative_on_positive_fails(corpus):
    case = fq.corpus_cases(corpus)["fu-006"]
    assert case["awaits_reply"] is True
    row = fq.score_case("fu-006", case, False, model_id="stub-detector")
    assert row["status"] == "FAIL"
    assert row["followup_match"]["fn"] == 1


def test_score_case_none_prediction_is_errored(corpus):
    case = fq.corpus_cases(corpus)["fu-001"]
    row = fq.score_case("fu-001", case, None, model_id="stub-detector", error="boom")
    assert row["status"] == "ERRORED"
    assert row["error"] == "boom"
    assert "followup_match" not in row


def test_summarize_micro_confusion_and_scorecard(corpus):
    """A perfect run (every predicted flag == label) → precision/recall/F1 all
    1.0 and a build_scorecard-compatible summary."""
    cases = fq.corpus_cases(corpus)
    results = [
        fq.score_case(cid, c, c["awaits_reply"], model_id="stub-detector")
        for cid, c in cases.items()
    ]
    summary = fq.summarize_followups(
        results,
        run_id="unit",
        thresholds=fq.FollowupThresholds(f1_min=0.70, recall_min=0.80),
    )
    agg = summary["followups"]
    assert agg["cases_scored"] == len(cases)
    assert agg["precision"] == 1.0
    assert agg["recall"] == 1.0
    assert agg["f1"] == 1.0
    assert agg["fp"] == 0 and agg["fn"] == 0
    assert agg["hard_negatives_total"] >= 3
    assert agg["hard_negative_correct_rate"] == 1.0
    # Gate passes in report mode (perfect run).
    assert summary["followup_gate"]["passed"] is True
    assert summary["followup_gate"]["should_fail"] is False
    # Rows also aggregate through the shared scorecard builder unchanged.
    scorecard = build_scorecard("unit", results, {})
    assert scorecard["summary"]["total_scenarios"] == len(cases)
    assert scorecard["summary"]["passed"] == len(cases)


def test_summarize_counts_false_positive_and_negative(corpus):
    """A spurious flag on a hard negative is an FP; a missed positive is an FN,
    and both drop the corresponding rate below perfect."""
    cases = fq.corpus_cases(corpus)
    results = []
    for cid, c in cases.items():
        if cid == "fu-neg-002":
            predicted = True  # false positive on a no-reply-needed broadcast
        elif cid == "fu-005":
            predicted = False  # false negative on a genuine unanswered question
        else:
            predicted = c["awaits_reply"]
        results.append(fq.score_case(cid, c, predicted, model_id="stub-detector"))
    agg = fq.summarize_followups(results, run_id="unit")["followups"]
    assert agg["fp"] >= 1
    assert agg["fn"] >= 1
    assert agg["precision"] < 1.0
    assert agg["recall"] < 1.0
    assert agg["hard_negative_correct_rate"] < 1.0


def test_summarize_gate_skipped_when_nothing_scored():
    """All-errored run → gate is a loud explicit skip, never a pass."""
    results = [{"id": "fu-001", "category": "m", "status": "ERRORED", "error": "x"}]
    summary = fq.summarize_followups(
        results, run_id="unit", thresholds=fq.FollowupThresholds(f1_min=0.70)
    )
    assert "followups" not in summary
    assert summary["followup_gate"]["skipped"] is True
    assert summary["followup_gate"]["should_fail"] is False


# ---------------------------------------------------------------------------
# Generation stage — injected stub detector (offline, no email package)
# ---------------------------------------------------------------------------


def test_generate_detections_with_stub_detector(corpus):
    """generate_detections builds a backend per case, runs the injected
    detector, and maps its flagged thread-ids back to per-case booleans."""
    flag = {"fu-001", "fu-neg-001"}

    def stub_detector(backend, *, window_days, now_ms):
        listing = backend.list_messages(label_ids=["SENT"], max_results=50)
        tids = {m["threadId"] for m in listing["messages"]}
        return {"awaiting_reply": [{"thread_id": t} for t in tids if t in flag]}

    gens = fq.generate_detections(corpus_path=CORPUS_PATH, detector_fn=stub_detector)
    by_id = {g["case_id"]: g for g in gens}
    assert by_id["fu-001"]["predicted"] is True
    assert by_id["fu-neg-001"]["predicted"] is True
    assert by_id["fu-006"]["predicted"] is False
    assert by_id["fu-neg-006"]["predicted"] is False
    assert all(not g["error"] for g in gens)

    # Score the stub run: fu-001 TP (PASS), fu-neg-001 FP (FAIL), fu-006 FN (FAIL).
    results = fq.score_generations(corpus, gens, model_id="stub-detector")
    by_row = {r["id"]: r for r in results}
    assert by_row["fu-001"]["status"] == "PASS"
    assert by_row["fu-neg-001"]["status"] == "FAIL"
    assert by_row["fu-006"]["status"] == "FAIL"


def test_score_generations_unknown_case_id_rejected(corpus):
    with pytest.raises(ValueError, match="unknown case id"):
        fq.score_generations(
            corpus, [{"case_id": "nope", "predicted": True}], model_id="m"
        )


# ---------------------------------------------------------------------------
# Generation stage — REAL detector (offline; needs the email agent installed)
# ---------------------------------------------------------------------------


def test_real_detector_drives_both_failure_modes(corpus):
    """Over the committed corpus, the REAL latest-outbound heuristic must exhibit
    both failure modes the eval exists to catch: false positives on courtesy /
    FYI notes and a false negative on the auto-reply trap. This locks the corpus
    to its intended behavior against the shipped detector."""
    pytest.importorskip("gaia_agent_email")

    gens = fq.generate_detections(corpus_path=CORPUS_PATH)
    assert all(not g["error"] for g in gens), [g for g in gens if g["error"]]
    results = fq.score_generations(corpus, gens, model_id="check_followups")
    agg = fq.summarize_followups(results, run_id="unit")["followups"]

    # Detector flags every aged outbound-latest thread with an external
    # recipient — so it catches the real follow-ups (recall high) but also nags
    # on courtesy/FYI notes (precision low) and misses the auto-reply case.
    assert agg["tp"] == 5
    assert agg["fn"] == 1  # the out-of-office auto-reply trap (fu-006)
    assert agg["fp"] == 6  # thanks / FYI / acknowledgment / decline notes
    assert agg["tn"] == 4
    assert agg["fp"] > 0 and agg["fn"] > 0
    # The known precision gap is exactly why the committed gate ships report-mode.
    assert agg["precision"] < 0.7


# ---------------------------------------------------------------------------
# Committed thresholds manifest + gate contract
# ---------------------------------------------------------------------------


def test_committed_thresholds_manifest_is_report_mode():
    thresholds = fq.load_followup_thresholds(THRESHOLDS_PATH)
    assert thresholds.f1_min == 0.70
    assert thresholds.recall_min == 0.80
    assert thresholds.precision_min == 0.60
    assert thresholds.enforce is False  # report mode — see the manifest comment
    assert fq.default_followup_thresholds_path() == THRESHOLDS_PATH


def test_malformed_thresholds_manifest_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"enforce": false}', encoding="utf-8")
    with pytest.raises(ValueError, match="f1_min"):
        fq.load_followup_thresholds(path)


def test_gate_report_mode_never_fails_the_build():
    gate = fq.evaluate_followup_gate(
        {"f1": 0.10, "recall": 0.10, "precision": 0.10},
        fq.FollowupThresholds(f1_min=0.70, recall_min=0.80, enforce=False),
    )
    assert gate["passed"] is False
    assert gate["breaches"]
    assert gate["should_fail"] is False


def test_gate_enforced_breach_fails():
    gate = fq.evaluate_followup_gate(
        {"f1": 0.10, "recall": 0.10, "precision": 0.10},
        fq.FollowupThresholds(f1_min=0.70, enforce=True),
    )
    assert gate["should_fail"] is True


def test_gate_only_checks_set_bars():
    """An unset recall_min/precision_min (0.0) is not gated."""
    gate = fq.evaluate_followup_gate(
        {"f1": 0.80, "recall": 0.10, "precision": 0.10},
        fq.FollowupThresholds(f1_min=0.70, enforce=True),
    )
    assert gate["passed"] is True
    assert gate["should_fail"] is False


def test_gate_missing_f1_is_loud():
    with pytest.raises(ValueError, match="f1"):
        fq.evaluate_followup_gate({}, fq.FollowupThresholds(f1_min=0.70))
