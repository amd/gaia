# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool-prompt cost baseline for the dynamic tool-loader (#1448, parent #688).

Part 0 is measure-only. This pins the *current* (unfiltered) tool-prompt
cost so Part 1 (#1449) can prove a reduction, and asserts the load-bearing
shape of the measurements:

* the deterministic doc tool set is a fixed size,
* the native (JSON-schema) path is much heavier than the text path — that
  is where the loader's savings come from,
* prompt cost grows ~linearly per added tool, and
* a fixed *loaded* subset stays flat as the *registry* grows (cost tracks
  tools loaded, not registered).

The cost/slope/distribution checks are model-free (no Lemonade backend).
The TTFT parser is exercised against a committed scorecard fixture, so the
Component-C parsing logic is covered without a live eval run.

The pinned numbers below are tiktoken `cl100k_base` / char measurements at
the time of #1448. They are a deliberate baseline: if you legitimately add
or remove a doc-profile tool, update these in the same commit (and note it
in the PR) — the same discipline as the #1030 system-prompt budget test.
"""

from __future__ import annotations

import os

import pytest

from gaia.eval.tool_cost import (
    FIXED_SUBSET_DEFAULT,
    build_doc_agent_skeleton,
    get_tokenizer,
    measure_fixed_subset,
    measure_slope,
    measure_tool_prompt_cost,
    parse_ttft_from_scorecard,
    tool_size_distribution,
)

# --- Pinned baseline (#1448, doc profile, deterministic tool set) ----------
EXPECTED_DOC_TOOL_COUNT = 37
BASELINE_TEXT_CHARS = 4863
BASELINE_NATIVE_CHARS = 21957
BASELINE_TEXT_TOKENS = 1014
BASELINE_NATIVE_TOKENS = 5128
# Band tolerates trivial wording edits; a real tool add/remove blows past it
# and should bump the baseline deliberately.
TOLERANCE = 0.10

_SCORECARD_FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
    "eval_baselines",
    "gemma-4-e4b-d71cd914",
    "scorecard_tool_selection.json",
)


def _within(value: float, baseline: float, tol: float = TOLERANCE) -> bool:
    return abs(value - baseline) <= baseline * tol


@pytest.fixture(scope="module")
def doc_agent():
    """A deterministic doc-profile skeleton (built once for the module)."""
    return build_doc_agent_skeleton(profile="doc", deterministic=True)


def test_harness_runs_and_pins_baseline(doc_agent):
    """The harness runs and the measured cost matches the pinned baseline."""
    cost = measure_tool_prompt_cost(doc_agent)

    assert cost["tool_count"] == EXPECTED_DOC_TOOL_COUNT, (
        f"doc tool count changed: {cost['tool_count']} != "
        f"{EXPECTED_DOC_TOOL_COUNT}. If you added/removed a doc-profile tool, "
        f"update the pinned baseline in this file in the same commit."
    )

    # Char counts are tokenizer-agnostic and fully deterministic.
    assert _within(cost["text_chars"], BASELINE_TEXT_CHARS), (
        f"text-path chars drifted: {cost['text_chars']} vs "
        f"{BASELINE_TEXT_CHARS} baseline (>±{TOLERANCE:.0%})."
    )
    assert _within(cost["native_chars"], BASELINE_NATIVE_CHARS), (
        f"native-path chars drifted: {cost['native_chars']} vs "
        f"{BASELINE_NATIVE_CHARS} baseline (>±{TOLERANCE:.0%})."
    )


def test_native_path_is_heavier_than_text(doc_agent):
    """The native schema path is where the real tokens are."""
    cost = measure_tool_prompt_cost(doc_agent)
    assert cost["native_chars"] > cost["text_chars"]
    # Native is several× the text path — the headroom the loader targets.
    assert cost["native_chars"] / cost["text_chars"] > 2.0


def test_token_baseline_when_tiktoken_available(doc_agent):
    """When tiktoken is installed, token counts match the pinned baseline."""
    tok = get_tokenizer()
    if tok is None:
        pytest.skip("tiktoken not installed — char baseline covers this case")
    cost = measure_tool_prompt_cost(doc_agent, tok=tok)
    assert cost["native_tokens"] > cost["text_tokens"]
    assert _within(cost["text_tokens"], BASELINE_TEXT_TOKENS)
    assert _within(cost["native_tokens"], BASELINE_NATIVE_TOKENS)


def test_slope_is_linear(doc_agent):
    """Prompt cost grows ~linearly per added tool, on both paths."""
    result = measure_slope(doc_agent)
    rows = result["rows"]
    assert [r["k"] for r in rows] == [0, 10, 20, 40]

    # Synthetic tools are clones of the median real tool, so each block of
    # 10 adds the same cost — the per-step increment must stay constant.
    increments = []
    for prev, cur in zip(rows, rows[1:]):
        dk = cur["k"] - prev["k"]
        increments.append((cur["native_chars"] - prev["native_chars"]) / dk)
    assert all(inc > 0 for inc in increments), "native cost must grow with K"
    spread = (max(increments) - min(increments)) / max(increments)
    assert spread < 0.02, f"per-tool slope not linear: increments={increments}"

    assert result["slope"]["native_chars"] > 0


def test_fixed_subset_stays_flat(doc_agent):
    """A fixed loaded subset costs the same as the registry grows."""
    rows = measure_fixed_subset(doc_agent, subset=FIXED_SUBSET_DEFAULT)
    native = {r["native_chars"] for r in rows}
    text = {r["text_chars"] for r in rows}
    assert len(native) == 1, f"loaded-subset native cost drifted with K: {rows}"
    assert len(text) == 1, f"loaded-subset text cost drifted with K: {rows}"


def test_size_distribution_native_exceeds_text(doc_agent):
    """Per-tool native sizes dominate text sizes across the distribution."""
    dist = tool_size_distribution(doc_agent)
    assert dist["native"]["chars"]["median"] > dist["text"]["chars"]["median"]
    assert dist["native"]["chars"]["max"] > dist["native"]["chars"]["min"]


def test_parse_ttft_from_committed_scorecard():
    """Component-C parser: first-vs-later TTFT and needed-sets from a scorecard.

    Uses the committed gemma-4-e4b tool_selection baseline so the parsing
    logic is covered without a live backend.
    """
    ttft = parse_ttft_from_scorecard(_SCORECARD_FIXTURE)

    # First-turn TTFTs in the fixture: [0.231, 0.856, 0.122, None].
    assert ttft["first_turn"]["n"] == 3
    assert ttft["first_turn_null_count"] == 1
    assert ttft["first_turn"]["min"] == pytest.approx(0.122)
    assert ttft["first_turn"]["max"] == pytest.approx(0.856)
    # Needed-set = the per-turn agent_tools; the recall floor Part 1 must hit.
    assert ttft["max_needed_set"] == 2
