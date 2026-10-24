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
    DEFAULT_SUGGEST_THRESHOLD,
    SkillSuggester,
)


def _never(_text):
    raise AssertionError("the embedder should not be called")


def _agent(monkeypatch, *, catalog=True, **config):
    monkeypatch.delenv("GAIA_SKILL_SUGGEST", raising=False)
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
    def __init__(self, names):
        self.names = names
        self.calls = []

    def suggest(self, query, skills, loaded=frozenset()):
        self.calls.append((query, set(skills), set(loaded)))
        return list(self.names)


def _wired(monkeypatch, names, *, memory_store=object()):
    agent = _agent(monkeypatch)
    agent.skill_suggester = _Suggester(names)
    agent._memory_store = memory_store
    agent._loaded_skills = {"coding": SimpleNamespace(name="coding")}
    agent._skill_manager = SimpleNamespace(
        discover=lambda: {"coding": object(), "summarize": object()}
    )
    return agent


def test_scores_the_current_message_against_installed_skills(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"])
    agent.conversation_history = [{"role": "user", "content": "earlier ask"}]
    assert agent._select_skill_suggestions_for_turn("sum this up") == ["summarize"]
    query, installed, loaded = agent.skill_suggester.calls[0]
    assert query == "sum this up"
    assert installed == {"coding", "summarize"}
    assert loaded == {"coding"}


def test_no_suggestions_without_the_memory_embedder(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"], memory_store=None)
    assert agent._select_skill_suggestions_for_turn("sum this up") == []
    assert agent.skill_suggester.calls == []


def test_no_suggestions_when_the_suggester_is_off(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"])
    agent.skill_suggester = None
    assert agent._select_skill_suggestions_for_turn("sum this up") == []


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
    assert not [name for name in fragments if "suggest" in name]
    assert not [n for n in Agent.VOLATILE_PROMPT_FRAGMENTS if "suggest" in n]


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
