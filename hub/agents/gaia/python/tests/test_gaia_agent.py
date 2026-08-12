# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract tests for the flagship ``gaia`` agent.

The load-bearing one is :func:`test_registers_every_capability_the_starter_skills_need`.
``tools_required`` in a ``SKILL.md`` is advisory — the loader logs at INFO when a
declared tool is missing and loads the skill anyway — so a capability gap here
does not fail at load, it fails mid-run as a broken answer. These assertions are
the only thing standing between a config regression and that failure mode.

Measured, not assumed: ``prompt_profile="doc"`` with ``enable_scratchpad`` and
``enable_browser`` both True registers 38 tools and ZERO of either, because
``ChatAgent._register_tools`` keys off ``ProfileSpec.tool_groups`` and never reads
those flags. That is why the profile is ``"full"``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from gaia_agent_gaia import build_gaia
from gaia_agent_gaia.agent import GaiaAgent, GaiaAgentConfig

MANIFEST = Path(__file__).resolve().parent.parent / "gaia-agent.yaml"


@pytest.fixture(scope="module")
def registered_tools():
    """Tool names registered by the default construction."""
    agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
    agent._register_tools()
    return sorted(agent._tools_registry.keys())


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Capability breadth — the reason this agent exists
# --------------------------------------------------------------------------

#: Capability -> tool-name substrings, one row per starter-pack consumer.
CAPABILITIES = {
    "rag (document-brief)": ("query_documents", "index_document"),
    "scratchpad (data-explore)": ("create_table", "list_tables"),
    "browser (research-report)": ("fetch_page", "fetch_webpage"),
    "file io (research-report)": ("read_file", "write_file"),
    "memory (check-in)": ("remember", "recall"),
}


@pytest.mark.parametrize("capability,needles", list(CAPABILITIES.items()))
def test_registers_every_capability_the_starter_skills_need(
    capability, needles, registered_tools
):
    missing = [n for n in needles if not any(n in t for t in registered_tools)]
    assert not missing, (
        f"{capability}: no registered tool matches {missing}. A skill declaring "
        f"these in tools_required would still LOAD (tools_required is advisory) "
        f"and then fail mid-run when the model calls one. Check "
        f"GaiaAgentConfig.prompt_profile is still 'full' — 'doc' silently drops "
        f"scratchpad and browser."
    )


def test_profile_is_full_not_doc():
    """Pin the profile: 'doc' looks right and silently costs 17 tools."""
    assert GaiaAgentConfig().prompt_profile == "full"


# --------------------------------------------------------------------------
# Manifest honesty
# --------------------------------------------------------------------------


def test_manifest_tools_count_matches_real_registry(manifest, registered_tools):
    assert manifest["tools_count"] == len(registered_tools), (
        f"gaia-agent.yaml tools_count={manifest['tools_count']} but the real "
        f"registry has {len(registered_tools)}. Hand-typed drift — the hub page "
        f"would over- or under-claim what this agent can do."
    )


def test_registration_tools_count_matches_manifest(manifest):
    assert build_gaia().tools_count == manifest["tools_count"]


def test_registration_identity_matches_manifest(manifest):
    reg = build_gaia()
    assert reg.id == manifest["id"] == "gaia"
    assert reg.category == manifest["category"]
    assert reg.icon == manifest["icon"]


def test_manifest_declares_a_security_tier(manifest):
    """An omitted tier defaults to `experimental`, which makes install refuse
    without an explicit --trust opt-in."""
    assert manifest.get("security_tier") in {"verified", "community", "experimental"}


# --------------------------------------------------------------------------
# Skills wiring
# --------------------------------------------------------------------------


def test_skill_dirs_point_at_the_bundled_library():
    assert GaiaAgent.SKILL_DIRS, "no bundled skill root — SKILL_DIRS is empty"
    assert Path(GaiaAgent.SKILL_DIRS[0]).name == "skills"


def test_skill_manifest_resolves():
    assert GaiaAgent.SKILL_MANIFEST is not None
    assert Path(GaiaAgent.SKILL_MANIFEST).is_file()


def test_no_skill_set_loads_by_default(monkeypatch):
    """#2848 precedent: skill bodies cost prompt tokens and shrink the result
    envelope, so nothing loads until an eval measures the trade."""
    monkeypatch.delenv("GAIA_SKILL_SET", raising=False)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig()
    assert agent.select_skill_set() is None


def test_env_selects_a_skill_set(monkeypatch):
    monkeypatch.setenv("GAIA_SKILL_SET", "research")
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig()
    assert agent.select_skill_set() == "research"


def test_explicit_config_beats_env(monkeypatch):
    monkeypatch.setenv("GAIA_SKILL_SET", "research")
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(skill_set="documents")
    assert agent.select_skill_set() == "documents"


def test_manifest_ships_default_skill_set_disabled(manifest):
    """The manifest may declare sets, but none may be active out of the box."""
    assert not manifest.get("default_skill_set"), (
        "default_skill_set is live — skills would load for every user before an "
        "eval has measured their prompt-token cost (see #2848)."
    )
