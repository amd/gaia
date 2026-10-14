# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Cloud models worth steering users to, with the measurement behind each pick.

The Python mirror of ``RecommendedModels`` in
``tui/internal/lemonade/cloud.go``, so the Agent UI ranks models exactly as the
TUI's provider screen does. ``tests/unit/chat/ui/test_providers_router.py`` parses the
Go list and fails when the two drift; edit both together.

Evidence is from the harness x model table published in amd/gaia#4335 (the
``everyday`` suite: 14 tasks, mean of 3 runs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Recommendation:
    id: str
    note: str
    evidence: str = ""


RECOMMENDED_MODELS: Tuple[Recommendation, ...] = (
    Recommendation(
        "fireworks.deepseek-v4p1-flash",
        "best overall, fastest",
        "14/14 tasks · quality 4.92/5 · $0.10 per 14-task run",
    ),
    Recommendation(
        "fireworks.glm-5p3-flash",
        "cheapest",
        "14/14 tasks · quality 4.89/5 · $0.09 per 14-task run",
    ),
    Recommendation("fireworks.deepseek-v4-pro-0813", "most truthful"),
)


def _rank_key(model_id: str) -> str:
    """``fireworks.accounts/fireworks/models/x`` and ``fireworks.x`` compare equal."""
    provider, sep, name = model_id.partition(".")
    if not sep:
        return model_id
    return f"{provider}.{name.rsplit('/', 1)[-1]}"


def rank(model_id: str) -> Optional[Tuple[int, Recommendation]]:
    """``(1-based rank, recommendation)`` for a recommended model, else None."""
    key = _rank_key(model_id)
    for i, rec in enumerate(RECOMMENDED_MODELS):
        if _rank_key(rec.id) == key:
            return i + 1, rec
    return None
