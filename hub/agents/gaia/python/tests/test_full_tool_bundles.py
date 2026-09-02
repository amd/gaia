# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Drift gate for the flagship's ``full``-profile tool bundles.

``FULL_CORE_TOOLS`` union all ``FULL_BUNDLES`` members must equal the live
flagship registry **exactly**, in both directions:

* a registry tool covered by neither would still be semantically selectable but
  could never be pulled in with its cohort, nor recovered through
  ``load_tools`` — the escape hatch resolves bundle *and* tool names through the
  bundle reverse index, so an unbundled tool has no way back;
* a configured name absent from the registry is dead config (or a typo) that
  ``ToolLoader.validate_registry`` rejects at runtime, unless it is declared in
  ``FULL_OPTIONAL_TOOLS``.

This lives beside the flagship rather than in ``tests/unit/`` because the
``full`` registry is GaiaAgent's, not ChatAgent's: 11 of its tools come from the
skill-library and code-index mixins this package composes. The sibling guard for
the ``doc`` profile is ``tests/unit/test_chat_tool_bundles.py``.
"""

from __future__ import annotations

import contextlib
import json

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
from gaia_agent_chat.tool_bundles import (
    FULL_BUNDLES,
    FULL_CORE_TOOLS,
    FULL_OPTIONAL_TOOLS,
    PROFILE_TOOL_CONFIGS,
)

from gaia.agents.base.tools import _TOOL_REGISTRY

#: Ceiling on bundle size. A pull-in must never be able to exhaust the dynamic
#: slots on its own (GaiaAgentConfig.dynamic_tools_max=26 minus 10 CORE leaves 16).
MAX_BUNDLE_MEMBERS = 6


@contextlib.contextmanager
def _isolated_registry():
    """@tool writes into a process-global dict shared with every other agent."""
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        yield
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture(scope="module")
def flagship_registry():
    """The flagship's tool names with memory on and the loader active.

    Memory is forced off during construction (no embedder in CI) and the store
    is then faked so ``register_memory_tools`` runs — the same pinning
    ``test_gaia_agent.py`` uses, for the same reason: an environment-dependent
    count made the drift guard pass locally and fail in CI.
    """
    with _isolated_registry(), pytest.MonkeyPatch.context() as mp:
        mp.setenv("GAIA_MEMORY_DISABLED", "1")
        agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
        assert agent.tool_loader is not None, (
            "dynamic tool loading is off for the flagship — GaiaAgentConfig."
            "dynamic_tools must default True and 'full' must be in "
            "PROFILE_TOOL_CONFIGS, or load_tools never registers."
        )
        agent._memory_store = object()
        agent._register_tools()
        return set(agent._tools_registry)


def _bundle_members() -> set[str]:
    members: set[str] = set()
    for bundle in FULL_BUNDLES:
        members |= set(bundle.members)
    return members


def test_core_and_bundles_cover_the_flagship_registry_exactly(flagship_registry):
    covered = set(FULL_CORE_TOOLS) | _bundle_members()

    uncovered = sorted(flagship_registry - covered)
    dangling = sorted(covered - flagship_registry)

    assert not uncovered, (
        f"flagship tools covered by neither FULL_CORE_TOOLS nor a bundle: "
        f"{uncovered}. Add each to a bundle (or CORE) in "
        "hub/agents/chat/python/gaia_agent_chat/tool_bundles.py — an uncovered "
        "tool can never be pulled in with its cohort or recovered via load_tools."
    )
    assert not dangling, (
        f"FULL_CORE_TOOLS/FULL_BUNDLES names absent from the flagship registry: "
        f"{dangling}. Remove them, fix the name, or — if the tool is absent by "
        "construction on some installs — declare it in FULL_OPTIONAL_TOOLS."
    )


def test_escape_hatch_is_registered_and_core(flagship_registry):
    """Without load_tools in the registry a semantic miss is unrecoverable."""
    assert "load_tools" in FULL_CORE_TOOLS
    assert "load_tools" in flagship_registry


def test_bundle_menu_renders_for_the_flagship():
    """An undiscoverable escape hatch is the same as no escape hatch."""
    with _isolated_registry(), pytest.MonkeyPatch.context() as mp:
        mp.setenv("GAIA_MEMORY_DISABLED", "1")
        agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
        prompt = agent.system_prompt
    assert "==== LOADABLE TOOL BUNDLES ====" in prompt
    for bundle in FULL_BUNDLES:
        assert f"- {bundle.name}:" in prompt


def test_optional_tools_are_present_on_a_full_install(flagship_registry):
    """FULL_OPTIONAL_TOOLS is a runtime tolerance, never a coverage exemption.

    Every name in it must still exist on the flagship, so a rename fails here
    instead of quietly disabling the validation that would have caught it.
    """
    missing = sorted(FULL_OPTIONAL_TOOLS - flagship_registry)
    assert not missing, (
        f"declared optional but absent even on a full install: {missing}. "
        "Either the tool was renamed/removed, or it never belonged in "
        "FULL_OPTIONAL_TOOLS."
    )


def test_core_is_subset_of_bundle_union():
    """Every CORE tool is in a bundle too, except the CORE-only load_tools."""
    assert set(FULL_CORE_TOOLS) - _bundle_members() == {"load_tools"}


def test_bundles_have_unique_names():
    names = [b.name for b in FULL_BUNDLES]
    assert len(names) == len(set(names)), f"duplicate bundle names: {names}"


def test_bundles_stay_small():
    oversized = {
        b.name: len(b.members)
        for b in FULL_BUNDLES
        if len(b.members) > MAX_BUNDLE_MEMBERS
    }
    assert not oversized, (
        f"bundles over {MAX_BUNDLE_MEMBERS} members: {oversized}. One pull-in "
        "must not be able to exhaust the dynamic slots — split the group."
    )


def test_bundles_carry_a_description():
    """The description IS the menu line the model picks a bundle from."""
    missing = [b.name for b in FULL_BUNDLES if not b.description.strip()]
    assert not missing, f"bundles with no menu description: {missing}"


def test_full_profile_is_wired_to_the_full_config():
    cfg = PROFILE_TOOL_CONFIGS["full"]
    assert cfg.core is FULL_CORE_TOOLS
    assert cfg.bundles is FULL_BUNDLES
    assert cfg.optional is FULL_OPTIONAL_TOOLS


def test_trimming_actually_saves_most_of_the_tool_payload():
    """The point of the whole change, pinned so a regression is visible.

    Measured on the real renderers with tiktoken (cl100k) as a tokenizer-agnostic
    proxy — Gemma's own counts differ, but the ratio is what this guards. Uses
    the offline skeleton: constructing a live GaiaAgent preloads a model.
    """
    tiktoken = pytest.importorskip("tiktoken")
    from gaia.eval.tool_cost import build_full_agent_skeleton

    enc = tiktoken.get_encoding("cl100k_base")
    agent = build_full_agent_skeleton()
    registry = agent._tools_registry

    def cost(names):
        schemas = json.dumps(agent._build_openai_tool_schemas(filter_to=names))
        return len(enc.encode(schemas)) + len(
            enc.encode(agent._format_tools_for_prompt(filter_to=names))
        )

    everything = cost(None)
    core_only = cost(sorted(n for n in FULL_CORE_TOOLS if n in registry))
    # A saturated session: CORE plus enough dynamic slots to reach the cap.
    cap = GaiaAgentConfig().dynamic_tools_max
    saturated_names = sorted(registry)[:cap]
    saturated = cost(saturated_names)

    assert core_only < everything * 0.30, (
        f"CORE-only tool prompt is {core_only} tokens against {everything} for "
        "the whole registry — the always-on set has grown too expensive."
    )
    assert saturated < everything * 0.55, (
        f"a capped-out session costs {saturated} tokens against {everything} "
        f"unfiltered (cap={cap}). Either the cap or CORE has drifted up."
    )


def test_cap_leaves_room_for_two_whole_bundles():
    """CORE plus the two largest bundles must fit under the cap.

    Otherwise the flagship LRU-evicts mid-turn on a perfectly ordinary
    two-capability question instead of loading both cohorts.
    """
    cap = GaiaAgentConfig().dynamic_tools_max
    two_largest = sorted((len(b.members) for b in FULL_BUNDLES), reverse=True)[:2]
    need = len(FULL_CORE_TOOLS) + sum(two_largest)
    assert cap >= need, (
        f"dynamic_tools_max={cap} but CORE ({len(FULL_CORE_TOOLS)}) plus the two "
        f"largest bundles ({two_largest}) needs {need}."
    )
