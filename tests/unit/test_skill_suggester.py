# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-turn skill suggestions (``gaia.agents.base.skill_suggester``).

Selection logic runs against a deterministic fake embedder: each skill's doc
text embeds to its own axis and a query embeds to the scores the test assigns,
so ``dot(query, skill)`` is exactly that score.

The calibration against the real embedder is opt-in
(``GAIA_SKILL_SUGGEST_CALIBRATE=1``, plus ``LEMONADE_BASE_URL`` naming a
running Lemonade Server); it scores the
labelled requests in ``tests/fixtures/skills/skill_suggestion_cases.json``
against every hub skill and holds the defaults to the precision and recall
they were tuned at.
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
    DEFAULT_COMPANION_THRESHOLD,
    DEFAULT_SUGGEST_MARGIN,
    DEFAULT_SUGGEST_THRESHOLD,
    DEFAULT_SUGGEST_TOP_K,
    SkillSuggester,
    render_suggestion,
    suggest_env_override,
)
from gaia.skills.manager import ROOT_CLAUDE_IMPORT

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
    assert "embedder down" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    tried = len(calls)
    assert s.suggest("draw me a cat", skills) == []
    assert len(calls) == tried  # no retry while cooling down


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
    text = render_suggestion(["alpha", "beta"])
    assert text.startswith("Skills that match this request and are not loaded yet:")
    assert "not loaded yet: alpha, beta" in text
    assert len(text.splitlines()) == 1
    assert "load_skill" in text


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


def test_doc_text_is_what_the_fake_embedder_expects():
    assert skill_doc_text(_skill("alpha")) == "alpha: does alpha"
