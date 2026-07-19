# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression guard for Lemonade model residency.

GAIA assumes a chat model and an embedder stay co-resident: ``RAGSDK`` scopes its
embedder unload precisely so a global ``/unload`` can't evict the chat model
(#1544), and the model consolidation onto a single Gemma build assumes agent
switching never triggers a reload.

Both assumptions rest on how Lemonade actually accounts for capacity. Observed on
Lemonade 10.10.0:

    "max_models":    {"llm": 1, "embedding": 1, "reranking": 1, ...}
    "pinned_models": {"llm": 0, "embedding": 0, ...}

``max_models`` is **per model type**, so the default of 1 already keeps one LLM and
one embedder resident at once — an LLM+embedder pair needs no capacity tuning, and
setting ``max_loaded_models=2`` would license a second *LLM*, not the pair. Lemonade
also runs a separate ``llama-server`` subprocess per model on its own port, so there
is no single global "slot" to raise.

These tests pin that behavior down so the question stops being re-litigated from
assumption, and so a Lemonade upgrade that changes the accounting fails loudly here
rather than silently costing a ~100s cold reload on every turn.
"""

import os

import pytest
import requests

pytestmark = pytest.mark.integration

LEMONADE_URL = os.getenv("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")

# Types GAIA depends on holding a model simultaneously: the chat/agent LLM and
# the RAG embedder.
_LLM = "llm"
_EMBEDDING = "embedding"


def _health() -> dict:
    response = requests.get(f"{LEMONADE_URL}/health", timeout=10)
    response.raise_for_status()
    return response.json()


def _loaded_by_type(health: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for entry in health.get("all_models_loaded", []):
        grouped.setdefault(entry.get("type", "unknown"), []).append(entry)
    return grouped


def test_health_reports_per_type_capacity(require_lemonade):
    """``max_models`` is keyed by model TYPE, not one global slot count.

    This is the fact the whole co-residency assumption rests on. If a Lemonade
    release flattens it into a single number, GAIA's "LLM and embedder both stay
    loaded" expectation silently stops holding.
    """
    health = _health()

    max_models = health.get("max_models")
    assert isinstance(max_models, dict), (
        "Lemonade /health no longer reports per-type 'max_models'. GAIA assumes "
        "capacity is accounted per model type (an LLM and an embedder can be "
        f"resident together). Got: {max_models!r}"
    )
    for model_type in (_LLM, _EMBEDDING):
        assert model_type in max_models, (
            f"'{model_type}' missing from /health max_models={max_models!r}; GAIA "
            "needs this type to hold a model independently of the others."
        )
        assert max_models[model_type] >= 1, (
            f"Lemonade reports capacity {max_models[model_type]} for "
            f"'{model_type}' — GAIA cannot keep a {model_type} resident."
        )


def test_llm_and_embedder_are_co_resident(require_lemonade):
    """An LLM and an embedder hold slots simultaneously at DEFAULT config.

    GAIA never sets ``max_loaded_models`` (0 occurrences in src/ and hub/). If this
    fails, the per-type accounting has changed and RAG indexing will evict the chat
    model on every retrieval — the ~100s cold reload #1544 was fixed to avoid.
    """
    health = _health()
    loaded = _loaded_by_type(health)

    if not loaded.get(_LLM) or not loaded.get(_EMBEDDING):
        counts = {k: len(v) for k, v in loaded.items()}
        pytest.skip(
            "Needs one LLM and one embedding model loaded to observe co-residency; "
            f"currently loaded: {counts}. "
            "Load both (e.g. run a RAG query) and re-run."
        )

    llm = loaded[_LLM][0]
    embedder = loaded[_EMBEDDING][0]

    assert llm["loaded"] and embedder["loaded"], (
        "An LLM and an embedder are both present in /health but not both marked "
        f"loaded: llm={llm.get('model_name')} embedder={embedder.get('model_name')}"
    )
    # Distinct subprocesses on distinct ports — there is no shared global slot.
    assert llm.get("pid") != embedder.get("pid"), (
        "LLM and embedder report the same pid; Lemonade is expected to run one "
        "llama-server subprocess per model."
    )


def test_co_residency_needs_no_pinning(require_lemonade):
    """Co-residency holds WITHOUT pinning, so GAIA needn't manage the pinned flag.

    Lemonade exposes a ``pinned`` parameter that exempts a model from LRU eviction.
    GAIA does not use it. This records that it isn't required — if co-residency ever
    starts depending on pinning, this test fails and tells us to reach for that knob
    rather than a capacity setting.
    """
    health = _health()
    loaded = _loaded_by_type(health)

    if not loaded.get(_LLM) or not loaded.get(_EMBEDDING):
        pytest.skip("Needs an LLM and an embedder loaded; see co-residency test.")

    for model_type in (_LLM, _EMBEDDING):
        entry = loaded[model_type][0]
        assert entry.get("pinned") is False, (
            f"{model_type} '{entry.get('model_name')}' is pinned. GAIA never sets "
            "pinned=true, so something else did — co-residency here may not "
            "reflect default behavior."
        )


@pytest.mark.real_model
def test_swapping_the_llm_does_not_evict_the_embedder(require_lemonade):
    """Swapping the resident LLM must leave the embedder untouched.

    This is the load-bearing one. Switching agents evicts and cold-reloads the LLM
    (~8s observed); GAIA's fix is to put every agent on one model id. That fix only
    helps if the LLM slot is independent of the embedder slot — otherwise each swap
    would churn the embedder too and RAG would pay the cost anyway.

    The embedder's **pid** is the assertion, not just its presence: Lemonade runs one
    llama-server subprocess per model, so a stable pid proves it was never restarted.

    Marked ``real_model``: mutates server state (~8s per load) and must never run
    concurrently with an eval (CLAUDE.md — concurrent runs race-evict each other).
    """
    before = _loaded_by_type(_health())
    if not before.get(_LLM) or not before.get(_EMBEDDING):
        pytest.skip("Needs an LLM and an embedder loaded to observe eviction.")

    embedder_before = before[_EMBEDDING][0]
    llm_before = before[_LLM][0]

    # Pick a DIFFERENT llm so the load genuinely swaps the slot. Asserting on a
    # same-model reload is worthless: _ensure_model_loaded early-returns when the
    # resident ctx already satisfies GAIA's expectation, so the test would pass
    # without any load happening at all.
    catalog = requests.get(f"{LEMONADE_URL}/models", timeout=30).json()
    alternatives = [
        m["id"]
        for m in catalog.get("data", [])
        if m.get("downloaded")
        and m["id"] != llm_before["model_name"]
        and "embed" not in m["id"].lower()
    ]
    if not alternatives:
        pytest.skip(
            "Needs a second downloaded LLM to force a slot swap; only "
            f"'{llm_before['model_name']}' is available."
        )

    response = requests.post(
        f"{LEMONADE_URL}/load",
        json={"model_name": alternatives[0], "ctx_size": 32768},
        timeout=900,
    )
    response.raise_for_status()

    after = _loaded_by_type(_health())
    assert after.get(_LLM), "No LLM resident after the swap."

    # Guard against a vacuous pass: if the LLM slot did not actually turn over,
    # this test observed nothing and must not report success.
    assert after[_LLM][0]["pid"] != llm_before["pid"], (
        "LLM pid unchanged — the swap did not occur, so embedder residency was "
        "never actually exercised."
    )

    embedders_after = {e["model_name"]: e["pid"] for e in after.get(_EMBEDDING, [])}
    assert embedder_before["model_name"] in embedders_after, (
        f"Swapping the LLM evicted embedder '{embedder_before['model_name']}'. "
        "Consolidating agents onto one model id is then insufficient — every swap "
        f"churns the embedder too. Embedders still loaded: {embedders_after or '{}'}"
    )
    assert embedders_after[embedder_before["model_name"]] == embedder_before["pid"], (
        f"Embedder '{embedder_before['model_name']}' was restarted during the LLM "
        f"swap (pid {embedder_before['pid']} -> "
        f"{embedders_after[embedder_before['model_name']]}), so its slot is not "
        "independent of the LLM slot."
    )
