# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Bound plain-text tool evidence while retaining its beginning and end."""

import json


def elide_text(text: str, max_chars: int) -> dict:
    """Return head/tail evidence within a serialized JSON character budget.

    Include JSON escaping in the budget so quotes and control characters cannot
    expand a nominally bounded excerpt past the downstream prompt limit.
    """

    def render(keep: int) -> dict:
        head_size = (keep + 1) // 2
        tail_size = keep // 2
        return {
            "truncated": True,
            "original_chars": len(text),
            "omitted_chars": len(text) - keep,
            "head": text[:head_size],
            "tail": text[-tail_size:] if tail_size else "",
        }

    def cost(value: dict) -> int:
        return len(json.dumps(value, ensure_ascii=False))

    if cost(render(0)) > max_chars:
        raise ValueError("Tool output budget is too small for truncation metadata.")
    low, high = 0, min(len(text), max_chars)
    while low < high:
        keep = (low + high + 1) // 2
        if cost(render(keep)) <= max_chars:
            low = keep
        else:
            high = keep - 1
    return render(low)
