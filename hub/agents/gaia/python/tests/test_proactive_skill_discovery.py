# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GaiaAgent's proactive skill-discovery wiring.

The retriever's accuracy lives in ``tests/unit/test_skill_retriever.py`` and the
per-turn behaviour in ``tests/unit/test_skill_discovery.py``. This file covers
only the wiring: the toggles resolve, the hook runs before the body filter, a
skill loaded proactively is pinned so the very next filter cannot hide it, and
every agent that did *not* opt in has a byte-identical prompt.

Built with ``GaiaAgent.__new__(GaiaAgent)`` like ``test_lazy_skill_activation.py``
— no LLM, no Lemonade, no embedder.
"""

from __future__ import annotations


import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.skill_discovery import (
    DISCOVERY_ENV,
    DISCOVERY_THRESHOLD_ENV,
    DiscoveryResult,
    SkillDiscovery,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(DISCOVERY_ENV, raising=False)
    monkeypatch.delenv(DISCOVERY_THRESHOLD_ENV, raising=False)


def _agent(**config_kwargs) -> GaiaAgent:
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(**config_kwargs)
    return agent


# ── toggles ──────────────────────────────────────────────────────────────


def test_discovery_is_on_by_default_for_the_flagship():
    """This is the agent that ships a skill library and meets users who have
    never read it — the one place the default has to be on."""
    assert GaiaAgentConfig().skill_discovery is True


def test_config_can_switch_it_off():
    assert _agent(skill_discovery=False)._maybe_build_skill_discovery() is None


def test_it_builds_when_enabled():
    """``skill_manager`` is a lazy property and building it touches no disk —
    discovery only scans on the first turn."""
    assert isinstance(_agent()._maybe_build_skill_discovery(), SkillDiscovery)


def test_env_override_wins_over_config(monkeypatch):
    monkeypatch.setenv(DISCOVERY_ENV, "0")
    assert _agent(skill_discovery=True)._maybe_build_skill_discovery() is None


def test_env_override_can_force_it_on(monkeypatch):
    monkeypatch.setenv(DISCOVERY_ENV, "1")
    agent = _agent(skill_discovery=False)
    assert isinstance(agent._maybe_build_skill_discovery(), SkillDiscovery)


def test_threshold_env_override_wins(monkeypatch):
    monkeypatch.setenv(DISCOVERY_THRESHOLD_ENV, "0.42")
    assert _agent(skill_discovery_threshold=0.2)._resolve_discovery_threshold() == (
        pytest.approx(0.42)
    )


def test_malformed_threshold_env_fails_loudly(monkeypatch):
    monkeypatch.setenv(DISCOVERY_THRESHOLD_ENV, "not-a-float")
    with pytest.raises(ValueError, match=DISCOVERY_THRESHOLD_ENV):
        _agent()._resolve_discovery_threshold()


def test_threshold_defaults_to_the_module_constant():
    """None means "use the benchmarked MIN_SCORE" — the config does not fork it."""
    assert GaiaAgentConfig().skill_discovery_threshold is None


# ── the per-turn hook ────────────────────────────────────────────────────


class _Discovery:
    """Stand-in that returns a scripted result and records the query it got."""

    def __init__(self, result: DiscoveryResult):
        self.result = result
        self.queries: list[str] = []

    def run(self, query, *, loaded, load_fn):
        self.queries.append(query)
        return self.result


def _hooked_agent(result: DiscoveryResult, **kwargs) -> GaiaAgent:
    agent = _agent(**kwargs)
    agent._skill_discovery = _Discovery(result)
    agent._skill_discovery_result = None
    agent._loaded_skills = {}
    agent._sticky_skill_turns = None
    agent._active_skill_filter = None
    agent.rebuild_system_prompt = lambda: None
    return agent


def test_the_hook_records_this_turns_result():
    agent = _hooked_agent(DiscoveryResult(loaded="github-triage"))
    agent._discover_skills_for_turn("triage my github inbox")
    assert agent._skill_discovery_result.loaded == "github-triage"


def test_an_auto_loaded_skill_is_pinned_so_the_body_filter_cannot_hide_it():
    """Discovery runs before the first body-filter refresh of a session.

    Without the pin the filter that runs immediately afterwards could score the
    skill below threshold and collapse the body of the very skill this turn was
    loaded for.
    """
    agent = _hooked_agent(DiscoveryResult(loaded="github-triage"))
    agent._discover_skills_for_turn("triage my github inbox")
    assert agent._sticky_skill_turns["github-triage"] == GaiaAgent.STICKY_SKILL_TURNS


def test_nothing_is_pinned_when_nothing_loaded():
    agent = _hooked_agent(DiscoveryResult(shortlist=("price-watch",)))
    agent._discover_skills_for_turn("watch this")
    assert not (agent._sticky_skill_turns or {})


def test_the_hook_is_a_no_op_without_discovery():
    agent = _agent()
    agent._skill_discovery = None
    agent._skill_discovery_result = None
    agent._discover_skills_for_turn("anything")
    assert agent._skill_discovery_result is None


def test_the_query_carries_the_previous_turn_for_short_follow_ups():
    """ "and the one before that?" has no subject of its own."""
    agent = _hooked_agent(DiscoveryResult())
    agent._build_tool_selection_query = lambda text: f"PREV {text}"
    agent._discover_skills_for_turn("and the one before that?")
    assert agent._skill_discovery.queries == ["PREV and the one before that?"]


def test_the_query_falls_back_to_the_bare_message():
    agent = _hooked_agent(DiscoveryResult())
    agent._discover_skills_for_turn("triage my github inbox")
    assert agent._skill_discovery.queries == ["triage my github inbox"]


# ── the prompt fragment ──────────────────────────────────────────────────


def test_agents_without_discovery_get_no_fragment_at_all():
    """Byte-identical guarantee: every other agent's composed prompt is unchanged."""
    agent = _agent()
    agent._skill_discovery = None
    assert agent.get_skill_discovery_system_prompt() == ""


def test_the_sourcing_rule_is_present_even_when_no_skill_matched():
    """The observed fabrication happened on a turn where nothing matched, so the
    rule cannot be conditional on a match."""
    agent = _hooked_agent(DiscoveryResult())
    agent._discover_skills_for_turn("what is 17 times 23?")
    fragment = agent.get_skill_discovery_system_prompt()
    assert "SOURCING" in fragment
    assert "SKILL ACTIVATED" not in fragment


def test_a_failed_load_reaches_the_model_as_a_refusal():
    agent = _hooked_agent(
        DiscoveryResult(failed=("github-triage", "gh is not installed"))
    )
    agent._discover_skills_for_turn("triage my github inbox")
    fragment = agent.get_skill_discovery_system_prompt()
    assert "SKILL UNAVAILABLE" in fragment
    assert "gh is not installed" in fragment


def test_the_fragment_is_discovered_by_the_mixin_prompt_scan():
    """``_get_mixin_prompts`` finds ``get_*_system_prompt`` by name — a rename
    would silently drop this from the prompt with nothing failing."""
    assert hasattr(GaiaAgent, "get_skill_discovery_system_prompt")
    name = "get_skill_discovery_system_prompt"
    assert name.startswith("get_") and name.endswith("_system_prompt")
