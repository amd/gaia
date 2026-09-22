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

The pinned numbers below are tiktoken `cl100k_base` / char measurements of
the unfiltered doc registry. They are a deliberate baseline: if you
legitimately add or remove a doc-profile tool, update these in the same
commit (and note it in the PR) — the same discipline as the #1030
system-prompt budget test.

Only ``test_harness_runs_and_pins_baseline`` and its token twin compare
against those pins, so a registry-wide schema change fails there and nowhere
else. The Part-1 reduction guards below express shares of a baseline measured
in the same run, which is what makes them detect a real regression in the
filtered set rather than growth anywhere in the registry.
"""

from __future__ import annotations

import json
import os

import pytest

# DOC_CORE_TOOLS ships with the standalone gaia-agent-chat wheel (#1102); skip
# the whole module when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.tool_bundles import DOC_CORE_TOOLS  # noqa: E402

from gaia.eval.tool_cost import (  # noqa: E402
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
# 38 since #3670 added `read_tool_output` to the doc profile. The char/token
# pins below stay as they are: #4038 trims the docstrings they measure, so a
# value re-pinned here is wrong the moment it lands.
EXPECTED_DOC_TOOL_COUNT = 38
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
    """A deterministic doc-profile skeleton (built once for the module).

    Loader-off, so the registry is the pinned unfiltered baseline
    (``load_tools`` is *not* registered) — keep it that way for the baseline
    and slope/distribution pins below.
    """
    return build_doc_agent_skeleton(profile="doc", deterministic=True)


@pytest.fixture(scope="module")
def doc_agent_loader_on():
    """Doc skeleton with the loader active, so ``load_tools`` is registered.

    The CORE-floor guard must measure the set that actually ships every active
    turn, which includes the always-on ``load_tools`` escape hatch (#1450). The
    loader-off ``doc_agent`` fixture omits it, and a filtered render silently
    drops any name absent from the registry — so the floor would under-count.
    """
    return build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )


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


# --- Part-1 reduction proxy (#1449) ----------------------------------------
#
# Static, model-free proxy for the live ≥60% first-turn TTFT-reduction gate.
# These bound the *native-schema* token cost of a filtered loaded set as a share
# of the unfiltered registry. They are a proxy only — the authoritative gate is
# the live ``measure_prefill_ttft`` run in Step 6; token count tracks prefill
# cost but is not identical to it.
#
# Both guards measure their baseline in the same run rather than reading the
# pinned constants above. A share is the quantity these tests are actually
# about, and it is the only form that stays meaningful as the registry grows:
# a pinned denominator turns every schema-wide edit into a spurious failure
# here, on top of the one real failure in the pin test.
#
# Measured reality (worth knowing — it tempers the original estimate): the
# always-on CORE tools alone render ~40% of the native baseline, because the
# memory tools carry the longest docstrings. So CORE-only is the best case
# (~60% token reduction, right at the gate boundary), and a worst-case
# ``max_tools=14`` loaded set lands around ~60% of baseline. The first-turn win
# is real and large; whether it clears ≥60% in *TTFT* terms is what the live run
# decides.

# CORE is the always-on floor; its share of the registry sits near 40% on both
# paths. The ceilings leave room for wording edits and bite on real bloat.
CORE_NATIVE_SHARE_MAX = 0.45
CORE_TEXT_SHARE_MAX = 0.45
MAX_LOADED_NATIVE_SHARE_MAX = 0.70


def _filtered_native_tokens(agent, names, tok) -> int:
    return len(
        tok.encode(json.dumps(agent._build_openai_tool_schemas(filter_to=names)))
    )


def _filtered_text_tokens(agent, names, tok) -> int:
    return len(tok.encode(agent._format_tools_for_prompt(filter_to=names)))


def test_core_only_is_the_reduction_best_case(doc_agent_loader_on):
    """CORE-only (the always-on floor) renders well under half the baseline cost.

    Uses the loader-on skeleton so ``load_tools`` — a CORE member that ships
    every active turn — is in the registry and counted in the floor. Its own
    unfiltered render is the baseline, so this fails when CORE grows relative
    to the registry and stays quiet when the whole registry moves together.
    """
    tok = get_tokenizer()
    if tok is None:
        pytest.skip("tiktoken not installed — token proxy unavailable")
    unfiltered = measure_tool_prompt_cost(doc_agent_loader_on, tok=tok)
    core = sorted(DOC_CORE_TOOLS)
    native = _filtered_native_tokens(doc_agent_loader_on, core, tok)
    text = _filtered_text_tokens(doc_agent_loader_on, core, tok)
    assert native <= CORE_NATIVE_SHARE_MAX * unfiltered["native_tokens"], (
        f"CORE native tokens {native} exceeded "
        f"{CORE_NATIVE_SHARE_MAX:.0%} of the {unfiltered['native_tokens']}-token "
        "registry measured in this run — CORE is the always-on floor; keep its "
        "docstrings lean."
    )
    assert text <= CORE_TEXT_SHARE_MAX * unfiltered["text_tokens"], (
        f"CORE text tokens {text} exceeded {CORE_TEXT_SHARE_MAX:.0%} of the "
        f"{unfiltered['text_tokens']}-token registry measured in this run."
    )


def test_max_loaded_set_substantially_shrinks_native_cost(doc_agent):
    """A full ``max_tools=14`` loaded set still costs far less than the registry.

    Uses the 14 *largest* doc tools as a conservative worst case: if even those
    clear the ceiling, any real 14-tool selection does. Measured against the
    same run's unfiltered cost, so adding tools to the registry can only move
    this ratio in the direction it actually moved.
    """
    tok = get_tokenizer()
    if tok is None:
        pytest.skip("tiktoken not installed — token proxy unavailable")
    cost = measure_tool_prompt_cost(doc_agent, tok=tok)
    per = cost["per_tool"]
    largest14 = sorted(per, key=lambda n: per[n]["native_tokens"], reverse=True)[:14]
    native = _filtered_native_tokens(doc_agent, largest14, tok)
    assert native <= MAX_LOADED_NATIVE_SHARE_MAX * cost["native_tokens"], (
        f"worst-case 14-tool native tokens {native} exceeded "
        f"{MAX_LOADED_NATIVE_SHARE_MAX:.0%} of the {cost['native_tokens']}-token "
        "registry measured in this run — the loaded set is not shrinking as "
        "expected."
    )


def test_filtered_baselines_do_not_touch_unfiltered_pins(doc_agent):
    """Filtering must not change the unfiltered render (byte-identical guarantee)."""
    legacy_native = json.dumps(doc_agent._build_openai_tool_schemas())
    legacy_text = doc_agent._format_tools_for_prompt()
    assert (
        json.dumps(doc_agent._build_openai_tool_schemas(filter_to=None))
        == legacy_native
    )
    assert doc_agent._format_tools_for_prompt(filter_to=None) == legacy_text
