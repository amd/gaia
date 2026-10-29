# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Where a local model runs, and whether this PC can run it at all.

A model's device is not a setting: each Lemonade recipe targets one. llama.cpp
(GGUF) runs on the GPU through Vulkan, ROCm or Metal, or on the CPU when that
is the only backend; FastFlowLM and Ryzen AI builds run on the NPU. Before a
model loads, its recipe says where it *should* run. Once it is loaded,
Lemonade's ``/health`` says where it *does* run, and only that is reported as
fact — the two can differ, most often a GGUF model that fell back to the CPU.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from gaia.llm.model_server_info import (
    default_llamacpp_backend,
    describe_loaded_model,
)

#: NPU recipes, and the backend name each shows as.
NPU_RECIPES = {"flm": "flm", "ryzenai-llm": "ryzenai"}

_DEVICE_LABELS = {"gpu": "GPU", "npu": "NPU", "cpu": "CPU"}
_BACKEND_LABELS = {
    "vulkan": "Vulkan",
    "rocm": "ROCm",
    "metal": "Metal",
    "cpu": "CPU",
    "flm": "FLM",
    "ryzenai": "Ryzen AI",
    "npu": "NPU",
}

#: ``/system-info`` backend states a load can use (installed now, or fetched on first use).
_USABLE_STATES = frozenset({"installed", "installable", "update_required"})


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_device(raw: Any) -> Optional[str]:
    """``gpu``, ``npu``, ``cpu`` or ``npu+gpu`` from Lemonade's device string."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    found: List[str] = []
    for token in re.split(r"[^a-z]+", raw.lower()):
        name = "gpu" if token in ("gpu", "igpu", "dgpu") else token
        if name in _DEVICE_LABELS and name not in found:
            found.append(name)
    if not found:
        return raw.strip().lower()
    order = ["npu", "gpu", "cpu"]
    return "+".join(sorted(found, key=order.index))


def placement_label(device: Optional[str], backend: Optional[str]) -> Optional[str]:
    """ "GPU · Vulkan", "NPU · FLM", "CPU" — or None when the device is unknown."""
    if not device:
        return None
    dev = " + ".join(_DEVICE_LABELS.get(d, d.upper()) for d in device.split("+"))
    if not backend or backend in (device, "npu"):
        return dev
    return f"{dev} · {_BACKEND_LABELS.get(backend, backend)}"


def expected_placement(
    recipe: Optional[str],
    system_info: Any,
    preferred_backend: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """``(device, backend)`` a model of *recipe* targets before it loads.

    *preferred_backend* is the saved llama.cpp backend choice (None = Auto,
    which is Lemonade's ``default_backend``).
    """
    if recipe == "llamacpp":
        backend = preferred_backend or default_llamacpp_backend(system_info)
        if backend is None:
            return "gpu", None
        return ("cpu" if backend == "cpu" else "gpu"), backend
    if recipe in NPU_RECIPES:
        return "npu", NPU_RECIPES[recipe]
    return None, None


def describe_placement(
    recipe: Optional[str],
    loaded_entry: Optional[Dict[str, Any]],
    system_info: Any,
    preferred_backend: Optional[str] = None,
) -> Dict[str, Any]:
    """Device and backend for one model, with where the answer came from.

    ``source`` is ``reported`` (Lemonade's ``/health`` for the loaded model) or
    ``expected`` (its recipe, before it loads). ``warning`` says so plainly
    when the reported device is not the expected one.
    """
    exp_device, exp_backend = expected_placement(recipe, system_info, preferred_backend)
    if loaded_entry is not None:
        loaded = describe_loaded_model(loaded_entry, system_info)
        device = normalize_device(loaded_entry.get("device"))
        backend = loaded.get("backend")
        if recipe in NPU_RECIPES:
            backend = NPU_RECIPES[recipe]
        if device is None and backend == "cpu":
            device = "cpu"
        warning = None
        if device and exp_device and device != exp_device:
            if device == "cpu":
                warning = (
                    f"Running on the CPU instead of the {_DEVICE_LABELS.get(exp_device, exp_device)}, "
                    "so replies will be slow. Check the GPU driver, or pick another "
                    "backend in Settings › Advanced › Model server."
                )
            else:
                warning = (
                    f"Lemonade reports {placement_label(device, backend)}, not the "
                    f"expected {placement_label(exp_device, exp_backend)}."
                )
        return {
            "device": device,
            "backend": backend,
            "label": placement_label(device, backend),
            "source": "reported" if device else None,
            "warning": warning,
        }
    return {
        "device": exp_device,
        "backend": exp_backend,
        "label": placement_label(exp_device, exp_backend),
        "source": "expected" if exp_device else None,
        "warning": None,
    }


def recipe_unavailable_reason(recipe: Optional[str], system_info: Any) -> Optional[str]:
    """Why no backend for *recipe* runs on this PC, or None when one can.

    Unknown means can run: an older Lemonade that does not report a recipe's
    backends offers no grounds to hide its models.
    """
    if not recipe:
        return None
    info = _as_dict(system_info)
    unavailable = info.get("unavailable_recipes") or []
    if recipe in unavailable:
        return f"this Lemonade cannot run {recipe} models on this PC"
    entry = _as_dict(_as_dict(info.get("recipes")).get(recipe))
    backends = _as_dict(entry.get("backends"))
    if not backends:
        return None
    states = [_as_dict(b) for b in backends.values()]
    if any(b.get("state") in _USABLE_STATES for b in states):
        return None
    detail = "; ".join(
        sorted({str(b.get("message")) for b in states if b.get("message")})
    )
    if recipe in NPU_RECIPES:
        why = "needs an AMD Ryzen AI NPU and its driver"
    else:
        name = entry.get("display_name") or recipe
        why = f"no {name} backend runs on this PC"
    return f"{why} ({detail})" if detail else why


def loaded_entries(health: Any) -> Dict[str, Dict[str, Any]]:
    """``all_models_loaded`` keyed by lower-cased model name."""
    out: Dict[str, Dict[str, Any]] = {}
    for entry in _as_dict(health).get("all_models_loaded") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("model_name") or entry.get("id") or "")
        if name:
            out[name.lower()] = entry
    return out


def find_loaded(
    entries: Dict[str, Dict[str, Any]], model_id: str
) -> Optional[Dict[str, Any]]:
    """The loaded entry for *model_id*, tolerating Lemonade's ``user.`` prefix."""
    key = model_id.lower()
    bare = key[5:] if key.startswith("user.") else key
    return entries.get(key) or entries.get(bare)
