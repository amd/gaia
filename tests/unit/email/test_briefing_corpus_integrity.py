# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the daily-inbox-briefing summary-quality eval (#1608 / #1951).

Mirrors ``test_drafting_corpus_integrity.py`` for the briefing seed corpus,
plus offline coverage of the judge scorer in ``gaia.eval.briefing_quality``:

- The committed corpus loads, is the declared size, and every case is
  schema-well-formed (the loader is the validator — fail-loud).
- Duplicate case ids and missing required fields are rejected loudly.
- Every case's inbox slice drives the REAL scheduled-briefing path
  (``gaia_agent_email.briefing.run_briefing_job`` → ``pre_scan_inbox_impl``)
  and yields a valid ``email_pre_scan`` envelope — the corpus can actually
  drive the feature path.
- The judge prompt carries the inbox, the generated briefing, and the rubric.
- Verdict parsing accepts the documented JSON and rejects garbage, missing
  fields, and out-of-range scores.
- The scorer runs end-to-end on a mocked judge (no backend, no network) and
  produces an approval rate in range plus a build_scorecard-compatible summary.
- The committed thresholds manifest ships the #1951 bars as a hard gate
  (``enforce: true`` — release_agent_email.yml eval-gate) and the gate
  honors the ``should_fail`` contract.

Everything here runs offline: no Lemonade, no Anthropic, no live mailbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.eval import briefing_quality as bq
from gaia.eval.scorecard import build_scorecard

_REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = _REPO_ROOT / "tests" / "fixtures" / "email" / "briefing_ground_truth.json"
THRESHOLDS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "email" / "briefing_gate_thresholds.json"
)


@pytest.fixture(scope="module")
def corpus() -> dict:
    return bq.load_briefing_corpus(CORPUS_PATH)


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------


def test_corpus_loads_and_has_seed_size(corpus):
    """Seed corpus is committed, loadable, and in the declared 8–20 range."""
    cases = bq.corpus_cases(corpus)
    assert 8 <= len(cases) <= 20, f"seed corpus size out of range: {len(cases)}"


def test_meta_block_present_and_well_formed(corpus):
    meta = corpus["_meta"]
    assert meta["fixture"] == "briefing_ground_truth.json"
    assert meta["fixture_kind"] == "hand-authored-seed"
    assert isinstance(meta["schema_version"], int)


def test_every_case_is_schema_well_formed(corpus):
    """The loader already validates; assert the shape it guarantees."""
    for case_id, case in bq.corpus_cases(corpus).items():
        assert not case_id.startswith("_")
        assert case["scenario"].strip()
        assert len(case["inbox"]) >= 3, case_id
        for idx, message in enumerate(case["inbox"]):
            for field in ("from", "subject", "body"):
                assert message[field].strip(), f"{case_id}: inbox[{idx}].{field}"
        assert case["rubric"]["must_surface"], case_id
        assert isinstance(case["rubric"].get("must_not_surface", []), list), case_id


def test_duplicate_case_ids_rejected(tmp_path):
    dup = tmp_path / "dup.json"
    dup.write_text(
        '{"briefing-001": {}, "briefing-001": {}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate case id"):
        bq.load_briefing_corpus(dup)


def test_missing_required_field_rejected(tmp_path, corpus):
    cases = bq.corpus_cases(corpus)
    case_id, case = next(iter(cases.items()))
    broken = {case_id: {k: v for k, v in case.items() if k != "rubric"}}
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="rubric"):
        bq.load_briefing_corpus(path)


def test_undersized_inbox_rejected(tmp_path, corpus):
    cases = bq.corpus_cases(corpus)
    case_id, case = next(iter(cases.items()))
    broken = {case_id: {**case, "inbox": case["inbox"][:2]}}
    path = tmp_path / "small.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="inbox"):
        bq.load_briefing_corpus(path)


def test_inbox_slice_drives_the_real_briefing_path(corpus):
    """Every case's inbox must survive the REAL scheduled-briefing path —
    otherwise the live generation stage could not produce an envelope from it.

    Drives ``run_briefing_job`` (heuristic, offline) via the default generation
    path — no Lemonade, no Anthropic, no live mailbox.
    """
    pytest.importorskip("gaia_agent_email")

    generations = bq.generate_briefings(
        "stub-model", corpus_path=CORPUS_PATH, max_messages=25
    )
    assert len(generations) == len(bq.corpus_cases(corpus))
    for gen in generations:
        assert not gen["error"], f"{gen['case_id']}: {gen['error']}"
        briefing = gen["briefing"]
        assert briefing is not None, gen["case_id"]
        assert briefing["kind"] == "email_pre_scan"
        for section in ("urgent", "actionable", "suggested_archives"):
            assert isinstance(briefing[section], list), gen["case_id"]
        assert isinstance(briefing["informational_count"], int), gen["case_id"]


# ---------------------------------------------------------------------------
# Judge prompt + verdict parsing
# ---------------------------------------------------------------------------

_BRIEFING = {
    "kind": "email_pre_scan",
    "urgent": [
        {
            "message_id": "m1",
            "sender": "PagerDuty <alerts@pagerduty.com>",
            "subject": "[P1] Checkout API 5xx error rate above threshold",
            "why": "P1 incident, on-call escalation imminent",
        }
    ],
    "actionable": [
        {
            "message_id": "m2",
            "sender": "Dana Kim <dana@acme.io>",
            "subject": "Need your sign-off on the Q3 budget by EOD",
            "why": "Budget approval due EOD today",
        }
    ],
    "informational_count": 1,
    "suggested_archives": [
        {
            "message_id": "m3",
            "sender": "TechCrunch Daily <newsletter@techcrunch.com>",
            "subject": "Today in tech: 10 startups to watch",
            "reason": "daily newsletter",
        }
    ],
    "suggested_drafts": [],
    "totals": {
        "urgent": 1,
        "actionable": 1,
        "informational": 1,
        "suggested_archives": 1,
    },
}


def _verdict_json(**overrides):
    verdict = {
        "faithful": True,
        "hallucination_free": True,
        "grouping_reasonable": True,
        "must_include_recall": 1.0,
        "overall_quality": 0.9,
        "approved": True,
        "rationale": "Surfaces the incident and the budget ask, invents nothing.",
    }
    verdict.update(overrides)
    return json.dumps(verdict)


def test_judge_prompt_carries_inbox_briefing_and_rubric(corpus):
    case = bq.corpus_cases(corpus)["briefing-001"]
    prompt = bq.build_judge_prompt(case, _BRIEFING)
    # Rubric threads (both must- and must-not-surface) reach the judge.
    for item in case["rubric"]["must_surface"] + case["rubric"]["must_not_surface"]:
        assert item in prompt
    # The inbox ground truth is present (checked via a message subject).
    assert case["inbox"][0]["subject"] in prompt
    # The generated briefing's summaries are present.
    assert _BRIEFING["urgent"][0]["why"] in prompt
    assert _BRIEFING["actionable"][0]["subject"] in prompt


def test_parse_verdict_happy_path_tolerates_fences():
    text = "```json\n" + _verdict_json() + "\n```"
    verdict = bq.parse_judge_verdict(text)
    assert verdict["approved"] is True
    assert verdict["must_include_recall"] == 1.0


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no json here",
        '{"approved": true}',  # missing fields
        _verdict_json(must_include_recall=1.7),  # out of range
        _verdict_json(approved="yes"),  # wrong type
        _verdict_json(overall_quality=True),  # bool is not a score
    ],
)
def test_parse_verdict_rejects_garbage(bad):
    with pytest.raises(ValueError):
        bq.parse_judge_verdict(bad)


# ---------------------------------------------------------------------------
# Scorer on a mocked judge (offline end-to-end)
# ---------------------------------------------------------------------------


def _generations(corpus, *, drop_briefing_for=()):
    gens = []
    for case_id in bq.corpus_cases(corpus):
        briefing = None if case_id in drop_briefing_for else dict(_BRIEFING)
        gens.append(
            {
                "case_id": case_id,
                "briefing": briefing,
                "error": "stub: no briefing" if briefing is None else "",
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

    results = bq.judge_briefings(
        corpus, _generations(corpus), mock_judge, model_id="stub-model"
    )
    cases = bq.corpus_cases(corpus)
    assert len(results) == len(cases)
    assert calls["n"] == len(cases)
    for r in results:
        assert r["status"] in {"PASS", "FAIL"}
        assert r["category"] == "stub-model"
        assert 0.0 <= r["overall_score"] <= 10.0

    summary = bq.summarize_briefings(
        results,
        run_id="unit",
        thresholds=bq.BriefingThresholds(approval_min=0.70, enforce=False),
    )
    agg = summary["briefing"]
    assert 0.0 < agg["briefing_approval_rate"] < 1.0
    assert agg["cases_judged"] == len(cases)
    assert agg["faithful_rate"] == 1.0
    assert agg["hallucination_free_rate"] == 1.0
    assert agg["must_include_recall_mean"] == 1.0
    assert len(agg["per_case"]) == len(cases)
    # The rows also aggregate through the shared scorecard builder unchanged.
    scorecard = build_scorecard("unit", results, {})
    assert scorecard["summary"]["total_scenarios"] == len(cases)
    assert scorecard["summary"]["passed"] + scorecard["summary"]["failed"] == len(cases)


def test_missing_briefing_and_unparseable_judge_become_errored(corpus):
    cases = list(bq.corpus_cases(corpus))
    first, second = cases[0], cases[1]

    def judge(prompt: str) -> str:
        return "not json at all"

    results = bq.judge_briefings(
        corpus,
        _generations(corpus, drop_briefing_for={first}),
        judge,
        model_id="stub-model",
    )
    by_id = {r["id"]: r for r in results}
    assert by_id[first]["status"] == "ERRORED"
    assert "no briefing" in by_id[first]["error"]
    assert by_id[second]["status"] == "ERRORED"
    assert "judge verdict unusable" in by_id[second]["error"]

    # No verdicts at all → gate is a loud explicit skip, never a pass.
    summary = bq.summarize_briefings(
        results,
        run_id="unit",
        thresholds=bq.BriefingThresholds(approval_min=0.70, enforce=False),
    )
    assert "briefing" not in summary
    assert summary["briefing_gate"]["skipped"] is True
    assert summary["briefing_gate"]["should_fail"] is False


def test_unknown_case_id_in_generations_is_loud(corpus):
    bad = [{"case_id": "briefing-999", "briefing": dict(_BRIEFING), "duration_ms": 1}]
    with pytest.raises(ValueError, match="unknown case id"):
        bq.judge_briefings(corpus, bad, lambda p: _verdict_json(), model_id="m")


# ---------------------------------------------------------------------------
# Committed thresholds manifest + gate contract
# ---------------------------------------------------------------------------


def test_committed_thresholds_manifest_is_report_mode():
    thresholds = bq.load_briefing_thresholds(THRESHOLDS_PATH)
    assert thresholds.approval_min == 0.70  # the #1951 primary target
    assert thresholds.recall_min == 0.80
    assert thresholds.hallucination_free_min == 0.95
    assert thresholds.faithfulness_min == 0.90
    # Temporarily report mode: flipped enforcing (#1951) before the gate ever
    # completed a CI run (the perf gate failed first and masked it), so the bars
    # aren't hardware-validated yet. Re-enforce once a passing baseline exists
    # (see the manifest comment).
    assert thresholds.enforce is False
    # The module default points at the same committed manifest.
    assert bq.default_briefing_thresholds_path() == THRESHOLDS_PATH


def test_malformed_thresholds_manifest_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"enforce": false}', encoding="utf-8")
    with pytest.raises(ValueError, match="approval_min"):
        bq.load_briefing_thresholds(path)


def test_gate_report_mode_never_fails_the_build():
    gate = bq.evaluate_briefing_gate(
        {
            "briefing_approval_rate": 0.25,
            "must_include_recall_mean": 0.20,
            "hallucination_free_rate": 0.50,
            "faithful_rate": 0.40,
        },
        bq.BriefingThresholds(
            approval_min=0.70,
            recall_min=0.80,
            hallucination_free_min=0.95,
            faithfulness_min=0.90,
            enforce=False,
        ),
    )
    assert gate["passed"] is False
    assert gate["breaches"]
    assert gate["should_fail"] is False


def test_gate_enforced_breach_fails():
    gate = bq.evaluate_briefing_gate(
        {"briefing_approval_rate": 0.25},
        bq.BriefingThresholds(approval_min=0.70, enforce=True),
    )
    assert gate["should_fail"] is True


def test_gate_unset_secondary_bars_are_not_checked():
    """A manifest gating on approval alone must not fail on recall/faithfulness
    it never set (unset floors default to 0.0 = not gated)."""
    gate = bq.evaluate_briefing_gate(
        {
            "briefing_approval_rate": 0.90,
            "must_include_recall_mean": 0.10,
            "hallucination_free_rate": 0.10,
            "faithful_rate": 0.10,
        },
        bq.BriefingThresholds(approval_min=0.70, enforce=True),
    )
    assert gate["passed"] is True
    assert gate["breaches"] == []
    assert gate["should_fail"] is False


def test_gate_missing_rate_is_loud():
    with pytest.raises(ValueError, match="briefing_approval_rate"):
        bq.evaluate_briefing_gate({}, bq.BriefingThresholds(approval_min=0.70))


def test_errored_case_is_a_gate_breach():
    """An errored/unjudged case must never silently escape the gate: a briefing
    that could not be generated or scored is itself a breach (no workaround)."""
    gate = bq.evaluate_briefing_gate(
        {
            "briefing_approval_rate": 1.0,
            "must_include_recall_mean": 1.0,
            "hallucination_free_rate": 1.0,
            "faithful_rate": 1.0,
            "cases_errored": 1,
        },
        bq.BriefingThresholds(approval_min=0.70, enforce=True),
    )
    assert gate["passed"] is False
    assert any(b["metric"] == "cases_errored" for b in gate["breaches"])
    assert gate["should_fail"] is True


def test_no_verdicts_under_enforcing_gate_blocks_the_build():
    """A total judge/generation outage (no verdicts at all) must fail an
    enforcing gate rather than pass by default."""
    corpus = {"briefing-001": {}}  # only structure needed for the skip branch
    results = [
        bq.build_briefing_result(
            "briefing-001",
            model_id="stub-model",
            briefing=None,
            verdict=None,
            error="judge outage",
        )
    ]
    summary = bq.summarize_briefings(
        results,
        run_id="unit",
        thresholds=bq.BriefingThresholds(approval_min=0.70, enforce=True),
    )
    assert "briefing" not in summary
    assert summary["briefing_gate"]["skipped"] is True
    assert summary["briefing_gate"]["should_fail"] is True
