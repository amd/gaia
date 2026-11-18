# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a ``delegate_task`` result shows a person: the summary and each helper.

The tool's result also carries ``advice`` and ``error`` text written for the
model, token counts and clipped-answer handles. None of that reaches a screen,
so every UI surface (the in-process stream, the relay and saved steps) reads
the result through :func:`helpers_view`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

DELEGATE_TOOL = "delegate_task"
#: ``result_data["type"]`` of the view, beside ``file_list`` and ``search_results``.
HELPERS_RESULT_TYPE = "helpers"
#: Most helpers one call runs (``MAX_HELPER_TASKS`` in delegate_tools.py).
MAX_SHOWN_HELPERS = 16

_RUN_STATUSES = frozenset({"success", "partial", "error"})
_HELPER_STATUSES = frozenset({"ok", "partial", "failed"})


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _helper(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict) or raw.get("status") not in _HELPER_STATUSES:
        return None
    if not isinstance(raw.get("id"), int) or isinstance(raw.get("id"), bool):
        return None
    item: Dict[str, Any] = {
        "id": raw["id"],
        "task": raw["task"] if isinstance(raw.get("task"), str) else "",
        "status": raw["status"],
    }
    for key in ("reason", "conclusion"):
        if isinstance(raw.get(key), str):
            item[key] = raw[key]
    if isinstance(raw.get("steps"), int) and not isinstance(raw.get("steps"), bool):
        item["steps"] = raw["steps"]
    if _number(raw.get("wall_s")):
        item["wall_s"] = raw["wall_s"]
    return item


def helpers_view(data: Any) -> Optional[Dict[str, Any]]:
    """The part of a ``delegate_task`` result a person may see.

    ``None`` when *data* is not a run of helpers (a refusal before anything
    ran has no ``helpers`` list), so the caller keeps its generic summary.
    """
    if not isinstance(data, dict) or not isinstance(data.get("helpers"), list):
        return None
    status = data.get("status")
    summary = data.get("summary")
    if status not in _RUN_STATUSES or not isinstance(summary, str):
        return None
    helpers: List[Dict[str, Any]] = [
        item for item in map(_helper, data["helpers"]) if item is not None
    ]
    view: Dict[str, Any] = {
        "type": HELPERS_RESULT_TYPE,
        "status": status,
        "success": status != "error",
        "summary": summary,
        "helpers": helpers[:MAX_SHOWN_HELPERS],
    }
    if len(helpers) > MAX_SHOWN_HELPERS:
        view["hidden"] = len(helpers) - MAX_SHOWN_HELPERS
    if isinstance(data.get("width"), int) and not isinstance(data.get("width"), bool):
        view["width"] = data["width"]
    if isinstance(data.get("width_note"), str) and data["width_note"]:
        view["width_note"] = data["width_note"]
    return view
