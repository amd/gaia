# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-turn skill suggestions (``gaia.agents.base.skill_suggester``).

Selection logic runs against a deterministic fake embedder: each skill's doc
text embeds to its own axis and a query embeds to the scores the test assigns,
so ``dot(query, skill)`` is exactly that score.

The calibration against the real embedder is opt-in
(``GAIA_SKILL_SUGGEST_CALIBRATE=1``, plus ``LEMONADE_BASE_URL`` naming a
running Lemonade Server). It scores the labelled requests in
``tests/fixtures/skills/skill_suggestion_cases.json`` against every hub skill
on disk, whatever both hold today. The suggestion test holds the defaults to
the precision and recall measured on an older, smaller set (54 requests, 20
hub skills). The auto-load test prints a threshold-by-margin table and holds
the defaults to no load on a negative and :data:`AUTOLOAD_RECALL_FLOOR`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from gaia.agents.base.skill_loader import skill_doc_text
from gaia.agents.base.skill_suggester import (
    AUTOLOAD_LEAD_EPSILON,
    DEFAULT_AUTOLOAD_MARGIN,
    DEFAULT_AUTOLOAD_THRESHOLD,
    DEFAULT_COMPANION_THRESHOLD,
    DEFAULT_SUGGEST_MARGIN,
    DEFAULT_SUGGEST_THRESHOLD,
    DEFAULT_SUGGEST_TOP_K,
    MAX_AUTOLOADS_PER_TURN,
    SkillMatches,
    SkillSuggester,
    autoload_env_override,
    autoload_margin_env_override,
    autoload_threshold_env_override,
    render_autoloaded,
    render_suggestion,
    suggest_env_override,
)
from gaia.skills.manager import ROOT_CLAUDE_IMPORT, ROOT_CLAUDE_PROJECT

REPO = Path(__file__).resolve().parents[2]
CASES = REPO / "tests" / "fixtures" / "skills" / "skill_suggestion_cases.json"
HUB_SKILLS = REPO / "hub" / "skills"
DIM = 32


def _skill(name, pairs=(), root="agent"):
    return SimpleNamespace(
        name=name,
        description=f"does {name}",
        root=root,
        gaia=SimpleNamespace(pairs_with=list(pairs)),
    )


def _skills(*specs):
    return {s.name: s for s in specs}


class FakeEmbedder:
    """Doc texts map to one-hot axes; queries map to assigned scores."""

    def __init__(self, names, queries):
        self.axis = {name: i for i, name in enumerate(names)}
        self.queries = queries
        self.doc_calls = 0
        self.query_calls = 0

    def __call__(self, text):
        v = np.zeros(DIM, dtype=np.float32)
        name = text.split(":", 1)[0]
        if text == f"{name}: does {name}" and name in self.axis:
            self.doc_calls += 1
            v[self.axis[name]] = 1.0
            return v
        self.query_calls += 1
        for skill, score in self.queries[text].items():
            v[self.axis[skill]] = score
        return v


NAMES = ["alpha", "beta", "gamma", "delta", "epsilon"]


def _suggester(queries, **kwargs):
    embed = FakeEmbedder(NAMES, queries)
    return SkillSuggester(embed, **kwargs), embed


def test_defaults_are_the_calibrated_values():
    assert DEFAULT_SUGGEST_THRESHOLD == pytest.approx(0.30)
    assert DEFAULT_SUGGEST_MARGIN == pytest.approx(0.02)
    assert DEFAULT_SUGGEST_TOP_K == 3
    assert DEFAULT_COMPANION_THRESHOLD == pytest.approx(0.40)


def test_suggests_a_clear_match_and_nothing_below_the_threshold():
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _suggester({"q": {"alpha": 0.45, "beta": 0.20}, "none": {"alpha": 0.29}})
    assert s.suggest("q", skills) == ["alpha"]
    assert s.suggest("none", skills) == []


def test_a_second_skill_needs_to_score_close_to_the_best():
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _suggester(
        {"close": {"alpha": 0.50, "beta": 0.49}, "far": {"alpha": 0.50, "beta": 0.45}}
    )
    assert s.suggest("close", skills) == ["alpha", "beta"]
    assert s.suggest("far", skills) == ["alpha"]


def test_top_k_caps_the_scored_suggestions():
    skills = _skills(*(_skill(n) for n in NAMES))
    scores = {n: 0.50 - i * 0.001 for i, n in enumerate(NAMES)}
    s, _ = _suggester({"q": scores}, top_k=2)
    assert s.suggest("q", skills) == ["alpha", "beta"]


def test_loaded_skills_are_never_suggested_but_still_set_the_bar():
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _suggester({"q": {"alpha": 0.60, "beta": 0.59, "gamma": 0.45}})
    # alpha is loaded and the best match; beta is close enough to join it,
    # gamma clears the threshold but is far below the request's real fit.
    assert s.suggest("q", skills, loaded={"alpha"}) == ["beta"]


def test_unloaded_companions_follow_their_skill():
    skills = _skills(
        _skill("alpha", pairs=["delta", "missing"]),
        _skill("beta"),
        _skill("gamma", pairs=["alpha"]),
        _skill("delta"),
        _skill("epsilon"),
    )
    s, _ = _suggester({"q": {"alpha": 0.60, "delta": 0.40, "gamma": 0.41}})
    assert s.suggest("q", skills) == ["alpha", "delta", "gamma"]
    assert s.suggest("q", skills, loaded={"delta"}) == ["alpha", "gamma"]


def test_a_companion_unrelated_to_the_request_is_left_out():
    """Pairing is mutual, so a popular skill pairs with many; only the
    companions the request is also about join the line."""
    skills = _skills(
        _skill("alpha", pairs=["beta", "gamma"]),
        _skill("beta"),
        _skill("gamma"),
    )
    s, _ = _suggester({"q": {"alpha": 0.60, "beta": 0.45, "gamma": 0.39}})
    assert s.suggest("q", skills) == ["alpha", "beta"]


def test_claude_imports_are_never_scored():
    skills = _skills(_skill("alpha", root=ROOT_CLAUDE_IMPORT), _skill("beta"))
    s, embed = _suggester({"q": {"alpha": 0.90, "beta": 0.40}})
    assert s.suggest("q", skills) == ["beta"]
    assert embed.doc_calls == 1


def test_project_skills_are_scored_only_when_the_catalogue_lists_them():
    skills = _skills(_skill("alpha", root=ROOT_CLAUDE_PROJECT), _skill("beta"))
    s, _ = _suggester({"q": {"alpha": 0.90, "beta": 0.40}})
    assert s.suggest("q", skills) == ["beta"]
    assert s.suggest("q", skills, list_project=True) == ["alpha"]


def test_an_unlisted_project_companion_is_not_suggested():
    skills = _skills(
        _skill("alpha", pairs=["beta"]), _skill("beta", root=ROOT_CLAUDE_PROJECT)
    )
    s, _ = _suggester({"q": {"alpha": 0.90, "beta": 0.60}})
    assert s.suggest("q", skills) == ["alpha"]
    assert s.suggest("q", skills, list_project=True) == ["alpha", "beta"]


def test_descriptions_embed_once_and_each_turn_costs_one_query():
    skills = _skills(*(_skill(n) for n in NAMES))
    s, embed = _suggester({"one": {"alpha": 0.5}, "two": {"beta": 0.5}})
    s.suggest("one", skills)
    s.suggest("two", skills)
    s.suggest("one", skills)
    assert embed.doc_calls == len(NAMES)
    assert embed.query_calls == 3


def test_batch_embedder_is_used_for_descriptions():
    skills = _skills(*(_skill(n) for n in NAMES))
    embed = FakeEmbedder(NAMES, {"q": {"gamma": 0.5}})
    batches = []

    def batch(texts):
        batches.append(list(texts))
        return np.stack([embed(t) for t in texts])

    s = SkillSuggester(embed, embed_batch_fn=batch)
    assert s.suggest("q", skills) == ["gamma"]
    assert len(batches) == 1 and len(batches[0]) == len(NAMES)


def test_an_edited_description_is_re_embedded():
    skills = _skills(_skill("alpha"))
    calls = []

    def embed(text):
        calls.append(text)
        return np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)

    s = SkillSuggester(embed)
    s.suggest("q", skills)
    skills["alpha"].description = "does something else now"
    s.suggest("q", skills)
    assert calls.count("alpha: does alpha") == 1
    assert "alpha: does something else now" in calls


def test_embedder_failure_means_no_suggestion_and_a_cooldown(caplog):
    skills = _skills(*(_skill(n) for n in NAMES))
    calls = []

    def broken(text):
        calls.append(text)
        raise ConnectionError("embedder down")

    s = SkillSuggester(broken)
    with caplog.at_level(logging.DEBUG, logger="gaia.agents.base.skill_suggester"):
        assert s.suggest("draw me a cat", skills) == []
    assert s.cooling_down
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "embedder down" in warnings[0].getMessage()
    tried = len(calls)
    with caplog.at_level(logging.DEBUG, logger="gaia.agents.base.skill_suggester"):
        assert s.suggest("draw me a cat", skills) == []
    assert len(calls) == tried  # no retry while cooling down
    # One warning per outage, not one per turn.
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_a_short_batch_is_treated_as_a_failure():
    skills = _skills(*(_skill(n) for n in NAMES))
    embed = FakeEmbedder(NAMES, {"q": {"alpha": 0.9}})
    s = SkillSuggester(embed, embed_batch_fn=lambda texts: np.zeros((1, DIM)))
    assert s.suggest("q", skills) == []
    assert s.cooling_down


def test_blank_request_or_no_skills_suggests_nothing():
    s, embed = _suggester({})
    assert s.suggest("   ", _skills(_skill("alpha"))) == []
    assert s.suggest("anything", {}) == []
    assert embed.query_calls == 0


def test_long_requests_are_cut_before_embedding():
    seen = []

    def embed(text):
        seen.append(text)
        return np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)

    SkillSuggester(embed).suggest("x" * 20000, _skills(_skill("alpha")))
    assert max(len(t) for t in seen) <= 4000


def test_top_k_must_be_positive():
    with pytest.raises(ValueError, match="top_k"):
        SkillSuggester(lambda t: np.zeros(DIM), top_k=0)


def test_render_suggestion():
    assert render_suggestion([]) == ""
    assert render_suggestion(["alpha"]) == (
        "Call load_skill('alpha') before you start: it covers this kind of request."
    )
    assert render_suggestion(["alpha", "beta", "gamma"]) == (
        "Call load_skill('alpha') before you start: it covers this kind of "
        "request. Also consider: beta, gamma."
    )


def test_a_suggestion_tells_the_model_to_load_the_best_match():
    """A local model passes over an offer, so the best match is an instruction;
    the others keep their order and stay an offer, all on one line."""
    text = render_suggestion(["weekly-audit-patterns", "summarize", "pdf"])
    assert text.startswith("Call load_skill('weekly-audit-patterns') before")
    assert text.index("weekly-audit-patterns") < text.index("summarize")
    assert text.index("summarize") < text.index("pdf")
    assert text.count("load_skill") == 1
    assert len(text.splitlines()) == 1


@pytest.mark.parametrize(
    "raw,expected",
    [(None, None), ("1", True), ("on", True), ("0", False), ("off", False)],
)
def test_env_override(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("GAIA_SKILL_SUGGEST", raising=False)
    else:
        monkeypatch.setenv("GAIA_SKILL_SUGGEST", raw)
    assert suggest_env_override() is expected


# ── auto-load ───────────────────────────────────────────────────────────────


def _auto(queries, **kwargs):
    kwargs.setdefault("autoload_threshold", 0.55)
    return _suggester(queries, **kwargs)


def test_autoload_defaults():
    assert DEFAULT_AUTOLOAD_THRESHOLD == pytest.approx(0.55)
    assert DEFAULT_AUTOLOAD_MARGIN == pytest.approx(0.03)
    assert MAX_AUTOLOADS_PER_TURN == 2
    # Well clear of the suggestion floor: an auto-load is a far stronger claim.
    assert DEFAULT_AUTOLOAD_THRESHOLD >= DEFAULT_SUGGEST_THRESHOLD + 0.2


def test_nothing_is_auto_loaded_without_an_autoload_threshold():
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _suggester({"q": {"alpha": 0.90}})
    assert s.autoload_threshold is None
    assert s.select("q", skills) == SkillMatches(auto_load=[], suggest=["alpha"])


def test_the_autoload_threshold_is_inclusive_and_lower_scores_stay_suggestions():
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _auto({"at": {"alpha": 0.55}, "below": {"alpha": 0.549}})
    assert s.select("at", skills) == SkillMatches(auto_load=["alpha"], suggest=[])
    assert s.select("below", skills) == SkillMatches(auto_load=[], suggest=["alpha"])


def test_an_auto_load_must_be_the_clear_best_match():
    """A close runner-up below the threshold means the model should choose."""
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _auto(
        {
            "close": {"alpha": 0.60, "beta": 0.58},
            "clear": {"alpha": 0.60, "beta": 0.50},
        },
        autoload_threshold=0.59,
        margin=0.05,
    )
    assert s.select("close", skills) == SkillMatches(
        auto_load=[], suggest=["alpha", "beta"]
    )
    assert s.select("clear", skills) == SkillMatches(auto_load=["alpha"], suggest=[])


def test_two_unrelated_skills_scoring_alike_are_only_suggested():
    """Neither pairs with the other, so the request is ambiguous."""
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _auto({"q": {"alpha": 0.60, "beta": 0.59, "gamma": 0.40}})
    assert s.select("q", skills) == SkillMatches(
        auto_load=[], suggest=["alpha", "beta"]
    )


def test_companions_of_the_best_match_are_not_ambiguity():
    """Skills that pair score alike by design: the leader loads with its best
    companion even though neither leads the other by the margin."""
    skills = _skills(
        _skill("slide-decks", pairs=["report-from-sources", "office-docs"]),
        _skill("report-from-sources", pairs=["office-docs"]),
        _skill("office-docs"),
        _skill("pdf"),
    )
    scores = {"slide-decks": 0.62, "report-from-sources": 0.60, "office-docs": 0.595}
    embed = FakeEmbedder(list(skills), {"q": {**scores, "pdf": 0.30}})
    s = SkillSuggester(embed, autoload_threshold=0.55, margin=0.05)
    assert s.select("q", skills) == SkillMatches(
        auto_load=["slide-decks", "report-from-sources"], suggest=["office-docs"]
    )


def test_pairing_declared_by_the_other_skill_counts():
    skills = _skills(_skill("alpha"), _skill("beta", pairs=["alpha"]), _skill("gamma"))
    s, _ = _auto({"q": {"alpha": 0.60, "beta": 0.59, "gamma": 0.40}})
    assert s.select("q", skills).auto_load == ["alpha", "beta"]


def test_a_rival_that_is_not_a_companion_still_blocks_the_load():
    skills = _skills(_skill("alpha", pairs=["beta"]), _skill("beta"), _skill("gamma"))
    s, _ = _auto({"q": {"alpha": 0.62, "beta": 0.60, "gamma": 0.61}}, margin=0.05)
    assert s.select("q", skills).auto_load == []


def test_only_one_companion_loads_with_the_best_match():
    skills = _skills(
        _skill("alpha", pairs=["beta", "gamma"]), _skill("beta"), _skill("gamma")
    )
    s, _ = _auto({"q": {"alpha": 0.80, "beta": 0.76, "gamma": 0.79}}, margin=0.05)
    assert s.select("q", skills) == SkillMatches(
        auto_load=["alpha", "gamma"], suggest=["beta"]
    )
    assert MAX_AUTOLOADS_PER_TURN == 2


def test_max_autoloads_of_one_loads_the_best_match_alone():
    skills = _skills(_skill("alpha", pairs=["beta"]), _skill("beta"), _skill("gamma"))
    s, _ = _auto({"q": {"alpha": 0.80, "beta": 0.79}}, max_autoloads=1)
    assert s.select("q", skills).auto_load == ["alpha"]


def test_a_second_skill_far_behind_the_best_match_does_not_load_with_it():
    """Above the threshold but not a companion: the best match loads alone."""
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _auto({"q": {"alpha": 0.80, "beta": 0.60}}, margin=0.25)
    assert s.select("q", skills) == SkillMatches(auto_load=["alpha"], suggest=["beta"])


def test_a_companion_below_the_threshold_is_only_suggested():
    skills = _skills(_skill("alpha", pairs=["beta"]), _skill("beta"), _skill("gamma"))
    s, _ = _auto({"q": {"alpha": 0.70, "beta": 0.54, "gamma": 0.30}})
    assert s.select("q", skills) == SkillMatches(auto_load=["alpha"], suggest=["beta"])


def test_a_companion_above_the_threshold_can_auto_load():
    skills = _skills(_skill("alpha", pairs=["beta"]), _skill("beta"), _skill("gamma"))
    s, _ = _auto({"q": {"alpha": 0.70, "beta": 0.56, "gamma": 0.30}})
    assert s.select("q", skills).auto_load == ["alpha", "beta"]


@pytest.mark.parametrize(
    "best,rival,loads",
    [(0.58, 0.55, True), (0.62, 0.59, True), (0.85, 0.82, True), (0.58, 0.551, False)],
)
def test_a_lead_of_exactly_the_margin_counts(best, rival, loads):
    """0.58 - 0.55 is 0.02999... in floating point, and still a lead of 0.03."""
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _auto({"q": {"alpha": best, "beta": rival}}, autoload_margin=0.03)
    assert (s.select("q", skills).auto_load == ["alpha"]) is loads
    assert AUTOLOAD_LEAD_EPSILON < 0.001


def test_a_loaded_skill_is_never_auto_loaded_and_a_better_one_blocks_it():
    skills = _skills(*(_skill(n) for n in NAMES))
    s, _ = _auto({"q": {"alpha": 0.70, "beta": 0.69}})
    # alpha is loaded and the best match: beta is close behind it, so the
    # request is already covered and beta is only suggested.
    assert s.select("q", skills, loaded={"alpha"}) == SkillMatches(
        auto_load=[], suggest=["beta"]
    )
    assert s.select("q", skills, loaded={"alpha", "beta"}) == SkillMatches()


def test_an_unlisted_project_skill_is_never_auto_loaded():
    skills = _skills(_skill("alpha", root=ROOT_CLAUDE_PROJECT), _skill("beta"))
    s, _ = _auto({"q": {"alpha": 0.90, "beta": 0.10}})
    assert s.select("q", skills) == SkillMatches()
    assert s.select("q", skills, list_project=True).auto_load == ["alpha"]


@pytest.mark.parametrize("autoload_threshold", [None, 0.55])
def test_auto_load_never_changes_what_suggest_returns(autoload_threshold):
    skills = _skills(_skill("alpha", pairs=["gamma"]), _skill("beta"), _skill("gamma"))
    queries = {"q": {"alpha": 0.60, "beta": 0.62, "gamma": 0.45}}
    s, _ = _suggester(queries, autoload_threshold=autoload_threshold, margin=0.05)
    assert s.suggest("q", skills) == ["beta", "alpha", "gamma"]
    matches = s.select("q", skills)
    assert sorted(matches.auto_load + matches.suggest) == ["alpha", "beta", "gamma"]


def test_embedder_failure_auto_loads_nothing():
    skills = _skills(*(_skill(n) for n in NAMES))

    def broken(text):
        raise ConnectionError("embedder down")

    s = SkillSuggester(broken, autoload_threshold=0.1)
    assert s.select("draw me a cat", skills) == SkillMatches()
    assert s.cooling_down


def test_max_autoloads_must_not_be_negative():
    with pytest.raises(ValueError, match="max_autoloads"):
        SkillSuggester(lambda t: np.zeros(DIM), max_autoloads=-1)


def test_render_autoloaded():
    assert render_autoloaded([]) == ""
    assert render_autoloaded(["report-from-sources"]).startswith(
        "Loaded report-from-sources because it matches this request"
    )
    two = render_autoloaded(["alpha", "beta"])
    assert two.startswith("Loaded alpha and beta because they match this request")
    assert len(two.splitlines()) == 1


@pytest.mark.parametrize(
    "raw,expected",
    [(None, None), ("1", True), ("on", True), ("0", False), ("off", False)],
)
def test_autoload_env_override(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("GAIA_SKILL_AUTOLOAD", raising=False)
    else:
        monkeypatch.setenv("GAIA_SKILL_AUTOLOAD", raw)
    assert autoload_env_override() is expected


@pytest.mark.parametrize(
    "name,parse",
    [
        ("GAIA_SKILL_AUTOLOAD_TAU", autoload_threshold_env_override),
        ("GAIA_SKILL_AUTOLOAD_MARGIN", autoload_margin_env_override),
    ],
)
def test_autoload_threshold_and_margin_env_overrides(monkeypatch, name, parse):
    monkeypatch.delenv(name, raising=False)
    assert parse() is None
    monkeypatch.setenv(name, "0.45")
    assert parse() == pytest.approx(0.45)
    monkeypatch.setenv(name, "high")
    with pytest.raises(ValueError, match=name):
        parse()


# ── the labelled request set ────────────────────────────────────────────────


def _cases():
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def _hub_skills():
    from gaia.skills.format import parse_skill_file

    return {
        skill.name: skill
        for skill in (
            parse_skill_file(path) for path in sorted(HUB_SKILLS.glob("*/SKILL.md"))
        )
    }


def _present_cases(present):
    """The labelled cases restricted to the skills on disk.

    A label may name a skill another change adds. It is dropped here, and a case
    whose expected skills are all missing is skipped rather than turned into a
    negative.
    """
    kept = []
    for case in _cases():
        expected = [n for n in case["expected"] if n in present]
        if case["expected"] and not expected:
            continue
        acceptable = [n for n in case["acceptable"] if n in present]
        kept.append({**case, "expected": expected, "acceptable": acceptable})
    return kept


def test_every_hub_skill_has_labelled_requests():
    """Adding a hub skill means adding requests for it to the labelled set."""
    present = set(_hub_skills())
    cases = _cases()
    for case in cases:
        assert not set(case["expected"]) & set(case["acceptable"]), case["request"]
    labelled = {n for case in _present_cases(present) for n in case["expected"]}
    unlabelled = sorted(present - labelled)
    assert not unlabelled, (
        f"hub skill(s) {', '.join(unlabelled)} have no request in "
        f"{CASES.relative_to(REPO).as_posix()}. Add about three invented "
        "requests that should suggest each one, plus a near-miss negative, "
        "then rerun test_calibration_against_the_real_embedder."
    )
    assert len(cases) >= 30
    assert sum(1 for case in cases if not case["expected"]) >= 5


def _measure(suggester, skills, cases):
    tp = fp = fn = negatives_hit = 0
    for case in cases:
        got = suggester.suggest(case["request"], skills)
        expected = set(case["expected"])
        allowed = expected | set(case["acceptable"])
        tp += len(expected & set(got))
        fn += len(expected - set(got))
        fp += len([name for name in got if name not in allowed])
        negatives_hit += bool(got) and not expected
    precision = tp / (tp + fp) if tp + fp else 1.0
    return precision, tp / (tp + fn), negatives_hit


def _real_embedder():
    """Embeddings straight from ``/embeddings``: no load or unload request.

    The server still loads the embedding model into its embedding slot if it
    is not loaded already.
    """
    if os.environ.get("GAIA_SKILL_SUGGEST_CALIBRATE") != "1":
        pytest.skip(
            "calibration against the real embedder is opt-in: set "
            "GAIA_SKILL_SUGGEST_CALIBRATE=1 and LEMONADE_BASE_URL"
        )
    import requests

    from gaia.agents.base.memory import EMBEDDING_MODEL

    base_url = os.environ.get("LEMONADE_BASE_URL", "").rstrip("/")
    if not base_url:
        pytest.fail("set LEMONADE_BASE_URL to the running Lemonade Server")
    model = os.environ.get("GAIA_SKILL_SUGGEST_EMBED_MODEL", EMBEDDING_MODEL)

    def batch(texts):
        response = requests.post(
            f"{base_url}/embeddings",
            json={"model": model, "input": list(texts)},
            timeout=120,
        )
        response.raise_for_status()
        rows = [item["embedding"] for item in response.json()["data"]]
        vecs = np.asarray(rows, dtype=np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    return (lambda text: batch([text])[0]), batch


@pytest.mark.real_model
@pytest.mark.allow_network
def test_calibration_against_the_real_embedder():
    """Holds the defaults to what they measured, with pairs_with companions on.

    Measured with embeddinggemma-300m over 20 hub skills: precision 0.84,
    recall 0.93, 3 of 12 negatives suggested (all near misses).
    """
    embed, batch = _real_embedder()
    embed("probe")  # an unreachable embedder fails here, not as recall 0
    skills = _hub_skills()
    cases = _present_cases(set(skills))
    suggester = SkillSuggester(embed, embed_batch_fn=batch)
    precision, recall, negatives_hit = _measure(suggester, skills, cases)
    print(f"precision={precision:.2f} recall={recall:.2f} negatives={negatives_hit}")
    assert precision >= 0.80
    assert recall >= 0.90
    assert negatives_hit <= 3


#: The auto-load grid the calibration prints: floor by lead.
SWEEP_THRESHOLDS = (0.40, 0.45, 0.50, 0.55, 0.60)
SWEEP_MARGINS = (0.00, 0.01, 0.02, 0.03, 0.04, 0.05)

#: Least share of the labelled skills the default floor and lead must load.
#: Not a measurement: it sits under the 0.21 measured on an older 71-request
#: set, before companions stopped counting as rivals. Raise it to what the
#: sweep shows once the defaults are re-measured.
AUTOLOAD_RECALL_FLOOR = 0.15


def _memoized(embed):
    """*embed* with each text embedded once: the sweep re-asks every request."""
    seen = {}

    def cached(text):
        if text not in seen:
            seen[text] = embed(text)
        return seen[text]

    return cached


def _measure_autoload(suggester, skills, cases):
    """Auto-load counts over *cases* at the suggester's floor and lead."""
    tp = fn = wrong = loads = on_negatives = 0
    for case in cases:
        got = suggester.select(case["request"], skills).auto_load
        expected = set(case["expected"])
        allowed = expected | set(case["acceptable"])
        loads += len(got)
        tp += len(expected & set(got))
        fn += len(expected - set(got))
        wrong += len([name for name in got if name not in allowed])
        on_negatives += bool(got) and not expected
    return {
        "precision": tp / (tp + wrong) if tp + wrong else 1.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "loads": loads,
        "wrong": wrong,
        "on_negatives": on_negatives,
    }


def _autoload_sweep(embed, batch, skills, cases):
    """``{(threshold, margin): counts}`` over the grid, embedding each text once."""
    suggester = SkillSuggester(
        _memoized(embed), embed_batch_fn=batch, autoload_threshold=0.0
    )
    cells = {}
    for threshold in SWEEP_THRESHOLDS:
        for margin in SWEEP_MARGINS:
            suggester.autoload_threshold = threshold
            suggester.autoload_margin = margin
            cells[(threshold, margin)] = _measure_autoload(suggester, skills, cases)
    return cells


def _sweep_table(cells):
    lines = ["threshold margin precision recall loads wrong on-negatives"]
    for (threshold, margin), cell in sorted(cells.items()):
        lines.append(
            f"{threshold:9.2f} {margin:6.2f} {cell['precision']:9.2f} "
            f"{cell['recall']:6.2f} {cell['loads']:5d} {cell['wrong']:5d} "
            f"{cell['on_negatives']:12d}"
        )
    return "\n".join(lines)


def _word_embedder():
    """A stand-in embedder: one hashed axis per word, so shared words score."""
    import hashlib
    import re

    def embed(text):
        v = np.zeros(256, dtype=np.float32)
        for word in re.findall(r"[a-z]{3,}", text.lower()):
            v[int(hashlib.sha256(word.encode()).hexdigest(), 16) % 256] += 1.0
        norm = float(np.linalg.norm(v))
        return v / norm if norm else v

    return embed


def test_the_autoload_sweep_runs_on_the_current_fixture_and_skills():
    """The calibration's own code, on what is on disk, with no real embedder."""
    skills = _hub_skills()
    cases = _present_cases(set(skills))
    cells = _autoload_sweep(_word_embedder(), None, skills, cases)
    assert set(cells) == {(t, m) for t in SWEEP_THRESHOLDS for m in SWEEP_MARGINS}
    assert (DEFAULT_AUTOLOAD_THRESHOLD, DEFAULT_AUTOLOAD_MARGIN) in cells
    for cell in cells.values():
        assert 0.0 <= cell["precision"] <= 1.0 and 0.0 <= cell["recall"] <= 1.0
        assert cell["wrong"] <= cell["loads"]
    # A lower floor or a smaller lead can only load more.
    assert cells[(0.40, 0.00)]["loads"] >= cells[(0.60, 0.05)]["loads"]
    table = _sweep_table(cells)
    assert len(table.splitlines()) == 1 + len(cells)
    assert table.splitlines()[0].split() == [
        "threshold",
        "margin",
        "precision",
        "recall",
        "loads",
        "wrong",
        "on-negatives",
    ]


def test_measure_autoload_counts_wrong_loads_and_negatives():
    skills = _skills(_skill("alpha", pairs=["beta"]), _skill("beta"), _skill("gamma"))
    queries = {
        "right": {"alpha": 0.70, "beta": 0.60},
        "wrong": {"gamma": 0.70},
        "negative": {"gamma": 0.70},
        "missed": {"alpha": 0.40},
    }
    s = SkillSuggester(FakeEmbedder(list(skills), queries), autoload_threshold=0.55)
    cases = [
        {"request": "right", "expected": ["alpha"], "acceptable": ["beta"]},
        {"request": "wrong", "expected": ["alpha"], "acceptable": []},
        {"request": "negative", "expected": [], "acceptable": []},
        {"request": "missed", "expected": ["alpha"], "acceptable": []},
    ]
    assert _measure_autoload(s, skills, cases) == {
        "precision": pytest.approx(1 / 3),
        "recall": pytest.approx(1 / 3),
        "loads": 4,
        "wrong": 2,
        "on_negatives": 1,
    }


@pytest.mark.real_model
@pytest.mark.allow_network
def test_autoload_calibration_against_the_real_embedder(capsys):
    """Prints the floor-by-lead table and holds the defaults to their bar.

    The bar for turning ``skill_autoload`` on by default: nothing loaded on a
    request no skill should match, and :data:`AUTOLOAD_RECALL_FLOOR`.
    """
    embed, batch = _real_embedder()
    embed("probe")  # an unreachable embedder fails here, not as recall 0
    skills = _hub_skills()
    cases = _present_cases(set(skills))
    cells = _autoload_sweep(embed, batch, skills, cases)
    with capsys.disabled():
        print(
            f"\nauto-load sweep: {len(cases)} requests, "
            f"{sum(1 for case in cases if not case['expected'])} negatives, "
            f"{len(skills)} hub skills"
        )
        print(_sweep_table(cells))
    defaults = cells[(DEFAULT_AUTOLOAD_THRESHOLD, DEFAULT_AUTOLOAD_MARGIN)]
    assert defaults["on_negatives"] == 0, defaults
    assert defaults["recall"] >= AUTOLOAD_RECALL_FLOOR, defaults


def test_doc_text_is_what_the_fake_embedder_expects():
    assert skill_doc_text(_skill("alpha")) == "alpha: does alpha"
