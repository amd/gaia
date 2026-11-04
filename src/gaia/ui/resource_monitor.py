# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Live CPU, memory, GPU and NPU usage for the Agent UI's resource strip.

On Windows the numbers come from the performance counters Task Manager reads,
so the two agree: CPU is ``% Processor Utility`` (not psutil's ``% Processor
Time``, which reads low on a boosting CPU), a GPU or NPU is its busiest engine,
and adapters are told apart through D3DKMT (an NPU is a compute-only adapter).
Elsewhere CPU and memory come from psutil and AMD GPUs from sysfs.

One sampler thread runs while someone is watching and stops on its own once
nobody has asked for a while, so a closed strip costs nothing.
"""

from __future__ import annotations

import ctypes
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple

import psutil

from gaia.logger import get_logger

logger = get_logger(__name__)

INTERVAL_S = 1.0
HISTORY_LEN = 60
IDLE_STOP_S = 15.0


class ResourceProbeError(RuntimeError):
    """A counter source could not be opened or read."""


@dataclass
class Adapter:
    """One GPU or NPU as the OS reports it."""

    key: str
    name: str
    kind: str  # "gpu" or "npu"


@dataclass
class DeviceSample:
    key: str
    name: str
    kind: str
    percent: float
    memory_used_bytes: Optional[int] = None


@dataclass
class Sample:
    cpu_percent: float
    cpu_ghz: Optional[float]
    memory_used_bytes: int
    memory_total_bytes: int
    devices: List[DeviceSample] = field(default_factory=list)


_ENGINE_RE = re.compile(
    r"^pid_\d+_luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_(\d+)_eng_(\d+)_engtype_"
)
_ADAPTER_MEM_RE = re.compile(r"^luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_\d+$")


def busiest_engine(samples: Iterable[Tuple[str, float]]) -> Dict[str, float]:
    """Per-adapter load the way Task Manager shows it: its busiest engine.

    ``GPU Engine`` instances are per process; each engine's load is the sum
    over processes, and the adapter's load is the highest engine.
    """
    engines: Dict[Tuple[str, str, str], float] = {}
    for instance, value in samples:
        m = _ENGINE_RE.match(instance)
        if not m:
            continue
        key = (m.group(1).lower(), m.group(2), m.group(3))
        engines[key] = engines.get(key, 0.0) + value
    adapters: Dict[str, float] = {}
    for (luid, _, _), load in engines.items():
        adapters[luid] = min(100.0, max(adapters.get(luid, 0.0), load))
    return adapters


def adapter_memory(
    dedicated: Iterable[Tuple[str, float]], shared: Iterable[Tuple[str, float]]
) -> Dict[str, int]:
    """Dedicated plus shared memory in use per adapter, as Task Manager adds them."""
    used: Dict[str, int] = {}
    for instance, value in list(dedicated) + list(shared):
        m = _ADAPTER_MEM_RE.match(instance)
        if m:
            luid = m.group(1).lower()
            used[luid] = used.get(luid, 0) + int(value)
    return used


# ── Windows: PDH counters and D3DKMT adapters ────────────────────────────────

if sys.platform == "win32":
    from ctypes import wintypes

    _PDH_FMT_DOUBLE = 0x00000200
    _PDH_FMT_NOCAP100 = 0x00008000
    _PDH_MORE_DATA = 0x800007D2
    # PDH_CSTATUS_NO_INSTANCE, PDH_NO_DATA: a wildcard that matches nothing yet.
    _PDH_NOTHING_RUNNING = (0x800007D1, 0x800007D5)
    _PDH_CSTATUS_VALID = (0, 1)  # VALID_DATA, NEW_DATA

    class _PdhValue(ctypes.Structure):
        _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

    class _PdhItem(ctypes.Structure):
        _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PdhValue)]

    class _LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class _AdapterInfo(ctypes.Structure):
        _fields_ = [
            ("hAdapter", wintypes.UINT),
            ("AdapterLuid", _LUID),
            ("NumOfSources", wintypes.ULONG),
            ("bPrecisePresentRegionsPreferred", wintypes.BOOL),
        ]

    class _EnumAdapters3(ctypes.Structure):
        _fields_ = [
            ("Filter", ctypes.c_uint64),
            ("NumAdapters", wintypes.UINT),
            ("pAdapters", ctypes.POINTER(_AdapterInfo)),
        ]

    class _QueryAdapterInfo(ctypes.Structure):
        _fields_ = [
            ("hAdapter", wintypes.UINT),
            ("Type", ctypes.c_int),
            ("pPrivateDriverData", ctypes.c_void_p),
            ("PrivateDriverDataSize", wintypes.UINT),
        ]

    # NPUs are compute-only adapters, which only EnumAdapters3 lists.
    _ENUM_INCLUDE_COMPUTE_ONLY = 1
    _KMTQAITYPE_ADAPTERTYPE = 15
    _KMTQAITYPE_DRIVER_DESCRIPTION = 65
    # D3DKMT_ADAPTERTYPE bit positions.
    _RENDER_SUPPORTED = 1 << 0
    _SOFTWARE_DEVICE = 1 << 2
    _COMPUTE_ONLY = 1 << 11


def _luid_key(luid) -> str:
    return f"0x{luid.HighPart & 0xFFFFFFFF:08x}_0x{luid.LowPart:08x}"


def windows_adapters() -> List[Adapter]:
    """Hardware GPUs and NPUs, keyed by the LUID the counters use."""
    gdi = ctypes.WinDLL("gdi32")
    enum = _EnumAdapters3(Filter=_ENUM_INCLUDE_COMPUTE_ONLY)
    _check_nt(gdi.D3DKMTEnumAdapters3(ctypes.byref(enum)), "D3DKMTEnumAdapters3")
    infos = (_AdapterInfo * enum.NumAdapters)()
    enum = _EnumAdapters3(
        Filter=_ENUM_INCLUDE_COMPUTE_ONLY,
        NumAdapters=enum.NumAdapters,
        pAdapters=infos,
    )
    _check_nt(gdi.D3DKMTEnumAdapters3(ctypes.byref(enum)), "D3DKMTEnumAdapters3")

    opened = infos[: enum.NumAdapters]
    try:
        return [a for a in (_describe(gdi, info) for info in opened) if a]
    finally:
        for info in opened:
            gdi.D3DKMTCloseAdapter(ctypes.byref(wintypes.UINT(info.hAdapter)))


def _describe(gdi, info) -> Optional[Adapter]:
    flags = wintypes.UINT(0)
    _query_adapter(gdi, info.hAdapter, _KMTQAITYPE_ADAPTERTYPE, flags)
    bits = flags.value
    if bits & _SOFTWARE_DEVICE:
        return None
    if bits & _COMPUTE_ONLY:
        kind = "npu"
    elif bits & _RENDER_SUPPORTED:
        kind = "gpu"
    else:
        return None
    name = (ctypes.c_wchar * 4096)()
    _query_adapter(gdi, info.hAdapter, _KMTQAITYPE_DRIVER_DESCRIPTION, name)
    return Adapter(key=_luid_key(info.AdapterLuid), name=name.value.strip(), kind=kind)


def _check_nt(status: int, what: str) -> None:
    if status != 0:
        raise ResourceProbeError(f"{what} failed (0x{status & 0xFFFFFFFF:08x})")


def _query_adapter(gdi, handle: int, kind: int, out) -> None:
    query = _QueryAdapterInfo(
        hAdapter=handle,
        Type=kind,
        pPrivateDriverData=ctypes.cast(ctypes.byref(out), ctypes.c_void_p),
        PrivateDriverDataSize=ctypes.sizeof(out),
    )
    _check_nt(
        gdi.D3DKMTQueryAdapterInfo(ctypes.byref(query)),
        f"D3DKMTQueryAdapterInfo type {kind}",
    )


class _PdhQuery:
    """A PDH query with named counters, read as single values or instance arrays."""

    def __init__(self, paths: Dict[str, str]):
        self._pdh = ctypes.WinDLL("pdh")
        self._query = wintypes.HANDLE()
        self._check(
            self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)),
            "PdhOpenQuery",
        )
        self._counters: Dict[str, wintypes.HANDLE] = {}
        try:
            for name, path in paths.items():
                handle = wintypes.HANDLE()
                self._check(
                    self._pdh.PdhAddEnglishCounterW(
                        self._query, path, None, ctypes.byref(handle)
                    ),
                    f"PdhAddEnglishCounter {path}",
                )
                self._counters[name] = handle
        except ResourceProbeError:
            self.close()
            raise

    @staticmethod
    def _check(status: int, what: str) -> None:
        if status != 0:
            raise ResourceProbeError(f"{what} failed (0x{status & 0xFFFFFFFF:08x})")

    def collect(self) -> None:
        self._check(self._pdh.PdhCollectQueryData(self._query), "PdhCollectQueryData")

    def value(self, name: str) -> Optional[float]:
        out = _PdhValue()
        status = self._pdh.PdhGetFormattedCounterValue(
            self._counters[name],
            _PDH_FMT_DOUBLE | _PDH_FMT_NOCAP100,
            None,
            ctypes.byref(out),
        )
        if status != 0 or out.CStatus not in _PDH_CSTATUS_VALID:
            return None
        return out.doubleValue

    def array(self, name: str) -> List[Tuple[str, float]]:
        size = wintypes.DWORD(0)
        count = wintypes.DWORD(0)
        fmt = _PDH_FMT_DOUBLE | _PDH_FMT_NOCAP100
        handle = self._counters[name]
        status = self._pdh.PdhGetFormattedCounterArrayW(
            handle, fmt, ctypes.byref(size), ctypes.byref(count), None
        )
        status &= 0xFFFFFFFF
        if status == 0 or status in _PDH_NOTHING_RUNNING:
            # No process is using any engine right now.
            return []
        if status != _PDH_MORE_DATA:
            self._check(status, f"PdhGetFormattedCounterArray {name}")
        buf = ctypes.create_string_buffer(size.value)
        self._check(
            self._pdh.PdhGetFormattedCounterArrayW(
                handle, fmt, ctypes.byref(size), ctypes.byref(count), buf
            ),
            f"PdhGetFormattedCounterArray {name}",
        )
        items = ctypes.cast(buf, ctypes.POINTER(_PdhItem))
        return [
            (items[i].szName, items[i].FmtValue.doubleValue)
            for i in range(count.value)
            if items[i].FmtValue.CStatus in _PDH_CSTATUS_VALID
        ]

    def close(self) -> None:
        self._pdh.PdhCloseQuery(self._query)


class WindowsProbe:
    def __init__(self) -> None:
        self.adapters = windows_adapters()
        self._query = _PdhQuery(
            {
                "cpu": r"\Processor Information(_Total)\% Processor Utility",
                "perf": r"\Processor Information(_Total)\% Processor Performance",
                "freq": r"\Processor Information(_Total)\Processor Frequency",
                "engine": r"\GPU Engine(*)\Utilization Percentage",
                "dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage",
                "shared": r"\GPU Adapter Memory(*)\Shared Usage",
            }
        )
        try:
            self._query.collect()
        except ResourceProbeError:
            self._query.close()
            raise

    def sample(self) -> Sample:
        self._query.collect()
        cpu = self._query.value("cpu")
        if cpu is None:
            raise ResourceProbeError("CPU utility counter returned no data")
        perf = self._query.value("perf")
        freq_mhz = self._query.value("freq")
        ghz = perf * freq_mhz / 100 / 1000 if perf is not None and freq_mhz else None
        loads = busiest_engine(self._query.array("engine"))
        mem = adapter_memory(
            self._query.array("dedicated"), self._query.array("shared")
        )
        vm = psutil.virtual_memory()
        return Sample(
            cpu_percent=min(100.0, cpu),
            cpu_ghz=ghz,
            memory_used_bytes=vm.total - vm.available,
            memory_total_bytes=vm.total,
            devices=[
                DeviceSample(
                    key=a.key,
                    name=a.name,
                    kind=a.kind,
                    percent=loads.get(a.key, 0.0),
                    memory_used_bytes=mem.get(a.key),
                )
                for a in self.adapters
            ],
        )

    def close(self) -> None:
        self._query.close()


# ── Linux / macOS: psutil, plus AMD GPUs through sysfs ───────────────────────

_DRM = Path("/sys/class/drm")


def _read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _runtime_suspended(dev: Path) -> bool:
    try:
        return (dev / "power" / "runtime_status").read_text().strip() == "suspended"
    except OSError:
        return False


def sysfs_gpus(root: Path = _DRM) -> List[Tuple[Path, str]]:
    """AMD GPU device dirs that report a busy percentage."""
    found = []
    for card in sorted(root.glob("card[0-9]*")):
        if "-" in card.name:  # connectors such as card0-DP-1
            continue
        dev = card / "device"
        if not (dev / "gpu_busy_percent").exists():
            continue
        name = ""
        try:
            name = (dev / "product_name").read_text().strip()
        except OSError:
            pass
        found.append((dev, name or f"AMD GPU ({card.name})"))
    return found


class PosixProbe:
    def __init__(self, drm_root: Path = _DRM) -> None:
        self._gpus = sysfs_gpus(drm_root)
        self.adapters = [
            Adapter(key=str(dev), name=name, kind="gpu") for dev, name in self._gpus
        ]
        psutil.cpu_percent(interval=None)

    def sample(self) -> Sample:
        try:
            freq = psutil.cpu_freq()
        except (OSError, NotImplementedError) as e:
            # Some macOS builds can't read the clock; the speed is optional.
            logger.debug("CPU clock speed unavailable: %s", e)
            freq = None
        vm = psutil.virtual_memory()
        devices = []
        for (dev, name), adapter in zip(self._gpus, self.adapters):
            if _runtime_suspended(dev):
                # Reading a suspended GPU would wake it, or fail on newer kernels.
                devices.append(DeviceSample(adapter.key, name, "gpu", 0.0))
                continue
            busy = _read_int(dev / "gpu_busy_percent")
            if busy is None:
                raise ResourceProbeError(f"Could not read {dev / 'gpu_busy_percent'}")
            vram = _read_int(dev / "mem_info_vram_used")
            gtt = _read_int(dev / "mem_info_gtt_used")
            used = None if vram is None and gtt is None else (vram or 0) + (gtt or 0)
            devices.append(
                DeviceSample(
                    adapter.key, name, "gpu", float(busy), memory_used_bytes=used
                )
            )
        return Sample(
            cpu_percent=psutil.cpu_percent(interval=None),
            cpu_ghz=freq.current / 1000 if freq and freq.current else None,
            memory_used_bytes=vm.total - vm.available,
            memory_total_bytes=vm.total,
            devices=devices,
        )

    def close(self) -> None:
        pass


def default_probe():
    return WindowsProbe() if sys.platform == "win32" else PosixProbe()


# ── Sampler ──────────────────────────────────────────────────────────────────


class ResourceSampler:
    """Samples once a second into a short history while someone is watching."""

    def __init__(
        self,
        probe_factory: Callable[[], object] = default_probe,
        interval_s: float = INTERVAL_S,
        idle_stop_s: float = IDLE_STOP_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe_factory = probe_factory
        self._interval = interval_s
        self._idle_stop = idle_stop_s
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._history: Deque[Sample] = deque(maxlen=HISTORY_LEN)
        self._last_read = 0.0
        self._error: Optional[str] = None
        self._logged_error: Optional[str] = None
        self._ready = threading.Event()

    def snapshot(self, wait_s: float = 2.5) -> dict:
        """The latest sample plus history; starts sampling if it was idle."""
        with self._lock:
            self._last_read = self._clock()
            if self._thread is None:
                self._start_locked()
        self._ready.wait(wait_s)
        with self._lock:
            if self._error:
                raise ResourceProbeError(self._error)
            history = list(self._history)
        return to_payload(history, self._interval)

    def _start_locked(self) -> None:
        self._history.clear()
        self._error = None
        self._ready.clear()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="gaia-resource-sampler", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            probe = self._probe_factory()
        except Exception as e:  # reported through snapshot(), not swallowed
            self._fail("could not start", e)
            return
        try:
            while not self._stop.wait(self._interval):
                try:
                    sample = probe.sample()
                except Exception as e:  # reported through snapshot()
                    self._fail("sample failed", e)
                    return
                with self._lock:
                    self._history.append(sample)
                    self._error = None
                    self._logged_error = None
                    if self._clock() - self._last_read > self._idle_stop:
                        self._thread = None
                self._ready.set()
                if self._thread is not threading.current_thread():
                    return
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
        finally:
            probe.close()

    def _fail(self, what: str, e: Exception) -> None:
        message = f"Could not read this PC's resource usage: {e}"
        with self._lock:
            # The UI retries every few seconds; log the traceback once per cause.
            if message != self._logged_error:
                logger.error("Resource monitor %s: %s", what, e, exc_info=True)
                self._logged_error = message
            else:
                logger.warning("Resource monitor %s again: %s", what, e)
            self._error = message
            self._thread = None
        self._ready.set()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._interval * 3)


def to_payload(history: List[Sample], interval_s: float) -> dict:
    if not history:
        return {"interval_ms": int(interval_s * 1000), "ready": False}
    latest = history[-1]
    total = latest.memory_total_bytes
    devices = []
    for i, dev in enumerate(latest.devices):
        devices.append(
            {
                "id": dev.key,
                "name": dev.name,
                "kind": dev.kind,
                "percent": round(dev.percent, 1),
                "memory_used_bytes": dev.memory_used_bytes,
                "history": [
                    round(s.devices[i].percent, 1) if i < len(s.devices) else 0.0
                    for s in history
                ],
            }
        )
    return {
        "interval_ms": int(interval_s * 1000),
        "ready": True,
        "cpu": {
            "percent": round(latest.cpu_percent, 1),
            "ghz": round(latest.cpu_ghz, 2) if latest.cpu_ghz else None,
            "history": [round(s.cpu_percent, 1) for s in history],
        },
        "memory": {
            "used_bytes": latest.memory_used_bytes,
            "total_bytes": total,
            "percent": (
                round(latest.memory_used_bytes / total * 100, 1) if total else 0.0
            ),
            "history": [
                (
                    round(s.memory_used_bytes / s.memory_total_bytes * 100, 1)
                    if s.memory_total_bytes
                    else 0.0
                )
                for s in history
            ],
        },
        "devices": devices,
    }


_sampler: Optional[ResourceSampler] = None
_sampler_lock = threading.Lock()


def get_sampler() -> ResourceSampler:
    global _sampler
    with _sampler_lock:
        if _sampler is None:
            _sampler = ResourceSampler()
        return _sampler
