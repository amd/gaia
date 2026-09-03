# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Proactive skill retrieval — scoring rules and the labeled accuracy gate.

The behavioural tests fix the *rules* (a tie never auto-loads, a generic verb is
not evidence, a name token is). ``test_benchmark_*`` is the accuracy gate: it
runs the same labeled query set as ``util/skill_retrieval_bench.py`` against the
checked-in starter pack and fails if precision or recall regresses. Retune the
constants in :mod:`gaia.agents.base.skill_retriever` and this is what tells you
whether the retune was an improvement or a trade.

Nothing here needs Lemonade, an embedder, or an agent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gaia.agents.base.skill_retriever import (
    MIN_SCORE,
    SkillRetriever,
    tokenize,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: A miniature corpus with the shapes that matter: two skills sharing a word
#: ("watch"), one whose name is a strong term, one with a generic verb.
CORPUS = {
    "github-triage": (
        "Triage GitHub work with the gh CLI — your unread notification inbox, or "
        "one repository's issue backlog. Use when asked to triage issues or "
        "review a backlog."
    ),
    "price-watch": (
        "Check product pages for a price drop, comparing against the lowest price "
        "seen before. Use when the user asks to watch a price or track a deal."
    ),
    "source-watch": (
        "Check a web page or feed for something worth telling the user about, "
        "remembering what was already reported so nothing repeats."
    ),
    "data-explore": (
        "Load messy tabular data into SQL scratchpad tables and answer questions "
        "with real queries. Use when the user has a CSV or spreadsheet export."
    ),
    "research-report": (
        "Research a topic on the open web and write a cited Markdown report."
    ),
}


@pytest.fixture
def retriever() -> SkillRetriever:
    r = SkillRetriever()
    r.index_texts(CORPUS)
    return r


# ── tokenization ─────────────────────────────────────────────────────────


def test_stopwords_and_stemming():
    assert tokenize("What are the issues you would use?") == ["issue"]


def test_plural_and_singular_forms_share_a_stem():
    assert tokenize("issue issues note notes file files") == [
        "issue",
        "issue",
        "note",
        "note",
        "file",
        "file",
    ]


def test_plural_query_matches_singular_skill_name():
    retriever = SkillRetriever()
    retriever.index_texts({"note-taker": "Organize personal notes and reminders."})
    assert retriever.decide("my notes").load == "note-taker"


def test_stemmer_never_shortens_below_the_minimum():
    """ "gas" must not become "ga" — a two-character stem matches everything."""
    assert tokenize("gas apis") == ["gas", "api"]


def test_tokenize_is_empty_for_pure_filler():
    """An affirmation carries no subject, so it must not steer a skill choice."""
    assert tokenize("yes, go ahead please") == []
    assert tokenize("cool, thanks") == []


# ── the rules ────────────────────────────────────────────────────────────


def test_names_the_skill_the_user_described_without_naming_it(retriever):
    """The defect this whole feature exists for: the user never says the name."""
    decision = retriever.decide("what's been going on in my github inbox lately?")
    assert decision.load == "github-triage"


def test_a_single_name_token_is_enough_evidence(retriever):
    """One term, but it is half the skill's identity — that is a real match.

    Also the regression guard for the IDF floor being *relative*: with an
    absolute floor this five-skill corpus could never clear it, and auto-load
    was silently dead for every small library.
    """
    decision = retriever.decide("what is new on github?")
    assert decision.load == "github-triage"
    top = decision.ranked[0]
    assert top.matched == ("github",) and top.name_hit


def test_a_single_generic_verb_is_not_evidence(retriever):
    """ "remember" appears only in source-watch, so IDF alone would rank it 1.0.

    It names what an assistant does, not what the skill is about — the exact
    shape of every false auto-load the benchmark produced.
    """
    decision = retriever.decide("remember that my favourite colour is teal")
    assert decision.outcome == "none"
    assert decision.load is None and decision.shortlist == ()


def test_a_single_subject_noun_is_evidence(retriever):
    """Same one-term shape as above, but the term names the subject, not the verb."""
    decision = retriever.decide("I have a csv here")
    assert decision.ranked[0].name == "data-explore"
    assert decision.ranked[0].strong == ("csv",)


def test_a_tie_is_reported_as_a_tie_never_resolved(retriever):
    """Two skills share "watch"; neither dominates, so neither loads."""
    decision = retriever.decide("watch this for me")
    assert decision.load is None
    assert set(decision.shortlist) <= {"price-watch", "source-watch"}


def test_unrelated_questions_match_nothing(retriever):
    for query in ("what is 17 times 23?", "hi", "what's the capital of France?"):
        assert retriever.decide(query).outcome == "none", query


def test_unknown_words_count_against_a_match(retriever):
    """One incidental hit inside a question about something else is not a match.

    Without charging for unseen terms the denominator is built only from words
    the corpus knows, so a lone hit looks like total agreement.
    """
    decision = retriever.decide("what is the report id for this API endpoint?")
    assert decision.load is None


def test_loaded_skills_are_excluded_from_matching(retriever):
    decision = retriever.decide("triage my github issues", exclude={"github-triage"})
    assert decision.load != "github-triage"


def test_scores_are_bounded_and_ordered(retriever):
    ranked = retriever.rank("triage my github issue backlog")
    assert ranked == sorted(ranked, key=lambda c: (-c.score, c.name))
    assert all(0.0 <= c.score <= 1.0 for c in ranked)


def test_winner_must_clear_the_score_floor(retriever, monkeypatch):
    """The floor is load-bearing, not decorative — raising it past 1.0 stops every load."""
    import gaia.agents.base.skill_retriever as module

    monkeypatch.setattr(module, "MIN_SCORE", 1.01)
    assert retriever.decide("triage my github issues").load is None


def test_empty_index_is_safe():
    assert SkillRetriever().decide("anything").outcome == "none"


def test_is_stale_tracks_description_changes():
    class _Skill:
        def __init__(self, description):
            self.description = description

    first = {"a": _Skill("about cats")}
    r = SkillRetriever(first)
    assert not r.is_stale(first)
    assert r.is_stale({"a": _Skill("about dogs")})
    assert r.is_stale({"a": _Skill("about cats"), "b": _Skill("about birds")})


# ── the accuracy gate ────────────────────────────────────────────────────


def _bench():
    """Import the benchmark module, which lives in ``util/`` and is not a package."""
    sys.path.insert(0, str(REPO_ROOT / "util"))
    try:
        import skill_retrieval_bench

        return skill_retrieval_bench
    finally:
        sys.path.pop(0)


@pytest.fixture(scope="module")
def starter_pack_metrics():
    bench = _bench()
    retriever = SkillRetriever()
    retriever.index_texts(bench.load_corpus("hub/skills"))
    return bench.evaluate(retriever, bench.QUERIES)


def test_benchmark_never_loads_the_wrong_skill(starter_pack_metrics):
    """Precision is the number that must not slip.

    A miss costs a tool call; a wrong load drags an unrelated skill's
    instructions into the answer and spends prompt budget doing it.
    """
    assert starter_pack_metrics["auto_wrong"] == 0
    assert starter_pack_metrics["false_loads"] == []
    assert starter_pack_metrics["auto_precision"] == 1.0


def test_benchmark_recall_does_not_regress(starter_pack_metrics):
    # Measured 0.667 auto / 0.952 including shortlist at the shipping constants.
    assert starter_pack_metrics["auto_recall"] >= 0.65
    assert starter_pack_metrics["recall_incl_shortlist"] >= 0.90


def test_benchmark_stays_quiet_on_unrelated_turns(starter_pack_metrics):
    # Measured 0.909 — one negative shortlists (it does not load).
    assert starter_pack_metrics["negative_specificity"] >= 0.85


def test_scoring_is_cheap_enough_to_run_every_turn(starter_pack_metrics):
    """The budget claim in the module docstring, asserted.

    Measured ~0.015 ms/query. The 5 ms ceiling is ~300x headroom, so this fails
    only on an algorithmic regression, never on a slow CI box.
    """
    assert starter_pack_metrics["latency_ms_mean"] < 5.0


def test_shipping_threshold_is_the_value_the_benchmark_was_tuned_at():
    """Guards against a threshold edit that silently skips the benchmark."""
    assert MIN_SCORE == 0.50


def test_the_idf_floor_scales_with_corpus_size():
    """A five-skill library and a thirty-skill one must both be able to load.

    IDF grows with N, so an absolute floor is a floor only for large libraries.
    """
    small = SkillRetriever()
    small.index_texts({k: CORPUS[k] for k in ("github-triage", "data-explore")})
    assert small.decide("what is new on github?").load == "github-triage"
