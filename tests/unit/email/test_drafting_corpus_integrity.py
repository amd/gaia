# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the voice/style drafting eval (#1607 / #1269 / #1948).

Mirrors ``test_corpus_integrity.py`` for the drafting seed corpus, plus
offline coverage of the judge scorer in ``gaia.eval.draft_quality``:

- The committed corpus loads, is the declared size, and every case is
  schema-well-formed (the loader is the validator — fail-loud).
- Duplicate case ids and missing required fields are rejected loudly.
- Every case's ``sent_history`` feeds the REAL #1607 analyzer
  (``analyze_sent_bodies``) and yields a profile — the corpus can actually
  drive the feature path.
- The judge prompt carries the rubric, intent, and draft.
- Verdict parsing accepts the documented JSON and rejects garbage,
  missing fields, and out-of-range scores.
- The scorer runs end-to-end on a mocked judge (no backend, no network)
  and produces an approval rate in range plus a build_scorecard-compatible
  summary.
- The committed thresholds manifest ships the #1269 bar in report mode
  (``enforce: false``) and the gate honors the ``should_fail`` contract.

Everything here runs offline: no Lemonade, no Anthropic, no live mailbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.eval import draft_quality as dq
from gaia.eval.scorecard import build_scorecard

_REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = _REPO_ROOT / "tests" / "fixtures" / "email" / "drafting_ground_truth.json"
THRESHOLDS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "email" / "drafting_gate_thresholds.json"
)


@pytest.fixture(scope="module")
def corpus() -> dict:
    return dq.load_drafting_corpus(CORPUS_PATH)


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------


def test_corpus_loads_and_has_seed_size(corpus):
    """Seed corpus is committed, loadable, and in the declared 15–25 range."""
    cases = dq.corpus_cases(corpus)
    assert 15 <= len(cases) <= 25, f"seed corpus size out of range: {len(cases)}"


def test_meta_block_present_and_well_formed(corpus):
    meta = corpus["_meta"]
    assert meta["fixture"] == "drafting_ground_truth.json"
    assert meta["fixture_kind"] == "hand-authored-seed"
    assert isinstance(meta["schema_version"], int)


def test_every_case_is_schema_well_formed(corpus):
    """The loader already validates; assert the shape it guarantees."""
    for case_id, case in dq.corpus_cases(corpus).items():
        assert not case_id.startswith("_")
        assert case["persona"].strip()
        for field in ("from", "subject", "body"):
            assert case["incoming"][field].strip(), f"{case_id}: incoming.{field}"
        assert len(case["sent_history"]) >= 3, case_id
        assert case["reply_intent"].strip(), case_id
        assert case["rubric"]["must"], case_id
        assert isinstance(case["rubric"]["must_not"], list), case_id


def test_duplicate_case_ids_rejected(tmp_path):
    dup = tmp_path / "dup.json"
    dup.write_text(
        '{"draft-001": {}, "draft-001": {}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate case id"):
        dq.load_drafting_corpus(dup)


def test_missing_required_field_rejected(tmp_path, corpus):
    cases = dq.corpus_cases(corpus)
    case_id, case = next(iter(cases.items()))
    broken = {case_id: {k: v for k, v in case.items() if k != "reply_intent"}}
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="reply_intent"):
        dq.load_drafting_corpus(path)


def test_sent_history_drives_the_real_voice_analyzer(corpus):
    """Every case's style signal must survive the REAL #1607 analyzer —
    otherwise the live generation stage could not build a profile from it.
    """
    pytest.importorskip("gaia_agent_email")
    from gaia_agent_email.voice_profile import analyze_sent_bodies

    for case_id, case in dq.corpus_cases(corpus).items():
        profile = analyze_sent_bodies(case["sent_history"])
        assert profile["sample_count"] == len(case["sent_history"]), case_id
        assert profile["median_words"] > 0, case_id


# ---------------------------------------------------------------------------
# Judge prompt + verdict parsing
# ---------------------------------------------------------------------------

_DRAFT = {
    "to": "Dana Kim <dana@acme.io>",
    "subject": "Re: Design review + onboarding flow",
    "body": "Hey Dana,\n\n3pm Thursday works! I'll send onboarding notes tomorrow.\n\nCheers,\nSam",
}


def _verdict_json(**overrides):
    verdict = {
        "recipient_and_intent": True,
        "grounded": True,
        "no_fabricated_commitments": True,
        "voice_match": 0.9,
        "overall_quality": 0.85,
        "approved": True,
        "rationale": "Matches intent and voice.",
    }
    verdict.update(overrides)
    return json.dumps(verdict)


def test_judge_prompt_carries_rubric_intent_and_draft(corpus):
    case = dq.corpus_cases(corpus)["draft-001"]
    prompt = dq.build_judge_prompt(case, _DRAFT)
    assert case["reply_intent"] in prompt
    for item in case["rubric"]["must"] + case["rubric"]["must_not"]:
        assert item in prompt
    assert _DRAFT["body"] in prompt
    # Voice exemplars from Sent history are what the judge matches against.
    assert case["sent_history"][0] in prompt


def test_parse_verdict_happy_path_tolerates_fences():
    text = "```json\n" + _verdict_json() + "\n```"
    verdict = dq.parse_judge_verdict(text)
    assert verdict["approved"] is True
    assert verdict["voice_match"] == 0.9


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no json here",
        '{"approved": true}',  # missing fields
        _verdict_json(voice_match=1.7),  # out of range
        _verdict_json(approved="yes"),  # wrong type
        _verdict_json(overall_quality=True),  # bool is not a score
    ],
)
def test_parse_verdict_rejects_garbage(bad):
    with pytest.raises(ValueError):
        dq.parse_judge_verdict(bad)


# ---------------------------------------------------------------------------
# Scorer on a mocked judge (offline end-to-end)
# ---------------------------------------------------------------------------


def _generations(corpus, *, drop_draft_for=()):
    gens = []
    for case_id in dq.corpus_cases(corpus):
        draft = None if case_id in drop_draft_for else dict(_DRAFT)
        gens.append(
            {
                "case_id": case_id,
                "draft": draft,
                "error": "stub: no draft" if draft is None else "",
                "duration_ms": 10,
            }
        )
    return gens


def test_scorer_end_to_end_with_mocked_judge(corpus):
    """Mocked judge → approval rate in range, scorecard-compatible rows."""
    calls = {"n": 0}

    def mock_judge(prompt: str) -> str:
        calls["n"] += 1
        # Alternate approve/reject so the rate is strictly inside (0, 1).
        return _verdict_json(approved=calls["n"] % 2 == 1)

    results = dq.judge_drafts(
        corpus, _generations(corpus), mock_judge, model_id="stub-model"
    )
    cases = dq.corpus_cases(corpus)
    assert len(results) == len(cases)
    assert calls["n"] == len(cases)
    for r in results:
        assert r["status"] in {"PASS", "FAIL"}
        assert r["category"] == "stub-model"
        assert 0.0 <= r["overall_score"] <= 10.0

    summary = dq.summarize_drafting(
        results,
        run_id="unit",
        thresholds=dq.DraftingThresholds(approval_min=0.70, enforce=False),
    )
    agg = summary["drafting"]
    assert 0.0 < agg["draft_approval_rate"] < 1.0
    assert agg["cases_judged"] == len(cases)
    assert len(agg["per_case"]) == len(cases)
    # The rows also aggregate through the shared scorecard builder unchanged.
    scorecard = build_scorecard("unit", results, {})
    assert scorecard["summary"]["total_scenarios"] == len(cases)
    assert scorecard["summary"]["passed"] + scorecard["summary"]["failed"] == len(cases)


def test_missing_draft_and_unparseable_judge_become_errored(corpus):
    cases = list(dq.corpus_cases(corpus))
    first, second = cases[0], cases[1]

    def judge(prompt: str) -> str:
        return "not json at all"

    results = dq.judge_drafts(
        corpus,
        _generations(corpus, drop_draft_for={first}),
        judge,
        model_id="stub-model",
    )
    by_id = {r["id"]: r for r in results}
    assert by_id[first]["status"] == "ERRORED"
    assert "no draft" in by_id[first]["error"]
    assert by_id[second]["status"] == "ERRORED"
    assert "judge verdict unusable" in by_id[second]["error"]

    # No verdicts at all → gate is a loud explicit skip, never a pass.
    summary = dq.summarize_drafting(
        results,
        run_id="unit",
        thresholds=dq.DraftingThresholds(approval_min=0.70, enforce=False),
    )
    assert "drafting" not in summary
    assert summary["drafting_gate"]["skipped"] is True
    assert summary["drafting_gate"]["should_fail"] is False


# ---------------------------------------------------------------------------
# Committed thresholds manifest + gate contract
# ---------------------------------------------------------------------------


def test_committed_thresholds_manifest_is_report_mode():
    thresholds = dq.load_drafting_thresholds(THRESHOLDS_PATH)
    assert thresholds.approval_min == 0.70  # the #1269 reported target
    assert thresholds.enforce is False  # report mode — see the manifest comment
    # The module default points at the same committed manifest.
    assert dq.default_drafting_thresholds_path() == THRESHOLDS_PATH


def test_malformed_thresholds_manifest_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"enforce": false}', encoding="utf-8")
    with pytest.raises(ValueError, match="approval_min"):
        dq.load_drafting_thresholds(path)


def test_gate_report_mode_never_fails_the_build():
    gate = dq.evaluate_drafting_gate(
        {"draft_approval_rate": 0.25},
        dq.DraftingThresholds(approval_min=0.70, enforce=False),
    )
    assert gate["passed"] is False
    assert gate["breaches"]
    assert gate["should_fail"] is False


def test_gate_enforced_breach_fails():
    gate = dq.evaluate_drafting_gate(
        {"draft_approval_rate": 0.25},
        dq.DraftingThresholds(approval_min=0.70, enforce=True),
    )
    assert gate["should_fail"] is True


def test_gate_missing_rate_is_loud():
    with pytest.raises(ValueError, match="draft_approval_rate"):
        dq.evaluate_drafting_gate({}, dq.DraftingThresholds(approval_min=0.70))
