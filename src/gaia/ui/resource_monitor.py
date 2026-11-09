# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Live CPU, memory, GPU, NPU and network usage for the Agent UI's resource strip.

On Windows the numbers come from the performance counters Task Manager reads,
so the two agree: CPU is ``% Processor Utility`` (not psutil's ``% Processor
Time``, which reads low on a boosting CPU), a GPU or NPU is its busiest engine,
and adapters are told apart through D3DKMT (an NPU is a compute-only adapter),
and network adapters are the connected hardware ones Task Manager lists.
Elsewhere CPU, memory and network come from psutil, AMD GPUs from sysfs, and
on Linux an AMD NPU from its ``amdxdna`` driver's busiest column.

One sampler thread runs while someone is watching and stops on its own once
nobody has asked for a while, so a closed strip costs nothing.
"""

from __future__ import annotations

import ctypes
import errno
import os
import re
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Callable,
    Deque,
    Dict,
    Generic,
    Iterable,
    List,
    Optional,
    Tuple,
    TypeVar,
)

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
class NetworkSample:
    key: str
    name: str
    description: str
    kind: str  # "ethernet", "wifi" or "network"
    send_bps: float
    recv_bps: float


@dataclass
class Sample:
    cpu_percent: float
    cpu_ghz: Optional[float]
    memory_used_bytes: int
    memory_total_bytes: int
    devices: List[DeviceSample] = field(default_factory=list)
    networks: List[NetworkSample] = field(default_factory=list)
    # Why GPUs/NPUs or networks can't be read, when only that part failed.
    devices_error: Optional[str] = None
    networks_error: Optional[str] = None
    # Linux reads NPUs on their own, so they can fail apart from the GPUs.
    npus_error: Optional[str] = None


T = TypeVar("T")

# What breaking GPU or network reads raise: a failed counter or API call, an
# unreadable file, or a Windows export missing from an older build.
_PART_ERRORS = (ResourceProbeError, OSError, AttributeError)


class OptionalPart(Generic[T]):
    """A GPU or network reading that can fail on its own.

    CPU and memory still show when it does; the part reports why it's
    missing instead, logs the cause once, and stays off until the sampler
    restarts.
    """

    def __init__(
        self,
        what: str,
        start: Callable[[], T],
        close: Callable[[T], None] = lambda _: None,
    ) -> None:
        self.what = what
        self.error: Optional[str] = None
        self._state: Optional[T] = None
        self._close = close
        try:
            self._state = start()
        except _PART_ERRORS as e:
            self._broke(e)

    def read(self, fn: Callable[[T], list]) -> list:
        if self._state is None:
            return []
        try:
            return fn(self._state)
        except _PART_ERRORS as e:
            self.close()
            self._broke(e)
            return []

    def _broke(self, e: Exception) -> None:
        self.error = str(e) or type(e).__name__
        logger.warning(
            "Resource monitor: %s usage unavailable: %s", self.what, e, exc_info=True
        )

    def close(self) -> None:
        if self._state is not None:
            state, self._state = self._state, None
            self._close(state)


# key -> (name, description, kind, bytes sent, bytes received)
NetCounters = Dict[str, Tuple[str, str, str, int, int]]


class NetworkRates:
    """Turns running byte counters into bits per second between two reads."""

    def __init__(
        self,
        read: Callable[[], NetCounters],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read
        self._clock = clock
        self._prev = read()
        self._prev_t = clock()

    def sample(self) -> List[NetworkSample]:
        now, t = self._read(), self._clock()
        elapsed = t - self._prev_t
        out = []
        for key, (name, desc, kind, sent, recv) in now.items():
            before = self._prev.get(key)
            if before is None or elapsed <= 0:
                send_bps = recv_bps = 0.0  # just connected: no baseline yet
            else:
                # A counter that went backwards was reset; count from zero.
                send_bps = max(0, sent - before[3]) * 8 / elapsed
                recv_bps = max(0, recv - before[4]) * 8 / elapsed
            out.append(NetworkSample(key, name, desc, kind, send_bps, recv_bps))
        self._prev, self._prev_t = now, t
        return out


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

# Win32 integer widths, spelled out so the module imports on every OS; only
# Windows ever calls into these.
_DWORD = _UINT = _ULONG = ctypes.c_uint32
_LONG = _BOOL = ctypes.c_int32
_HANDLE = ctypes.c_void_p

_PDH_FMT_DOUBLE = 0x00000200
_PDH_FMT_NOCAP100 = 0x00008000
_PDH_MORE_DATA = 0x800007D2
# PDH_CSTATUS_NO_INSTANCE, PDH_NO_DATA: a wildcard that matches nothing yet.
_PDH_NOTHING_RUNNING = (0x800007D1, 0x800007D5)
_PDH_CSTATUS_VALID = (0, 1)  # VALID_DATA, NEW_DATA


class _PdhValue(ctypes.Structure):
    _fields_ = [("CStatus", _DWORD), ("doubleValue", ctypes.c_double)]


class _PdhItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PdhValue)]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", _DWORD), ("HighPart", _LONG)]


class _AdapterInfo(ctypes.Structure):
    _fields_ = [
        ("hAdapter", _UINT),
        ("AdapterLuid", _LUID),
        ("NumOfSources", _ULONG),
        ("bPrecisePresentRegionsPreferred", _BOOL),
    ]


class _EnumAdapters3(ctypes.Structure):
    _fields_ = [
        ("Filter", ctypes.c_uint64),
        ("NumAdapters", _UINT),
        ("pAdapters", ctypes.POINTER(_AdapterInfo)),
    ]


class _QueryAdapterInfo(ctypes.Structure):
    _fields_ = [
        ("hAdapter", _UINT),
        ("Type", ctypes.c_int),
        ("pPrivateDriverData", ctypes.c_void_p),
        ("PrivateDriverDataSize", _UINT),
    ]


class _MibIfRow2(ctypes.Structure):
    _fields_ = [
        ("InterfaceLuid", ctypes.c_uint64),
        ("InterfaceIndex", _ULONG),
        ("InterfaceGuid", ctypes.c_ubyte * 16),
        ("Alias", ctypes.c_wchar * 257),
        ("Description", ctypes.c_wchar * 257),
        ("PhysicalAddressLength", _ULONG),
        ("PhysicalAddress", ctypes.c_ubyte * 32),
        ("PermanentPhysicalAddress", ctypes.c_ubyte * 32),
        ("Mtu", _ULONG),
        ("Type", _ULONG),
        ("TunnelType", ctypes.c_int),
        ("MediaType", ctypes.c_int),
        ("PhysicalMediumType", ctypes.c_int),
        ("AccessType", ctypes.c_int),
        ("DirectionType", ctypes.c_int),
        ("InterfaceAndOperStatusFlags", ctypes.c_ubyte),
        ("OperStatus", ctypes.c_int),
        ("AdminStatus", ctypes.c_int),
        ("MediaConnectState", ctypes.c_int),
        ("NetworkGuid", ctypes.c_ubyte * 16),
        ("ConnectionType", ctypes.c_int),
        ("TransmitLinkSpeed", ctypes.c_uint64),
        ("ReceiveLinkSpeed", ctypes.c_uint64),
        ("InOctets", ctypes.c_uint64),
        ("_in_counters", ctypes.c_uint64 * 8),
        ("OutOctets", ctypes.c_uint64),
        ("_out_counters", ctypes.c_uint64 * 8),
    ]


_IF_HARDWARE = 1 << 0
_IF_FILTER = 1 << 1
_IF_OPER_UP = 1
_IF_KINDS = {6: "ethernet", 71: "wifi"}  # IF_TYPE_ETHERNET_CSMACD, IEEE80211

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
            gdi.D3DKMTCloseAdapter(ctypes.byref(_UINT(info.hAdapter)))


def _describe(gdi, info) -> Optional[Adapter]:
    flags = _UINT(0)
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


def windows_networks() -> NetCounters:
    """Connected hardware network adapters, as Task Manager lists them.

    Hyper-V switches, WSL, VPN tunnels and per-adapter filter layers are
    software interfaces, so the hardware flag leaves them out.
    """
    iphlp = ctypes.WinDLL("iphlpapi")
    table = ctypes.c_void_p()
    _check_nt(iphlp.GetIfTable2(ctypes.byref(table)), "GetIfTable2")
    try:
        count = ctypes.c_ulong.from_address(table.value).value
        # MIB_IF_TABLE2: a ULONG count, then rows aligned to 8 bytes.
        rows = (_MibIfRow2 * count).from_address(table.value + 8)
        out: NetCounters = {}
        for row in rows:
            flags = row.InterfaceAndOperStatusFlags
            if not flags & _IF_HARDWARE or flags & _IF_FILTER:
                continue
            if row.OperStatus != _IF_OPER_UP:
                continue
            out[f"net_{row.InterfaceLuid:016x}"] = (
                row.Alias,
                row.Description,
                _IF_KINDS.get(row.Type, "network"),
                row.OutOctets,
                row.InOctets,
            )
        return out
    finally:
        iphlp.FreeMibTable(table)


class _PdhQuery:
    """A PDH query with named counters, read as single values or instance arrays."""

    def __init__(self, paths: Dict[str, str]):
        self._pdh = ctypes.WinDLL("pdh")
        self._query = _HANDLE()
        self._check(
            self._pdh.PdhOpenQueryW(None, None, ctypes.byref(self._query)),
            "PdhOpenQuery",
        )
        self._counters: Dict[str, _HANDLE] = {}
        try:
            for name, path in paths.items():
                handle = _HANDLE()
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
        size = _DWORD(0)
        count = _DWORD(0)
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


def _collected(paths: Dict[str, str]) -> _PdhQuery:
    query = _PdhQuery(paths)
    try:
        query.collect()
    except ResourceProbeError:
        query.close()
        raise
    return query


def _open_windows_gpus() -> Tuple[List[Adapter], _PdhQuery]:
    adapters = windows_adapters()
    query = _collected(
        {
            "engine": r"\GPU Engine(*)\Utilization Percentage",
            "dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage",
            "shared": r"\GPU Adapter Memory(*)\Shared Usage",
        }
    )
    return adapters, query


def _read_windows_gpus(state: Tuple[List[Adapter], _PdhQuery]) -> List[DeviceSample]:
    adapters, query = state
    query.collect()
    loads = busiest_engine(query.array("engine"))
    mem = adapter_memory(query.array("dedicated"), query.array("shared"))
    return [
        DeviceSample(
            key=a.key,
            name=a.name,
            kind=a.kind,
            percent=loads.get(a.key, 0.0),
            memory_used_bytes=mem.get(a.key),
        )
        for a in adapters
    ]


class WindowsProbe:
    """CPU and memory must read; GPUs and networks may fail on their own
    (a VM, Remote Desktop, or a PC with no GPU driver has no GPU counters)."""

    def __init__(
        self,
        open_gpus: Callable[[], Tuple[List[Adapter], _PdhQuery]] = _open_windows_gpus,
        read_networks: Callable[[], NetCounters] = windows_networks,
    ) -> None:
        self._query = _collected(
            {
                "cpu": r"\Processor Information(_Total)\% Processor Utility",
                "perf": r"\Processor Information(_Total)\% Processor Performance",
                "freq": r"\Processor Information(_Total)\Processor Frequency",
            }
        )
        self._gpus: OptionalPart = OptionalPart(
            "GPU and NPU", open_gpus, close=lambda state: state[1].close()
        )
        self._net: OptionalPart = OptionalPart(
            "network", lambda: NetworkRates(read_networks)
        )

    def sample(self) -> Sample:
        self._query.collect()
        cpu = self._query.value("cpu")
        if cpu is None:
            raise ResourceProbeError("CPU utility counter returned no data")
        perf = self._query.value("perf")
        freq_mhz = self._query.value("freq")
        ghz = perf * freq_mhz / 100 / 1000 if perf is not None and freq_mhz else None
        devices = self._gpus.read(_read_windows_gpus)
        networks = self._net.read(lambda rates: rates.sample())
        vm = psutil.virtual_memory()
        return Sample(
            cpu_percent=min(100.0, cpu),
            cpu_ghz=ghz,
            memory_used_bytes=vm.total - vm.available,
            memory_total_bytes=vm.total,
            devices=devices,
            networks=networks,
            devices_error=self._gpus.error,
            networks_error=self._net.error,
        )

    def close(self) -> None:
        self._gpus.close()
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


_SYS_NET = Path("/sys/class/net")


def posix_networks(sys_net: Path = _SYS_NET) -> NetCounters:
    """Network interfaces that are up and backed by hardware.

    Linux marks hardware with a ``device`` link in sysfs, which leaves out
    bridges, Docker and VPN interfaces. macOS has no such marker, so there
    the ``en`` interfaces (Ethernet and Wi-Fi) stand in for the hardware.
    """
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)
    on_linux = sys_net.is_dir()
    out: NetCounters = {}
    for name, st in stats.items():
        io = counters.get(name)
        if not st.isup or io is None:
            continue
        if on_linux:
            if not (sys_net / name / "device").exists():
                continue
            kind = "wifi" if (sys_net / name / "wireless").exists() else "ethernet"
        elif name.startswith("en"):
            kind = "network"
        else:
            continue
        out[f"net_{name}"] = (name, name, kind, io.bytes_sent, io.bytes_recv)
    return out


def _read_sysfs_gpus(gpus: List[Tuple[Path, str]]) -> List[DeviceSample]:
    devices = []
    for dev, name in gpus:
        if _runtime_suspended(dev):
            # Reading a suspended GPU would wake it, or fail on newer kernels.
            devices.append(DeviceSample(str(dev), name, "gpu", 0.0))
            continue
        busy = _read_int(dev / "gpu_busy_percent")
        if busy is None:
            raise ResourceProbeError(f"Could not read {dev / 'gpu_busy_percent'}")
        vram = _read_int(dev / "mem_info_vram_used")
        gtt = _read_int(dev / "mem_info_gtt_used")
        used = None if vram is None and gtt is None else (vram or 0) + (gtt or 0)
        devices.append(
            DeviceSample(str(dev), name, "gpu", float(busy), memory_used_bytes=used)
        )
    return devices


# ── Linux: AMD NPUs through the amdxdna driver ───────────────────────────────

# From the kernel's include/uapi/drm/amdxdna_accel.h.
_DRM_COMMAND_BASE = 0x40
_DRM_AMDXDNA_GET_INFO = 7
_DRM_AMDXDNA_QUERY_SENSORS = 4
_SENSOR_COLUMN_UTILIZATION = 1
_GET_INFO = struct.Struct("<IIQ")  # param, buffer_size, buffer
# label, input, max, average, highest, status, units, unitm, type, pad
_SENSOR = struct.Struct("<64s4I64s16sbB6x")
_MAX_SENSORS = 9  # total power, then at most 8 columns
_IOCTL_GET_INFO = (
    (3 << 30)  # read and write
    | (_GET_INFO.size << 16)
    | (ord("d") << 8)
    | (_DRM_COMMAND_BASE + _DRM_AMDXDNA_GET_INFO)
)
_NO_USAGE = (
    "its driver reports usage only on Linux 7.1 or later, with the AMD PMF "
    "driver loaded"
)


def linux_npus(root: Path = Path("/")) -> List[Tuple[str, Path, Path]]:
    """``(accel name, PCI device dir, /dev node)`` for each NPU on ``amdxdna``."""
    from gaia.llm.npu_linux import bound_devices

    return [
        (name, dev, root / "dev" / "accel" / name) for name, dev in bound_devices(root)
    ]


def _open_npu(node: Path) -> int:
    try:
        return os.open(node, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    except OSError as e:
        raise ResourceProbeError(f"Can't open {node}: {e.strerror or e}") from e


def amdxdna_busy_percent(fd: int) -> float:
    """The NPU's busiest column, from the driver's sensor query."""
    import fcntl  # POSIX only; the module still imports on Windows

    buf = ctypes.create_string_buffer(_SENSOR.size * _MAX_SENSORS)
    arg = bytearray(
        _GET_INFO.pack(_DRM_AMDXDNA_QUERY_SENSORS, len(buf), ctypes.addressof(buf))
    )
    try:
        fcntl.ioctl(fd, _IOCTL_GET_INFO, arg, True)
    except OSError as e:
        if e.errno in (errno.EOPNOTSUPP, errno.EINVAL, errno.ENOTTY):
            raise ResourceProbeError(_NO_USAGE) from e
        raise ResourceProbeError(
            f"The NPU driver couldn't report usage: {e.strerror or e}"
        ) from e
    _, size, _ = _GET_INFO.unpack(arg)
    columns = [
        fields[1]
        for off in range(0, min(size, len(buf)) - _SENSOR.size + 1, _SENSOR.size)
        for fields in [_SENSOR.unpack_from(buf, off)]
        if fields[8] == _SENSOR_COLUMN_UTILIZATION
    ]
    if not columns:
        raise ResourceProbeError(_NO_USAGE)
    return float(min(100, max(columns)))


class AmdxdnaNpus:
    """Each NPU's busiest column, read once a sample through its /dev node."""

    def __init__(
        self,
        found: List[Tuple[str, Path, Path]],
        open_node: Callable[[Path], int] = _open_npu,
        busy: Callable[[int], float] = amdxdna_busy_percent,
        close_fd: Callable[[int], None] = os.close,
    ) -> None:
        self._busy = busy
        self._close_fd = close_fd
        self._npus: List[Tuple[str, Path, int]] = []
        try:
            for name, dev, node in found:
                fd = open_node(node)
                self._npus.append((name, dev, fd))
                # Once at start, even if asleep, so an unsupported driver says so now.
                busy(fd)
        except BaseException:
            self.close()
            raise

    def sample(self) -> List[DeviceSample]:
        out = []
        for name, dev, fd in self._npus:
            # The query wakes a sleeping NPU; asleep means idle.
            percent = 0.0 if _runtime_suspended(dev) else self._busy(fd)
            out.append(DeviceSample(f"npu_{name}", f"AMD NPU ({name})", "npu", percent))
        return out

    def close(self) -> None:
        npus, self._npus = self._npus, []
        for _, _, fd in npus:
            self._close_fd(fd)


class PosixProbe:
    def __init__(
        self,
        drm_root: Path = _DRM,
        sys_net: Path = _SYS_NET,
        npus: Optional[Callable[[], AmdxdnaNpus]] = None,
    ) -> None:
        self._gpus: OptionalPart = OptionalPart("GPU", lambda: sysfs_gpus(drm_root))
        if npus is None:
            npus = (
                (lambda: AmdxdnaNpus(linux_npus()))
                if sys.platform.startswith("linux")
                else (lambda: AmdxdnaNpus([]))
            )
        self._npus: OptionalPart = OptionalPart("NPU", npus, close=lambda n: n.close())
        self._net: OptionalPart = OptionalPart(
            "network", lambda: NetworkRates(lambda: posix_networks(sys_net))
        )
        psutil.cpu_percent(interval=None)

    def sample(self) -> Sample:
        # psutil has no cpu_freq on Apple silicon, and some Intel Macs can't
        # read it; the speed is optional and shown blank.
        read_freq = getattr(psutil, "cpu_freq", None)
        freq = None
        if read_freq is not None:
            try:
                freq = read_freq()
            except (OSError, NotImplementedError) as e:
                logger.debug("CPU clock speed unavailable: %s", e)
        vm = psutil.virtual_memory()
        devices = self._gpus.read(_read_sysfs_gpus)
        devices += self._npus.read(lambda npus: npus.sample())
        networks = self._net.read(lambda rates: rates.sample())
        return Sample(
            cpu_percent=psutil.cpu_percent(interval=None),
            cpu_ghz=freq.current / 1000 if freq and freq.current else None,
            memory_used_bytes=vm.total - vm.available,
            memory_total_bytes=vm.total,
            devices=devices,
            networks=networks,
            devices_error=self._gpus.error,
            networks_error=self._net.error,
            npus_error=self._npus.error,
        )

    def close(self) -> None:
        self._npus.close()


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
                    # Set ready before letting go, so a sampler started
                    # right after this one never sees a stale ready flag.
                    self._ready.set()
                    if self._clock() - self._last_read > self._idle_stop:
                        self._thread = None
                    current = self._thread is threading.current_thread()
                if not current:
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
            if self._thread is threading.current_thread():
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
    networks = []
    for net in latest.networks:
        past = [{n.key: n for n in s.networks}.get(net.key) for s in history]
        networks.append(
            {
                "id": net.key,
                "name": net.name,
                "description": net.description,
                "kind": net.kind,
                "send_bps": round(net.send_bps),
                "recv_bps": round(net.recv_bps),
                "send_history": [round(n.send_bps) if n else 0 for n in past],
                "recv_history": [round(n.recv_bps) if n else 0 for n in past],
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
        "networks": networks,
        "devices_error": latest.devices_error,
        "networks_error": latest.networks_error,
        "npus_error": latest.npus_error,
    }


_sampler: Optional[ResourceSampler] = None
_sampler_lock = threading.Lock()


def get_sampler() -> ResourceSampler:
    global _sampler
    with _sampler_lock:
        if _sampler is None:
            _sampler = ResourceSampler()
        return _sampler
