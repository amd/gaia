# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Will a local model fit and run on this machine, and with how large a window?

One rule, read off Lemonade's own ``/system-info``, so GAIA and the TUI agree
with the server that will actually load the model:

* **Memory** is the pool llama.cpp loads into. On an AMD APU (Strix Halo) that
  is the iGPU's dedicated VRAM plus its shared GTT memory — the same sum
  Lemonade's ``select_gpu_memory_pool`` uses. A discrete GPU contributes its
  VRAM; Apple Silicon its Metal working set. With no GPU, system RAM.
* **Disk** is the free space in Lemonade's model store.

A model fits when ``size * MEMORY_OVERHEAD_FACTOR + MEMORY_OVERHEAD_GB`` plus
its KV cache fits the memory pool, and its download fits the disk. The KV cache
grows linearly with the context window, so it is charged at the window the model
loads with (``ModelRequirement.kv_bytes_per_token``). :func:`largest_context`
turns the rule around: given the memory left after the weights, how large a
window the KV cache can take.

``tui/internal/lemonade/recommended_models.json`` carries the same two constants for
the Go picker; ``tests/unit/test_model_fit.py`` fails if they drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Multiplier on the weights' size for runtime buffers that scale with the model.
MEMORY_OVERHEAD_FACTOR = 1.05
#: Fixed headroom for compute buffers and a small KV cache.
MEMORY_OVERHEAD_GB = 1.0
#: ``MachineCapacity.memory_source`` when no GPU holds the model.
SYSTEM_RAM = "System RAM"
#: Share of the pool a window above a model's floor must leave free: the
#: desktop and driver, and llama.cpp compute buffers that grow with the window.
CONTEXT_HEADROOM_FRACTION = 0.10
#: Windows above the floor are whole multiples of this many tokens.
CONTEXT_STEP_TOKENS = 8192


class ModelFitError(RuntimeError):
    """Lemonade's system info could not be read as a machine capacity."""


@dataclass(frozen=True)
class MachineCapacity:
    """What this machine can hold, as Lemonade reports it."""

    memory_gb: float
    #: Where ``memory_gb`` came from, for messages ("AMD iGPU", "System RAM"...).
    memory_source: str
    #: Free space in the model store, or ``None`` when Lemonade did not say.
    disk_free_gb: Optional[float]

    @property
    def on_gpu(self) -> bool:
        """The memory pool is a GPU's, not system RAM on a CPU-only PC."""
        return self.memory_source != SYSTEM_RAM

    @property
    def shares_system_ram(self) -> bool:
        """A model on the CPU backend draws from this same pool.

        True for unified memory (an AMD iGPU, Apple Silicon) and for a CPU-only
        PC; False for a discrete GPU, whose VRAM the CPU backend never touches.
        """
        return self.memory_source in ("AMD iGPU", "Apple GPU", SYSTEM_RAM)


@dataclass(frozen=True)
class FitVerdict:
    fits: bool
    #: Why not, in words a user can act on. Empty when it fits.
    reason: str = ""


def check_server_supports(
    min_version: Optional[str], server_version: Optional[str]
) -> FitVerdict:
    """Whether a Lemonade server is new enough to load a model.

    An unknown server version cannot show support, so it does not pass: the
    cost of guessing wrong is a large download the server then cannot load.
    """
    if not min_version:
        return FitVerdict(True)
    from gaia.version import parse_version

    found, wanted = parse_version(server_version), parse_version(min_version)
    if found is not None and wanted is not None and found >= wanted:
        return FitVerdict(True)
    running = (
        f"this server is v{server_version}"
        if server_version
        else ("this server's version is unknown")
    )
    return FitVerdict(
        False,
        f"needs Lemonade v{min_version} or newer ({running}); "
        "upgrade it with `gaia init --force-reinstall`",
    )


def required_memory_gb(size_gb: float, kv_cache_gb: float = 0.0) -> float:
    """Memory a model of ``size_gb`` weights needs to load and run.

    ``kv_cache_gb`` is the model's KV cache at the window it loads with, when it
    is larger than the shared margin allows for.
    """
    return size_gb * MEMORY_OVERHEAD_FACTOR + MEMORY_OVERHEAD_GB + kv_cache_gb


def kv_cache_gb(kv_bytes_per_token: int, ctx_size: int) -> float:
    """KV cache, in GB, for a window of ``ctx_size`` tokens."""
    return kv_bytes_per_token * ctx_size / 1e9


def largest_context(
    *,
    size_gb: float,
    kv_bytes_per_token: int,
    min_ctx: int,
    max_ctx: int,
    capacity: MachineCapacity,
    reserve_gb: float = 0.0,
) -> int:
    """The largest window in ``[min_ctx, max_ctx]`` this machine can hold.

    The weights and ``reserve_gb`` (models that stay loaded beside this one)
    come off the pool first; the KV cache may grow into what is left, less
    ``CONTEXT_HEADROOM_FRACTION`` of the pool. Never below ``min_ctx``: that is
    the window the model is registered with, which the default-model fit
    check already judged.
    """
    usable = capacity.memory_gb * (1.0 - CONTEXT_HEADROOM_FRACTION)
    room_gb = usable - reserve_gb - required_memory_gb(size_gb)
    if kv_bytes_per_token <= 0 or room_gb <= 0:
        return min_ctx
    tokens = int(room_gb * 1e9 // kv_bytes_per_token)
    tokens -= tokens % CONTEXT_STEP_TOKENS
    return max(min_ctx, min(max_ctx, tokens))


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _physical_memory_gb(info: Dict[str, Any]) -> float:
    raw = str(info.get("Physical Memory") or "")
    match = re.search(r"([\d.]+)\s*GB", raw)
    return float(match.group(1)) if match else 0.0


#: GPU keys Lemonade has used in ``devices``, with a label and whether an entry
#: under the key is an integrated (unified-memory) GPU by definition.
_GPU_KEYS = (
    ("amd_gpu", "AMD", False),
    ("amd_igpu", "AMD", True),
    ("amd_dgpu", "AMD", False),
    ("nvidia_gpu", "NVIDIA", False),
    ("metal", "Apple", False),
)


def _gpu_entries(devices: Dict[str, Any]) -> List[Tuple[str, bool, Dict[str, Any]]]:
    """Available GPU entries as ``(vendor, integrated, entry)``.

    Lemonade reports some keys as lists and others as one object; an entry with
    no ``available`` flag counts as available, as ``lemonade_manager`` treats it.
    """
    out = []
    for key, vendor, integrated_key in _GPU_KEYS:
        value = devices.get(key)
        for entry in value if isinstance(value, list) else [value]:
            if isinstance(entry, dict) and entry.get("available", True):
                out.append(
                    (vendor, integrated_key or bool(entry.get("integrated")), entry)
                )
    return out


def _gpu_pool(devices: Dict[str, Any]) -> Optional[Tuple[float, str]]:
    """The memory llama.cpp loads into, or ``None`` when no GPU is reported.

    Raises :class:`ModelFitError` when a GPU is reported without its memory:
    judging such a PC on system RAM would call an 82 GB model a fit for a
    24 GB graphics card.
    """
    entries = _gpu_entries(devices)
    for vendor, integrated, gpu in entries:
        if vendor == "AMD" and integrated and _num(gpu.get("vram_gb")):
            return (
                _num(gpu.get("vram_gb")) + _num(gpu.get("virtual_mem_gb")),
                "AMD iGPU",
            )
    for vendor, _, gpu in entries:
        if _num(gpu.get("vram_gb")):
            return _num(gpu.get("vram_gb")), f"{vendor} GPU"
    if entries:
        names = ", ".join(str(g.get("name") or vendor) for vendor, _, g in entries)
        raise ModelFitError(
            f"Lemonade reports a GPU ({names}) but not its memory, so GAIA cannot "
            "tell which models fit. Update Lemonade (`gaia init`) and retry."
        )
    return None


def capacity_from_system_info(info: Dict[str, Any]) -> MachineCapacity:
    """Read a :class:`MachineCapacity` from Lemonade's ``/system-info`` body.

    Raises :class:`ModelFitError` when the body names no memory at all — a
    capacity of zero would make every model look too big, and a guess would
    let one through that cannot load.
    """
    if not isinstance(info, dict):
        raise ModelFitError("Lemonade /system-info did not return an object")
    pool = _gpu_pool(info.get("devices") or {})
    if pool is None:
        ram = _physical_memory_gb(info)
        if ram <= 0:
            raise ModelFitError(
                "Lemonade /system-info reported neither GPU memory nor "
                "'Physical Memory', so GAIA cannot tell which models fit. "
                "Update Lemonade (`gaia init`) and retry."
            )
        pool = (ram, SYSTEM_RAM)
    storage = info.get("model_storage") or {}
    free = storage.get("free_bytes")
    disk = float(free) / 1e9 if isinstance(free, (int, float)) else None
    return MachineCapacity(memory_gb=pool[0], memory_source=pool[1], disk_free_gb=disk)


def check_fit(
    size_gb: float, capacity: MachineCapacity, kv_cache_gb: float = 0.0
) -> FitVerdict:
    """Whether a local model of ``size_gb`` fits ``capacity``, and if not why."""
    need = required_memory_gb(size_gb, kv_cache_gb)
    if need > capacity.memory_gb:
        return FitVerdict(
            False,
            f"needs ~{need:.0f} GB of memory; this PC has "
            f"{capacity.memory_gb:.0f} GB ({capacity.memory_source})",
        )
    if capacity.disk_free_gb is not None and size_gb > capacity.disk_free_gb:
        return FitVerdict(
            False,
            f"needs {size_gb:.0f} GB of disk; {capacity.disk_free_gb:.0f} GB free",
        )
    return FitVerdict(True)


def pick_default_model(candidates: Iterable[tuple], capacity: MachineCapacity) -> tuple:
    """First ``(model_id, size_gb[, kv_cache_gb])`` candidate that fits, with the
    skipped reasons.

    ``candidates`` runs largest-first and must end with the floor model, which
    is returned even when it does not fit: a machine too small for the floor
    already failed that way before this rule existed, and refusing to pick
    anything would leave ``gaia init`` with no chat model at all.

    Returns ``(model_id, skipped)`` where ``skipped`` lists ``(model_id,
    reason)`` for every larger candidate passed over.
    """
    items = list(candidates)
    if not items:
        raise ValueError("pick_default_model needs at least one candidate")
    skipped: List[Tuple[str, str]] = []
    for model_id, size_gb, *kv in items[:-1]:
        verdict = check_fit(size_gb, capacity, kv[0] if kv else 0.0)
        if verdict.fits:
            return model_id, skipped
        skipped.append((model_id, verdict.reason))
    return items[-1][0], skipped
