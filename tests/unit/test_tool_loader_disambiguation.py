# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression: doc-profile data-tool vs. memory-recall disambiguation (#800).

#800 framed the collision as ``scratchpad.query_data`` vs ``memory.recall``.
Scratchpad tools never enter the ChatAgent ``doc`` profile (they are gated to the
``data``/``full`` profiles), so the literal pair is structurally impossible in
that profile. The doc-profile analog the loader actually arbitrates is
``analyze_data_file`` (the structured-data tool, outside CORE) vs ``recall``
(always-on CORE).

These tests pin that resolution against the **real** ``DOC_CORE_TOOLS`` /
``DOC_BUNDLES`` config and the production threshold, using the deterministic
one-hot embedder pattern from ``test_tool_loader_selection.py`` (each tool doc
embeds to a distinct axis; a query embeds to a coordinate vector, so
``dot(query, tool)`` recovers exactly the assigned score). No Lemonade backend,
no network — a fresh loader per scenario is the cold/empty-memory new-user state.
"""

from __future__ import annotations

import numpy as np
import pytest

from gaia.agents.base.tool_loader import DEFAULT_THRESHOLD, ToolLoader

# DOC_BUNDLES ships in the flagship gaia-agent-gaia wheel (#6099); skip the
# whole module when a framework-only env lacks it.
pytest.importorskip("gaia_agent")

from gaia_agent.tool_bundles import DOC_BUNDLES, DOC_CORE_TOOLS  # noqa: E402

DIM = 768


def _all_doc_tools() -> list[str]:
    """Every doc-profile tool name: CORE ∪ all bundle members (sorted)."""
    names: set[str] = set(DOC_CORE_TOOLS)
    for bundle in DOC_BUNDLES:
        names |= set(bundle.members)
    return sorted(names)


def _data_bundle_members() -> frozenset[str]:
    """The ``data`` bundle's members (the conditional structured-data group)."""
    for bundle in DOC_BUNDLES:
        if bundle.name == "data":
            return bundle.members
    raise AssertionError("no 'data' bundle in DOC_BUNDLES")


def _make_embed_fn(tools: list[str], query_scores: dict[str, dict[str, float]]):
    """Deterministic embedder over *tools* (one-hot docs, coordinate queries).

    Args:
        tools: tool names; each gets a distinct one-hot embedding axis.
        query_scores: ``{query_text: {tool_name: score}}``. A query embeds to the
            coordinate vector with those scores; ``dot`` with a tool's one-hot
            axis recovers the score exactly. A query text with an empty score map
            embeds to the zero vector (nothing matches) — the "recall turn that
            justifies no data tool" case.
    """
    axis = {name: i for i, name in enumerate(tools)}
    assert len(tools) <= DIM
    docs = {f"{name}: does {name}": name for name in tools}

    def embed(text: str) -> np.ndarray:
        v = np.zeros(DIM, dtype=np.float32)
        if text in docs:
            v[axis[docs[text]]] = 1.0
            return v
        if text in query_scores:
            for tool, score in query_scores[text].items():
                v[axis[tool]] = score
            return v
        raise AssertionError(f"unexpected text embedded: {text!r}")

    return embed


def _registry(tools: list[str]) -> dict[str, dict]:
    """A doc registry whose descriptions yield the embedder's one-hot doc keys."""
    return {name: {"description": f"does {name}"} for name in tools}


def _doc_loader(query_scores: dict[str, dict[str, float]]) -> tuple[ToolLoader, dict]:
    """A fresh loader over the real doc CORE/bundles + a deterministic embedder."""
    tools = _all_doc_tools()
    embed = _make_embed_fn(tools, query_scores)
    loader = ToolLoader(DOC_CORE_TOOLS, DOC_BUNDLES, embed, threshold=DEFAULT_THRESHOLD)
    return loader, _registry(tools)


def _core_in_registry_order() -> list[str]:
    return [name for name in _all_doc_tools() if name in DOC_CORE_TOOLS]


# ── config pin: the decision that resolves the collision ───────────────────


def test_data_tool_is_conditional_and_recall_is_core():
    """``recall`` is always-on CORE; ``analyze_data_file`` sits outside it.

    This is the structural fact #800 turns on: the two only co-occur once the
    session has moved to the full list — recall is never the gated side.
    """
    assert "recall" in DOC_CORE_TOOLS
    assert "analyze_data_file" not in DOC_CORE_TOOLS
    assert "analyze_data_file" in _data_bundle_members()


# ── the data tool arrives only when justified ──────────────────────────────


def test_structured_data_query_moves_to_the_full_list_with_recall_present():
    loader, reg = _doc_loader({"aggregate the sales csv": {"analyze_data_file": 0.9}})
    assert loader.select("aggregate the sales csv", reg) == _all_doc_tools()


def test_recall_query_stays_on_core():
    """Empty query scores → zero vector → nothing clears τ, so the list stays core.

    This realizes #800 AC #2 ("not both unless justified") on the conditional side.
    """
    loader, reg = _doc_loader({"what did we cover earlier": {}})
    assert loader.select("what did we cover earlier", reg) == _core_in_registry_order()


def test_recall_winning_the_query_keeps_the_data_tool_out():
    """Both clear τ on a question about the past; a listed tool that wins stays."""
    query = "what did the csv say last time"
    loader, reg = _doc_loader({query: {"analyze_data_file": 0.7, "recall": 0.8}})
    assert loader.select(query, reg) == _core_in_registry_order()


# ── mid-conversation pivot (AC #7, in-profile) ─────────────────────────────


def test_pivot_moves_to_the_full_list_mid_conversation():
    """One session: a recall turn stays on core, a later data turn moves to full.

    Nothing is pruned on the way — core tools are on both lists. AC #7's
    in-profile re-evaluation.
    """
    loader, reg = _doc_loader(
        {
            "what did we cover earlier": {},  # turn 1: no data justification
            "now total the Q1 revenue in the csv": {"analyze_data_file": 0.9},
        }
    )

    turn1 = loader.select("what did we cover earlier", reg)
    turn2 = loader.select("now total the Q1 revenue in the csv", reg)

    assert turn1 == _core_in_registry_order()
    assert turn2 == _all_doc_tools()
    assert set(turn1) <= set(turn2)
