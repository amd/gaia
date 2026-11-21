# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The offered tool list is stable in order, so a skill's tools only append.

A model's prompt cache is a prefix match, and some chat templates render the
tool list *before* the system prompt — so re-ordering the tool list re-prefills
it and everything after it. These tests pin the one property that makes that
cheap: CORE is offered in registry order, a skill's tools append after it, and
only the one jump to the full list may change what came before.

Two layers, one invariant:

* ``ToolLoader`` returns a list that only ever grows at the end until a jump;
* both render paths (text + native schemas) render the order they are given.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from gaia.agents.base.tool_loader import ToolLoader

# build_doc_agent_skeleton builds a doc-profile ChatAgent, which ships in the
# flagship gaia-agent-gaia wheel (#6099); skip when a framework-only env lacks it.
pytest.importorskip("gaia_agent")

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


# ── ToolLoader: stable order, never alphabetical ──────────────────────────


def test_core_is_offered_in_registry_order_not_alphabetical():
    """CORE leads the list in registry order — the unfiltered prompt's order."""
    tools = ["z_core", "a_core", "m_tool"]
    embed = _make_embed_fn(tools, {"q": {"z_core": 0.0, "a_core": 0.0, "m_tool": 0.0}})
    loader = ToolLoader(frozenset({"z_core", "a_core"}), [], embed, threshold=0.55)
    assert loader.select("q", _registry(tools)) == ["z_core", "a_core"]


def test_a_new_session_sends_the_same_list_whatever_order_core_is_configured_in():
    """Two agents with the same tools send the same bytes; warm-up relies on it."""
    tools = ["z_core", "a_core", "m_core"]
    embed = _make_embed_fn(tools, {})
    forward = ToolLoader(["a_core", "m_core", "z_core"], [], embed)
    backward = ToolLoader(["z_core", "m_core", "a_core"], [], embed)
    assert forward.offered(_registry(tools)) == backward.offered(_registry(tools))


def test_skill_tools_append_and_leave_the_prefix_identical():
    tools = ["m_tool", "c1", "z_tool", "a_tool"]
    embed = _make_embed_fn(tools, {})
    loader = ToolLoader(frozenset({"c1"}), [], embed, threshold=0.55)
    reg = _registry(tools)
    first = loader.admit_tools(["z_tool"], reg)
    assert first == ["c1", "z_tool"]
    second = loader.admit_tools(["a_tool", "m_tool"], reg)
    assert second == ["c1", "z_tool", "a_tool", "m_tool"]
    assert second[: len(first)] == first


def test_removing_a_skill_tool_keeps_the_survivors_relative_order():
    tools = ["c1", "d1", "d2", "d3"]
    loader = ToolLoader(frozenset({"c1"}), [], _make_embed_fn(tools, {}))
    reg = _registry(tools)
    loader.admit_tools(["d3", "d1", "d2"], reg)
    assert loader.remove_tools(["d1"], reg) == ["c1", "d3", "d2"]


def test_the_jump_lands_on_registry_order_not_on_the_old_list_plus_more():
    """The full list is one fixed list: the one a warm-up primed on its slot."""
    tools = ["d1", "c1", "d2"]
    embed = _make_embed_fn(tools, {"q": {"d1": 0.9, "c1": 0.0, "d2": 0.0}})
    loader = ToolLoader(frozenset({"c1"}), [], embed, threshold=0.55)
    reg = _registry(tools)
    assert loader.admit_tools(["d2"], reg) == ["c1", "d2"]
    assert loader.select("q", reg) == list(reg)


def test_repeat_turns_return_a_byte_identical_list():
    tools = ["c1", "d1", "d2"]
    embed = _make_embed_fn(tools, {"q": {"c1": 0.0, "d1": 0.0, "d2": 0.0}})
    loader = ToolLoader(frozenset({"c1"}), [], embed, threshold=0.55)
    reg = _registry(tools)
    turns = [loader.select("q", reg, skill_tools=["d2"]) for _ in range(5)]
    assert turns == [["c1", "d2"]] * 5


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
