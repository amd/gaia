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
on disk. The requests are split in two by a hash (:func:`_half`): thresholds
and prompts are chosen on the tuning half only, and the
defaults are held to :data:`AUTOLOAD_BAR` and :data:`SUGGEST_BAR` on the
held-out half.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from gaia.agents.base.skill_suggester import (
    AUTOLOAD_LEAD_EPSILON,
    DEFAULT_AUTOLOAD_MARGIN,
    DEFAULT_AUTOLOAD_THRESHOLD,
    DEFAULT_COMPANION_THRESHOLD,
    DEFAULT_SUGGEST_MARGIN,
    DEFAULT_SUGGEST_THRESHOLD,
    DEFAULT_SUGGEST_TOP_K,
    DOC_PROMPT,
    MAX_AUTOLOADS_PER_TURN,
    QUERY_CHARS,
    QUERY_PROMPT,
    SkillMatches,
    SkillSuggester,
    autoload_env_override,
    autoload_margin_env_override,
    autoload_threshold_env_override,
    render_autoloaded,
    render_suggestion,
    skill_match_text,
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
        name = text.removeprefix(f"{DOC_PROMPT}does ")
        if text.startswith(DOC_PROMPT) and name in self.axis:
            self.doc_calls += 1
            v[self.axis[name]] = 1.0
            return v
        assert text.startswith(QUERY_PROMPT), text
        self.query_calls += 1
        for skill, score in self.queries[text.removeprefix(QUERY_PROMPT)].items():
            v[self.axis[skill]] = score
        return v


NAMES = ["alpha", "beta", "gamma", "delta", "epsilon"]


def _suggester(queries, **kwargs):
    embed = FakeEmbedder(NAMES, queries)
    return SkillSuggester(embed, **kwargs), embed


def test_defaults_are_the_calibrated_values():
    assert DEFAULT_SUGGEST_THRESHOLD == pytest.approx(0.35)
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
    assert calls.count(f"{DOC_PROMPT}does alpha") == 1
    assert f"{DOC_PROMPT}does something else now" in calls


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
    assert max(len(t) for t in seen) <= QUERY_CHARS + len(QUERY_PROMPT)


# ── what is embedded ────────────────────────────────────────────────────────


def test_a_skill_is_embedded_by_its_description_without_its_name():
    assert skill_match_text(_skill("alpha")) == f"{DOC_PROMPT}does alpha"
    multiline = _skill("alpha")
    multiline.description = "\n  First line.  \nSecond line."
    assert skill_match_text(multiline) == f"{DOC_PROMPT}First line."


def test_a_skill_without_a_description_is_embedded_by_its_name():
    bare = _skill("alpha")
    bare.description = ""
    assert skill_match_text(bare) == f"{DOC_PROMPT}alpha"


def test_the_request_is_embedded_under_the_query_prompt():
    seen = []

    def embed(text):
        seen.append(text)
        return np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)

    SkillSuggester(embed).suggest("Draw a cat.", _skills(_skill("alpha")))
    assert seen == [f"{DOC_PROMPT}does alpha", f"{QUERY_PROMPT}Draw a cat."]


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


def test_after_an_auto_load_the_other_matches_are_only_named_as_available():
    """The best match is loaded already, so no name is an instruction."""
    assert render_suggestion([], after_autoload=True) == ""
    text = render_suggestion(["beta", "gamma"], after_autoload=True)
    assert text == (
        "Also available: beta, gamma. Call load_skill for one only if this "
        "request needs it."
    )
    assert "before you start" not in text
    assert len(text.splitlines()) == 1


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
    assert DEFAULT_AUTOLOAD_THRESHOLD == pytest.approx(0.40)
    assert DEFAULT_AUTOLOAD_MARGIN == pytest.approx(0.03)
    assert MAX_AUTOLOADS_PER_TURN == 2
    # Clear of the suggestion floor: an auto-load is a stronger claim.
    assert DEFAULT_AUTOLOAD_THRESHOLD >= DEFAULT_SUGGEST_THRESHOLD + 0.05


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
    """Skills that pair score alike by design, so the leader loads although it
    does not lead its companions by the margin. They stay suggestions: neither
    leads the other, so neither clears the bar itself."""
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
        auto_load=["slide-decks"], suggest=["report-from-sources", "office-docs"]
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


def test_a_companion_that_clears_the_bar_itself_can_auto_load():
    """At the threshold and leading every skill but the best match."""
    skills = _skills(_skill("alpha", pairs=["beta"]), _skill("beta"), _skill("gamma"))
    s, _ = _auto({"q": {"alpha": 0.70, "beta": 0.56, "gamma": 0.30}})
    assert s.select("q", skills).auto_load == ["alpha", "beta"]


def test_a_companion_is_not_loaded_on_the_strength_of_its_partner():
    """Above the threshold, but no further ahead of an unrelated skill than
    that skill is of the next: the request is about the best match only."""
    skills = _skills(_skill("alpha", pairs=["beta"]), _skill("beta"), _skill("gamma"))
    s, _ = _auto({"q": {"alpha": 0.70, "beta": 0.57, "gamma": 0.56}}, margin=0.20)
    assert s.select("q", skills) == SkillMatches(
        auto_load=["alpha"], suggest=["beta", "gamma"]
    )


@pytest.mark.parametrize(
    "mate,rival,loads", [(0.60, 0.57, True), (0.60, 0.571, False), (0.60, 0.62, False)]
)
def test_a_companion_must_lead_the_remaining_skills_by_the_margin(mate, rival, loads):
    skills = _skills(_skill("alpha", pairs=["beta"]), _skill("beta"), _skill("gamma"))
    s, _ = _auto({"q": {"alpha": 0.90, "beta": mate, "gamma": rival}})
    assert s.select("q", skills).auto_load == (
        ["alpha", "beta"] if loads else ["alpha"]
    )


def test_a_second_companion_is_not_tried_when_the_best_one_falls_short():
    """One candidate: a weaker companion never loads past a stronger one."""
    skills = _skills(
        _skill("alpha", pairs=["beta", "gamma"]), _skill("beta"), _skill("gamma")
    )
    s, _ = _auto({"q": {"alpha": 0.90, "beta": 0.60, "gamma": 0.59}})
    assert s.select("q", skills).auto_load == ["alpha"]


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


def _half(request):
    """``"tuning"`` or ``"held-out"``, by a hash of the request's text.

    A request never changes half, and a new one lands in either. Choose
    thresholds, prompts and description wording on the tuning half only: the
    held-out half is what says whether the choice generalises.
    """
    digest = hashlib.sha256(request.encode("utf-8")).digest()
    return "tuning" if digest[0] % 2 == 0 else "held-out"


def _halves(cases):
    halves = {"tuning": [], "held-out": []}
    for case in cases:
        halves[_half(case["request"])].append(case)
    return halves


def _negatives(cases):
    return sum(1 for case in cases if not case["expected"])


def test_every_hub_skill_has_labelled_requests():
    """Adding a hub skill means adding requests for it to the labelled set."""
    present = set(_hub_skills())
    cases = _cases()
    assert len({case["request"] for case in cases}) == len(cases)
    for case in cases:
        assert not set(case["expected"]) & set(case["acceptable"]), case["request"]
    labelled = {n for case in _present_cases(present) for n in case["expected"]}
    unlabelled = sorted(present - labelled)
    assert not unlabelled, (
        f"hub skill(s) {', '.join(unlabelled)} have no request in "
        f"{CASES.relative_to(REPO).as_posix()}. Add about six invented "
        "requests that should suggest each one, plus a near-miss negative, "
        "then rerun the calibration tests."
    )
    assert len(cases) >= 30
    assert sum(1 for case in cases if not case["expected"]) >= 5


def test_the_split_is_fixed_and_both_halves_can_be_measured():
    assert _half("What's unread in my mailbox?") == "tuning"
    assert _half("Transcribe team-sync.mp4") == "held-out"
    present = set(_hub_skills())
    halves = _halves(_present_cases(present))
    for name, cases in halves.items():
        assert len(cases) >= 80, name
        # A fifth of each half needs no skill: wrong loads have to show.
        assert _negatives(cases) >= len(cases) // 5, name
        covered = {n for case in cases for n in case["expected"]}
        assert covered == present, (name, sorted(present - covered))
        # Long task prompts, where the ask is a small part of the text.
        assert sum(len(c["request"]) > 500 for c in cases) >= 8, name


#: Questions *about* a skill's topic. They score as high against that skill as
#: a real request does, so some load at any useful floor.
EXPLAIN_NEGATIVES = {
    "What is RSS and how do RSS feeds work?": "rss-digest",
    "How does image generation with diffusion models work, conceptually?": (
        "image-gen"
    ),
    "What does a source watch mean in a newsroom?": "source-watch",
    "Explain how GitHub issue triage works": "github-triage",
}


def test_questions_about_a_skills_topic_are_labelled_negatives():
    """The auto-load calibration must count these as loads on a negative."""
    present = set(_hub_skills())
    by_request = {case["request"]: case for case in _present_cases(present)}
    for request, near_miss in EXPLAIN_NEGATIVES.items():
        assert near_miss in present, near_miss
        case = by_request.get(request)
        assert case is not None, f"{request!r} is missing from {CASES.name}"
        assert case["expected"] == [] and case["acceptable"] == [], request


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
    from gaia.llm.lemonade_client import (
        EMBED_MAX_CHARS,
        lemonade_auth_headers,
        resolve_lemonade_api_key,
    )

    base_url = os.environ.get("LEMONADE_BASE_URL", "").rstrip("/")
    if not base_url:
        pytest.fail("set LEMONADE_BASE_URL to the running Lemonade Server")
    model = os.environ.get("GAIA_SKILL_SUGGEST_EMBED_MODEL", EMBEDDING_MODEL)
    # GAIA's own Lemonade is key-protected, and the suite hides its state file
    # (conftest's _isolate_embedded_lemonade_paths): name the real home to read it.
    with pytest.MonkeyPatch.context() as mp:
        if not os.environ.get("GAIA_HOME", "").strip():
            mp.setenv("GAIA_HOME", str(Path.home() / ".gaia"))
        headers = lemonade_auth_headers(resolve_lemonade_api_key(base_url=base_url))

    def batch(texts):
        response = requests.post(
            f"{base_url}/embeddings",
            json={"model": model, "input": list(texts)},
            headers=headers,
            timeout=120,
        )
        if response.status_code in (401, 403):
            pytest.fail(
                f"{base_url} rejected the request ({response.status_code}): "
                + (
                    "the resolved API key is not this server's."
                    if headers
                    else "no API key was found for it."
                )
                + " Set LEMONADE_API_KEY to the server's key."
            )
        response.raise_for_status()
        rows = [item["embedding"] for item in response.json()["data"]]
        vecs = np.asarray(rows, dtype=np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)

    # Cut like the agent's own embedder (MemoryMixin._embed_text), so a long
    # request is measured as a turn would score it.
    return (lambda text: batch([text[:EMBED_MAX_CHARS]])[0]), batch


#: What the suggestion defaults must reach on the held-out half, a little
#: under what they measured so an embedder build that rounds differently does
#: not fail it. Measured with embeddinggemma-300m, 28 hub skills:
#:
#:     half      requests negatives precision recall negatives-suggested
#:     tuning         114        33      0.85   0.83   6
#:     held-out       120        29      0.77   0.78   11
#:
#: A suggestion is one line the model may ignore, so recall is what it is for;
#: the negatives suggested are near misses ("what is a pivot table?").
SUGGEST_BAR = {"precision": 0.72, "recall": 0.72, "negatives": 0.45}


@pytest.mark.real_model
@pytest.mark.allow_network
def test_calibration_against_the_real_embedder(capsys):
    """Holds the suggestion defaults to :data:`SUGGEST_BAR`, companions on."""
    embed, batch = _real_embedder()
    embed("probe")  # an unreachable embedder fails here, not as recall 0
    skills = _hub_skills()
    halves = _halves(_present_cases(set(skills)))
    suggester = SkillSuggester(_memoized(embed), embed_batch_fn=batch)
    measured = {}
    for name, cases in halves.items():
        precision, recall, negatives_hit = _measure(suggester, skills, cases)
        measured[name] = (precision, recall, negatives_hit / _negatives(cases))
        with capsys.disabled():
            print(
                f"\nsuggestions, {name}: {len(cases)} requests, precision="
                f"{precision:.2f} recall={recall:.2f} negatives suggested="
                f"{negatives_hit}/{_negatives(cases)}"
            )
    precision, recall, negative_rate = measured["held-out"]
    assert precision >= SUGGEST_BAR["precision"]
    assert recall >= SUGGEST_BAR["recall"]
    assert negative_rate <= SUGGEST_BAR["negatives"]


#: The auto-load grid the calibration prints: floor by lead.
SWEEP_THRESHOLDS = (0.35, 0.40, 0.45, 0.50, 0.55)
SWEEP_MARGINS = (0.00, 0.03, 0.05, 0.08)

#: What the auto-load defaults (floor 0.40, lead 0.03) must reach on the
#: held-out half, a little under what they measured. Measured with
#: embeddinggemma-300m, 28 hub skills:
#:
#:     half      requests negatives precision recall loads wrong on-negatives
#:     tuning         114        33      0.91   0.72   68    6  4
#:     held-out       120        29      0.93   0.59   60    4  4
#:
#: A companion loads only when it leads the remaining skills itself. When
#: reaching the floor was enough, the tuning half had 74 loads, 7 wrong
#: (0.90, 0.73): 1 of the 7 companions loaded was a labelled skill.
#:
#: The floor and lead this replaced (0.55, 0.03, name-and-description text
#: with no prompt) measured precision 0.91, recall 0.11 (12 loads) on the
#: held-out half as it stood before office-docs joined (118 requests).
#: A wrong load costs context, not correctness (two skills a turn at most,
#: under the host's share-of-context cap), so a few loads on requests that
#: need no skill are accepted. They are questions about a skill's topic:
#: see EXPLAIN_NEGATIVES.
AUTOLOAD_BAR = {"precision": 0.88, "recall": 0.50, "negatives": 0.20}


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
    lowest = cells[(min(SWEEP_THRESHOLDS), min(SWEEP_MARGINS))]
    assert (
        lowest["loads"] >= cells[(max(SWEEP_THRESHOLDS), max(SWEEP_MARGINS))]["loads"]
    )
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


def _per_request(suggester, skills, cases):
    """One line per request: what loaded, the labels, and the two best scores."""
    lines = []
    for case in cases:
        scores = suggester.scores(case["request"], skills)
        got = suggester.select(case["request"], skills).auto_load
        allowed = set(case["expected"]) | set(case["acceptable"])
        if [name for name in got if name not in allowed]:
            verdict = "WRONG"
        elif got:
            verdict = "load"
        else:
            verdict = "miss" if case["expected"] else "-"
        top = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:2]
        lines.append(
            f"{verdict:5s} expected={','.join(case['expected']) or '-':22s} "
            + " ".join(f"{name}={score:.2f}" for name, score in top)
            + f" | {' '.join(case['request'].split())[:70]}"
        )
    return "\n".join(lines)


def test_per_request_lines_name_the_verdict_and_the_best_scores():
    skills = _skills(_skill("alpha"), _skill("beta"), _skill("gamma"))
    queries = {
        "right": {"alpha": 0.70, "beta": 0.30},
        "wrong": {"gamma": 0.70},
        "missed": {"alpha": 0.40},
        "chat": {"beta": 0.20},
    }
    s = SkillSuggester(FakeEmbedder(list(skills), queries), autoload_threshold=0.55)
    cases = [
        {"request": "right", "expected": ["alpha"], "acceptable": []},
        {"request": "wrong", "expected": [], "acceptable": []},
        {"request": "missed", "expected": ["alpha"], "acceptable": []},
        {"request": "chat", "expected": [], "acceptable": []},
    ]
    lines = _per_request(s, skills, cases).splitlines()
    assert lines[0].split() == [
        "load",
        "expected=alpha",
        "alpha=0.70",
        "beta=0.30",
        "|",
        "right",
    ]
    assert [line.split()[0] for line in lines] == ["load", "WRONG", "miss", "-"]


@pytest.mark.real_model
@pytest.mark.allow_network
def test_autoload_calibration_against_the_real_embedder(capsys):
    """Prints the floor-by-lead table per half and holds the defaults to
    :data:`AUTOLOAD_BAR` on the held-out half.

    Run with ``-s``. The tuning half is also printed request by request, with
    the two best-scoring skills, to show why a request loaded or did not; the
    held-out half is only ever shown as totals.
    """
    embed, batch = _real_embedder()
    embed("probe")  # an unreachable embedder fails here, not as recall 0
    embed = _memoized(embed)
    skills = _hub_skills()
    halves = _halves(_present_cases(set(skills)))
    cells = {}
    for name, cases in halves.items():
        cells[name] = _autoload_sweep(embed, batch, skills, cases)
        with capsys.disabled():
            print(
                f"\nauto-load sweep, {name}: {len(cases)} requests, "
                f"{_negatives(cases)} negatives, {len(skills)} hub skills"
            )
            print(_sweep_table(cells[name]))
    with capsys.disabled():
        suggester = SkillSuggester(
            embed,
            embed_batch_fn=batch,
            autoload_threshold=DEFAULT_AUTOLOAD_THRESHOLD,
            autoload_margin=DEFAULT_AUTOLOAD_MARGIN,
        )
        print("\ntuning half at the defaults, request by request")
        print(_per_request(suggester, skills, halves["tuning"]))
    defaults = cells["held-out"][(DEFAULT_AUTOLOAD_THRESHOLD, DEFAULT_AUTOLOAD_MARGIN)]
    assert defaults["precision"] >= AUTOLOAD_BAR["precision"], defaults
    assert defaults["recall"] >= AUTOLOAD_BAR["recall"], defaults
    negative_rate = defaults["on_negatives"] / _negatives(halves["held-out"])
    assert negative_rate <= AUTOLOAD_BAR["negatives"], defaults


def test_doc_text_is_what_the_fake_embedder_expects():
    assert skill_match_text(_skill("alpha")) == f"{DOC_PROMPT}does alpha"
