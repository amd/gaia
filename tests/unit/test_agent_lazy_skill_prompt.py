# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Base ``Agent`` lazy skill-body activation (#2848 follow-up).

Drives the real ``Agent`` methods (``get_skills_system_prompt``, ``load_skill``,
``unload_skill``, the ``_active_skill_filter`` hooks) through a stub double that
never boots the LLM stack — the same pattern ``test_skill_sets.py`` uses for
skill-SET tests. Every skill here is a real, on-disk ``Skill`` loaded through the
real ``SkillManager``, so what these tests exercise is the actual prompt-render
code path, not a re-implementation of it.

The load-bearing assertions:

* ``test_legacy_default_...`` — an agent that never sets the filter (every
  agent except GaiaAgent, today) gets the byte-identical old behavior. This is
  the regression guard for #2848: the fix must be additive, not a default
  behavior change for every other agent that composes skills.
* ``test_inactive_skill_body_is_absent_...`` — the actual bug fix: a loaded
  skill outside the active filter contributes a menu line, not its body.
* ``test_load_skill_reactivates_...`` — capability preservation: the explicit
  escape hatch always works, even when the per-turn selector misses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.agents.base.agent import Agent

from .skills_helpers import isolated_manager, write_skill_dir

# A distinctive marker so "is the body in the prompt" is a real assertion, not
# an accidental substring match.
BODY_MARKER_TMPL = "ZZ-{name}-BODY-MARKER-ZZ"


def _skill_text(name: str, description: str | None = None) -> str:
    desc = description or f"Test skill {name}. Use when exercising lazy loading."
    marker = BODY_MARKER_TMPL.format(name=name.upper().replace("-", "_"))
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\n{marker}\n"


class _StubAgent:
    """Drives the real skill-prompt methods without booting the LLM stack."""

    REQUIRED_CONNECTORS: list = []
    SKILL_DIRS: list = []
    SKILL_MANIFEST = None
    _instance_tools = None
    _loaded_skills = None
    _skill_sets = None
    _requested_skill_set = None
    _active_skill_set = None
    _skill_set_loaded = None
    _active_skill_filter = None
    _SKILL_MANIFEST_FILENAME = Agent._SKILL_MANIFEST_FILENAME

    skill_manager = Agent.skill_manager
    skill_sets = Agent.skill_sets
    _resolve_skill_manifest = Agent._resolve_skill_manifest
    _parse_skill_declarations = Agent._parse_skill_declarations
    active_skill_set = Agent.active_skill_set
    loaded_skills = Agent.loaded_skills
    granted_binaries = Agent.granted_binaries
    _tools_registry = Agent._tools_registry
    _format_tools_for_prompt = Agent._format_tools_for_prompt
    load_skill = Agent.load_skill
    unload_skill = Agent.unload_skill
    select_skill_set = Agent.select_skill_set
    resolve_skill_set = Agent.resolve_skill_set
    load_skill_set = Agent.load_skill_set
    get_skills_system_prompt = Agent.get_skills_system_prompt
    _select_skills_for_turn = Agent._select_skills_for_turn
    _refresh_active_skill_filter = Agent._refresh_active_skill_filter
    _union_sticky_skills = Agent._union_sticky_skills
    STICKY_SKILL_TURNS = Agent.STICKY_SKILL_TURNS
    _sticky_skill_turns = None
    _note_skill_active = Agent._note_skill_active
    _pin_skill_body = Agent._pin_skill_body
    _always_on_skill_names = Agent._always_on_skill_names

    def __init__(self, manager, *, manifest=None, skill_set=None):
        self._skill_manager = manager
        self.SKILL_MANIFEST = str(manifest) if manifest else None
        self._requested_skill_set = skill_set
        self.rebuilt = 0

    def rebuild_system_prompt(self):
        self.rebuilt += 1

    def _apply_skill_filter(self, new_filter):
        # The real base method recomposes via ``_compose_system_prompt`` — this
        # stub never builds a full agent, so it mirrors ``rebuild_system_prompt``
        # (a counter) the same way it stands in for a real prompt cache.
        self._active_skill_filter = new_filter
        self.rebuild_system_prompt()


@pytest.fixture
def bundled(tmp_path: Path) -> Path:
    root = tmp_path / "pkg" / "skills"
    for name in ("skill-a", "skill-b", "always-on"):
        write_skill_dir(root, name, _skill_text(name))
    return root


@pytest.fixture
def agent(tmp_path, bundled) -> _StubAgent:
    return _StubAgent(isolated_manager(tmp_path, agent_skill_dirs=[bundled]))


def _marker(name: str) -> str:
    return BODY_MARKER_TMPL.format(name=name.upper().replace("-", "_"))


# ── legacy path: unchanged for every agent that hasn't opted in ─────────


def test_legacy_default_renders_full_body_for_every_loaded_skill(agent):
    """``_active_skill_filter is None`` (never touched) -> old behavior."""
    agent.load_skill("skill-a")
    agent.load_skill("skill-b")

    prompt = agent.get_skills_system_prompt()

    assert prompt.startswith("==== LOADED SKILLS ====")
    assert "(active this turn)" not in prompt
    assert "(instructions hidden" not in prompt
    assert _marker("skill-a") in prompt
    assert _marker("skill-b") in prompt


def test_default_select_skills_for_turn_hook_returns_none(agent):
    """The base class hook is a no-op — every existing agent stays on legacy."""
    assert agent._select_skills_for_turn("anything") is None


def test_no_skills_loaded_is_still_empty_string(agent):
    assert agent.get_skills_system_prompt() == ""


# ── lazy path: the actual fix ────────────────────────────────────────────


def test_inactive_skill_body_is_absent_but_named_in_a_menu(agent):
    agent.load_skill("skill-a")
    agent.load_skill("skill-b")
    agent._active_skill_filter = ["skill-a"]  # simulate a turn that only needs a

    prompt = agent.get_skills_system_prompt()

    assert "==== LOADED SKILLS (active this turn) ====" in prompt
    assert _marker("skill-a") in prompt
    assert _marker("skill-b") not in prompt
    assert "==== LOADED SKILLS (instructions hidden" in prompt
    assert "- skill-b:" in prompt


def test_empty_active_filter_hides_every_body(agent):
    agent.load_skill("skill-a")
    agent.load_skill("skill-b")
    agent._active_skill_filter = []  # simulate a turn that needs neither

    prompt = agent.get_skills_system_prompt()

    assert "(active this turn)" not in prompt  # no body section at all
    assert _marker("skill-a") not in prompt
    assert _marker("skill-b") not in prompt
    assert "- skill-a:" in prompt
    assert "- skill-b:" in prompt


def test_menu_entry_carries_the_real_description(agent):
    agent.load_skill("skill-a")
    agent._active_skill_filter = []

    prompt = agent.get_skills_system_prompt()

    assert "Test skill skill-a. Use when exercising lazy loading." in prompt


# ── always-on exemption (the manifest's plain `skills:` list) ───────────


def _write_manifest(tmp_path: Path) -> Path:
    import yaml

    path = tmp_path / "gaia-agent.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "demo",
                "name": "Demo",
                "version": "0.1.0",
                "description": "A demo agent.",
                "author": "AMD",
                "license": "MIT",
                "language": "python",
                "skills": ["always-on"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_always_on_skill_renders_in_full_even_when_the_filter_excludes_it(
    tmp_path, bundled
):
    manifest = _write_manifest(tmp_path)
    stub = _StubAgent(
        isolated_manager(tmp_path, agent_skill_dirs=[bundled]), manifest=manifest
    )
    stub.load_skill_set()  # loads the always-on 'always-on' skill
    stub.load_skill("skill-a")
    stub._active_skill_filter = []  # a turn that (per the selector) needs neither

    prompt = stub.get_skills_system_prompt()

    # always-on: exempt from the filter, full body every turn.
    assert _marker("always-on") in prompt
    # skill-a: not always-on, so it collapses to a menu line.
    assert _marker("skill-a") not in prompt
    assert "- skill-a:" in prompt


# ── the explicit reactivation escape hatch ───────────────────────────────


def test_load_skill_reactivates_an_already_loaded_but_hidden_skill(agent):
    agent.load_skill("skill-a")
    agent.load_skill("skill-b")
    agent._active_skill_filter = []  # both hidden
    assert _marker("skill-a") not in agent.get_skills_system_prompt()
    rebuilds_before = agent.rebuilt

    agent.load_skill("skill-a")  # already loaded — the reactivation path

    assert agent._active_skill_filter == ["skill-a"]
    assert agent.rebuilt == rebuilds_before + 1
    prompt = agent.get_skills_system_prompt()
    assert _marker("skill-a") in prompt
    assert _marker("skill-b") not in prompt  # b stays hidden


def test_reactivating_an_already_active_skill_is_a_no_op(agent):
    agent.load_skill("skill-a")
    agent._active_skill_filter = ["skill-a"]
    rebuilds_before = agent.rebuilt

    agent.load_skill("skill-a")

    assert agent.rebuilt == rebuilds_before  # no spurious rebuild


# ── explicit reactivation survives a few turns, then expires ─────────────


def test_explicit_reactivation_is_sticky_across_refreshes(agent):
    """A follow-up like "yes, continue" scores nothing against the skill's
    description; the explicit load_skill must survive those turns or the model
    is stranded mid-recipe one exchange after the user asked for the skill."""
    agent.load_skill("skill-a")
    agent._active_skill_filter = []
    agent.load_skill("skill-a")  # explicit reactivation — pins it
    agent._select_skills_for_turn = lambda _q: []  # selector never matches again

    for _ in range(agent.STICKY_SKILL_TURNS):
        agent._refresh_active_skill_filter("yes, continue")
        assert agent._active_skill_filter == ["skill-a"]

    agent._refresh_active_skill_filter("something else entirely")
    assert agent._active_skill_filter == []  # pin expired, semantics rule again


# ── unload prunes the filter ─────────────────────────────────────────────


def test_unload_skill_prunes_it_from_the_active_filter(agent):
    agent.load_skill("skill-a")
    agent.load_skill("skill-b")
    agent._active_skill_filter = ["skill-a", "skill-b"]

    agent.unload_skill("skill-a")

    assert agent._active_skill_filter == ["skill-b"]
    assert "skill-a" not in agent.loaded_skills


# ── per-turn refresh: recompute-on-change semantics ──────────────────────


def test_refresh_only_rebuilds_the_prompt_when_the_selection_changes(agent):
    agent.load_skill("skill-a")
    calls = {"selected": []}

    def fake_select(user_input):
        calls["selected"].append(user_input)
        return ["skill-a"] if "github" in user_input else []

    agent._select_skills_for_turn = fake_select

    agent._refresh_active_skill_filter("please help with github")
    rebuilds_after_first = agent.rebuilt
    assert agent._active_skill_filter == ["skill-a"]
    assert rebuilds_after_first >= 1

    # Same selection again (still "github") -> filter unchanged -> no rebuild.
    agent._refresh_active_skill_filter("more on github please")
    assert agent.rebuilt == rebuilds_after_first

    # Topic moves on -> selection changes -> exactly one more rebuild.
    agent._refresh_active_skill_filter("what's the weather")
    assert agent._active_skill_filter == []
    assert agent.rebuilt == rebuilds_after_first + 1
