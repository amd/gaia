# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Drift gate for the ChatAgent doc-profile tool bundles (#1449).

``DOC_CORE_TOOLS`` ∪ all ``DOC_BUNDLES`` members must equal the live doc-profile
tool registry **exactly**, in both directions:

* a registry tool not covered by CORE or a bundle would silently ship
  unselected — the loader would never surface it;
* a CORE/bundle name not in the registry is dead config (or a typo) that
  ``ToolLoader.validate_registry`` will reject at runtime.

This test makes either drift a CI failure, so adding a doc-profile tool forces a
conscious bundling decision in the same change.
"""

from __future__ import annotations

import pytest

# DOC_BUNDLES ships with the standalone gaia-agent-chat wheel (#1102); skip the
# whole module when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.tool_bundles import DOC_BUNDLES, DOC_CORE_TOOLS  # noqa: E402

from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402


def _bundle_union() -> set[str]:
    names: set[str] = set(DOC_CORE_TOOLS)
    for bundle in DOC_BUNDLES:
        names |= set(bundle.members)
    return names


def test_core_and_bundles_cover_doc_registry_exactly():
    # Loader-on skeleton so the CORE-only load_tools meta-tool (#1450) is
    # registered — the doc registry must balance against CORE∪bundles with it.
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    registry = set(agent._tools_registry)
    covered = _bundle_union()

    uncovered = sorted(registry - covered)
    dangling = sorted(covered - registry)

    assert not uncovered, (
        f"doc-profile tools not covered by CORE or any bundle: {uncovered}. "
        "Add each to a bundle (or CORE) in "
        "hub/agents/chat/python/gaia_agent_chat/tool_bundles.py "
        "— an uncovered tool would never be surfaced by the loader."
    )
    assert not dangling, (
        f"CORE/bundle names absent from the doc registry: {dangling}. "
        "Remove them or fix the name — validate_registry rejects these at runtime."
    )
    # The escape hatch is present in both CORE and the live registry (#1450).
    assert "load_tools" in DOC_CORE_TOOLS
    assert "load_tools" in registry


def test_core_is_subset_of_bundle_union():
    """Every CORE tool is in a bundle too, except the CORE-only load_tools (#1450)."""
    bundle_members: set[str] = set()
    for bundle in DOC_BUNDLES:
        bundle_members |= set(bundle.members)
    assert DOC_CORE_TOOLS - bundle_members == {"load_tools"}


def test_bundles_have_unique_names():
    names = [b.name for b in DOC_BUNDLES]
    assert len(names) == len(set(names)), f"duplicate bundle names: {names}"
