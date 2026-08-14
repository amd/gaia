# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The flagship must be able to find the starter pack in a source checkout.

Packaging stages hub/skills into ``gaia_agent/skills``; a checkout has only a
``.gitkeep`` there. Before this, ``SKILL_DIRS`` named that empty directory and
nothing else, so a developer running from the tree discovered zero bundled
skills and "load the github-triage skill" failed on a repo that visibly
contains github-triage. The agent answered by loading a *different*,
similarly-named skill and reporting success.

Discovery only: bundling makes a skill loadable on request. What costs prompt
tokens is ``default_skill_set`` in gaia-agent.yaml, which stays commented out.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from gaia_agent.agent import (
    _HUB_SKILLS_DIR,
    _SKILLS_DIR,
    GaiaAgent,
    _bundled_skill_roots,
)

#: tests/ -> python/ -> gaia/ -> agents/ -> hub/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[5]


def test_hub_skills_dir_points_at_the_starter_pack():
    """Guard the parents[4] arithmetic — an off-by-one silently yields nothing."""
    assert _HUB_SKILLS_DIR == REPO_ROOT / "hub" / "skills"
    assert _HUB_SKILLS_DIR.is_dir(), "the starter pack lane moved"


def test_starter_pack_is_discoverable_from_a_checkout():
    """The regression: a checkout could not load a skill the tree contains."""
    from gaia.skills.manager import SkillManager

    discovered = set(SkillManager(agent_skill_dirs=GaiaAgent.SKILL_DIRS).discover())

    assert "github-triage" in discovered
    # Every skill in the lane, not just the one that exposed the bug.
    for skill_dir in _HUB_SKILLS_DIR.iterdir():
        if (skill_dir / "SKILL.md").is_file():
            assert skill_dir.name in discovered, f"{skill_dir.name} is not discoverable"


def test_roots_are_ordered_packaged_first():
    """A staged copy must shadow the checkout, not disagree with it silently."""
    roots = _bundled_skill_roots()

    assert roots, "no bundled skill root resolved at all"
    if _SKILLS_DIR.is_dir() and _HUB_SKILLS_DIR.is_dir():
        assert roots.index(str(_SKILLS_DIR)) < roots.index(str(_HUB_SKILLS_DIR))


def test_missing_roots_are_skipped_not_listed(monkeypatch, tmp_path):
    """A frozen sidecar has no hub/ tree; a non-existent root must not be named.

    SkillManager treats a listed root as a place to scan, so handing it a path
    that cannot exist turns a clean start into per-scan noise.
    """
    import gaia_agent.agent as agent_module

    monkeypatch.setattr(agent_module, "_SKILLS_DIR", tmp_path / "nope")
    monkeypatch.setattr(agent_module, "_HUB_SKILLS_DIR", tmp_path / "also-nope")

    assert agent_module._bundled_skill_roots() == []


def test_default_skill_set_stays_opt_in():
    """Discovery must not have quietly become auto-loading.

    The manifest deliberately declines to load skill bodies by default: that is
    the prompt-token trade no eval has measured for this agent. This test fails
    if someone "helpfully" enables it alongside a discovery change.
    """
    manifest = (REPO_ROOT / "hub/agents/gaia/python/gaia-agent.yaml").read_text(
        encoding="utf-8"
    )
    live = [
        ln
        for ln in manifest.splitlines()
        if ln.strip().startswith("default_skill_set:")
    ]

    assert not live, f"default_skill_set is now active: {live}"


@pytest.fixture(scope="module")
def flagship():
    """One real flagship agent — constructing it is expensive, so share it."""
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    return GaiaAgent(config=GaiaAgentConfig(silent_mode=True))


class TestTheFlagshipCanSearchCode:
    """Semantic code search is what makes this agent usable ON a codebase.

    The mixin existed in `gaia.agents.tools.code_index_tools` and was composed
    onto nothing: the flagship had `search_file_content` (grep — finds a string)
    and no way to find the function that does the thing you described.
    """

    def test_the_code_index_tools_are_registered(self, flagship):
        for name in (
            "index_codebase",
            "search_code_index",
            "get_index_status",
            "clear_code_index",
        ):
            assert name in flagship._tools_registry, (
                f"{name} is missing — the flagship cannot index or search code"
            )

    def test_the_index_is_rooted_somewhere_real(self, flagship):
        assert Path(flagship._repo_path).is_dir(), (
            "the code index has no valid repository root, so indexing cannot start"
        )
