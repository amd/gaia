# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The chat's resource strip: how loads are added up, the sampler, the route."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import psutil
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui import resource_monitor as rm
from gaia.ui.routers import resources as resources_router

pytestmark = pytest.mark.allow_network  # TestClient only; nothing leaves the machine

UI = {"X-Gaia-UI": "1"}
GPU = "0x00000000_0x0000d938"
NPU = "0x00000000_0x0000f994"


def engine(pid: int, luid: str, eng: int, kind: str = "3d") -> str:
    return f"pid_{pid}_luid_{luid}_phys_0_eng_{eng}_engtype_{kind}"


# ── Adding up the counters ───────────────────────────────────────────────────


def test_adapter_load_is_its_busiest_engine_summed_over_processes():
    loads = rm.busiest_engine(
        [
            (engine(1, GPU, 0), 10.0),
            (engine(2, GPU, 0), 15.0),  # same 3D engine, another process
            (engine(3, GPU, 7, "video codec engine"), 20.0),
            (engine(4, NPU, 0, "compute"), 3.0),
        ]
    )
    assert loads == {GPU: 25.0, NPU: 3.0}


def test_adapter_load_caps_at_100_and_skips_unknown_instances():
    loads = rm.busiest_engine(
        [(engine(1, GPU, 0), 70.0), (engine(2, GPU, 0), 60.0), ("_Total", 99.0)]
    )
    assert loads == {GPU: 100.0}


def test_luid_match_is_case_insensitive():
    upper = GPU.replace("d938", "D938")
    assert rm.busiest_engine([(engine(1, upper, 0), 5.0)]) == {GPU: 5.0}


def test_adapter_memory_adds_dedicated_and_shared():
    used = rm.adapter_memory(
        [(f"luid_{GPU}_phys_0", 2048.0), ("garbage", 1.0)],
        [(f"luid_{GPU}_phys_0", 1024.0), (f"luid_{NPU}_phys_0", 512.0)],
    )
    assert used == {GPU: 3072, NPU: 512}


# ── Payload ──────────────────────────────────────────────────────────────────


def sample(cpu: float, gpu: float = 0.0, used: int = 4, total: int = 16) -> rm.Sample:
    return rm.Sample(
        cpu_percent=cpu,
        cpu_ghz=4.512,
        memory_used_bytes=used,
        memory_total_bytes=total,
        devices=[rm.DeviceSample(GPU, "Radeon", "gpu", gpu, memory_used_bytes=2**30)],
    )


def test_payload_before_first_sample_is_not_ready():
    assert rm.to_payload([], 1.0) == {"interval_ms": 1000, "ready": False}


def test_payload_carries_latest_reading_and_history_oldest_first():
    out = rm.to_payload([sample(10.04, 5), sample(20.06, 6, used=8)], 1.0)
    assert out["ready"] is True
    assert out["cpu"] == {"percent": 20.1, "ghz": 4.51, "history": [10.0, 20.1]}
    assert out["memory"] == {
        "used_bytes": 8,
        "total_bytes": 16,
        "percent": 50.0,
        "history": [25.0, 50.0],
    }
    assert out["devices"] == [
        {
            "id": GPU,
            "name": "Radeon",
            "kind": "gpu",
            "percent": 6.0,
            "memory_used_bytes": 2**30,
            "history": [5.0, 6.0],
        }
    ]


def test_payload_validates_against_the_response_model():
    out = rm.to_payload([sample(1.0)], 1.0)
    assert resources_router.ResourceUsage(**out).cpu.percent == 1.0
    assert resources_router.ResourceUsage(**rm.to_payload([], 1.0)).devices == []


# ── Sampler ──────────────────────────────────────────────────────────────────


class FakeProbe:
    def __init__(self, fail_after: int | None = None):
        self.calls = 0
        self.closed = threading.Event()
        self.fail_after = fail_after

    def sample(self) -> rm.Sample:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise OSError("counter vanished")
        return sample(float(self.calls))

    def close(self) -> None:
        self.closed.set()


def test_sampler_starts_on_first_read_and_fills_history():
    probe = FakeProbe()
    sampler = rm.ResourceSampler(lambda: probe, interval_s=0.01)
    try:
        first = sampler.snapshot()
        assert first["ready"] is True
        time.sleep(0.1)
        later = sampler.snapshot()
        assert len(later["cpu"]["history"]) > 1
        assert later["cpu"]["history"] == sorted(later["cpu"]["history"])
    finally:
        sampler.stop()
    assert probe.closed.wait(1)


def test_sampler_history_is_capped():
    probe = FakeProbe()
    sampler = rm.ResourceSampler(lambda: probe, interval_s=0.001)
    try:
        sampler.snapshot()
        deadline = time.monotonic() + 5
        while probe.calls < rm.HISTORY_LEN + 10 and time.monotonic() < deadline:
            sampler.snapshot(wait_s=0)
            time.sleep(0.01)
        assert len(sampler.snapshot()["cpu"]["history"]) == rm.HISTORY_LEN
    finally:
        sampler.stop()


def test_sampler_stops_once_nobody_reads():
    now = [0.0]
    probe = FakeProbe()
    sampler = rm.ResourceSampler(
        lambda: probe, interval_s=0.01, idle_stop_s=5, clock=lambda: now[0]
    )
    sampler.snapshot()
    now[0] = 100.0  # nobody has asked for 100 s
    assert probe.closed.wait(2), "sampler kept running with nobody watching"
    calls = probe.calls
    time.sleep(0.05)
    assert probe.calls == calls


def test_sampler_restarts_after_stopping_idle():
    now = [0.0]
    probes: list[FakeProbe] = []

    def factory():
        probes.append(FakeProbe())
        return probes[-1]

    sampler = rm.ResourceSampler(
        factory, interval_s=0.01, idle_stop_s=5, clock=lambda: now[0]
    )
    sampler.snapshot()
    now[0] = 100.0
    assert probes[0].closed.wait(2)
    assert sampler.snapshot()["ready"] is True
    sampler.stop()
    assert len(probes) == 2


def test_sampler_reports_a_probe_that_cannot_open():
    def broken():
        raise rm.ResourceProbeError("PdhOpenQuery failed (0xc0000bb8)")

    sampler = rm.ResourceSampler(broken, interval_s=0.01)
    with pytest.raises(rm.ResourceProbeError, match="PdhOpenQuery failed"):
        sampler.snapshot()


def test_sampler_reports_a_failed_sample_instead_of_serving_stale_numbers():
    probe = FakeProbe(fail_after=1)
    sampler = rm.ResourceSampler(lambda: probe, interval_s=0.01)
    sampler.snapshot()
    assert probe.closed.wait(2)
    # The next read tries again and reports the failure, not the old sample.
    with pytest.raises(rm.ResourceProbeError, match="counter vanished"):
        sampler.snapshot()


def test_sampler_recovers_when_the_counters_come_back():
    probes = [FakeProbe(fail_after=0), FakeProbe()]
    sampler = rm.ResourceSampler(lambda: probes.pop(0), interval_s=0.01)
    with pytest.raises(rm.ResourceProbeError):
        sampler.snapshot()
    try:
        assert sampler.snapshot()["ready"] is True
    finally:
        sampler.stop()


def test_a_repeating_failure_logs_its_traceback_once(caplog):
    def broken():
        raise rm.ResourceProbeError("PdhAddEnglishCounter failed (0xc0000bb8)")

    sampler = rm.ResourceSampler(broken, interval_s=0.01)
    for _ in range(3):
        with pytest.raises(rm.ResourceProbeError):
            sampler.snapshot()
    tracebacks = [r for r in caplog.records if r.exc_info]
    assert len(tracebacks) == 1
    assert sum("again" in r.getMessage() for r in caplog.records) == 2


# ── Linux sysfs ──────────────────────────────────────────────────────────────


def test_posix_probe_without_a_clock_reading_leaves_the_speed_blank(
    tmp_path, monkeypatch
):
    monkeypatch.delattr(psutil, "cpu_freq", raising=False)  # as on Apple silicon
    s = rm.PosixProbe(drm_root=tmp_path).sample()
    assert s.cpu_ghz is None
    assert 0 <= s.cpu_percent <= 100


def test_posix_probe_reads_amd_gpus_from_sysfs(tmp_path):
    dev = tmp_path / "card1" / "device"
    dev.mkdir(parents=True)
    (dev / "gpu_busy_percent").write_text("42\n")
    (dev / "mem_info_vram_used").write_text(str(2 * 2**30))
    (dev / "mem_info_gtt_used").write_text(str(2**30))
    (dev / "product_name").write_text("AMD Radeon 8060S\n")
    (tmp_path / "card1-DP-1").mkdir()  # a connector, not a GPU
    (tmp_path / "card0" / "device").mkdir(parents=True)  # no busy file: not AMD

    probe = rm.PosixProbe(drm_root=tmp_path)
    s = probe.sample()
    assert [(d.name, d.kind, d.percent, d.memory_used_bytes) for d in s.devices] == [
        ("AMD Radeon 8060S", "gpu", 42.0, 3 * 2**30)
    ]
    assert 0 <= s.cpu_percent <= 100
    assert s.memory_total_bytes == psutil.virtual_memory().total


def test_posix_probe_leaves_a_sleeping_gpu_asleep(tmp_path):
    dev = tmp_path / "card1" / "device"
    (dev / "power").mkdir(parents=True)
    (dev / "gpu_busy_percent").write_text("7")
    (dev / "power" / "runtime_status").write_text("suspended\n")
    probe = rm.PosixProbe(drm_root=tmp_path)
    (dev / "gpu_busy_percent").unlink()  # a read would fail, as on newer kernels
    [gpu] = probe.sample().devices
    assert (gpu.percent, gpu.memory_used_bytes) == (0.0, None)
    (dev / "power" / "runtime_status").write_text("active\n")
    assert "gpu_busy_percent" in probe.sample().devices_error


def test_a_gpu_that_stops_reporting_says_why_and_cpu_still_shows(tmp_path, caplog):
    dev = tmp_path / "card0" / "device"
    dev.mkdir(parents=True)
    (dev / "gpu_busy_percent").write_text("1")
    probe = rm.PosixProbe(drm_root=tmp_path)
    (dev / "gpu_busy_percent").unlink()
    s = probe.sample()
    assert s.devices == []
    assert "gpu_busy_percent" in s.devices_error
    assert 0 <= s.cpu_percent <= 100
    # Stays off and logs the cause once, not every second.
    probe.sample()
    assert len([r for r in caplog.records if "unavailable" in r.getMessage()]) == 1
    payload = rm.to_payload([s], 1.0)
    assert payload["devices"] == [] and "gpu_busy_percent" in payload["devices_error"]


def test_an_optional_part_that_cannot_start_reports_why():
    def no_counters():
        raise rm.ResourceProbeError("PdhAddEnglishCounter failed (0xc0000bb8)")

    part = rm.OptionalPart("GPU and NPU", no_counters)
    assert part.read(lambda state: [1]) == []
    assert part.error == "PdhAddEnglishCounter failed (0xc0000bb8)"


def test_an_optional_part_closes_what_it_opened_when_a_read_breaks():
    closed = []
    part = rm.OptionalPart("network", lambda: "state", close=closed.append)

    def broken(state):
        raise OSError("adapter vanished")

    assert part.read(broken) == []
    assert closed == ["state"] and part.error == "adapter vanished"
    part.close()
    assert closed == ["state"]  # closed once


# ── Route ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(resources_router.router)
    return TestClient(app)


def test_route_needs_the_ui_header(client):
    # conftest gives every TestClient the header; blank it to send none.
    res = client.get("/api/system/resources", headers={"X-Gaia-UI": ""})
    assert res.status_code == 403


def test_route_returns_the_snapshot(client, monkeypatch):
    class Stub:
        def snapshot(self):
            return rm.to_payload([sample(12.0, 3.0)], 1.0)

    monkeypatch.setattr(rm, "get_sampler", lambda: Stub())
    body = client.get("/api/system/resources", headers=UI).json()
    assert body["cpu"]["percent"] == 12.0
    assert body["devices"][0]["kind"] == "gpu"


def test_route_turns_a_probe_failure_into_503(client, monkeypatch):
    class Stub:
        def snapshot(self):
            raise rm.ResourceProbeError("Could not read this PC's resource usage: boom")

    monkeypatch.setattr(rm, "get_sampler", lambda: Stub())
    res = client.get("/api/system/resources", headers=UI)
    assert res.status_code == 503
    assert "boom" in res.json()["detail"]


# ── The real counters on this PC ─────────────────────────────────────────────


class _FakePdh:
    def __init__(self, status: int):
        self.status = status

    def PdhGetFormattedCounterArrayW(self, *args):
        return self.status


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PDH")
@pytest.mark.parametrize("status", [0, 0x800007D1, 0x800007D5])
def test_counter_array_is_empty_while_no_engine_is_in_use(status):
    query = rm._PdhQuery({"engine": r"\GPU Engine(*)\Utilization Percentage"})
    real, query._pdh = query._pdh, _FakePdh(status)
    try:
        assert query.array("engine") == []
    finally:
        query._pdh = real
        query.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PDH")
def test_counter_array_raises_on_a_real_failure():
    query = rm._PdhQuery({"engine": r"\GPU Engine(*)\Utilization Percentage"})
    real, query._pdh = query._pdh, _FakePdh(0xC0000BC6)
    try:
        with pytest.raises(rm.ResourceProbeError, match="0xc0000bc6"):
            query.array("engine")
    finally:
        query._pdh = real
        query.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows performance counters")
def test_windows_probe_reads_real_counters():
    adapters = rm.windows_adapters()
    assert all(a.kind in ("gpu", "npu") and a.name for a in adapters)
    assert "Microsoft Basic Render Driver" not in [a.name for a in adapters]
    probe = rm.WindowsProbe()
    try:
        time.sleep(1)
        s = probe.sample()
    finally:
        probe.close()
    assert 0 <= s.cpu_percent <= 100
    assert s.cpu_ghz is None or 0.2 < s.cpu_ghz < 7
    vm = psutil.virtual_memory()
    assert s.memory_total_bytes == vm.total
    assert abs(s.memory_used_bytes - (vm.total - vm.available)) < 2 * 2**30
    assert [d.key for d in s.devices] == [a.key for a in adapters]
    assert all(0 <= d.percent <= 100 for d in s.devices)
    assert s.networks == [] or all(n.kind and n.name for n in s.networks)
    assert s.devices_error is None and s.networks_error is None


# ── Network ──────────────────────────────────────────────────────────────────


class _Counters:
    def __init__(self, *reads):
        self._reads = list(reads)

    def __call__(self):
        return self._reads.pop(0)


def _nic(sent: int, recv: int, name: str = "Ethernet 3"):
    return {"net_a": (name, "Realtek 2.5GbE", "ethernet", sent, recv)}


def test_network_rate_is_bits_per_second_between_reads():
    clock = iter([0.0, 2.0])
    rates = rm.NetworkRates(
        _Counters(_nic(1000, 5000), _nic(1250, 6000)), clock=lambda: next(clock)
    )
    [net] = rates.sample()
    assert (net.name, net.kind, net.send_bps, net.recv_bps) == (
        "Ethernet 3",
        "ethernet",
        1000.0,  # 250 bytes in 2 s
        4000.0,
    )


def test_a_newly_connected_adapter_starts_at_zero():
    clock = iter([0.0, 1.0])
    rates = rm.NetworkRates(
        _Counters({}, _nic(10**9, 10**9)), clock=lambda: next(clock)
    )
    [net] = rates.sample()
    assert (net.send_bps, net.recv_bps) == (0.0, 0.0)


def test_a_counter_reset_does_not_go_negative():
    clock = iter([0.0, 1.0])
    rates = rm.NetworkRates(
        _Counters(_nic(5000, 5000), _nic(100, 100)), clock=lambda: next(clock)
    )
    [net] = rates.sample()
    assert (net.send_bps, net.recv_bps) == (0.0, 0.0)


def test_payload_carries_network_history_by_adapter():
    def at(send: float, recv: float, present: bool = True) -> rm.Sample:
        s = sample(1.0)
        if present:
            s.networks = [
                rm.NetworkSample("net_a", "Wi-Fi", "Intel", "wifi", send, recv)
            ]
        return s

    out = rm.to_payload([at(0, 0, present=False), at(10.4, 20.6), at(30, 40)], 1.0)
    assert out["networks"] == [
        {
            "id": "net_a",
            "name": "Wi-Fi",
            "description": "Intel",
            "kind": "wifi",
            "send_bps": 30,
            "recv_bps": 40,
            "send_history": [0, 10, 30],
            "recv_history": [0, 21, 40],
        }
    ]
    assert resources_router.ResourceUsage(**out).networks[0].kind == "wifi"


def test_posix_networks_keeps_hardware_interfaces_that_are_up(tmp_path, monkeypatch):
    for name in ("eth0", "wlan0", "eth1"):
        (tmp_path / name / "device").mkdir(parents=True)
    (tmp_path / "wlan0" / "wireless").mkdir()
    (tmp_path / "docker0").mkdir()  # virtual: no device link
    stat = lambda up: SimpleNamespace(isup=up)  # noqa: E731
    io = lambda s, r: SimpleNamespace(bytes_sent=s, bytes_recv=r)  # noqa: E731
    monkeypatch.setattr(
        psutil,
        "net_if_stats",
        lambda: {
            "eth0": stat(True),
            "wlan0": stat(True),
            "eth1": stat(False),
            "docker0": stat(True),
            "lo": stat(True),
        },
    )
    monkeypatch.setattr(
        psutil,
        "net_io_counters",
        lambda pernic: {
            n: io(1, 2) for n in ("eth0", "wlan0", "eth1", "docker0", "lo")
        },
    )
    assert rm.posix_networks(tmp_path) == {
        "net_eth0": ("eth0", "eth0", "ethernet", 1, 2),
        "net_wlan0": ("wlan0", "wlan0", "wifi", 1, 2),
    }


@pytest.mark.skipif(sys.platform != "win32", reason="Windows performance counters")
def test_windows_cpu_and_memory_survive_missing_gpu_counters():
    def no_gpus():
        raise AttributeError("function 'D3DKMTEnumAdapters3' not found")

    def no_networks():
        raise rm.ResourceProbeError("GetIfTable2 failed (0x00000032)")

    probe = rm.WindowsProbe(open_gpus=no_gpus, read_networks=no_networks)
    try:
        time.sleep(1)
        s = probe.sample()
    finally:
        probe.close()
    assert 0 <= s.cpu_percent <= 100 and s.memory_total_bytes > 0
    assert s.devices == [] and "D3DKMTEnumAdapters3" in s.devices_error
    assert s.networks == [] and "GetIfTable2" in s.networks_error


@pytest.mark.skipif(sys.platform != "win32", reason="Windows IP Helper")
def test_windows_networks_lists_connected_hardware_adapters_only():
    nets = rm.windows_networks()
    names = [name for name, *_ in nets.values()]
    assert not any(n.startswith(("vEthernet", "Loopback")) for n in names)
    assert all(
        kind in ("ethernet", "wifi", "network") for _, _, kind, _, _ in nets.values()
    )
    # Not cross-checked against psutil: on Azure VMs the accelerated-networking
    # adapter is up here but not in psutil's view, and both are correct.
    assert all(name for name in names)
    assert all(sent >= 0 and recv >= 0 for *_, sent, recv in nets.values())
