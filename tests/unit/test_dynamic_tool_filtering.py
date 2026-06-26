# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Base-agent dual-path tool filtering (#1449).

Pins the load-bearing invariants of the base ``Agent`` hooks that the dynamic
tool loader rides on, all without a Lemonade backend (the doc skeleton from
``tool_cost`` bypasses ``Agent.__init__``):

* ``filter_to=None`` renders byte-identically to the legacy path on BOTH render
  paths (text + native) — the off-state safety net;
* a subset renders exactly those tools, in the given order, on both paths;
* compose order is legacy (tools before format template) when no filter is
  active, and tools-last when a filter is active (the KV-cache rule);
* the cached system prompt is recomputed ONLY when the selection changes.
"""

from __future__ import annotations

import json

import pytest

# build_doc_agent_skeleton builds a doc-profile ChatAgent, which ships in the
# standalone gaia-agent-chat wheel (#1102); skip when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402


def _agent():
    return build_doc_agent_skeleton(profile="doc", deterministic=True)


# ── filter_to=None is byte-identical to the legacy render ─────────────────


def test_text_path_none_is_byte_identical():
    agent = _agent()
    legacy = agent._format_tools_for_prompt()
    explicit_none = agent._format_tools_for_prompt(filter_to=None)
    assert explicit_none == legacy


def test_native_path_none_is_byte_identical():
    agent = _agent()
    legacy = json.dumps(agent._build_openai_tool_schemas())
    explicit_none = json.dumps(agent._build_openai_tool_schemas(filter_to=None))
    assert explicit_none == legacy


# ── subset renders exactly and in order on both paths ─────────────────────


def test_text_path_renders_subset_in_order():
    agent = _agent()
    subset = ["read_file", "query_documents", "remember"]
    text = agent._format_tools_for_prompt(filter_to=subset)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == len(subset)
    # Each rendered line starts with "- <name>(" in the requested order.
    for name, line in zip(subset, lines):
        assert line.startswith(f"- {name}("), (name, line)


def test_native_path_renders_subset_in_order():
    agent = _agent()
    subset = ["read_file", "query_documents", "remember"]
    schemas = agent._build_openai_tool_schemas(filter_to=subset)
    assert [s["function"]["name"] for s in schemas] == subset


def test_filter_skips_names_absent_from_registry():
    agent = _agent()
    subset = ["read_file", "does_not_exist", "remember"]
    schemas = agent._build_openai_tool_schemas(filter_to=subset)
    assert [s["function"]["name"] for s in schemas] == ["read_file", "remember"]
    text = agent._format_tools_for_prompt(filter_to=subset)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 2


# ── compose ordering ──────────────────────────────────────────────────────


# The response-format template is only composed for non-tool-calling models
# (``is_tool_calling_model(model_id)`` is False), which in practice means
# ``model_id is None``. Set a sentinel template so the ordering is observable.
_FMT_SENTINEL = "==== RESPONSE FORMAT (sentinel) ===="


def test_compose_order_is_legacy_when_no_filter():
    """With no filter, the AVAILABLE TOOLS block precedes the response format."""
    agent = _agent()
    agent.model_id = None  # non-tool-calling → format template emitted
    agent._response_format_template = _FMT_SENTINEL
    agent._active_tool_filter = None
    prompt = agent._compose_system_prompt()
    tools_idx = prompt.index("==== AVAILABLE TOOLS ====")
    fmt_idx = prompt.index(_FMT_SENTINEL)
    assert tools_idx < fmt_idx, "legacy order: tools block must precede format"


def test_compose_order_is_tools_last_when_filter_active():
    """With a filter active, the tools block comes AFTER the format template."""
    agent = _agent()
    agent.model_id = None
    agent._response_format_template = _FMT_SENTINEL
    agent._active_tool_filter = ["read_file", "query_documents"]
    prompt = agent._compose_system_prompt()
    tools_idx = prompt.index("==== AVAILABLE TOOLS ====")
    fmt_idx = prompt.index(_FMT_SENTINEL)
    assert tools_idx > fmt_idx, "KV-cache rule: tools block must come last"


# ── recompute only on change ──────────────────────────────────────────────


class _SpyAgent:
    """Minimal stand-in exercising the real ``_refresh_active_tool_filter``."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self._idx = 0
        self._active_tool_filter = None
        self._system_prompt_cache = "INITIAL"
        self.compose_calls = 0

    def _select_tools_for_turn(self, user_input):
        val = self._scripted[self._idx]
        self._idx += 1
        return val

    def _compose_system_prompt(self):
        self.compose_calls += 1
        return f"PROMPT::{self._active_tool_filter}"

    # Bind the real methods under test. ``_refresh_active_tool_filter`` now
    # delegates the filter+prompt swap to ``_apply_tool_filter`` (#1450), so the
    # spy must borrow both to exercise the real recompute-on-change path.
    from gaia.agents.base.agent import Agent

    _refresh_active_tool_filter = Agent._refresh_active_tool_filter
    _apply_tool_filter = Agent._apply_tool_filter


def test_recompute_only_on_change():
    # Sequence: None→[a]→[a]→[a,b]→[a,b]→None
    agent = _SpyAgent(scripted=[None, ["a"], ["a"], ["a", "b"], ["a", "b"], None])
    for i in range(6):
        agent._refresh_active_tool_filter(f"turn {i}")

    # Recompute happens only on the 3 transitions: None→[a], [a]→[a,b], [a,b]→None.
    assert agent.compose_calls == 3
    assert agent._active_tool_filter is None  # last selection


def test_no_recompute_when_filter_stable():
    agent = _SpyAgent(scripted=[["a", "b"], ["a", "b"], ["a", "b"]])
    for i in range(3):
        agent._refresh_active_tool_filter(f"turn {i}")
    # First turn changes None→[a,b] (1 recompute); the next two are stable.
    assert agent.compose_calls == 1
