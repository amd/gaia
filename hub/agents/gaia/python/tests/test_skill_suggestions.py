# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GaiaAgent's per-turn skill suggestions: settings, gates and the turn line.

Built via ``GaiaAgent.__new__`` like ``test_lazy_skill_activation.py``, so no
LLM stack or Lemonade is touched; selection itself is covered by
``tests/unit/test_skill_suggester.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig, _apply_fast_mode

from gaia.agents.base.agent import Agent
from gaia.agents.base.skill_suggester import (
    DEFAULT_AUTOLOAD_THRESHOLD,
    DEFAULT_SUGGEST_THRESHOLD,
    SkillMatches,
    SkillSuggester,
)


def _never(_text):
    raise AssertionError("the embedder should not be called")


def _agent(monkeypatch, *, catalog=True, **config):
    monkeypatch.delenv("GAIA_SKILL_SUGGEST", raising=False)
    monkeypatch.delenv("GAIA_SKILL_AUTOLOAD", raising=False)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(**config)
    agent._skill_catalog_enabled = catalog
    agent._embed_text = _never
    agent._embed_texts_batch = _never
    return agent


def test_on_by_default_with_the_calibrated_threshold():
    config = GaiaAgentConfig()
    assert config.skill_suggestions is True
    assert config.skill_suggestion_threshold == DEFAULT_SUGGEST_THRESHOLD


def test_built_when_on(monkeypatch):
    agent = _agent(monkeypatch, skill_suggestion_threshold=0.4)
    suggester = agent._maybe_build_skill_suggester()
    assert isinstance(suggester, SkillSuggester)
    assert suggester.threshold == pytest.approx(0.4)


def test_off_without_the_catalogue(monkeypatch):
    agent = _agent(monkeypatch, catalog=False)
    monkeypatch.setenv("GAIA_SKILL_SUGGEST", "1")
    assert agent._maybe_build_skill_suggester() is None


def test_config_can_turn_it_off(monkeypatch):
    agent = _agent(monkeypatch, skill_suggestions=False)
    assert agent._maybe_build_skill_suggester() is None


@pytest.mark.parametrize("raw,built", [("0", False), ("1", True)])
def test_env_wins_over_config(monkeypatch, raw, built):
    agent = _agent(monkeypatch, skill_suggestions=not built)
    monkeypatch.setenv("GAIA_SKILL_SUGGEST", raw)
    assert (agent._maybe_build_skill_suggester() is not None) is built


def test_fast_mode_turns_it_off():
    config = GaiaAgentConfig()
    _apply_fast_mode(config)
    assert config.skill_suggestions is False


class _Suggester:
    def __init__(self, names, auto=()):
        self.names = names
        self.auto = list(auto)
        self.calls = []

    def select(self, query, skills, loaded=frozenset()):
        self.calls.append((query, set(skills), set(loaded)))
        return SkillMatches(
            auto_load=[n for n in self.auto if n not in loaded],
            suggest=[n for n in self.names if n not in loaded],
        )


def _wired(monkeypatch, names, *, memory_store=object(), auto=()):
    agent = _agent(monkeypatch)
    agent.skill_suggester = _Suggester(names, auto)
    agent._memory_store = memory_store
    agent._loaded_skills = {"coding": SimpleNamespace(name="coding")}
    agent._skill_manager = SimpleNamespace(
        discover=lambda: {"coding": object(), "summarize": object()}
    )
    return agent


def test_scores_the_current_message_against_installed_skills(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"])
    agent.conversation_history = [{"role": "user", "content": "earlier ask"}]
    assert agent._select_skill_matches_for_turn("sum this up") == SkillMatches(
        suggest=["summarize"]
    )
    query, installed, loaded = agent.skill_suggester.calls[0]
    assert query == "sum this up"
    assert installed == {"coding", "summarize"}
    assert loaded == {"coding"}


def test_no_suggestions_without_the_memory_embedder(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"], memory_store=None)
    assert agent._select_skill_matches_for_turn("sum this up") == SkillMatches()
    assert agent.skill_suggester.calls == []


def test_no_suggestions_when_the_suggester_is_off(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"])
    agent.skill_suggester = None
    assert agent._select_skill_matches_for_turn("sum this up") == SkillMatches()


def _with_turn_context(monkeypatch, agent, query):
    from gaia.agents.base.project_map import ProjectMapMixin

    monkeypatch.setattr(
        ProjectMapMixin, "get_memory_dynamic_context", lambda self: "Current time: now"
    )
    agent._memory_turn_query = query
    return agent.get_memory_dynamic_context()


def test_the_line_joins_this_turns_context(monkeypatch):
    agent = _wired(monkeypatch, ["summarize", "data-explore"])
    context = _with_turn_context(monkeypatch, agent, "sum up this spreadsheet")
    assert context.splitlines() == [
        "Current time: now",
        "Skills that match this request and are not loaded yet: summarize, "
        "data-explore — load them with load_skill before starting.",
    ]
    assert agent.skill_suggester.calls[0][0] == "sum up this spreadsheet"


def test_no_match_leaves_the_turn_context_alone(monkeypatch):
    agent = _wired(monkeypatch, [])
    assert _with_turn_context(monkeypatch, agent, "thanks") == "Current time: now"


def test_the_system_prompt_never_carries_a_suggestion():
    """A per-turn line in the system prompt would break the prompt cache."""
    fragments = [
        name
        for name in dir(GaiaAgent)
        if name.startswith("get_") and name.endswith("_system_prompt")
    ]
    for word in ("suggest", "autoload", "auto_load"):
        assert not [name for name in fragments if word in name]
        assert not [n for n in Agent.VOLATILE_PROMPT_FRAGMENTS if word in n]


def test_a_real_suggester_reaches_the_turn_context(monkeypatch):
    agent = _agent(monkeypatch)
    skills = {
        name: SimpleNamespace(
            name=name,
            description=f"does {name}",
            root="agent",
            gaia=SimpleNamespace(pairs_with=[]),
        )
        for name in ("image-gen", "coding")
    }
    axis = {"image-gen: does image-gen": 0, "coding: does coding": 1}

    def embed(text):
        v = np.zeros(4, dtype=np.float32)
        v[axis.get(text, 0)] = 1.0
        return v

    agent.skill_suggester = SkillSuggester(embed)
    agent._memory_store = object()
    agent._loaded_skills = {}
    agent._skill_manager = SimpleNamespace(discover=lambda: skills)
    context = _with_turn_context(monkeypatch, agent, "draw a lighthouse")
    assert "not loaded yet: image-gen —" in context


# ── auto-load ───────────────────────────────────────────────────────────────


def test_autoload_on_by_default_with_the_calibrated_threshold():
    config = GaiaAgentConfig()
    assert config.skill_autoload is True
    assert config.skill_autoload_threshold == DEFAULT_AUTOLOAD_THRESHOLD


def test_the_suggester_carries_the_autoload_threshold(monkeypatch):
    agent = _agent(monkeypatch, skill_autoload_threshold=0.6)
    assert agent._maybe_build_skill_suggester().autoload_threshold == pytest.approx(0.6)


def test_config_can_turn_autoload_off_and_keep_suggestions(monkeypatch):
    agent = _agent(monkeypatch, skill_autoload=False)
    suggester = agent._maybe_build_skill_suggester()
    assert suggester is not None
    assert suggester.autoload_threshold is None


@pytest.mark.parametrize("raw,on", [("0", False), ("1", True)])
def test_autoload_env_wins_over_config(monkeypatch, raw, on):
    agent = _agent(monkeypatch, skill_autoload=not on)
    monkeypatch.setenv("GAIA_SKILL_AUTOLOAD", raw)
    suggester = agent._maybe_build_skill_suggester()
    assert (suggester.autoload_threshold is not None) is on


def test_no_autoload_without_suggestions(monkeypatch):
    agent = _agent(monkeypatch, skill_suggestions=False)
    monkeypatch.setenv("GAIA_SKILL_AUTOLOAD", "1")
    assert agent._maybe_build_skill_suggester() is None


def test_fast_mode_turns_autoload_off():
    config = GaiaAgentConfig()
    _apply_fast_mode(config)
    assert config.skill_autoload is False


def _full_skill(name, body="x" * 400, permissions=()):
    from gaia.skills.permissions import parse_permissions

    return SimpleNamespace(
        name=name,
        body=body,
        parsed_permissions=lambda: parse_permissions(
            list(permissions), skill_name=name
        ),
    )


def _loading(monkeypatch, auto, *, names=(), library=None, fail=None):
    """An agent whose suggester auto-loads *auto* and whose load_skill works."""
    agent = _wired(monkeypatch, list(names), auto=auto)
    agent._loaded_skills = {}
    library = library or {}
    for name in [*auto, *names]:
        library.setdefault(name, _full_skill(name))
    agent._skill_manager = SimpleNamespace(
        discover=lambda: dict(library), load=lambda name: library[name]
    )
    agent.loads = []

    def load_skill(name):
        if fail is not None and name == fail:
            from gaia.skills.errors import SkillPermissionError

            raise SkillPermissionError(f"{name} needs a connector")
        agent.loads.append(name)
        agent._loaded_skills[name] = library[name]
        return library[name]

    agent.load_skill = load_skill
    return agent


def test_a_high_confidence_match_is_loaded_and_announced(monkeypatch):
    agent = _loading(monkeypatch, ["report-from-sources"], names=["office-docs"])
    context = _with_turn_context(monkeypatch, agent, "build a deck from my docs")
    assert agent.loads == ["report-from-sources"]
    assert context.splitlines() == [
        "Current time: now",
        "Loaded report-from-sources because it matches this request — its "
        "instructions are in the system prompt.",
        "Skills that match this request and are not loaded yet: office-docs — "
        "load them with load_skill before starting.",
    ]
    # Pinned, so this turn's body selection cannot hide what it was loaded for.
    assert "report-from-sources" in agent._sticky_skill_turns


def test_a_loaded_skill_is_not_reloaded_or_re_suggested(monkeypatch):
    agent = _loading(monkeypatch, ["report-from-sources"])
    _with_turn_context(monkeypatch, agent, "build a deck from my docs")
    context = _with_turn_context(monkeypatch, agent, "now a second deck")
    assert agent.loads == ["report-from-sources"]
    assert context == "Current time: now"
    assert agent.skill_suggester.calls[1][2] == {"report-from-sources"}


def test_an_unloaded_auto_load_is_only_suggested_after(monkeypatch):
    agent = _loading(monkeypatch, ["summarize"])
    _with_turn_context(monkeypatch, agent, "sum this up")
    del agent._loaded_skills["summarize"]  # the model unloaded it
    context = _with_turn_context(monkeypatch, agent, "sum this up too")
    assert agent.loads == ["summarize"]
    assert "not loaded yet: summarize —" in context
    assert "Loaded summarize" not in context


def test_a_skill_that_grants_a_cli_is_suggested_not_loaded(monkeypatch):
    library = {
        "github-triage": _full_skill("github-triage", permissions=["shell:execute:gh"])
    }
    agent = _loading(monkeypatch, ["github-triage"], library=library)
    context = _with_turn_context(monkeypatch, agent, "triage my issues")
    assert agent.loads == []
    assert "not loaded yet: github-triage —" in context


def test_the_body_budget_stops_an_auto_load(monkeypatch):
    budget = GaiaAgent.SKILL_AUTOLOAD_BODY_TOKENS
    library = {
        "big": _full_skill("big", body="x" * (budget * 4 - 400)),
        "small": _full_skill("small", body="x" * 800),
    }
    agent = _loading(monkeypatch, ["big", "small"], library=library)
    context = _with_turn_context(monkeypatch, agent, "do both")
    assert agent.loads == ["big"]
    assert "Loaded big because it matches" in context
    assert "not loaded yet: small —" in context


def test_bodies_already_loaded_count_against_the_budget(monkeypatch):
    budget = GaiaAgent.SKILL_AUTOLOAD_BODY_TOKENS
    agent = _loading(monkeypatch, ["summarize"])
    agent._loaded_skills["coding"] = _full_skill("coding", body="x" * budget * 4)
    _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.loads == []


def test_a_failed_auto_load_falls_back_to_the_suggestion(monkeypatch, caplog):
    import logging

    agent = _loading(monkeypatch, ["inbox-triage"], fail="inbox-triage")
    with caplog.at_level(logging.WARNING):
        context = _with_turn_context(monkeypatch, agent, "check my mail")
    assert "not loaded yet: inbox-triage —" in context
    assert "could not auto-load 'inbox-triage'" in caplog.text


def test_no_embedder_means_no_auto_load(monkeypatch):
    agent = _loading(monkeypatch, ["summarize"])
    agent._memory_store = None
    assert _with_turn_context(monkeypatch, agent, "sum this up") == (
        "Current time: now"
    )
    assert agent.loads == []
    assert agent.skill_suggester.calls == []


def test_a_real_suggester_auto_loads_at_most_two(monkeypatch):
    names = ("alpha", "beta", "gamma")
    skills = {
        name: SimpleNamespace(
            name=name,
            description=f"does {name}",
            root="agent",
            gaia=SimpleNamespace(pairs_with=[]),
        )
        for name in names
    }
    axis = {f"{name}: does {name}": i for i, name in enumerate(names)}

    def embed(text):
        v = np.zeros(4, dtype=np.float32)
        if text in axis:
            v[axis[text]] = 1.0
        else:
            v[:3] = 0.57  # the request: every skill well above the threshold
        return v

    agent = _loading(monkeypatch, [], names=names)
    agent.skill_suggester = SkillSuggester(
        embed, autoload_threshold=0.55, autoload_margin=0.0
    )
    agent._skill_manager = SimpleNamespace(
        discover=lambda: skills,
        load=lambda name: _full_skill(name),
    )
    context = _with_turn_context(monkeypatch, agent, "do it all")
    assert agent.loads == ["alpha", "beta"]
    assert "Loaded alpha and beta because they match" in context
    assert "not loaded yet: gamma —" in context
