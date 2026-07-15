# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Budget-aware condensing for the bulk-triage result envelope (#2087).

The bulk-triage agent-loop path classifies each email cheaply (the per-message
classify prompt fits the pinned 16K ctx target), but the tool returns EVERY
verdict as one JSON blob. On the agent's next turn that entire envelope is
re-read alongside the system prompt and full tool schema — for a large batch
the verbatim verdict list overflows ``CONTEXT_TARGET_TOKENS`` (measured: 60
emails 400 at 16K, 300 emails fail even at the 32K max).

This module owns the "does the result envelope fit" gate, mirroring
``thread_fold.py``'s role for long threads. It keeps the compact id-to-category
map (``grouped``) verbatim and drops only trailing verbatim verdicts once the
serialized envelope would exceed the budget, replacing them with an explicit
``[omitted N ...]`` marker (the #1889 convention). Below budget it is a no-op:
the full result passes through unchanged, so small batches are untouched.

Deterministic and bounded by construction: exemplars are kept in original
order, omissions are explicit (never a silent mid-list clip), and
``usage`` / ``llm_classified_count`` (#1891) pass through verbatim so token
accounting still sums across the whole batch — condensing changes only the
FEEDBACK the agent re-reads, never the classification work already done.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from gaia_agent_email.context_budget import envelope_budget_tokens, estimate_tokens_json


def _estimate_envelope_tokens(envelope: Dict[str, Any]) -> int:
    """Token estimate for ``envelope`` serialized exactly as the tool returns it.

    ``json.dumps(..., default=str)`` matches ``_triage_all_backends``'s own
    serialization contract, so the estimate reflects what the agent actually
    re-reads rather than an idealized shape. Uses the JSON-calibrated
    estimator — the prose ratio under-counts serialized JSON ~2x, which made
    the condenser no-op on batches that then 400'd (#2087 CI run).
    """
    return estimate_tokens_json(json.dumps(envelope, default=str))


def condense_triage_result(
    result: Dict[str, Any], *, budget_tokens: int | None = None
) -> Dict[str, Any]:
    """Bound the bulk-triage result envelope to the agent-loop ctx budget.

    Returns ``result`` unchanged when its serialized form already fits
    ``budget_tokens`` (defaults to ``context_budget.envelope_budget_tokens()``).
    Otherwise returns a condensed copy that keeps every key EXCEPT a trimmed
    ``results`` list: exemplars are kept in original order for as long as the
    whole envelope stays under budget, and the remainder is reported via an
    explicit ``results_omitted`` count plus a ``note`` marker. ``grouped`` is
    preserved verbatim because it already carries the complete id-to-category
    map compactly, so no verdict is truly lost — only its verbose per-message
    fields (subject / from / rationale).
    """
    if budget_tokens is None:
        budget_tokens = envelope_budget_tokens()

    if _estimate_envelope_tokens(result) <= budget_tokens:
        return result

    full_results: List[Dict[str, Any]] = list(result.get("results", []))

    # Base carries everything except the verbatim verdict list; ``grouped`` stays.
    base: Dict[str, Any] = {k: v for k, v in result.items() if k != "results"}
    base["results_condensed"] = True
    base["results_total"] = len(full_results)

    def _build(kept: List[Dict[str, Any]]) -> Dict[str, Any]:
        # The candidate envelope EXACTLY as it would be returned — note
        # included — so the budget check measures what the agent re-reads,
        # never a lighter draft of it.
        omitted = len(full_results) - len(kept)
        out = dict(base)
        out["results"] = kept
        out["results_omitted"] = omitted
        out["note"] = (
            f"[omitted {omitted} verbatim verdicts to fit the {budget_tokens}-token "
            f"context budget — {len(kept)} of {len(full_results)} shown; see 'grouped' "
            f"for the full id-to-category map]"
        )
        return out

    # Greedily keep exemplars (original order) while the fully-built envelope
    # — grouped/usage overhead, omission count, and note all included — stays
    # under budget.
    kept: List[Dict[str, Any]] = []
    for item in full_results:
        if _estimate_envelope_tokens(_build(kept + [item])) > budget_tokens:
            break
        kept.append(item)

    return _build(kept)
