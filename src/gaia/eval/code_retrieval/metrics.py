# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Ranking metrics: recall@k and reciprocal rank, over files or symbols.

A retriever's output is a ranked list of keys (file paths, or
``(path, symbol)`` pairs) with duplicates already removed. Recall@k is the
share of the gold keys found in the top *k*; reciprocal rank is ``1/rank`` of
the first gold key, 0 when none was returned.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Iterable, Mapping, Sequence
from typing import Dict, List


def dedupe(keys: Iterable[Hashable]) -> List[Hashable]:
    """*keys* in first-seen order, each once."""
    seen = set()
    out = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def recall_at(ranked: Sequence[Hashable], gold: Sequence[Hashable], k: int) -> float:
    if not gold:
        raise ValueError("recall is undefined for a query with no gold keys")
    top = set(ranked[:k])
    return sum(1 for g in set(gold) if g in top) / len(set(gold))


def reciprocal_rank(ranked: Sequence[Hashable], gold: Sequence[Hashable]) -> float:
    wanted = set(gold)
    for rank, key in enumerate(ranked, start=1):
        if key in wanted:
            return 1.0 / rank
    return 0.0


def score_query(
    ranked: Sequence[Hashable], gold: Sequence[Hashable], ks: Sequence[int]
) -> Dict[str, float]:
    """``recall@k`` for each *k* and ``rr`` for one query."""
    out = {f"recall@{k}": recall_at(ranked, gold, k) for k in ks}
    out["rr"] = reciprocal_rank(ranked, gold)
    return out


def mean(rows: Sequence[Mapping[str, float]], key: str) -> float:
    if not rows:
        return math.nan
    return sum(r[key] for r in rows) / len(rows)


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile: the smallest value with *pct*% at or below it."""
    if not values:
        raise ValueError("percentile of no values")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]
