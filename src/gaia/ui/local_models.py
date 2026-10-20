# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What the Agent UI says about each local model, read off Lemonade.

For every local chat model: its friendly name, whether it is downloaded or
loaded, where it runs (reported for a loaded model, expected otherwise),
whether this PC can run it and why not, its download size, and the speed
measured on this PC. Pure functions of Lemonade's ``/models``, ``/health`` and
``/system-info`` bodies, so tests drive them with recorded payloads.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Optional

from gaia.llm.model_device import (
    describe_placement,
    find_loaded,
    loaded_entries,
    recipe_unavailable_reason,
)

#: Turns averaged for a model's measured speed.
SPEED_SAMPLES = 20

LOADED, DOWNLOADED, NOT_DOWNLOADED = "loaded", "downloaded", "not_downloaded"


def friendly_name(model_id: str) -> str:
    """ "Qwen3.6 35B A3B" for ``Qwen3.6-35B-A3B-GGUF``; MODELS' name when it has one."""
    from gaia.llm.lemonade_client import find_model_requirement

    req = find_model_requirement(model_id)
    if req is not None and req.display_name:
        # MODELS names carry a capability note, "(Multimodal)"; the list shows that elsewhere.
        return re.sub(r"\s*\([^)]*\)\s*$", "", req.display_name)
    name = model_id[5:] if model_id.startswith("user.") else model_id
    words = []
    for word in re.split(r"[-_\s]+", name):
        low = word.lower()
        # Format and tuning tags, and the date stamp a user-registered build carries.
        if low in _NAME_NOISE or re.fullmatch(r"\d{8}", word):
            continue
        # Sizes read as sizes: 27b -> 27B, e2b -> E2B, a3b -> A3B.
        words.append(
            word.upper() if re.fullmatch(r"[a-z]?\d+(\.\d+)?[bkm]", low) else word
        )
    if not words:
        return model_id
    words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


#: Words in a model id that say how it is packaged or tuned, not what it is.
_NAME_NOISE = {"gguf", "flm", "onnx", "oga", "it", "instruct"}


def measured_speeds(stats: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """Median tokens/s per model over its newest ``SPEED_SAMPLES`` turns."""
    samples: Dict[str, List[float]] = {}
    for s in stats:
        model, tps = s.get("measured_model"), s.get("tokens_per_second")
        if not isinstance(model, str) or not isinstance(tps, (int, float)) or tps <= 0:
            continue
        bucket = samples.setdefault(model, [])
        if len(bucket) < SPEED_SAMPLES:
            bucket.append(float(tps))
    return {m: round(statistics.median(v), 1) for m, v in samples.items()}


def _size_gb(model: Dict[str, Any]) -> Optional[float]:
    from gaia.llm.lemonade_client import find_model_requirement

    size = model.get("size_gb")
    if isinstance(size, (int, float)) and size > 0:
        return float(size)
    req = find_model_requirement(model["id"])
    return req.size_gb if req is not None and req.size_gb else None


def _fit(model: Dict[str, Any], size_gb: Optional[float], capacity) -> Optional[str]:
    """Why the model does not fit this PC's memory, '' when it does, None if unknown."""
    from gaia.llm.lemonade_client import find_model_requirement
    from gaia.llm.model_fit import check_fit, kv_cache_gb

    if capacity is None or size_gb is None:
        return None
    req = find_model_requirement(model["id"])
    kv = kv_cache_gb(req.kv_bytes_per_token, req.min_ctx_size) if req else 0.0
    # A downloaded model's disk is already spent.
    cap = replace(capacity, disk_free_gb=None) if model.get("downloaded") else capacity
    verdict = check_fit(size_gb, cap, kv)
    return "" if verdict.fits else verdict.reason


def annotate_local_models(
    models: List[Dict[str, Any]],
    *,
    health: Optional[Dict[str, Any]],
    system_info: Optional[Dict[str, Any]],
    capacity,
    preferred_backend: Optional[str],
    speeds: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Add state, placement, fit and speed to each local model from ``_list_models``.

    *health* None means the server did not answer: nothing is reported loaded.
    *capacity* None means memory is unknown: no fit verdict, nothing hidden for it.
    """
    loaded = loaded_entries(health or {})
    out = []
    for m in models:
        entry = find_loaded(loaded, m["id"])
        size = _size_gb(m)
        fit_reason = _fit(m, size, capacity)
        recipe_reason = recipe_unavailable_reason(m.get("recipe"), system_info)
        # A model that is running proves it runs, whatever the estimate says.
        unavailable = (
            None if entry is not None else (recipe_reason or fit_reason or None)
        )
        out.append(
            {
                **m,
                "display_name": friendly_name(m["id"]),
                "state": (
                    LOADED
                    if entry is not None
                    else DOWNLOADED if m.get("downloaded") else NOT_DOWNLOADED
                ),
                "placement": describe_placement(
                    m.get("recipe"), entry, system_info, preferred_backend
                ),
                "size_gb": round(size, 1) if size else None,
                "fits": None if fit_reason is None else fit_reason == "",
                "available": unavailable is None,
                "unavailable_reason": unavailable,
                "tokens_per_second": speeds.get(m["id"]),
            }
        )
    return out
