# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the long-thread adversarial fold eval (#1889 Increment 2).

Mirrors ``test_followups_corpus_integrity.py`` for a committed hand-authored
corpus, plus offline coverage of the scorer + aggregation in
``gaia.eval.longthread_quality``. Unlike the follow-up eval this increment
ships NO live-runner hook and NO thresholds/gate manifest — that lands with
the consolidated eval pass (#1319). The corpus exists to answer one question:
does the #1889 single-shot thread-fold actually preserve the LATEST message's
decisive fact when an older, now-superseded fact is also present in the
thread? Each case is built as a reversal: an older message states a "stale"
answer (a venue, a budget figure, a vendor) and the newest message overturns
it with the real "decisive" answer. A summarizer that silently clips instead
of folding would answer with the stale fact; one that folds correctly answers
with the decisive fact — the corpus and scorer together are the trap that
tells the two apart.

- **Corpus integrity**: the committed corpus loads, is exactly the 3 pinned
  cases (``lt-001``/``lt-002``/``lt-003``), the ``_meta`` block (including the
  budget block) is well-formed, every case is schema-well-formed with at least
  24 messages, and the four adversarial invariants hold: the decisive phrase
  lives ONLY in the last message, the stale phrase lives in an older message
  and NOT the last, ``must_mention``/``must_not_mention`` agree with the last
  message, and the raw newest-first join overflows
  ``context_budget.thread_budget_tokens()`` — the size precondition that makes
  the fold path actually engage.
- **Malformed-corpus rejection**: the loader is the validator (fail-loud) —
  duplicate ids, a missing ``messages`` list, a decisive phrase missing from
  the last message, a stale phrase leaking into the last message, a stale
  phrase absent from every older message, and an empty ``must_mention`` are
  all rejected with an actionable ``ValueError``.
- **Scorer on hand-fed summaries** (fully offline): case-insensitive
  substring matching against ``must_mention``/``must_not_mention`` drives
  PASS/FAIL/ERRORED, independent of how the summary was produced.
- **Aggregation**: a perfect run scores 1.0 accuracy and rolls through
  ``build_scorecard`` unchanged; a mixed run drops below 1.0; an all-errored
  run reports no invented ``"longthreads"`` aggregate.
- **Hermetic generation**: an injected fake chat plays fold call, classify
  call, and an INPUT-DEPENDENT summary call that echoes back whichever
  keyword actually reached its prompt — proving the real #1889 fold path
  (already landed in Increment 1) carries the decisive fact through to the
  model, and that the scorer genuinely fails the stale answer a hypothetical
  latest-dropping clip would have produced instead.

Everything here runs offline: no Lemonade, no Anthropic, no live mailbox.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.eval import longthread_quality as lq

_REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "email" / "longthread_ground_truth.json"
)

# Pinned per the #1889 I2 corpus design — the implementer builds the fixture
# to these exact keywords, mirroring how test_followups_corpus_integrity.py
# hardcodes "fu-001".
PINNED_CASES = {
    "lt-001": {"stale": "Lisbon", "decisive": "Madrid"},
    "lt-002": {"stale": "48,000", "decisive": "27,500"},
    "lt-003": {"stale": "Northwind", "decisive": "Contoso"},
}


@pytest.fixture(scope="module")
def corpus() -> dict:
    return lq.load_longthread_corpus(CORPUS_PATH)


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------


def test_corpus_loads_and_has_exactly_three_pinned_cases(corpus):
    cases = lq.corpus_cases(corpus)
    assert len(cases) == 3
    assert set(cases.keys()) == set(PINNED_CASES.keys())


def test_meta_block_present_and_well_formed(corpus):
    meta = corpus["_meta"]
    assert meta["fixture"] == "longthread_ground_truth.json"
    assert meta["fixture_kind"] == "hand-authored-adversarial"
    assert isinstance(meta["schema_version"], int)
    assert meta["purpose"].strip()
    budget = meta["budget"]
    assert budget["thread_budget_tokens"] == 13824
    assert budget["estimator"] == "max(chars//4, words*1.3)"


def test_every_case_is_schema_well_formed(corpus):
    """The loader already validates; assert the shape it guarantees."""
    for case_id, case in lq.corpus_cases(corpus).items():
        assert not case_id.startswith("_")
        assert case["scenario"].strip(), case_id
        assert case["thread_id"].strip(), case_id
        assert case["principal"].strip(), case_id
        messages = case["messages"]
        assert isinstance(messages, list) and len(messages) >= 24, case_id
        for msg in messages:
            assert msg["from"].strip(), case_id
            assert msg["subject"].strip(), case_id
            assert msg["body"].strip(), case_id
        assert case["decisive_phrase"].strip(), case_id
        assert case["stale_phrase"].strip(), case_id
        assert isinstance(case["must_mention"], list) and case["must_mention"], case_id
        assert (
            isinstance(case["must_not_mention"], list) and case["must_not_mention"]
        ), case_id
        for kw in case["must_mention"] + case["must_not_mention"]:
            assert kw.strip(), case_id


def test_pinned_keywords_match_scenario_design(corpus):
    for case_id, pins in PINNED_CASES.items():
        case = corpus[case_id]
        assert case["stale_phrase"] == pins["stale"]
        assert case["decisive_phrase"] == pins["decisive"]
        assert case["must_mention"] == [pins["decisive"]]
        assert case["must_not_mention"] == [pins["stale"]]


def test_decisive_phrase_only_in_last_message(corpus):
    """Invariant 1: the decisive phrase appears in the LAST message's body and
    in no other message's body."""
    for case_id, case in lq.corpus_cases(corpus).items():
        messages = case["messages"]
        decisive = case["decisive_phrase"]
        last_body = messages[-1]["body"]
        assert decisive in last_body, case_id
        for msg in messages[:-1]:
            assert decisive not in msg["body"], (
                f"{case_id}: decisive phrase leaked into an older message"
            )


def test_stale_phrase_only_in_older_messages(corpus):
    """Invariant 2: the stale phrase appears in at least one older message and
    NOT in the last message."""
    for case_id, case in lq.corpus_cases(corpus).items():
        messages = case["messages"]
        stale = case["stale_phrase"]
        last_body = messages[-1]["body"]
        assert stale not in last_body, case_id
        assert any(stale in m["body"] for m in messages[:-1]), (
            f"{case_id}: stale phrase absent from every older message"
        )


def test_must_mention_and_must_not_mention_against_last_message(corpus):
    """Invariant 3: must_mention keywords are in the last body; must_not_mention
    keywords are absent from the last body and present in an older one."""
    for case_id, case in lq.corpus_cases(corpus).items():
        last_body = case["messages"][-1]["body"]
        older_bodies = [m["body"] for m in case["messages"][:-1]]
        for kw in case["must_mention"]:
            assert kw in last_body, case_id
        for kw in case["must_not_mention"]:
            assert kw not in last_body, case_id
            assert any(kw in b for b in older_bodies), (
                f"{case_id}: must_not_mention keyword {kw!r} never appears in "
                "an older message"
            )


def test_raw_newest_first_join_exceeds_thread_budget(corpus):
    """Invariant 4 — the adversarial size precondition: every case's raw
    newest-first join must overflow thread_budget_tokens(), or the corpus
    never actually forces the fold path to engage."""
    pytest.importorskip("gaia_agent_email")
    from gaia_agent_email.context_budget import estimate_tokens, thread_budget_tokens

    for case_id, case in lq.corpus_cases(corpus).items():
        messages = case["messages"]
        joined = "\n\n".join(f"{m['from']}: {m['body']}" for m in reversed(messages))
        assert estimate_tokens(joined) > thread_budget_tokens(), case_id


# ---------------------------------------------------------------------------
# Malformed-corpus rejection (tmp_path, offline)
# ---------------------------------------------------------------------------


def _minimal_case(**overrides) -> dict:
    """A minimal, invariant-satisfying case — tests break exactly one thing."""
    base = {
        "scenario": "test scenario",
        "thread_id": "thread-x",
        "principal": "user@example.com",
        "messages": [
            {
                "from": "alice@example.com",
                "subject": "s1",
                "body": "older message mentions STALEKW as the answer",
            },
            {
                "from": "bob@example.com",
                "subject": "s2",
                "body": "latest message mentions DECISIVEKW as the real answer",
            },
        ],
        "decisive_phrase": "DECISIVEKW",
        "stale_phrase": "STALEKW",
        "must_mention": ["DECISIVEKW"],
        "must_not_mention": ["STALEKW"],
    }
    base.update(overrides)
    return base


def test_duplicate_case_ids_rejected(tmp_path):
    dup = tmp_path / "dup.json"
    dup.write_text('{"lt-001": {}, "lt-001": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate case id"):
        lq.load_longthread_corpus(dup)


def test_missing_messages_field_rejected(tmp_path):
    case = _minimal_case()
    del case["messages"]
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"lt-x": case}), encoding="utf-8")
    with pytest.raises(ValueError, match="messages"):
        lq.load_longthread_corpus(path)


def test_decisive_phrase_absent_from_last_body_rejected(tmp_path):
    case = _minimal_case()
    case["messages"][-1]["body"] = "latest message with no decisive keyword at all"
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"lt-x": case}), encoding="utf-8")
    with pytest.raises(ValueError, match="decisive_phrase"):
        lq.load_longthread_corpus(path)


def test_stale_phrase_present_in_last_body_rejected(tmp_path):
    case = _minimal_case()
    case["messages"][-1]["body"] += " and also STALEKW is mentioned here too"
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"lt-x": case}), encoding="utf-8")
    with pytest.raises(ValueError, match="stale_phrase"):
        lq.load_longthread_corpus(path)


def test_stale_phrase_absent_from_every_older_body_rejected(tmp_path):
    case = _minimal_case()
    case["messages"][0]["body"] = "older message with no stale keyword at all"
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"lt-x": case}), encoding="utf-8")
    with pytest.raises(ValueError, match="stale_phrase"):
        lq.load_longthread_corpus(path)


def test_empty_must_mention_rejected(tmp_path):
    case = _minimal_case(must_mention=[])
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"lt-x": case}), encoding="utf-8")
    with pytest.raises(ValueError, match="must_mention"):
        lq.load_longthread_corpus(path)


# ---------------------------------------------------------------------------
# Scorer on hand-fed summaries (offline)
# ---------------------------------------------------------------------------


def test_score_case_correct_summary_passes(corpus):
    case = lq.corpus_cases(corpus)["lt-001"]
    row = lq.score_case(
        "lt-001", case, "The venue has moved to Madrid for the offsite.",
        model_id="stub",
    )
    assert row["status"] == "PASS"
    assert row["overall_score"] == 10.0
    assert row["category"] == "stub"
    match = row["longthread_match"]
    assert set(match.keys()) == {"mentioned", "missing", "forbidden_hits", "correct"}
    assert match["correct"] is True
    assert match["missing"] == []
    assert match["forbidden_hits"] == []
    assert "Madrid" in match["mentioned"]


def test_score_case_stale_answer_fails_with_forbidden_hit(corpus):
    case = lq.corpus_cases(corpus)["lt-001"]
    row = lq.score_case("lt-001", case, "The venue is Lisbon.", model_id="stub")
    assert row["status"] == "FAIL"
    assert row["overall_score"] == 0.0
    assert "Lisbon" in row["longthread_match"]["forbidden_hits"]
    assert row["longthread_match"]["correct"] is False


def test_score_case_missing_keyword_fails_with_missing(corpus):
    case = lq.corpus_cases(corpus)["lt-001"]
    row = lq.score_case(
        "lt-001", case, "The venue has not been decided yet.", model_id="stub"
    )
    assert row["status"] == "FAIL"
    assert "Madrid" in row["longthread_match"]["missing"]


def test_score_case_none_summary_is_errored(corpus):
    case = lq.corpus_cases(corpus)["lt-001"]
    row = lq.score_case("lt-001", case, None, model_id="stub", error="boom")
    assert row["status"] == "ERRORED"
    assert row["error"] == "boom"
    assert "longthread_match" not in row


def test_score_case_none_summary_default_error(corpus):
    case = lq.corpus_cases(corpus)["lt-001"]
    row = lq.score_case("lt-001", case, None, model_id="stub")
    assert row["status"] == "ERRORED"
    assert row["error"]


def test_score_case_matching_is_case_insensitive(corpus):
    case = lq.corpus_cases(corpus)["lt-001"]
    row = lq.score_case("lt-001", case, "the venue is madrid now.", model_id="stub")
    assert row["status"] == "PASS"


# ---------------------------------------------------------------------------
# Aggregation (offline)
# ---------------------------------------------------------------------------


def test_summarize_perfect_run(corpus):
    cases = lq.corpus_cases(corpus)
    results = [
        lq.score_case(cid, c, " ".join(c["must_mention"]), model_id="stub")
        for cid, c in cases.items()
    ]
    summary = lq.summarize_longthreads(results, run_id="unit")
    assert "scorecard" in summary
    assert summary["scorecard"]["summary"]["total_scenarios"] == 3

    agg = summary["longthreads"]
    assert agg["cases_total"] == 3
    assert agg["cases_scored"] == 3
    assert agg["cases_errored"] == 0
    assert agg["passed"] == 3
    assert agg["failed"] == 0
    assert agg["accuracy"] == 1.0
    assert len(agg["per_case"]) == 3
    for row in agg["per_case"]:
        assert row["status"] == "PASS"
        assert row["correct"] is True


def test_summarize_mixed_run(corpus):
    cases = lq.corpus_cases(corpus)
    results = []
    for cid, c in cases.items():
        if cid == "lt-002":
            summary_text = c["must_not_mention"][0]  # the stale answer
        else:
            summary_text = " ".join(c["must_mention"])
        results.append(lq.score_case(cid, c, summary_text, model_id="stub"))
    summary = lq.summarize_longthreads(results, run_id="unit")
    agg = summary["longthreads"]
    assert agg["accuracy"] < 1.0
    assert agg["failed"] >= 1
    assert agg["accuracy"] == round(agg["passed"] / agg["cases_scored"], 4)


def test_summarize_all_errored_run_has_no_longthreads_key():
    """All-errored run -> loud absence, never an invented aggregate."""
    results = [
        {"id": "lt-001", "category": "stub", "status": "ERRORED", "error": "boom"},
    ]
    summary = lq.summarize_longthreads(results, run_id="unit")
    assert "scorecard" in summary
    assert "longthreads" not in summary


# ---------------------------------------------------------------------------
# Hermetic generation — injected discriminator fake (offline, no Lemonade)
# ---------------------------------------------------------------------------


def _discriminator_chat_factory(fold_calls: dict):
    """Build ``chat_factory(case) -> fake chat`` that plays the fold call, the
    classify call, and an INPUT-DEPENDENT summary call.

    The summary call echoes back whichever of the case's decisive/stale
    keyword actually reached the prompt content it received — the clipping-
    vs-folding discriminator: a real fold (Increment 1's landed
    ``thread_fold``) keeps the latest message (carrying the decisive keyword)
    verbatim, so the fake emits the decisive keyword and the case PASSes. A
    hypothetical latest-dropping clip would never surface the decisive
    keyword to this fake, which would then fall back to the stale keyword and
    FAIL scoring — this is what the corpus + scorer exist to catch.
    """

    def factory(case: dict):
        from gaia_agent_email.tools.thread_fold import _FOLD_SYSTEM_PROMPT

        class _DiscriminatorChat:
            def send_messages(self, messages, system_prompt="", **kwargs):
                content = messages[0].get("content", "") if messages else ""
                if system_prompt == _FOLD_SYSTEM_PROMPT:
                    fold_calls.setdefault(case["decisive_phrase"], []).append(True)
                    return SimpleNamespace(text="digest of older messages")
                if "Classify" in content:
                    return SimpleNamespace(
                        text=json.dumps(
                            {
                                "category": "NEEDS_RESPONSE",
                                "confidence": 0.9,
                                "reasoning": "test",
                            }
                        )
                    )
                decisive = case["must_mention"][0]
                stale = case["must_not_mention"][0]
                if decisive in content:
                    return SimpleNamespace(text=decisive)
                return SimpleNamespace(text=stale)

        return _DiscriminatorChat()

    return factory


def test_generate_summaries_hermetic_and_folds_every_case(corpus):
    pytest.importorskip("gaia_agent_email")

    fold_calls: dict = {}
    chat_factory = _discriminator_chat_factory(fold_calls)

    gens = lq.generate_summaries(corpus_path=CORPUS_PATH, chat_factory=chat_factory)
    assert len(gens) == 3
    assert all(not g["error"] for g in gens), [g for g in gens if g["error"]]
    assert all(g["summary"] is not None for g in gens)

    for pins in PINNED_CASES.values():
        assert fold_calls.get(pins["decisive"]), (
            "expected at least one fold call per case — the raw join "
            "overflows the budget for every corpus case"
        )


def test_generate_summaries_hermetic_all_pass_scoring(corpus):
    """The folded path carries the decisive fact through, so scoring every
    generated summary against the corpus is a clean PASS."""
    pytest.importorskip("gaia_agent_email")

    fold_calls: dict = {}
    chat_factory = _discriminator_chat_factory(fold_calls)
    gens = lq.generate_summaries(corpus_path=CORPUS_PATH, chat_factory=chat_factory)

    cases = lq.corpus_cases(corpus)
    results = [
        lq.score_case(g["case_id"], cases[g["case_id"]], g["summary"], model_id="stub")
        for g in gens
    ]
    assert all(r["status"] == "PASS" for r in results), results


def test_score_case_rejects_the_stale_answer_for_every_case(corpus):
    """Inverse pin: feeding the scorer the STALE keyword (what a hypothetical
    latest-dropping clip would have produced) must FAIL for every case —
    proves the scorer genuinely distinguishes folding from clipping."""
    for case_id, case in lq.corpus_cases(corpus).items():
        row = lq.score_case(
            case_id, case, case["must_not_mention"][0], model_id="stub"
        )
        assert row["status"] == "FAIL", case_id


# ---------------------------------------------------------------------------
# Fixture path resolution
# ---------------------------------------------------------------------------


def test_default_longthread_corpus_path_resolves():
    path = lq.default_longthread_corpus_path()
    assert path.is_file()
    assert path.parts[-4:] == (
        "tests",
        "fixtures",
        "email",
        "longthread_ground_truth.json",
    )
