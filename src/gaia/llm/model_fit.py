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

Total memory says which models this machine can ever run. Before a load, what
is *free right now* decides too: other apps, and models another Lemonade holds,
take memory the total does not show. :func:`free_memory` reads it from the OS,
:func:`context_for_free_memory` shrinks a window to it (never below
``MIN_CONTEXT_TOKENS``), and :func:`check_free_memory` refuses a load that would
run the machine out of memory.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
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
#: The smallest window a chat model is shrunk to when free memory is short. The
#: flagship's system prompt and tools alone run ~20K tokens, so a smaller
#: window cannot hold a first turn; below this the load is refused instead.
MIN_CONTEXT_TOKENS = 32768
#: Free memory a shrunk window leaves for GAIA itself (UI, backend, indexes).
FREE_MEMORY_HEADROOM_GB = 1.0
#: Below this much system RAM, the Agent UI loads the chat model when the first
#: message is sent rather than at startup, so setup never races a preload.
EAGER_LOAD_MIN_RAM_GB = 24.0


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


#: Windows' display-adapter class; each adapter's subkey records its memory.
_DISPLAY_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)


def _windows_adapter_memory_gb(name: Any) -> float:
    """The dedicated memory Windows records for the adapter *name*, or 0.

    Lemonade reports no memory for an integrated AMD GPU on Windows; the
    driver's ``HardwareInformation.qwMemorySize`` is that GPU's BIOS carve-out,
    the heap llama.cpp's Vulkan backend loads into.
    """
    if sys.platform != "win32" or not isinstance(name, str) or not name:
        return 0.0
    import winreg  # pylint: disable=import-error

    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS_KEY)
    except OSError:
        return 0.0
    with root:
        index = 0
        while True:
            try:
                subkey = winreg.EnumKey(root, index)
            except OSError:
                return 0.0
            index += 1
            try:
                with winreg.OpenKey(root, subkey) as adapter:
                    if winreg.QueryValueEx(adapter, "DriverDesc")[0] != name:
                        continue
                    size = winreg.QueryValueEx(
                        adapter, "HardwareInformation.qwMemorySize"
                    )[0]
            except OSError:
                # "Properties" and other non-adapter subkeys refuse the read.
                continue
            return int(size) / 1024**3 if isinstance(size, int) else 0.0


def _gpu_pool(devices: Dict[str, Any]) -> Optional[Tuple[float, str]]:
    """The memory llama.cpp loads into, or ``None`` when no GPU is reported.

    Raises :class:`ModelFitError` when a GPU is reported without its memory:
    judging such a PC on system RAM would call an 82 GB model a fit for a
    24 GB graphics card.
    """
    entries = _gpu_entries(devices)
    for vendor, integrated, gpu in entries:
        if vendor != "AMD" or not integrated:
            continue
        if _num(gpu.get("vram_gb")):
            return (
                _num(gpu.get("vram_gb")) + _num(gpu.get("virtual_mem_gb")),
                "AMD iGPU",
            )
        dedicated = _windows_adapter_memory_gb(gpu.get("name"))
        if dedicated:
            return dedicated, "AMD iGPU"
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
    disk, _ = model_store_free_gb(info)
    return MachineCapacity(memory_gb=pool[0], memory_source=pool[1], disk_free_gb=disk)


def model_store_free_gb(info: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    """Free GB in Lemonade's model store and its path, from ``/system-info``.

    Either is ``None`` when Lemonade did not report it.
    """
    storage = (info.get("model_storage") if isinstance(info, dict) else None) or {}
    free = storage.get("free_bytes")
    disk = float(free) / 1e9 if isinstance(free, (int, float)) else None
    where = storage.get("path")
    return disk, where if isinstance(where, str) and where else None


def check_download_fits_disk(
    download_gb: float, disk_free_gb: Optional[float], store_path: Optional[str] = None
) -> FitVerdict:
    """Whether ``download_gb`` of models fits the free space in the model store.

    Unlike :func:`check_fit`, which judges one model, this is the whole set a
    setup is about to pull: each may fit alone while together they do not.
    """
    if disk_free_gb is None or download_gb <= disk_free_gb:
        return FitVerdict(True)
    where = f" at {store_path}" if store_path else ""
    return FitVerdict(
        False,
        f"the models need about {download_gb:.1f} GB of disk and the model "
        f"store{where} has {disk_free_gb:.1f} GB free. Free up "
        f"{download_gb - disk_free_gb:.1f} GB there, then re-run `gaia init`",
    )


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


# --- Free memory: what a load can take right now -------------------------------


@dataclass(frozen=True)
class GpuMemoryUse:
    """One AMD GPU's memory as the Linux ``amdgpu`` driver reports it, in GB."""

    vram_total_gb: float
    vram_used_gb: float
    gtt_total_gb: float
    gtt_used_gb: float


@dataclass(frozen=True)
class OsMemory:
    """Memory as the operating system reports it, in GB."""

    ram_total_gb: float
    #: Free plus reclaimable (page cache): what an allocation gets without swapping.
    ram_available_gb: float
    swap_free_gb: float = 0.0
    gpus: Tuple[GpuMemoryUse, ...] = ()


@dataclass(frozen=True)
class FreeMemory:
    """Memory a model load can take now, in GB."""

    #: Without pushing other apps into swap: what a window is sized to.
    free_gb: float
    #: Counting free swap: past this, the load runs the machine out of memory.
    limit_gb: float


def _amdgpu_cards(drm: Path = Path("/sys/class/drm")) -> Tuple[GpuMemoryUse, ...]:
    """Every AMD GPU's VRAM and GTT use from Linux sysfs; empty elsewhere."""
    if not sys.platform.startswith("linux"):
        return ()
    cards = []
    for card in sorted(drm.glob("card*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        dev = card / "device"
        names = ("vram_total", "vram_used", "gtt_total", "gtt_used")
        if not all((dev / f"mem_info_{n}").is_file() for n in names):
            continue  # Not an amdgpu card.
        gb = {}
        for n in names:
            path = dev / f"mem_info_{n}"
            try:
                gb[n] = int(path.read_text(encoding="ascii")) / 1e9
            except (OSError, ValueError) as e:
                raise ModelFitError(
                    f"Cannot read GPU memory use from {path}: {e}. Check that "
                    "this user can read /sys/class/drm, then retry."
                ) from e
        cards.append(
            GpuMemoryUse(
                vram_total_gb=gb["vram_total"],
                vram_used_gb=gb["vram_used"],
                gtt_total_gb=gb["gtt_total"],
                gtt_used_gb=gb["gtt_used"],
            )
        )
    return tuple(cards)


def read_os_memory() -> OsMemory:
    """This machine's memory, as the OS reports it now."""
    import psutil

    ram = psutil.virtual_memory()
    return OsMemory(
        ram_total_gb=ram.total / 1e9,
        ram_available_gb=ram.available / 1e9,
        swap_free_gb=psutil.swap_memory().free / 1e9,
        gpus=_amdgpu_cards(),
    )


def ram_total_gb() -> float:
    """This machine's installed RAM as the OS sees it, in GB."""
    import psutil

    return psutil.virtual_memory().total / 1e9


def preloads_chat_model(ram_gb: float) -> bool:
    """Whether to load the chat model at startup rather than on the first message."""
    return ram_gb >= EAGER_LOAD_MIN_RAM_GB


def _card_for(cards: Tuple[GpuMemoryUse, ...], vram_gb: float):
    """The sysfs card that is the GPU Lemonade reported, or None if unsure."""
    if vram_gb:
        close = [
            c
            for c in cards
            if abs(c.vram_total_gb - vram_gb) <= max(0.5, 0.1 * vram_gb)
        ]
        return close[0] if len(close) == 1 else None
    return cards[0] if len(cards) == 1 else None


def free_memory(info: Dict[str, Any], os_memory: OsMemory) -> Optional[FreeMemory]:
    """Memory a model load on the pool of :func:`capacity_from_system_info` can take now.

    ``None`` when nothing reports it: a discrete GPU without Linux ``amdgpu``
    sysfs, or an integrated one whose carve-out neither Lemonade nor Windows
    records. Running out of VRAM there fails the load in Lemonade, not the app.
    Raises :class:`ModelFitError` when ``/system-info`` is unusable.
    """
    if not isinstance(info, dict):
        raise ModelFitError("Lemonade /system-info did not return an object")
    ram = os_memory.ram_available_gb
    swap = os_memory.swap_free_gb
    entries = _gpu_entries(info.get("devices") or {})
    if not entries:
        if _physical_memory_gb(info) <= 0:
            raise ModelFitError(
                "Lemonade /system-info reported neither GPU memory nor "
                "'Physical Memory', so GAIA cannot tell whether a model fits. "
                "Update Lemonade (`gaia init`) and retry."
            )
        return FreeMemory(ram, ram + swap)
    for vendor, integrated, gpu in entries:
        if vendor == "Apple":
            # Unified memory: Metal allocates from system RAM.
            return FreeMemory(ram, ram + swap)
        if vendor != "AMD" or not integrated:
            continue
        card = _card_for(os_memory.gpus, _num(gpu.get("vram_gb")))
        if card is not None:
            carve_out = max(0.0, card.vram_total_gb - card.vram_used_gb)
            gtt = max(0.0, card.gtt_total_gb - card.gtt_used_gb)
            return FreeMemory(
                carve_out + min(gtt, ram), carve_out + min(gtt, ram + swap)
            )
        # No per-GPU report (Windows): the BIOS carve-out is outside the RAM the
        # OS reports, and only GPU work uses it.
        carve_out = _num(gpu.get("vram_gb")) or _windows_adapter_memory_gb(
            gpu.get("name")
        )
        if not carve_out:
            # Unknown, like a discrete card's: judging on RAM alone would
            # shrink or refuse a model the GPU holds.
            return None
        return FreeMemory(carve_out + ram, carve_out + ram + swap)
    for vendor, _, gpu in entries:
        vram = _num(gpu.get("vram_gb"))
        if vendor == "AMD":
            card = _card_for(os_memory.gpus, vram)
            if card is not None:
                left = max(0.0, card.vram_total_gb - card.vram_used_gb)
                return FreeMemory(left, left)
        if vram:
            return None
    return None


def context_for_free_memory(
    *,
    size_gb: float,
    kv_bytes_per_token: int,
    ctx: int,
    free_gb: float,
    reserve_gb: float = 0.0,
) -> Optional[int]:
    """The largest window up to ``ctx`` that ``free_gb`` holds, or None.

    The weights, ``reserve_gb`` (models still to load beside this one) and
    ``FREE_MEMORY_HEADROOM_GB`` come off first. None means not even
    ``MIN_CONTEXT_TOKENS`` (or ``ctx``, if smaller) fits.
    """
    floor = min(MIN_CONTEXT_TOKENS, ctx)
    room = free_gb - FREE_MEMORY_HEADROOM_GB - reserve_gb - required_memory_gb(size_gb)
    if room < 0:
        return None
    if kv_bytes_per_token <= 0:
        return ctx
    tokens = int(room * 1e9 // kv_bytes_per_token)
    if tokens >= ctx:
        return ctx
    tokens -= tokens % CONTEXT_STEP_TOKENS
    return tokens if tokens >= floor else None


def check_free_memory(
    size_gb: float, kv_cache_gb: float, limit_gb: float
) -> FitVerdict:
    """Whether loading a model now leaves the machine with memory to spare."""
    need = required_memory_gb(size_gb, kv_cache_gb)
    if need > limit_gb:
        return FitVerdict(
            False,
            f"it needs about {need:.1f} GB and only {max(limit_gb, 0.0):.1f} GB "
            "is free",
        )
    return FitVerdict(True)
