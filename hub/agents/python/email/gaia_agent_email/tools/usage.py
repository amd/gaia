# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pure LLM usage aggregation for triage (#1891).

Lifted out of ``api_routes._aggregate_usage`` so both the REST triage path
(``EmailTriageService``) and the tool-call triage path
(``EmailTriageAgent._triage_all_backends``) share ONE aggregation
implementation instead of two copies drifting apart.

Per-call entries can arrive in either shape a chat backend may produce:

- the Lemonade chat-completion response's ``usage`` shape (preferred,
  #1891): ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` /
  ``tokens_per_second``
- the legacy ``/stats``-polled shape (#1277/#1278): ``input_tokens`` /
  ``output_tokens`` / ``tokens_per_second``

``aggregate_usage_stats`` normalizes both into ONE output shape, in one
place, so callers never need to know which mechanism produced a given entry.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def aggregate_usage_stats(call_stats: List[Any]) -> Optional[Dict[str, Any]]:
    """Sum per-call usage/stats entries into one aggregate dict.

    ``prompt_tokens`` = Σ input tokens; ``total_tokens`` = Σ(input + output);
    ``tokens_per_second`` = Σ output / Σ decode_time, where each call's decode
    time is output_tokens / tokens_per_second (so the aggregate is total
    output tokens over total decode time, not a naive TPS average). Calls
    with no usable TPS (missing or <= 0) don't contribute to the decode-time
    denominator, so they can't inflate the aggregate.

    Entries that aren't a dict (e.g. a stray ``MagicMock()`` from an
    under-configured test double, or ``None``) are skipped rather than
    raising — malformed per-call data must never crash aggregation.

    Returns ``None`` only when ``call_stats`` itself is empty (no LLM call
    was made — the heuristic-only path). A non-empty list containing only
    malformed/unusable entries still returns the all-zero aggregate dict,
    never ``None`` — ``None`` specifically means "no LLM call happened",
    not "the LLM call's stats were unusable".
    """
    if not call_stats:
        return None
    total_input = 0
    total_output = 0
    decode_output = 0  # output only from calls with a usable TPS (>0)
    total_decode_time = 0.0
    for s in call_stats:
        if not isinstance(s, dict):
            continue
        inp = int(s.get("input_tokens") or s.get("prompt_tokens") or 0)
        out = int(s.get("output_tokens") or s.get("completion_tokens") or 0)
        tps = float(s.get("tokens_per_second") or 0.0)
        total_input += inp
        total_output += out
        if out and tps > 0:
            decode_output += out
            total_decode_time += out / tps
    # Numerator excludes output from tps==0 calls so they can't inflate the
    # aggregate (they add nothing to the decode-time denominator).
    agg_tps = decode_output / total_decode_time if total_decode_time > 0 else 0.0
    return {
        "prompt_tokens": total_input,
        "completion_tokens": total_output,
        "total_tokens": total_input + total_output,
        "tokens_per_second": agg_tps,
    }
