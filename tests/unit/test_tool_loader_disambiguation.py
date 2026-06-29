# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression: doc-profile data-tool vs. memory-recall disambiguation (#800).

#800 framed the collision as ``scratchpad.query_data`` vs ``memory.recall``.
Scratchpad tools never enter the ChatAgent ``doc`` profile (they are gated to the
``data``/``full`` profiles), and the loader is wired only to ``doc`` — so the
literal pair is structurally impossible in the loaded profile. The doc-profile
analog the loader actually arbitrates is ``analyze_data_file`` (the structured-
data tool, a *conditional* ``data``-bundle member) vs ``recall`` (always-on
CORE).

These tests pin that resolution against the **real** ``DOC_CORE_TOOLS`` /
``DOC_BUNDLES`` config and the production threshold/cap, using the deterministic
one-hot embedder pattern from ``test_tool_loader_selection.py`` (each tool doc
embeds to a distinct axis; a query embeds to a coordinate vector, so
``dot(query, tool)`` recovers exactly the assigned score). No Lemonade backend,
no network — a fresh loader per scenario is the cold/empty-memory new-user state.
"""

from __future__ import annotations

import numpy as np
import pytest

from gaia.agents.base.tool_loader import (
    DEFAULT_MAX_TOOLS,
    DEFAULT_THRESHOLD,
    ToolLoader,
)

# DOC_BUNDLES ships with the standalone gaia-agent-chat wheel (#1102); skip the
# whole module when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.tool_bundles import DOC_BUNDLES, DOC_CORE_TOOLS  # noqa: E402

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
    loader = ToolLoader(
        DOC_CORE_TOOLS,
        DOC_BUNDLES,
        embed,
        threshold=DEFAULT_THRESHOLD,
        max_tools=DEFAULT_MAX_TOOLS,
    )
    return loader, _registry(tools)


# ── config pin: the decision that resolves the collision ───────────────────


def test_data_tool_is_conditional_and_recall_is_core():
    """``recall`` is always-on CORE; ``analyze_data_file`` is a conditional bundle.

    This is the structural fact #800 turns on: the two only co-occur when the
    turn semantically justifies the data tool — recall is never the gated side.
    """
    assert "recall" in DOC_CORE_TOOLS
    assert "analyze_data_file" not in DOC_CORE_TOOLS
    assert "analyze_data_file" in _data_bundle_members()


# ── the data tool loads only when justified ────────────────────────────────


def test_structured_data_query_loads_data_tool_with_recall_present():
    """A data-style turn loads ``analyze_data_file`` *and* keeps ``recall`` (CORE)."""
    loader, reg = _doc_loader({"aggregate the sales csv": {"analyze_data_file": 0.9}})
    loaded = loader.select("aggregate the sales csv", reg)
    assert loaded is not None
    assert "analyze_data_file" in loaded  # justified → loaded
    assert "recall" in loaded  # CORE → always present


def test_recall_query_keeps_recall_and_omits_data_tool():
    """A turn that justifies no data tool keeps ``recall`` and omits the data tool.

    Empty query scores → zero vector → nothing clears τ, so only CORE is admitted.
    This realizes #800 AC #2 ("not both unless justified") on the conditional side.
    """
    loader, reg = _doc_loader({"what did we cover earlier": {}})
    loaded = loader.select("what did we cover earlier", reg)
    assert loaded is not None
    assert "recall" in loaded  # CORE → present
    assert "analyze_data_file" not in loaded  # unjustified → absent


# ── mid-conversation pivot (AC #7, in-profile) ─────────────────────────────


def test_pivot_loads_data_bundle_mid_conversation():
    """One session: a recall turn omits the data tool, a later data turn adds it.

    Monotonic growth — the data tool joins on the turn that justifies it, and
    ``recall`` (CORE) is present throughout. AC #7's in-profile re-evaluation.
    """
    loader, reg = _doc_loader(
        {
            "what did we cover earlier": {},  # turn 1: no data justification
            "now total the Q1 revenue in the csv": {"analyze_data_file": 0.9},
        }
    )

    turn1 = loader.select("what did we cover earlier", reg)
    assert turn1 is not None
    assert "analyze_data_file" not in turn1
    assert "recall" in turn1

    turn2 = loader.select("now total the Q1 revenue in the csv", reg)
    assert turn2 is not None
    assert "analyze_data_file" in turn2  # added on the justifying turn
    assert "recall" in turn2
    assert set(turn1) <= set(turn2)  # monotonic: nothing pruned on pivot
