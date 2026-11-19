# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the ToolLoader's session list (#1449, parent #688).

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

from gaia.agents.base.tool_loader import (
    DEFAULT_THRESHOLD,
    JUMP_MARGIN,
    SEARCH_FLOOR,
    ToolBundle,
    ToolLoader,
)

DIM = 768


class _Embedder:
    """Deterministic embedder over a fixed tool set; records what it embedded.

    Args:
        tools: tool names; each gets a distinct one-hot embedding axis.
        query_scores: ``{query_text: {tool_name: cosine_score}}``. A query
            embeds to the coordinate vector with those scores; ``dot`` with a
            tool's one-hot axis recovers the score exactly.
    """

    def __init__(self, tools: list[str], query_scores: dict[str, dict[str, float]]):
        assert len(tools) <= DIM
        self._axis = {name: i for i, name in enumerate(tools)}
        self._docs = {f"{name}: does {name}": name for name in tools}
        self._query_scores = query_scores
        self.calls: list[str] = []

    def __call__(self, text: str) -> np.ndarray:
        self.calls.append(text)
        v = np.zeros(DIM, dtype=np.float32)
        if text in self._docs:
            v[self._axis[self._docs[text]]] = 1.0
            return v
        if text in self._query_scores:
            for tool, score in self._query_scores[text].items():
                v[self._axis[tool]] = score
            return v
        raise AssertionError(f"unexpected text embedded: {text!r}")


def _registry(tools: list[str]) -> dict[str, dict]:
    return {name: {"description": f"does {name}"} for name in tools}


def _loader(tools, query_scores, core=(), threshold=0.55, **kwargs):
    embed = _Embedder(tools, query_scores)
    return ToolLoader(frozenset(core), [], embed, threshold=threshold, **kwargs), embed


# ── core ─────────────────────────────────────────────────────────────────


def test_the_jump_bar_is_the_tuned_values():
    assert (DEFAULT_THRESHOLD, JUMP_MARGIN) == (0.40, 0.03)


def test_a_session_starts_on_core_in_registry_order():
    tools = ["z_core", "a_tool", "a_core"]
    loader, _ = _loader(tools, {"q": {}}, core={"a_core", "z_core"})
    assert loader.offered(_registry(tools)) == ["z_core", "a_core"]
    assert loader.select("q", _registry(tools)) == ["z_core", "a_core"]


def test_a_core_name_missing_from_the_registry_is_left_off():
    tools = ["c1"]
    loader, _ = _loader(tools, {"q": {}}, core={"c1", "optional_tool"})
    assert loader.select("q", _registry(tools)) == ["c1"]


# ── retrieval jump ───────────────────────────────────────────────────────


def test_an_outside_tool_at_the_threshold_that_beats_the_list_jumps():
    """The bar is inclusive, and the jump lands on every tool, registry order."""
    tools = ["c1", "d2", "d1"]
    loader, _ = _loader(
        tools, {"q": {"c1": 0.1, "d1": 0.5, "d2": 0.0}}, core={"c1"}, threshold=0.5
    )
    assert loader.select("q", _registry(tools)) == ["c1", "d2", "d1"]
    assert loader.jumped is True


def test_an_outside_tool_just_below_the_threshold_does_not_jump():
    tools = ["c1", "d1"]
    loader, _ = _loader(
        tools, {"q": {"c1": 0.1, "d1": 0.4999}}, core={"c1"}, threshold=0.5
    )
    assert loader.select("q", _registry(tools)) == ["c1"]
    assert loader.jumped is False


@pytest.mark.parametrize(
    "inside",
    [0.9, 0.8, 0.8 - JUMP_MARGIN + 0.001],
    ids=["listed-beats", "tie", "inside-the-margin"],
)
def test_an_outside_tool_must_beat_every_listed_tool_by_the_margin(inside):
    """Small talk scores about the same on everything; only a clear win is a need."""
    tools = ["c1", "c2", "d1"]
    scores = {"q": {"c1": 0.1, "c2": inside, "d1": 0.8}}
    loader, _ = _loader(tools, scores, core={"c1", "c2"})
    assert loader.select("q", _registry(tools)) == ["c1", "c2"]
    assert loader.jumped is False


def test_an_outside_tool_past_the_margin_jumps():
    tools = ["c1", "c2", "d1"]
    scores = {"q": {"c1": 0.1, "c2": 0.8 - JUMP_MARGIN - 0.001, "d1": 0.8}}
    loader, _ = _loader(tools, scores, core={"c1", "c2"})
    assert loader.select("q", _registry(tools)) == tools


def test_nothing_outside_the_list_never_jumps():
    tools = ["c1", "c2"]
    loader, _ = _loader(tools, {"q": {"c1": 0.99, "c2": 0.99}}, core=set(tools))
    assert loader.select("q", _registry(tools)) == ["c1", "c2"]
    assert loader.jumped is False


def test_an_empty_list_jumps_on_a_tool_above_the_threshold():
    tools = ["d1", "d2"]
    loader, _ = _loader(tools, {"q": {"d1": 0.7, "d2": 0.0}})
    assert loader.select("q", _registry(tools)) == ["d1", "d2"]


def test_a_jump_offers_every_tool_however_many_there_are():
    """No cap: the full list is the whole registry."""
    tools = [f"tool_{i}" for i in range(300)]
    loader, _ = _loader(tools, {"q": {"tool_299": 0.9}}, core={"tool_0"})
    assert loader.select("q", _registry(tools)) == tools


def test_a_skill_tool_on_the_list_does_not_count_as_outside():
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {"q": {"c1": 0.0, "d1": 0.9}}, core={"c1"})
    assert loader.select("q", _registry(tools), skill_tools=["d1"]) == ["c1", "d1"]
    assert loader.jumped is False


# ── after a jump ─────────────────────────────────────────────────────────


def test_after_a_jump_the_list_is_frozen_and_nothing_is_embedded():
    tools = ["c1", "d1", "d2"]
    loader, embed = _loader(tools, {"q1": {"d1": 0.9}}, core={"c1"})
    reg = _registry(tools)
    first = loader.select("q1", reg)
    assert first == tools

    embed.calls.clear()
    second = loader.select("q2 never seen by the embedder", reg)

    assert second == first
    assert embed.calls == []


def test_a_tool_registered_after_a_jump_joins_the_list_in_registry_order():
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {"q": {"d1": 0.9}}, core={"c1"})
    reg = _registry(tools)
    loader.select("q", reg)
    assert loader.offered({**reg, "mcp_tool": {"description": "x"}}) == [
        "c1",
        "d1",
        "mcp_tool",
    ]


def test_a_tool_a_skill_required_stays_on_the_full_list_after_unloading():
    tools = ["c1", "d1", "d2"]
    loader, _ = _loader(tools, {"q": {"d2": 0.9}}, core={"c1"})
    reg = _registry(tools)
    loader.admit_tools(["d1"], reg)
    loader.select("q", reg)
    assert loader.remove_tools(["d1"], reg) == tools


def test_a_second_jump_changes_nothing_and_logs_nothing():
    loader, _ = _loader(["c1"], {}, core={"c1"})
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.jump("search")
        loader.jump("retrieval")
    assert [p for p in _loader_payloads(records) if p.get("event") == "jump"] == [
        {"turn": 0, "event": "jump", "reason": "search"}
    ]


# ── requested tools ──────────────────────────────────────────────────────


def test_a_requested_tool_outside_the_list_jumps_without_embedding():
    tools = ["c1", "asked"]
    loader, embed = _loader(tools, {}, core={"c1"})
    with _capture("gaia.agents.base.tool_loader") as records:
        loaded = loader.select("q", _registry(tools), requested_tools=["asked"])
    assert loaded == ["c1", "asked"]
    assert embed.calls == []
    assert _jump_reason(records) == "requested"


def test_a_requested_tool_on_the_list_is_no_jump():
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {"q": {"c1": 0.0, "d1": 0.0}}, core={"c1"})
    assert loader.select("q", _registry(tools), requested_tools=["c1"]) == ["c1"]
    assert loader.jumped is False


def test_a_requested_tool_absent_from_the_registry_is_no_jump():
    """A recalled procedure may name a tool this agent lacks."""
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {"q": {"c1": 0.0, "d1": 0.0}}, core={"c1"})
    assert loader.select("q", _registry(tools), requested_tools=["ghost"]) == ["c1"]
    assert loader.jumped is False


@pytest.mark.parametrize(
    "kwargs", [{}, {"requested_tools": None}, {"requested_tools": []}]
)
def test_no_requested_tools_falls_through_to_retrieval(kwargs):
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {"q": {"c1": 0.0, "d1": 0.9}}, core={"c1"})
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.select("q", _registry(tools), **kwargs)
    assert _jump_reason(records) == "retrieval"


def test_a_skill_tool_is_on_the_list_before_requested_tools_are_checked():
    """A skill's tool is both: it joins the list, so it is no reason to jump."""
    tools = ["c1", "needed"]
    loader, _ = _loader(tools, {"q": {"c1": 0.0, "needed": 0.0}}, core={"c1"})
    loaded = loader.select(
        "q", _registry(tools), skill_tools=["needed"], requested_tools=["needed"]
    )
    assert loaded == ["c1", "needed"]
    assert loader.jumped is False


# ── skill tools ──────────────────────────────────────────────────────────


def _skill_loader():
    """Tiny CORE, two outside tools, and an embedder that scores all of it zero."""
    tools = ["c1", "a1", "a2", "b1"]
    return (
        _loader(tools, {"q": {name: 0.0 for name in tools}}, core={"c1"})[0],
        _registry(tools),
    )


def test_skill_tools_join_without_a_jump():
    loader, reg = _skill_loader()
    assert loader.select("q", reg, skill_tools=["b1", "a1"]) == ["c1", "b1", "a1"]
    assert loader.jumped is False


def test_skill_tools_stay_for_the_session_once_admitted():
    loader, reg = _skill_loader()
    loader.select("q", reg, skill_tools=["a1"])
    assert loader.select("q", reg) == ["c1", "a1"]


@pytest.mark.parametrize("kwargs", [{}, {"skill_tools": None}, {"skill_tools": []}])
def test_no_skill_tools_leaves_the_list_alone(kwargs):
    loader, reg = _skill_loader()
    assert loader.select("q", reg, **kwargs) == ["c1"]


def test_a_skill_tool_absent_from_the_registry_is_dropped_not_raised():
    loader, reg = _skill_loader()
    assert loader.select("q", reg, skill_tools=["ghost"]) == ["c1"]
    assert loader._added == []  # pylint: disable=protected-access


def test_a_skill_tool_in_core_is_not_listed_twice():
    loader, reg = _skill_loader()
    assert loader.select("q", reg, skill_tools=["c1"]) == ["c1"]


def test_admit_tools_appends_in_the_order_given():
    loader, reg = _skill_loader()
    loader.select("q", reg)
    # Appended, not sorted: a re-sorted list voids the cached prefix.
    assert loader.admit_tools(["b1", "a1"], reg) == ["c1", "b1", "a1"]


def test_admit_tools_skips_names_absent_from_the_registry():
    """A skill may name a tool this agent lacks; the loader must not invent it."""
    loader, reg = _skill_loader()
    assert loader.admit_tools(["a1", "no_such_tool"], reg) == ["c1", "a1"]


def test_admit_tools_is_idempotent():
    loader, reg = _skill_loader()
    loader.admit_tools(["a1", "a1"], reg)
    assert loader.admit_tools(["a1"], reg) == ["c1", "a1"]


def test_admit_tools_alone_is_not_a_jump():
    loader, reg = _skill_loader()
    loader.admit_tools(["a1"], reg)
    assert loader.jumped is False


def test_remove_tools_takes_only_the_named_tools():
    loader, reg = _skill_loader()
    loader.admit_tools(["b1", "a1", "a2"], reg)
    assert loader.remove_tools(["a1"], reg) == ["c1", "b1", "a2"]


def test_remove_tools_never_removes_core():
    loader, reg = _skill_loader()
    loader.admit_tools(["c1", "a1"], reg)
    assert loader.remove_tools(["c1", "a1"], reg) == ["c1"]


def test_a_removed_tool_can_be_admitted_again():
    loader, reg = _skill_loader()
    loader.admit_tools(["a1", "b1"], reg)
    loader.remove_tools(["a1"], reg)
    assert loader.admit_tools(["a1"], reg) == ["c1", "b1", "a1"]


# ── rank (search_tools) ──────────────────────────────────────────────────


def test_rank_returns_the_best_scores_first_up_to_the_limit():
    tools = ["d1", "d2", "d3"]
    loader, _ = _loader(tools, {"q": {"d1": 0.3, "d2": 0.9, "d3": 0.6}})
    ranked = loader.rank("q", loader.tool_docs(_registry(tools)), 2)
    assert [name for name, _ in ranked] == ["d2", "d3"]
    assert [score for _, score in ranked] == pytest.approx([0.9, 0.6])


def test_rank_breaks_ties_by_name():
    tools = ["z1", "a1"]
    loader, _ = _loader(tools, {"q": {"z1": 0.5, "a1": 0.5}})
    ranked = loader.rank("q", loader.tool_docs(_registry(tools)), 2)
    assert [name for name, _ in ranked] == ["a1", "z1"]


def test_rank_leaves_out_what_is_below_the_floor():
    tools = ["hit", "at_floor", "under"]
    loader, _ = _loader(
        tools,
        {"q": {"hit": 0.9, "at_floor": SEARCH_FLOOR, "under": SEARCH_FLOOR - 0.01}},
    )
    ranked = loader.rank("q", loader.tool_docs(_registry(tools)), 3)
    assert [name for name, _ in ranked] == ["hit", "at_floor"]


@pytest.mark.parametrize(
    "docs, limit", [({}, 5), ({"d1": "d1: does d1"}, 0)], ids=["no-docs", "no-limit"]
)
def test_rank_of_nothing_embeds_nothing(docs, limit):
    loader, embed = _loader(["d1"], {})
    assert loader.rank("q", docs, limit) == []
    assert embed.calls == []


def test_rank_raises_when_the_embedder_fails():
    """A search tool must say so rather than report no matches."""

    def boom(_text):
        raise ConnectionError("embedder down")

    loader = ToolLoader(frozenset(), [], boom)
    with pytest.raises(ConnectionError):
        loader.rank("q", {"d1": "d1: does d1"}, 5)
    assert loader.session_disabled is False


def test_rank_does_not_touch_the_list():
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {"q": {"d1": 0.9}}, core={"c1"})
    reg = _registry(tools)
    loader.rank("q", loader.tool_docs(reg), 5)
    assert loader.jumped is False
    assert loader.offered(reg) == ["c1"]


def test_tool_docs_are_the_embedded_text_for_every_tool():
    tools = ["d1"]
    loader, _ = _loader(tools, {})
    registry = {
        "d1": {"description": "\n   First line.  \nSecond line."},
        "bare": {"description": ""},
        "none": {},
    }
    assert loader.tool_docs(registry) == {
        "d1": "d1: First line.",
        "bare": "bare",
        "none": "none",
    }


# ── embeddings ───────────────────────────────────────────────────────────


def test_tool_docs_embed_once_across_turns():
    tools = ["c1", "d1"]
    loader, embed = _loader(tools, {"q1": {"d1": 0.0}, "q2": {"d1": 0.1}}, core={"c1"})
    reg = _registry(tools)
    loader.select("q1", reg)
    loader.select("q2", reg)
    assert embed.calls == ["c1: does c1", "d1: does d1", "q1", "q2"]


def test_the_batch_embedder_is_preferred_for_tool_docs():
    tools = ["d1", "d2"]
    embed = _Embedder(tools, {"q": {}})
    batches: list[list[str]] = []

    def batch(texts):
        batches.append(list(texts))
        return np.stack([embed(t) for t in texts])

    loader = ToolLoader(frozenset(), [], embed, embed_batch_fn=batch)
    loader.select("q", _registry(tools))
    assert batches == [["d1: does d1", "d2: does d2"]]


def test_a_short_batch_disables_the_session_instead_of_dropping_tools():
    tools = ["d1", "d2"]
    embed = _Embedder(tools, {"q": {}})
    loader = ToolLoader(
        frozenset(), [], embed, embed_batch_fn=lambda _t: np.zeros((1, DIM))
    )
    with _capture("gaia.agents.base.tool_loader", level=logging.WARNING) as records:
        assert loader.select("q", _registry(tools)) is None
    assert loader.session_disabled is True
    assert any("returned 1 vectors for 2 texts" in r.getMessage() for r in records)


# ── embedder failure ─────────────────────────────────────────────────────


def test_embedder_failure_session_disables_loudly():
    """A raising embedder disables selection for the session with a WARNING."""
    calls: list[str] = []

    def boom(text: str) -> np.ndarray:
        calls.append(text)
        raise RuntimeError("lemonade down")

    loader = ToolLoader(frozenset(), [], boom, threshold=0.55)
    reg = _registry(["d1"])
    with _capture("gaia.agents.base.tool_loader", level=logging.WARNING) as records:
        assert loader.select("q", reg) is None
    assert loader.session_disabled is True
    assert any("disabled for this session" in r.getMessage() for r in records)
    # Stays disabled — returns None without re-trying the embedder.
    tried = len(calls)
    assert loader.select("q", reg) is None
    assert len(calls) == tried


def test_a_requested_tool_needs_no_embedder():
    """The jump is decided before the first embed, so a dead embedder cannot stop it."""

    def boom(_text):
        raise RuntimeError("lemonade down")

    tools = ["c1", "asked"]
    loader = ToolLoader(frozenset({"c1"}), [], boom)
    assert loader.select("q", _registry(tools), requested_tools=["asked"]) == tools
    assert loader.session_disabled is False


def test_a_disabled_session_ignores_skill_and_requested_tools():
    def boom(_text):
        raise RuntimeError("lemonade down")

    tools = ["c1", "asked"]
    loader = ToolLoader(frozenset({"c1"}), [], boom)
    reg = _registry(tools)
    loader.select("q", reg)
    assert (
        loader.select("q", reg, skill_tools=["asked"], requested_tools=["asked"])
        is None
    )
    assert loader.jumped is False
    assert loader._added == []  # pylint: disable=protected-access


# ── logging ──────────────────────────────────────────────────────────────


def test_each_select_logs_its_scores_and_list():
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {"q": {"c1": 0.25, "d1": 0.5}}, core={"c1"})
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.select("q", _registry(tools))
    payload = _selection_payload(records)
    assert payload["turn"] == 1
    assert payload["threshold"] == 0.55
    assert payload["scores"] == {"c1": 0.25, "d1": 0.5}
    assert payload["jump"] is None
    assert payload["loaded"] == ["c1"]
    assert len(payload["query_sha"]) == 12


def test_the_jump_turn_names_its_reason_and_the_list_it_landed_on():
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {"q": {"c1": 0.0, "d1": 0.9}}, core={"c1"})
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.select("q", _registry(tools))
    assert _jump_reason(records) == "retrieval"
    payload = _selection_payload(records)
    assert payload["jump"] == "retrieval"
    assert payload["loaded"] == tools


def test_a_turn_after_the_jump_logs_no_scores():
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {}, core={"c1"})
    loader.jump("search")
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.select("anything", _registry(tools))
    assert _selection_payload(records)["scores"] == {}


# ── reset / validate ─────────────────────────────────────────────────────


def test_reset_session_clears_state_but_keeps_cache():
    tools = ["c1", "d1", "d2"]
    loader, embed = _loader(
        tools, {"q": {"c1": 0.0, "d1": 0.9, "d2": 0.0}}, core={"c1"}
    )
    reg = _registry(tools)
    loader.admit_tools(["d2"], reg)
    loader.select("q", reg)
    assert loader.jumped is True

    embed.calls.clear()
    loader.reset_session()

    assert loader.jumped is False
    assert loader.offered(reg) == ["c1"]
    assert loader._turn == 0  # pylint: disable=protected-access
    assert loader.select("q", reg) == tools
    assert embed.calls == ["q"]  # content cache survives


def test_reset_session_re_enables_a_disabled_session():
    outcomes = iter([RuntimeError("down")])

    def flaky(_text):
        outcome = next(outcomes, None)
        if outcome is not None:
            raise outcome
        return np.zeros(DIM, dtype=np.float32)

    tools = ["c1"]
    loader = ToolLoader(frozenset({"c1"}), [], flaky)
    reg = _registry(tools)
    assert loader.select("q", reg) is None
    loader.reset_session()
    assert loader.session_disabled is False
    assert loader.select("q", reg) == ["c1"]


def test_reset_session_summarises_the_session_it_ends():
    tools = ["c1", "d1"]
    loader, _ = _loader(tools, {"q": {"c1": 0.0, "d1": 0.9}}, core={"c1"})
    loader.select("q", _registry(tools))
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.reset_session()
    assert _session_payload(records) == {"turns": 1, "jump": "retrieval"}


def test_reset_session_emits_no_summary_when_no_turns():
    loader, _ = _loader(["c1"], {}, core={"c1"})
    with _capture("gaia.agents.base.tool_loader") as records:
        loader.reset_session()
    assert not any("TOOL_LOADER_SESSION" in r.getMessage() for r in records)


def test_validate_registry_raises_with_missing_names():
    loader = ToolLoader(
        frozenset({"missing_core"}),
        [ToolBundle(name="B", members=frozenset({"missing_member"}))],
        lambda t: np.zeros(DIM, dtype=np.float32),
        threshold=0.55,
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
    )
    loader.validate_registry(_registry(["d1", "d2"]))  # no raise


def test_validate_registry_exempts_optional_tools_only():
    loader = ToolLoader(
        frozenset({"d1", "maybe"}),
        [ToolBundle(name="B", members=frozenset({"gone"}))],
        lambda t: np.zeros(DIM, dtype=np.float32),
        optional_tools=["maybe"],
    )
    with pytest.raises(ValueError) as exc:
        loader.validate_registry(_registry(["d1"]))
    msg = str(exc.value)
    assert "gone" in msg and "maybe" not in msg


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
    """All JSON payloads from ``TOOL_LOADER {...}`` lines (selection + jump)."""
    out: list[dict] = []
    for r in records:
        msg = r.getMessage()
        if msg.startswith("TOOL_LOADER {"):
            out.append(json.loads(msg[len("TOOL_LOADER ") :]))
    return out


def _selection_payload(records: list[logging.LogRecord]) -> dict:
    """Extract the JSON payload from the TOOL_LOADER selection log line."""
    for payload in _loader_payloads(records):
        if "event" not in payload:  # the per-turn select line (not a jump)
            return payload
    raise AssertionError("no TOOL_LOADER selection line captured")


def _jump_reason(records: list[logging.LogRecord]) -> str:
    """The reason on the TOOL_LOADER jump line."""
    for payload in _loader_payloads(records):
        if payload.get("event") == "jump":
            return payload["reason"]
    raise AssertionError("no TOOL_LOADER jump line captured")


def _session_payload(records: list[logging.LogRecord]) -> dict:
    """Extract the JSON payload from the TOOL_LOADER_SESSION summary line."""
    for r in records:
        msg = r.getMessage()
        if msg.startswith("TOOL_LOADER_SESSION {"):
            return json.loads(msg[len("TOOL_LOADER_SESSION ") :])
    raise AssertionError("no TOOL_LOADER_SESSION line captured")
