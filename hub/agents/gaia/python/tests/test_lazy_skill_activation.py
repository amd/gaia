# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GaiaAgent's per-turn skill-body activation wiring (#2848 follow-up).

Every test here builds via ``GaiaAgent.__new__(GaiaAgent)`` — the same pattern
``test_gaia_agent.py`` uses for ``select_skill_set`` — so none of this boots
the LLM stack or touches Lemonade. Only the specific attributes each hook
under test actually reads are set by hand; that is deliberate, not
laziness — it proves each hook depends on exactly what its docstring claims
and nothing else picked up incidentally from a full ``__init__``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.skill_loader import DEFAULT_SKILL_THRESHOLD, SkillLoader


def _fake_embed(_text):
    raise AssertionError("embed_fn should not be invoked by these tests")


# ── _maybe_build_skill_loader / enable resolution ────────────────────────


def test_dynamic_skills_on_by_default_for_this_agent():
    """Flagship default differs from ChatAgent's tool loader (off by default):
    this fix targets a measured, always-present cost for THIS agent."""
    assert GaiaAgentConfig().dynamic_skills is True


def test_maybe_build_skill_loader_off_when_config_disables_it(monkeypatch):
    monkeypatch.delenv("GAIA_DYNAMIC_SKILLS", raising=False)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(dynamic_skills=False)
    assert agent._maybe_build_skill_loader() is None


def test_maybe_build_skill_loader_builds_when_enabled(monkeypatch):
    monkeypatch.delenv("GAIA_DYNAMIC_SKILLS", raising=False)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(dynamic_skills=True)
    agent._embed_text = _fake_embed
    agent._embed_texts_batch = _fake_embed

    loader = agent._maybe_build_skill_loader()

    assert isinstance(loader, SkillLoader)
    assert loader.session_disabled is False


def test_env_override_wins_over_config_field(monkeypatch):
    monkeypatch.setenv("GAIA_DYNAMIC_SKILLS", "0")
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(dynamic_skills=True)  # config says on
    assert agent._resolve_dynamic_skills_enabled() is False  # env wins


def test_env_override_can_force_it_on(monkeypatch):
    monkeypatch.setenv("GAIA_DYNAMIC_SKILLS", "1")
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(dynamic_skills=False)
    assert agent._resolve_dynamic_skills_enabled() is True


# ── threshold resolution ─────────────────────────────────────────────────


def test_threshold_defaults_from_config(monkeypatch):
    monkeypatch.delenv("GAIA_DYNAMIC_SKILLS_TAU", raising=False)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(dynamic_skills_threshold=0.33)
    assert agent._resolve_dynamic_skills_threshold() == pytest.approx(0.33)


def test_threshold_env_override_wins(monkeypatch):
    monkeypatch.setenv("GAIA_DYNAMIC_SKILLS_TAU", "0.42")
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(dynamic_skills_threshold=0.20)
    assert agent._resolve_dynamic_skills_threshold() == pytest.approx(0.42)


def test_malformed_threshold_env_fails_loudly(monkeypatch):
    monkeypatch.setenv("GAIA_DYNAMIC_SKILLS_TAU", "not-a-float")
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig()
    with pytest.raises(ValueError, match="GAIA_DYNAMIC_SKILLS_TAU"):
        agent._resolve_dynamic_skills_threshold()


def test_default_threshold_matches_module_constant():
    assert GaiaAgentConfig().dynamic_skills_threshold == DEFAULT_SKILL_THRESHOLD


# ── _dynamic_skills_active / _select_skills_for_turn ─────────────────────


def _agent_with_loader(monkeypatch, *, memory_store, embed_fn=None):
    monkeypatch.delenv("GAIA_DYNAMIC_SKILLS", raising=False)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(dynamic_skills=True)
    agent.conversation_history = []
    agent._loaded_skills = {}
    agent._memory_store = memory_store
    agent.skill_loader = SkillLoader(embed_fn or _fake_embed)
    return agent


def test_off_when_memory_store_absent(monkeypatch):
    """Same off-switch ChatAgent's tool loader uses: no store, no selection."""
    agent = _agent_with_loader(monkeypatch, memory_store=None)
    assert agent._dynamic_skills_active() is False
    assert agent._select_skills_for_turn("anything") is None


def test_off_when_loader_not_built(monkeypatch):
    agent = _agent_with_loader(monkeypatch, memory_store=object())
    agent.skill_loader = None
    assert agent._dynamic_skills_active() is False
    assert agent._select_skills_for_turn("anything") is None


def test_off_after_loader_disables_itself(monkeypatch):
    def _boom(_text):
        raise RuntimeError("embedder down")

    agent = _agent_with_loader(monkeypatch, memory_store=object(), embed_fn=_boom)
    # Loader disables itself on first failed call (empty loaded set skips the
    # embedder, so give it one skill to actually trigger the failure).
    agent._loaded_skills = {"x": SimpleNamespace(name="x", description="does x")}
    assert agent._select_skills_for_turn("q") is None  # first call: fails once
    assert agent._dynamic_skills_active() is False  # now off for the session


def test_nothing_loaded_short_circuits_before_any_embedding(monkeypatch):
    def _boom(_text):
        raise AssertionError("nothing loaded — embed_fn must not be called")

    agent = _agent_with_loader(monkeypatch, memory_store=object(), embed_fn=_boom)
    agent._loaded_skills = {}

    assert agent._select_skills_for_turn("current message") == []


def test_active_query_is_previous_plus_current_message(monkeypatch):
    """Reuses ChatAgent's ``_build_tool_selection_query`` — prev + current —
    so a short follow-up still matches on the prior turn's context."""
    seen = {}
    import numpy as np

    def embed(text):
        seen["last"] = text
        return np.zeros(4, dtype=np.float32)

    agent = _agent_with_loader(monkeypatch, memory_store=object(), embed_fn=embed)
    agent._loaded_skills = {"x": SimpleNamespace(name="x", description="does x")}
    agent.conversation_history = [{"role": "user", "content": "earlier message"}]

    agent._select_skills_for_turn("current message")

    # The skill doc embeds first, the query last — the last call the fake
    # embedder saw is what was actually searched for.
    assert seen["last"] == "earlier message\ncurrent message"
