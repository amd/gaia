# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the per-turn ``SkillLoader`` (#2848 follow-up).

Pure loader logic — no Lemonade backend, no Agent. A deterministic fake
embedder gives exact control over cosine scores, mirroring
``test_tool_loader_selection.py``: each loaded skill's doc embeds to a
distinct one-hot axis, and a query embeds to a coordinate vector, so
``dot(query, skill_i)`` equals exactly the score assigned to that
(query, skill) pair.

The load-bearing test here is ``test_stale_match_is_dropped_next_turn`` — it
is the opposite assertion of ToolLoader's own
``test_monotonic_growth_no_pruning_on_score_drop``, and that contrast IS the
design: a tool's prompt line is cheap enough that monotonic growth is a
worthwhile trade for a stable KV-cache prefix, but a skill body is measured at
15-19KB (#2848), so the same monotonic trade would just reproduce the bug this
loader exists to fix.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from gaia.agents.base.skill_loader import SkillLoader, dynamic_skills_env_override

DIM = 32


def _skill(name: str, description: str) -> SimpleNamespace:
    """A minimal stand-in for ``gaia.skills.format.Skill`` — loader only reads
    ``.name`` and ``.description``."""
    return SimpleNamespace(name=name, description=description)


def _make_embed_fn(skills: list[str], query_scores: dict[str, dict[str, float]]):
    """Deterministic embedder over a fixed skill set (see module docstring)."""
    axis = {name: i for i, name in enumerate(skills)}
    assert len(skills) <= DIM
    docs = {f"{name}: does {name}": name for name in skills}

    def embed(text: str) -> np.ndarray:
        v = np.zeros(DIM, dtype=np.float32)
        if text in docs:
            v[axis[docs[text]]] = 1.0
            return v
        if text in query_scores:
            for name, score in query_scores[text].items():
                v[axis[name]] = score
            return v
        raise AssertionError(f"unexpected text embedded: {text!r}")

    return embed


def _loaded(skills: list[str]) -> dict[str, SimpleNamespace]:
    return {name: _skill(name, f"does {name}") for name in skills}


# ── basic selection ─────────────────────────────────────────────────────


def test_empty_loaded_skills_short_circuits_without_embedding():
    """No skills loaded -> [] immediately; embed_fn is never called."""

    def _boom(_text):
        raise AssertionError("embed_fn should not be called with nothing loaded")

    loader = SkillLoader(_boom)
    assert loader.select("anything", {}) == []


def test_threshold_boundary_is_inclusive():
    skills = ["hit", "miss"]
    embed = _make_embed_fn(skills, {"q": {"hit": 0.20, "miss": 0.1999}})
    loader = SkillLoader(embed, threshold=0.20)
    assert loader.select("q", _loaded(skills)) == ["hit"]


def test_below_threshold_collapses_to_empty_not_none():
    """A real miss is '[]' (render menus), never 'None' (render everything)."""
    skills = ["irrelevant"]
    embed = _make_embed_fn(skills, {"q": {"irrelevant": 0.0}})
    loader = SkillLoader(embed, threshold=0.20)
    assert loader.select("q", _loaded(skills)) == []


def test_multiple_skills_scored_independently():
    skills = ["a", "b", "c"]
    embed = _make_embed_fn(skills, {"q": {"a": 0.9, "b": 0.05, "c": 0.21}})
    loader = SkillLoader(embed, threshold=0.20)
    assert loader.select("q", _loaded(skills)) == ["a", "c"]


# ── non-monotonic re-evaluation (the actual bug fix) ────────────────────


def test_stale_match_is_dropped_next_turn():
    """A skill that matched turn 1 is NOT sticky when turn 2 no longer matches it.

    This is the opposite of ToolLoader's monotonic design, and deliberately
    so — see module docstring.
    """
    skills = ["github"]
    embed = _make_embed_fn(
        skills,
        {
            "q1": {"github": 0.9},  # turn 1: clearly relevant
            "q2": {"github": 0.0},  # turn 2: topic moved on
        },
    )
    loader = SkillLoader(embed, threshold=0.20)
    loaded = _loaded(skills)
    assert loader.select("q1", loaded) == ["github"]
    assert loader.select("q2", loaded) == []  # not ["github"] — no accumulation


def test_relevance_can_also_return_after_dropping():
    skills = ["github"]
    embed = _make_embed_fn(
        skills,
        {"q1": {"github": 0.9}, "q2": {"github": 0.0}, "q3": {"github": 0.5}},
    )
    loader = SkillLoader(embed, threshold=0.20)
    loaded = _loaded(skills)
    assert loader.select("q1", loaded) == ["github"]
    assert loader.select("q2", loaded) == []
    assert loader.select("q3", loaded) == ["github"]


# ── fail-safe posture (mirrors ToolLoader) ──────────────────────────────


def test_embedder_failure_disables_session_and_signals_fallback():
    def _boom(_text):
        raise RuntimeError("Lemonade unreachable")

    loader = SkillLoader(_boom)
    assert loader.select("q", _loaded(["a"])) is None
    assert loader.session_disabled is True
    # Stays disabled on a later call too, without calling embed_fn again.
    assert loader.select("q2", _loaded(["a"])) is None


def test_reset_session_clears_disabled_flag():
    calls = {"n": 0}

    def _flaky(_text):
        calls["n"] += 1
        raise RuntimeError("down")

    loader = SkillLoader(_flaky)
    assert loader.select("q", _loaded(["a"])) is None
    assert loader.session_disabled is True

    loader.reset_session()
    assert loader.session_disabled is False
    # A fresh session tries the embedder again (and fails again here).
    assert loader.select("q", _loaded(["a"])) is None
    assert calls["n"] == 2


# ── batch embedding + content-keyed cache ───────────────────────────────


def test_batch_embed_fn_used_when_provided_and_cached_after():
    skills = ["a", "b"]
    calls = {"batch": 0, "single": 0}

    def batch(texts):
        calls["batch"] += 1
        axis = {"a: does a": 0, "b: does b": 1}
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i, axis[t]] = 1.0
        return out

    def single(text):
        calls["single"] += 1
        if text == "q1":
            v = np.zeros(DIM, dtype=np.float32)
            v[0] = 0.9
            return v
        if text == "q2":
            v = np.zeros(DIM, dtype=np.float32)
            v[0] = 0.9
            v[1] = 0.9
            return v
        raise AssertionError(text)

    loader = SkillLoader(single, embed_batch_fn=batch, threshold=0.20)
    loaded = _loaded(skills)

    assert loader.select("q1", loaded) == ["a"]
    assert calls["batch"] == 1

    # Second turn: skill docs are unchanged, so the batch embedder is not
    # called again — only the (cheap) query embed runs.
    assert loader.select("q2", loaded) == ["a", "b"]
    assert calls["batch"] == 1


# ── env override ─────────────────────────────────────────────────────────


def test_env_override_parses_truthy_values(monkeypatch):
    monkeypatch.delenv("GAIA_DYNAMIC_SKILLS", raising=False)
    assert dynamic_skills_env_override() is None

    for value in ("1", "true", "Yes", "ON"):
        monkeypatch.setenv("GAIA_DYNAMIC_SKILLS", value)
        assert dynamic_skills_env_override() is True

    for value in ("0", "false", "no", "off"):
        monkeypatch.setenv("GAIA_DYNAMIC_SKILLS", value)
        assert dynamic_skills_env_override() is False
