# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The offered tool list is stable in order, so a mid-turn load only appends.

A model's prompt cache is a prefix match, and some chat templates render the
tool list *before* the system prompt — so re-ordering the tool list re-prefills
it and everything after it. These tests pin the one property that makes that
cheap: tools are offered in **admission order** (CORE in registry order, then
each later admission appended), never re-sorted, so admitting a tool mid-turn
leaves every byte before it unchanged.

Three layers, one invariant:

* ``ToolLoader`` returns the loaded set in admission order, and stays add-only
  within a turn (``load_bundle`` never evicts; the cap is restored at the next
  ``select``);
* both render paths (text + native schemas) render the order they are given;
* the ChatAgent ``load_tools`` escape hatch appends its bundle to the active
  filter instead of re-sorting it.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from gaia.agents.base.tool_loader import ToolBundle, ToolLoader

# build_doc_agent_skeleton builds a doc-profile ChatAgent, which ships in the
# standalone gaia-agent-chat wheel (#1102); skip when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402

DIM = 768


def _make_embed_fn(tools: list[str], query_scores: dict[str, dict[str, float]]):
    """Deterministic embedder: each tool doc is a one-hot axis (see #1449 tests)."""
    axis = {name: i for i, name in enumerate(tools)}
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
    return {name: {"description": f"does {name}"} for name in tools}


# ── ToolLoader: admission order, never alphabetical ───────────────────────


def test_core_is_offered_in_registry_order_not_alphabetical():
    """CORE leads the list in registry order — the unfiltered prompt's order."""
    tools = ["z_core", "a_core", "m_tool"]
    embed = _make_embed_fn(tools, {"q": {"z_core": 0.0, "a_core": 0.0, "m_tool": 0.0}})
    loader = ToolLoader(
        frozenset({"z_core", "a_core"}), [], embed, threshold=0.55, max_tools=14
    )
    assert loader.select("q", _registry(tools)) == ["z_core", "a_core"]


def test_later_admission_appends_and_leaves_the_prefix_identical():
    """A tool admitted on a later turn lands at the end; the prefix is untouched."""
    tools = ["z_tool", "a_tool", "m_tool"]
    embed = _make_embed_fn(
        tools,
        {
            "q1": {"z_tool": 0.9, "a_tool": 0.8, "m_tool": 0.0},
            "q2": {"z_tool": 0.0, "a_tool": 0.0, "m_tool": 0.9},
        },
    )
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)
    reg = _registry(tools)
    first = loader.select("q1", reg)
    assert first == ["z_tool", "a_tool"]  # by score, not alphabetically
    second = loader.select("q2", reg)
    assert second == ["z_tool", "a_tool", "m_tool"]
    assert second[: len(first)] == first


def test_bundle_pulled_mates_append_after_the_existing_set():
    """A later turn's bundle pull-in lands at the end, never interleaved."""
    tools = ["z1", "a1", "m1"]
    embed = _make_embed_fn(
        tools,
        {
            "q1": {"z1": 0.0, "a1": 0.0, "m1": 0.9},
            "q2": {"z1": 0.9, "a1": 0.0, "m1": 0.0},
        },
    )
    bundles = [ToolBundle(name="A", members=frozenset({"z1", "a1"}))]
    loader = ToolLoader(frozenset(), bundles, embed, threshold=0.55, max_tools=14)
    reg = _registry(tools)
    first = loader.select("q1", reg)
    assert first == ["m1"]
    # z1 matches and pulls its mate a1; both append after the existing m1.
    second = loader.select("q2", reg)
    assert second[: len(first)] == first
    assert set(second[len(first) :]) == {"z1", "a1"}


def test_load_bundle_appends_without_reordering():
    """The mid-turn escape hatch appends; everything already offered keeps its slot."""
    tools = ["z_core", "d1", "b1", "a1"]
    embed = _make_embed_fn(
        tools, {"q": {"z_core": 0.0, "d1": 0.9, "b1": 0.0, "a1": 0.0}}
    )
    bundles = [ToolBundle(name="A", members=frozenset({"b1", "a1"}))]
    loader = ToolLoader(
        frozenset({"z_core"}), bundles, embed, threshold=0.55, max_tools=14
    )
    reg = _registry(tools)
    before = loader.select("q", reg)
    assert before == ["z_core", "d1"]
    after = loader.load_bundle("A", reg)
    assert after == ["z_core", "d1", "b1", "a1"]
    assert after[: len(before)] == before


def test_load_bundle_is_add_only_within_a_turn():
    """At cap mid-turn the list grows rather than evicting — the prefix must hold."""
    tools = ["c1", "d1", "a1", "a2"]
    embed = _make_embed_fn(tools, {"q": {"c1": 0.0, "d1": 0.9, "a1": 0.0, "a2": 0.0}})
    bundles = [ToolBundle(name="A", members=frozenset({"a1", "a2"}), description="A")]
    loader = ToolLoader(frozenset({"c1"}), bundles, embed, threshold=0.55, max_tools=3)
    reg = _registry(tools)
    assert loader.select("q", reg) == ["c1", "d1"]
    loaded = loader.load_bundle("A", reg)
    assert loaded == ["c1", "d1", "a1", "a2"]  # d1 kept: no mid-turn eviction


def test_cap_is_restored_at_the_next_turn_boundary():
    """The overshoot from a mid-turn load is trimmed on the next select, LRU first."""
    tools = ["c1", "d1", "a1", "a2"]
    embed = _make_embed_fn(
        tools,
        {
            "q1": {"c1": 0.0, "d1": 0.9, "a1": 0.0, "a2": 0.0},
            "q2": {"c1": 0.0, "d1": 0.0, "a1": 0.0, "a2": 0.0},
        },
    )
    bundles = [ToolBundle(name="A", members=frozenset({"a1", "a2"}), description="A")]
    loader = ToolLoader(frozenset({"c1"}), bundles, embed, threshold=0.55, max_tools=3)
    reg = _registry(tools)
    loader.select("q1", reg)
    assert len(loader.load_bundle("A", reg)) == 4  # over cap mid-turn
    assert loader.select("q2", reg) == ["c1", "a1", "a2"]  # d1 (LRU) trimmed


def test_eviction_keeps_the_survivors_relative_order():
    """Trimming removes a tool; it never re-orders what is left."""
    tools = ["d1", "d2", "d3", "d4"]
    embed = _make_embed_fn(
        tools,
        {
            "q1": {"d1": 0.9, "d2": 0.8, "d3": 0.7, "d4": 0.0},
            "q2": {"d1": 0.0, "d2": 0.0, "d3": 0.0, "d4": 0.9},
        },
    )
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=3)
    reg = _registry(tools)
    assert loader.select("q1", reg) == ["d1", "d2", "d3"]
    loader._loaded["d1"].last_call_ts = 1000.0  # oldest call → the victim
    loader._loaded["d2"].last_call_ts = 5000.0
    loader._loaded["d3"].last_call_ts = 6000.0
    assert loader.select("q2", reg) == ["d2", "d3", "d4"]


# ── render paths honour the order they are given ──────────────────────────


def _agent():
    return build_doc_agent_skeleton(profile="doc", deterministic=True)


def test_render_paths_preserve_admission_order_and_prefix():
    """Appending a tool leaves the serialised schemas before it byte-identical."""
    agent = _agent()
    admitted = ["query_documents", "read_file"]
    before = agent._build_openai_tool_schemas(filter_to=admitted)
    assert [s["function"]["name"] for s in before] == admitted

    after = agent._build_openai_tool_schemas(filter_to=admitted + ["remember"])
    assert [s["function"]["name"] for s in after] == admitted + ["remember"]
    assert json.dumps(after[: len(before)]) == json.dumps(before)

    text_before = agent._format_tools_for_prompt(filter_to=admitted)
    text_after = agent._format_tools_for_prompt(filter_to=admitted + ["remember"])
    assert text_after.startswith(text_before)


def test_active_filter_is_compared_by_order_not_as_a_set():
    """Same set, same order recomputes nothing; a re-order is a real change."""
    agent = _agent()
    composed: list[int] = []
    agent._compose_system_prompt = lambda: composed.append(1) or "PROMPT"

    agent._select_tools_for_turn = lambda _q: ["read_file", "remember"]
    agent._refresh_active_tool_filter("q")
    agent._refresh_active_tool_filter("q")
    assert len(composed) == 1, "an unchanged selection must not recompute"

    agent._select_tools_for_turn = lambda _q: ["remember", "read_file"]
    agent._refresh_active_tool_filter("q")
    assert len(composed) == 2
    schemas = list(agent._openai_tools or [])
    assert [s["function"]["name"] for s in schemas] == ["remember", "read_file"]


# ── ChatAgent load_tools escape hatch ─────────────────────────────────────


def test_load_tools_handler_appends_to_the_active_filter():
    """``load_tools`` mid-turn appends the bundle; the offered prefix is unchanged."""
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    applied: dict = {}
    agent._apply_tool_filter = lambda f: applied.__setitem__("filter", f)
    before = agent.tool_loader.select("find the file", agent._tools_registry)

    result = agent._tools_registry["load_tools"]["function"]("file_search")
    assert result["status"] == "success"
    loaded = result["loaded_tools"]
    assert loaded[: len(before)] == before
    assert "search_file" in loaded[len(before) :]
    assert applied["filter"] == loaded
