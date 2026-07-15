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

from gaia_agent_email.context_budget import envelope_budget_tokens, estimate_tokens


def _estimate_envelope_tokens(envelope: Dict[str, Any]) -> int:
    """Token estimate for ``envelope`` serialized exactly as the tool returns it.

    ``json.dumps(..., default=str)`` matches ``_triage_all_backends``'s own
    serialization contract, so the estimate reflects what the agent actually
    re-reads rather than an idealized shape.
    """
    return estimate_tokens(json.dumps(envelope, default=str))


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
    condensed: Dict[str, Any] = {k: v for k, v in result.items() if k != "results"}
    condensed["results_condensed"] = True
    condensed["results_total"] = len(full_results)

    # Greedily keep exemplars (original order) while the SERIALIZED envelope —
    # including the growing omission count — stays under budget. Measuring the
    # full envelope each step keeps ``grouped``/``usage`` overhead in the math so
    # we never claim to fit while actually overflowing.
    kept: List[Dict[str, Any]] = []
    for item in full_results:
        trial = dict(condensed)
        trial["results"] = kept + [item]
        trial["results_omitted"] = len(full_results) - len(kept) - 1
        if _estimate_envelope_tokens(trial) > budget_tokens:
            break
        kept.append(item)

    omitted = len(full_results) - len(kept)
    condensed["results"] = kept
    condensed["results_omitted"] = omitted
    condensed["note"] = (
        f"[omitted {omitted} verbatim verdicts to fit the {budget_tokens}-token "
        f"context budget — {len(kept)} of {len(full_results)} shown; see 'grouped' "
        f"for the full id-to-category map]"
    )
    return condensed
