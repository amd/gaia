# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the semantic ToolLoader (#1449, parent #688).

Pure loader logic — no Lemonade backend. A deterministic fake embedder gives
exact control over cosine scores: every tool's doc embeds to a distinct
one-hot axis, and a query embeds to a coordinate vector, so
``dot(query, tool_i)`` equals exactly the score assigned to that
(query, tool) pair. Tool embeddings are content-cached (as in production), so
across turns we vary the *query* — never the tool docs — which is how scores
actually change turn to turn.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from gaia.agents.base.tool_loader import ToolBundle, ToolLoader

DIM = 768


def _make_embed_fn(tools: list[str], query_scores: dict[str, dict[str, float]]):
    """Build a deterministic embedder over a fixed tool set.

    Args:
        tools: tool names; each gets a distinct one-hot embedding axis.
        query_scores: ``{query_text: {tool_name: cosine_score}}``. A query
            embeds to the coordinate vector with those scores; ``dot`` with a
            tool's one-hot axis recovers the score exactly.
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
    return {name: {"description": f"does {name}"} for name in tools}


# ── threshold boundary ───────────────────────────────────────────────────


def test_threshold_boundary_is_inclusive():
    """score == threshold matches (inclusive); just below does not."""
    tools = ["hit", "miss"]
    embed = _make_embed_fn(tools, {"q": {"hit": 0.55, "miss": 0.5499}})
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)
    assert loader.select("q", _registry(tools)) == ["hit"]


# ── bundle pull-in ───────────────────────────────────────────────────────


def test_bundle_pull_in_and_mate_admission_score():
    """A matched member pulls its whole bundle; the mate inherits the bundle score.

    a1 matches at 0.9 and pulls its non-matching mate a2 (own score 0.1). The
    mate's admission score is lifted to 0.9, so under a 2-slot cap a2 outranks
    the independently-matched b1 (0.6) and b1 is skipped.
    """
    tools = ["a1", "a2", "b1"]
    embed = _make_embed_fn(tools, {"q": {"a1": 0.9, "a2": 0.1, "b1": 0.6}})
    bundles = [ToolBundle(name="A", members=frozenset({"a1", "a2"}))]
    loader = ToolLoader(frozenset(), bundles, embed, threshold=0.55, max_tools=2)
    assert loader.select("q", _registry(tools)) == ["a1", "a2"]


def test_bundle_pull_in_skips_members_absent_from_registry():
    """A bundle member not present in the registry is silently not pulled."""
    tools = ["a1"]
    embed = _make_embed_fn(tools, {"q": {"a1": 0.9}})
    bundles = [ToolBundle(name="A", members=frozenset({"a1", "ghost"}))]
    loader = ToolLoader(frozenset(), bundles, embed, threshold=0.55, max_tools=14)
    assert loader.select("q", _registry(tools)) == ["a1"]


# ── CORE ─────────────────────────────────────────────────────────────────


def test_core_always_admitted_even_without_match():
    """CORE tools are admitted unconditionally, regardless of score."""
    tools = ["c1", "d1"]
    embed = _make_embed_fn(tools, {"q": {"c1": 0.0, "d1": 0.7}})
    loader = ToolLoader(frozenset({"c1"}), [], embed, threshold=0.55, max_tools=14)
    assert loader.select("q", _registry(tools)) == ["c1", "d1"]


# ── monotonic growth ─────────────────────────────────────────────────────


def test_monotonic_growth_no_pruning_on_score_drop():
    """Once loaded, a tool stays even when a later turn no longer matches it."""
    tools = ["d1", "d2"]
    embed = _make_embed_fn(
        tools,
        {
            "q1": {"d1": 0.7, "d2": 0.0},  # turn 1 matches d1
            "q2": {"d1": 0.0, "d2": 0.8},  # turn 2 matches d2, not d1
        },
    )
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)
    reg = _registry(tools)
    assert loader.select("q1", reg) == ["d1"]
    assert loader.select("q2", reg) == ["d1", "d2"]  # monotonic union, d1 kept


def test_repeat_query_is_deterministic_and_identical():
    """The same query yields a byte-identical sorted set on repeat (KV-warm)."""
    tools = ["core_a", "d1", "d2", "d3"]
    embed = _make_embed_fn(
        tools, {"q": {"core_a": 0.0, "d1": 0.8, "d2": 0.7, "d3": 0.6}}
    )
    loader = ToolLoader(frozenset({"core_a"}), [], embed, threshold=0.55, max_tools=14)
    reg = _registry(tools)
    first = loader.select("q", reg)
    second = loader.select("q", reg)
    assert first == second == sorted(first)
    assert first == ["core_a", "d1", "d2", "d3"]


# ── LRU at cap ───────────────────────────────────────────────────────────


def test_skip_at_cap_when_nothing_evictable():
    """When every slot is a this-turn admission, excess candidates are skipped+logged."""
    tools = ["d1", "d2", "d3", "d4"]
    embed = _make_embed_fn(tools, {"q": {"d1": 0.9, "d2": 0.8, "d3": 0.7, "d4": 0.6}})
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=2)
    with _capture("gaia.agents.base.tool_loader") as records:
        loaded = loader.select("q", _registry(tools))
    assert loaded == ["d1", "d2"]  # top-2 by score
    assert set(_selection_payload(records)["skipped_at_cap"]) == {"d3", "d4"}


def test_core_never_evicted_at_cap():
    """A CORE tool is never chosen as an eviction victim."""
    tools = ["c1", "d1", "d2", "d3"]
    embed = _make_embed_fn(
        tools,
        {
            "q1": {"c1": 0.0, "d1": 0.9, "d2": 0.8, "d3": 0.0},
            "q2": {"c1": 0.0, "d1": 0.0, "d2": 0.0, "d3": 0.9},
        },
    )
    loader = ToolLoader(frozenset({"c1"}), [], embed, threshold=0.55, max_tools=3)
    reg = _registry(tools)
    assert loader.select("q1", reg) == ["c1", "d1", "d2"]  # at cap 3
    loaded = loader.select("q2", reg)  # d3 forces eviction of a non-CORE
    assert "c1" in loaded and "d3" in loaded and len(loaded) == 3


def test_lru_evicts_oldest_last_call():
    """At cap, the least-recently-called non-CORE tool is evicted first."""
    tools = ["d1", "d2", "d3"]
    embed = _make_embed_fn(
        tools,
        {
            "q1": {"d1": 0.9, "d2": 0.8, "d3": 0.0},
            "q2": {"d1": 0.0, "d2": 0.0, "d3": 0.9},
        },
    )
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=2)
    reg = _registry(tools)
    assert loader.select("q1", reg) == ["d1", "d2"]

    # d1 called recently, d2 long ago → d2 is the victim.
    loader._loaded["d1"].last_call_ts = 5000.0
    loader._loaded["d2"].last_call_ts = 1000.0
    assert loader.select("q2", reg) == ["d1", "d3"]


def test_lru_falls_back_to_load_time_for_never_called():
    """Never-called tools are ranked by load time (oldest loaded evicted first)."""
    tools = ["d1", "d2", "d3"]
    embed = _make_embed_fn(
        tools,
        {
            "q1": {"d1": 0.9, "d2": 0.8, "d3": 0.0},
            "q2": {"d1": 0.0, "d2": 0.0, "d3": 0.9},
        },
    )
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=2)
    reg = _registry(tools)
    assert loader.select("q1", reg) == ["d1", "d2"]

    # Neither called; make d1 the older load → d1 evicted first.
    loader._loaded["d1"].loaded_at = 1000.0
    loader._loaded["d2"].loaded_at = 2000.0
    assert loader.select("q2", reg) == ["d2", "d3"]


def test_evicted_tool_can_be_readmitted():
    """An evicted tool re-enters when it matches again (the monotonicity exception)."""
    tools = ["d1", "d2", "d3"]
    embed = _make_embed_fn(
        tools,
        {
            "q1": {"d1": 0.9, "d2": 0.8, "d3": 0.0},
            "q2": {"d1": 0.0, "d2": 0.0, "d3": 0.9},  # evicts d1
            "q3": {"d1": 0.95, "d2": 0.0, "d3": 0.0},  # d1 matches again
        },
    )
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=2)
    reg = _registry(tools)
    loader.select("q1", reg)
    loader._loaded["d1"].loaded_at = 1000.0
    loader._loaded["d2"].loaded_at = 2000.0
    assert "d1" not in loader.select("q2", reg)  # d1 evicted

    loader._loaded["d2"].loaded_at = 1500.0
    loader._loaded["d3"].loaded_at = 3000.0
    assert "d1" in loader.select("q3", reg)  # re-admitted


# ── record_tool_use ──────────────────────────────────────────────────────


def test_record_tool_use_updates_recency_for_loaded():
    tools = ["d1"]
    embed = _make_embed_fn(tools, {"q": {"d1": 0.9}})
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)
    loader.select("q", _registry(tools))
    assert loader._loaded["d1"].last_call_ts is None
    loader.record_tool_use("d1")
    assert loader._loaded["d1"].last_call_ts is not None


def test_record_tool_use_logs_escape_hatch_for_unloaded():
    """Executing an unlisted tool logs the escape-hatch signal (no auto-load)."""
    tools = ["d1"]
    embed = _make_embed_fn(tools, {"q": {"d1": 0.9}})
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)
    loader.select("q", _registry(tools))
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.record_tool_use("never_loaded")
    assert "never_loaded" not in loader._loaded
    assert any("TOOL_LOADER_ESCAPE_HATCH" in r.getMessage() for r in records)


# ── SKILL tier (Part 3, #1451) ─────────────────────────────────────────────


def test_skill_tool_admitted_ahead_of_semantic_at_cap():
    """At cap, a SKILL tool wins the last slot over a higher-scored semantic tool.

    One free slot, two candidates: the recalled recipe's ``x`` (no semantic
    match) and ``hi`` (semantic 0.9). SKILL is admitted before semantic, so ``x``
    takes the slot and the higher-scored ``hi`` is skipped — the precise
    realization of "ahead of semantic" (precedence CORE > SKILL > SEMANTIC).
    """
    tools = ["x", "hi"]
    embed = _make_embed_fn(tools, {"q": {"x": 0.0, "hi": 0.9}})
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=1)
    with _capture("gaia.agents.base.tool_loader") as records:
        loaded = loader.select("q", _registry(tools), skill_tools=["x"])
    assert loaded == ["x"]  # SKILL took the only slot
    payload = _selection_payload(records)
    assert payload["skill"] == ["x"]
    assert "hi" in payload["skipped_at_cap"]  # higher-scored semantic, skipped


def test_skill_tool_avoids_escape_hatch_activation():
    """Pre-loading the recipe's tool keeps the escape-hatch counter at 0.

    ``needed`` has no semantic match, so without the SKILL signal it would be
    absent and executing it would log the escape hatch. The recalled recipe
    pre-loads it, so the same execution is a normal recency update.
    """
    tools = ["needed"]
    embed = _make_embed_fn(tools, {"q": {"needed": 0.0}})

    # Control: no skill signal → tool absent → execution escape-hatches.
    control = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)
    assert control.select("q", _registry(tools)) == []
    control.record_tool_use("needed")
    assert control._escape_hatch_count == 1

    # SKILL signal pre-loads it → execution is a plain recency update.
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)
    assert loader.select("q", _registry(tools), skill_tools=["needed"]) == ["needed"]
    loader.record_tool_use("needed")
    assert loader._escape_hatch_count == 0


def test_skill_signal_absent_is_byte_identical():
    """``skill_tools`` None / [] / omitted give the same loaded set and log bytes.

    Graceful absence: the SKILL signal off must be byte-for-byte Parts 0-2 — same
    loaded set, same ``TOOL_LOADER`` payload, and no ``skill`` key.
    """
    tools = ["c1", "d1"]
    scores = {"q": {"c1": 0.0, "d1": 0.7}}

    def _run(**kwargs):
        embed = _make_embed_fn(tools, scores)
        loader = ToolLoader(frozenset({"c1"}), [], embed, threshold=0.55, max_tools=14)
        with _capture("gaia.agents.base.tool_loader") as records:
            loaded = loader.select("q", _registry(tools), **kwargs)
        return loaded, _selection_payload(records)

    base_loaded, base_payload = _run()
    none_loaded, none_payload = _run(skill_tools=None)
    empty_loaded, empty_payload = _run(skill_tools=[])

    assert base_loaded == none_loaded == empty_loaded == ["c1", "d1"]
    assert base_payload == none_payload == empty_payload
    assert "skill" not in base_payload


def test_skill_tool_not_in_registry_is_dropped():
    """A recalled tool absent from the registry is dropped, not raised."""
    tools = ["d1"]
    embed = _make_embed_fn(tools, {"q": {"d1": 0.7}})
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)
    with _capture("gaia.agents.base.tool_loader") as records:
        loaded = loader.select("q", _registry(tools), skill_tools=["ghost"])
    assert "ghost" not in loaded
    assert loaded == ["d1"]  # semantic match still loads
    assert "skill" not in _selection_payload(records)  # ghost dropped, nothing fired


def test_skill_tool_in_core_is_noop():
    """A recalled tool already in CORE is not double-admitted nor SKILL-logged."""
    tools = ["c1", "d1"]
    embed = _make_embed_fn(tools, {"q": {"c1": 0.0, "d1": 0.0}})
    loader = ToolLoader(frozenset({"c1"}), [], embed, threshold=0.55, max_tools=14)
    with _capture("gaia.agents.base.tool_loader") as records:
        loaded = loader.select("q", _registry(tools), skill_tools=["c1"])
    assert loaded == ["c1"]
    payload = _selection_payload(records)
    assert payload["admitted"] == ["c1"]  # admitted once, by CORE
    assert "skill" not in payload  # CORE already covered it; SKILL did not fire


def test_skill_log_key_present_only_when_skill_fires():
    """The ``skill`` log key appears only on a turn the SKILL signal contributes."""
    tools = ["x"]
    embed = _make_embed_fn(tools, {"q": {"x": 0.0}})
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)

    with _capture("gaia.agents.base.tool_loader") as fired:
        loader.select("q", _registry(tools), skill_tools=["x"])
    assert _selection_payload(fired)["skill"] == ["x"]

    # A later turn with no recall must not carry the key (x already loaded).
    with _capture("gaia.agents.base.tool_loader") as quiet:
        loader.select("q", _registry(tools), skill_tools=[])
    assert "skill" not in _selection_payload(quiet)


# ── load_bundle / menu / counters (Part 2, #1450) ──────────────────────────


def _loader_with_bundles(max_tools: int = 14):
    """A loader over a tiny CORE + two bundles, with a never-matching embedder."""
    tools = ["c1", "a1", "a2", "b1"]
    embed = _make_embed_fn(tools, {"q": {"c1": 0.0, "a1": 0.0, "a2": 0.0, "b1": 0.0}})
    bundles = [
        ToolBundle(name="A", members=frozenset({"a1", "a2"}), description="A tools"),
        ToolBundle(name="B", members=frozenset({"b1"}), description="B tools"),
    ]
    loader = ToolLoader(
        frozenset({"c1"}), bundles, embed, threshold=0.55, max_tools=max_tools
    )
    return loader, _registry(tools)


def test_bundle_names_are_sorted():
    loader, _ = _loader_with_bundles()
    assert loader.bundle_names() == ["A", "B"]


def test_format_bundle_menu_lists_name_and_description():
    loader, _ = _loader_with_bundles()
    menu = loader.format_bundle_menu()
    assert "- A: A tools" in menu
    assert "- B: B tools" in menu


def test_load_bundle_by_bundle_name_admits_members():
    loader, reg = _loader_with_bundles()
    loader.select("q", reg)  # turn 1: CORE only (c1)
    loaded = loader.load_bundle("A", reg)
    assert {"c1", "a1", "a2"} <= set(loaded)
    assert loader._load_tools_count == 1


def test_load_bundle_by_tool_name_resolves_to_owning_bundle():
    loader, reg = _loader_with_bundles()
    loader.select("q", reg)
    with _capture("gaia.agents.base.tool_loader") as records:
        loaded = loader.load_bundle("a1", reg)  # bare tool name → bundle A
    assert {"a1", "a2"} <= set(loaded)
    # The log records the resolved bundle ("A"), not the bare tool name ("a1").
    events = [p for p in _loader_payloads(records) if p.get("event") == "load_tools"]
    assert events and events[0]["bundle"] == "A"


def test_load_bundle_unknown_name_raises_keyerror():
    loader, reg = _loader_with_bundles()
    loader.select("q", reg)
    with pytest.raises(KeyError):
        loader.load_bundle("does_not_exist", reg)


def test_load_bundle_skips_members_absent_from_registry():
    tools = ["c1", "a1"]
    embed = _make_embed_fn(tools, {"q": {"c1": 0.0, "a1": 0.0}})
    bundles = [ToolBundle(name="A", members=frozenset({"a1", "ghost"}))]
    loader = ToolLoader(frozenset({"c1"}), bundles, embed, threshold=0.55, max_tools=14)
    reg = _registry(tools)
    loader.select("q", reg)
    loaded = loader.load_bundle("A", reg)
    assert "a1" in loaded and "ghost" not in loaded


def test_load_bundle_is_cap_aware_and_protects_just_loaded():
    """At cap, load_bundle evicts an LRU non-CORE tool, never CORE or just-loaded."""
    tools = ["c1", "d1", "a1", "a2"]
    embed = _make_embed_fn(tools, {"q": {"c1": 0.0, "d1": 0.9, "a1": 0.0, "a2": 0.0}})
    bundles = [ToolBundle(name="A", members=frozenset({"a1", "a2"}), description="A")]
    loader = ToolLoader(frozenset({"c1"}), bundles, embed, threshold=0.55, max_tools=3)
    reg = _registry(tools)
    assert loader.select("q", reg) == ["c1", "d1"]  # CORE + matched d1 (2 of 3)
    loaded = loader.load_bundle("A", reg)  # wants a1,a2 with 1 slot free → evict
    assert set(loaded) == {"c1", "a1", "a2"}  # cap held; d1 evicted
    assert "d1" not in loaded


def test_load_bundle_emits_same_turn_loaded_superset_line():
    loader, reg = _loader_with_bundles()
    loader.select("q", reg)
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.load_bundle("A", reg)
    events = [p for p in _loader_payloads(records) if p.get("event") == "load_tools"]
    assert events, "no load_tools TOOL_LOADER line captured"
    assert events[0]["turn"] == loader._turn
    assert {"a1", "a2"} <= set(events[0]["loaded"])


def test_escape_hatch_and_load_counters_increment():
    loader, reg = _loader_with_bundles()
    loader.select("q", reg)
    loader.record_tool_use("never_loaded")  # free recovery
    loader.load_bundle("A", reg)  # explicit recovery
    assert loader._escape_hatch_count == 1
    assert loader._load_tools_count == 1


def test_reset_session_emits_summary_then_zeroes_counters():
    loader, reg = _loader_with_bundles()
    loader.select("q", reg)
    loader.record_tool_use("never_loaded")
    loader.load_bundle("A", reg)
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.reset_session()
    summary = _session_payload(records)
    assert summary["turns"] == 1
    assert summary["escape_hatch_count"] == 1
    assert summary["load_tools_count"] == 1
    assert summary["escape_hatch_rate"] == pytest.approx(2.0)  # (1+1)/1
    assert loader._escape_hatch_count == 0
    assert loader._load_tools_count == 0


def test_reset_session_emits_no_summary_when_no_turns():
    loader, _ = _loader_with_bundles()
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.reset_session()  # turn == 0 → nothing to summarize
    assert not any("TOOL_LOADER_SESSION" in r.getMessage() for r in records)


# ── embedder failure ─────────────────────────────────────────────────────


def test_embedder_failure_session_disables_loudly():
    """A raising embedder disables selection for the session with a WARNING."""

    def boom(text: str) -> np.ndarray:
        raise RuntimeError("lemonade down")

    loader = ToolLoader(frozenset(), [], boom, threshold=0.55, max_tools=14)
    reg = _registry(["d1"])
    with _capture("gaia.agents.base.tool_loader", level=logging.WARNING) as records:
        assert loader.select("q", reg) is None
    assert loader.session_disabled is True
    assert any("disabled for this session" in r.getMessage() for r in records)
    # Stays disabled — returns None without re-trying the embedder.
    assert loader.select("q", reg) is None


# ── reset / validate ─────────────────────────────────────────────────────


def test_reset_session_clears_state_but_keeps_cache():
    tools = ["d1"]
    embed = _make_embed_fn(tools, {"q": {"d1": 0.9}})
    loader = ToolLoader(frozenset(), [], embed, threshold=0.55, max_tools=14)
    loader.select("q", _registry(tools))
    assert loader._loaded
    cache_size = len(loader._embed_cache)
    loader.reset_session()
    assert not loader._loaded
    assert loader._turn == 0
    assert loader.session_disabled is False
    assert len(loader._embed_cache) == cache_size  # content cache survives


def test_validate_registry_raises_with_missing_names():
    loader = ToolLoader(
        frozenset({"missing_core"}),
        [ToolBundle(name="B", members=frozenset({"missing_member"}))],
        lambda t: np.zeros(DIM, dtype=np.float32),
        threshold=0.55,
        max_tools=14,
    )
    with pytest.raises(ValueError) as exc:
        loader.validate_registry(_registry(["d1"]))
    msg = str(exc.value)
    assert "missing_core" in msg and "missing_member" in msg


def test_validate_registry_passes_when_covered():
    loader = ToolLoader(
        frozenset({"d1"}),
        [ToolBundle(name="B", members=frozenset({"d2"}))],
        lambda t: np.zeros(DIM, dtype=np.float32),
        threshold=0.55,
        max_tools=14,
    )
    loader.validate_registry(_registry(["d1", "d2"]))  # no raise


# ── helpers ──────────────────────────────────────────────────────────────


class _capture:
    """Context manager capturing log records from *logger_name*."""

    def __init__(self, logger_name: str, level: int = logging.INFO):
        self._logger = logging.getLogger(logger_name)
        self._level = level
        self._records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = self._records.append  # type: ignore[method-assign]
        self._prev_level = self._logger.level
        self._prev_propagate = self._logger.propagate

    def __enter__(self) -> list[logging.LogRecord]:
        self._logger.setLevel(self._level)
        self._logger.addHandler(self._handler)
        self._logger.propagate = False
        return self._records

    def __exit__(self, *exc) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prev_level)
        self._logger.propagate = self._prev_propagate


def _loader_payloads(records: list[logging.LogRecord]) -> list[dict]:
    """All JSON payloads from ``TOOL_LOADER {...}`` lines (selection + load_tools)."""
    out: list[dict] = []
    for r in records:
        msg = r.getMessage()
        if msg.startswith("TOOL_LOADER {"):
            out.append(json.loads(msg[len("TOOL_LOADER ") :]))
    return out


def _selection_payload(records: list[logging.LogRecord]) -> dict:
    """Extract the JSON payload from the TOOL_LOADER selection log line."""
    for payload in _loader_payloads(records):
        if "event" not in payload:  # the per-turn select line (not load_tools)
            return payload
    raise AssertionError("no TOOL_LOADER selection line captured")


def _session_payload(records: list[logging.LogRecord]) -> dict:
    """Extract the JSON payload from the TOOL_LOADER_SESSION summary line."""
    for r in records:
        msg = r.getMessage()
        if msg.startswith("TOOL_LOADER_SESSION {"):
            return json.loads(msg[len("TOOL_LOADER_SESSION ") :])
    raise AssertionError("no TOOL_LOADER_SESSION line captured")
