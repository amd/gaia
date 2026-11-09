# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The chat's resource strip: how loads are added up, the sampler, the route."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
import threading
import time
from pathlib import Path
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


@pytest.fixture(autouse=True)
def no_real_npu(monkeypatch):
    """Keep a PosixProbe off this machine's own NPU unless a test hands it one."""
    monkeypatch.setattr(rm, "linux_npus", lambda root=Path("/"): [])


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


# ── Linux NPUs (amdxdna) ─────────────────────────────────────────────────────


def sensor(kind: int, value: int, label: str = "") -> bytes:
    return rm._SENSOR.pack(label.encode(), value, 0, 0, 0, b"", b"", 0, kind)


class FakeFcntl:
    """Answers DRM_IOCTL_AMDXDNA_GET_INFO the way the kernel does."""

    def __init__(self, sensors=(), error=None):
        self.sensors = b"".join(sensors)
        self.error = error
        self.calls = []

    def ioctl(self, fd, request, arg, mutate):
        param, size, address = rm._GET_INFO.unpack(arg)
        self.calls.append((fd, request, param, size, mutate))
        if self.error is not None:
            raise OSError(self.error, "fake")
        data = self.sensors[:size]
        ctypes.memmove(address, data, len(data))
        arg[:] = rm._GET_INFO.pack(param, len(data), address)
        return 0


def fake_fcntl(monkeypatch, **kwargs) -> FakeFcntl:
    fake = FakeFcntl(**kwargs)
    monkeypatch.setitem(sys.modules, "fcntl", fake)
    return fake


def test_npu_load_is_its_busiest_column(monkeypatch):
    fake = fake_fcntl(
        monkeypatch,
        sensors=[
            sensor(0, 1500, "Total Power"),  # mW, not a load
            sensor(1, 20, "Column 0 Utilization"),
            sensor(1, 65, "Column 1 Utilization"),
            sensor(1, 0, "Column 2 Utilization"),
        ],
    )
    assert rm.amdxdna_busy_percent(7) == 65.0
    [(fd, request, param, size, mutate)] = fake.calls
    # DRM_IOWR('d', 0x40 + DRM_AMDXDNA_GET_INFO, struct amdxdna_drm_get_info)
    assert (fd, request, param, mutate) == (7, 0xC0106447, 4, True)
    assert size == 9 * 168  # power plus 8 columns, each struct amdxdna_drm_query_sensor
    assert rm._SENSOR.size == 168


def test_npu_load_caps_at_100(monkeypatch):
    fake_fcntl(monkeypatch, sensors=[sensor(0, 1), sensor(1, 250)])
    assert rm.amdxdna_busy_percent(3) == 100.0


@pytest.mark.parametrize(
    "err", [errno.EOPNOTSUPP, errno.EINVAL, errno.ENOTTY, errno.ENODEV]
)
def test_a_kernel_without_npu_usage_says_which_kernel_has_it(monkeypatch, err):
    # ENODEV is what amd_pmf_get_npu_data returns when amd_pmf isn't running.
    fake_fcntl(monkeypatch, error=err)
    with pytest.raises(rm.ResourceProbeError, match="Linux 7.1.*AMD PMF"):
        rm.amdxdna_busy_percent(3)


def test_a_driver_that_reports_only_power_says_it_has_no_usage(monkeypatch):
    fake_fcntl(monkeypatch, sensors=[sensor(0, 900, "Total Power")])
    with pytest.raises(rm.ResourceProbeError, match="Linux 7.1"):
        rm.amdxdna_busy_percent(3)


def test_another_driver_failure_names_the_cause(monkeypatch):
    fake_fcntl(monkeypatch, error=errno.EIO)
    with pytest.raises(rm.ResourceProbeError, match="couldn't report usage"):
        rm.amdxdna_busy_percent(3)


def test_npus_are_found_with_their_dev_nodes(monkeypatch, tmp_path):
    monkeypatch.undo()  # the real linux_npus, over a fake driver list
    from gaia.llm import npu_linux

    dev = tmp_path / "sys/class/accel/accel0/device"
    monkeypatch.setattr(npu_linux, "bound_devices", lambda root: [("accel0", dev)])
    assert rm.linux_npus(tmp_path) == [
        ("accel0", dev, tmp_path / "dev" / "accel" / "accel0")
    ]


class FakeNpu:
    """One NPU's sysfs power state, a clock, and the driver's answers."""

    def __init__(self, tmp_path, readings, status="active", autosuspend_ms="5000"):
        self.dev = tmp_path / "accel0"
        (self.dev / "power").mkdir(parents=True)
        if autosuspend_ms is not None:
            (self.dev / "power" / "autosuspend_delay_ms").write_text(autosuspend_ms)
        self.status(status)
        self.readings = list(readings)
        self.queried = []
        self.closed = []
        self.now = 100.0

    def status(self, value):
        (self.dev / "power" / "runtime_status").write_text(value + "\n")

    def busy(self, fd):
        self.queried.append(self.now)
        value = self.readings.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def reader(self):
        return rm.AmdxdnaNpus(
            [("accel0", self.dev, self.dev.parent / "dev/accel/accel0")],
            open_node=lambda node: 11,
            busy=self.busy,
            close_fd=self.closed.append,
            clock=lambda: self.now,
        )


def percents(reader):
    return [d.percent for d in reader.sample()]


def test_posix_probe_reads_the_npu_next_to_the_gpus(tmp_path):
    npu = FakeNpu(tmp_path, [12.0, 37.0])
    probe = rm.PosixProbe(drm_root=tmp_path / "drm", npus=npu.reader)
    s = probe.sample()
    assert [(d.key, d.name, d.kind, d.percent) for d in s.devices] == [
        ("npu_accel0", "AMD NPU (accel0)", "npu", 37.0)
    ]
    assert s.npus_error is None
    assert rm.to_payload([s], 1.0)["devices"][0]["kind"] == "npu"


def test_a_busy_npu_is_read_every_sample(tmp_path):
    npu = FakeNpu(tmp_path, [40.0, 55.0, 60.0])
    reader = npu.reader()
    for _ in range(2):
        npu.now += 1
        reader.sample()
    assert npu.queried == [100.0, 101.0, 102.0]


def test_an_idle_npu_is_left_to_fall_asleep(tmp_path):
    # Each query restarts the 5 s autosuspend timer; asking every second
    # would keep an idle NPU awake for as long as the strip is open.
    npu = FakeNpu(tmp_path, [0.0])
    reader = npu.reader()
    for _ in range(5):
        npu.now += 1
        assert percents(reader) == [0.0]
    npu.status("suspended")  # the timer ran out
    for _ in range(10):
        npu.now += 1
        assert percents(reader) == [0.0]
    assert npu.queried == [100.0]  # only the first look


def test_an_idle_npu_something_else_keeps_awake_is_still_checked(tmp_path):
    # Still awake after its timer: FastFlowLM holds it, so asking costs nothing.
    npu = FakeNpu(tmp_path, [0.0, 0.0, 70.0, 80.0])
    reader = npu.reader()
    npu.now += 6.0  # 5 s autosuspend plus a second
    assert percents(reader) == [0.0]
    npu.now += 6.0
    assert percents(reader) == [70.0]
    npu.now += 1.0
    assert percents(reader) == [80.0]  # busy again: every sample
    assert npu.queried == [100.0, 106.0, 112.0, 113.0]


def test_a_sleeping_npu_is_never_woken(tmp_path):
    npu = FakeNpu(tmp_path, [], status="suspended")
    reader = npu.reader()  # no look at start either
    for _ in range(3):
        npu.now += 10
        assert percents(reader) == [0.0]
    assert npu.queried == []


def test_an_npu_woken_by_something_else_is_read_at_once(tmp_path):
    npu = FakeNpu(tmp_path, [90.0], status="suspended")
    reader = npu.reader()
    npu.status("active")
    npu.now += 1
    assert percents(reader) == [90.0]


def test_an_npu_without_autosuspend_is_settled_a_second_after_a_query(tmp_path):
    npu = FakeNpu(tmp_path, [0.0, 0.0], autosuspend_ms=None)
    reader = npu.reader()
    npu.now += 0.5
    assert percents(reader) == [0.0] and len(npu.queried) == 1
    npu.now += 1.0
    reader.sample()
    assert len(npu.queried) == 2


def test_an_npu_that_cannot_report_usage_says_why_and_the_gpu_still_shows(tmp_path):
    drm = tmp_path / "drm"
    gpu = drm / "card1" / "device"
    gpu.mkdir(parents=True)
    (gpu / "gpu_busy_percent").write_text("42")
    npu = FakeNpu(tmp_path, [rm.ResourceProbeError(rm._NO_USAGE)])

    probe = rm.PosixProbe(drm_root=drm, npus=npu.reader)
    s = probe.sample()
    assert [d.kind for d in s.devices] == ["gpu"]
    assert s.devices_error is None
    assert "Linux 7.1" in s.npus_error
    assert npu.closed == [11]  # the device it opened is closed again
    payload = rm.to_payload([s], 1.0)
    assert "Linux 7.1" in payload["npus_error"]
    resources_router.ResourceUsage(**payload)


def test_an_npu_that_breaks_later_closes_and_says_why(tmp_path):
    npu = FakeNpu(tmp_path, [3.0, 5.0, rm.ResourceProbeError("boom")])
    probe = rm.PosixProbe(drm_root=tmp_path / "drm", npus=npu.reader)
    assert probe.sample().devices[0].percent == 5.0
    s = probe.sample()
    assert s.devices == [] and s.npus_error == "boom"
    assert npu.closed == [11]


def test_a_gpu_that_fails_does_not_hand_its_history_to_the_npu():
    gpu = rm.DeviceSample("gpu0", "Radeon", "gpu", 90.0)
    npu = rm.DeviceSample("npu_accel0", "AMD NPU (accel0)", "npu", 10.0)
    before = rm.Sample(1.0, None, 1, 2, devices=[gpu, npu])
    after = rm.Sample(1.0, None, 1, 2, devices=[npu], devices_error="gone")
    [out] = rm.to_payload([before, after], 1.0)["devices"]
    assert out["id"] == "npu_accel0" and out["history"] == [10.0, 10.0]


def test_an_npu_that_cannot_be_opened_says_so(tmp_path):
    with pytest.raises(rm.ResourceProbeError, match="Can't open"):
        rm._open_npu(tmp_path / "dev/accel/accel9")


def test_an_npu_this_account_may_not_open_says_how_to_fix_it(tmp_path, monkeypatch):
    def refuse(path, flags):
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(rm.os, "open", refuse)
    from gaia.llm import npu_linux

    monkeypatch.setattr(npu_linux, "_group_name", lambda gid: "render")
    node = tmp_path / "accel0"
    node.write_text("")
    with pytest.raises(rm.ResourceProbeError) as e:
        rm._open_npu(node)
    assert "can't open the NPU" in str(e.value)
    assert "render group" in str(e.value)


def test_closing_the_probe_closes_the_npu(tmp_path):
    npu = FakeNpu(tmp_path, [0.0])
    probe = rm.PosixProbe(drm_root=tmp_path / "drm", npus=npu.reader)
    probe.close()
    assert npu.closed == [11]


def test_closing_keeps_going_when_one_npu_will_not_close(tmp_path, caplog):
    closed = []

    def close_fd(fd):
        if fd == 1:
            raise OSError(errno.EBADF, "Bad file descriptor")
        closed.append(fd)

    fds = iter([1, 2])
    dev = tmp_path / "accel0"
    (dev / "power").mkdir(parents=True)
    (dev / "power" / "runtime_status").write_text("suspended\n")
    reader = rm.AmdxdnaNpus(
        [("accel0", dev, dev), ("accel1", dev, dev)],
        open_node=lambda node: next(fds),
        busy=lambda fd: 0.0,
        close_fd=close_fd,
    )
    reader.close()
    assert closed == [2]
    assert "closing NPU accel0 failed" in caplog.text


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="needs a Linux NPU driver"
)
def test_this_pcs_npu_answers_the_query(monkeypatch):
    """The real driver contract, on a PC with an amdxdna NPU this account can open."""
    monkeypatch.undo()  # this machine's own NPU
    found = [f for f in rm.linux_npus() if os.access(f[2], os.R_OK | os.W_OK)]
    if not found:
        pytest.skip("no amdxdna NPU this account can open")
    fd = rm._open_npu(found[0][2])
    try:
        percent = rm.amdxdna_busy_percent(fd)
    except rm.ResourceProbeError as e:
        if "Linux 7.1" in str(e):
            pytest.skip(f"this kernel has no NPU usage sensor: {e}")
        raise
    finally:
        os.close(fd)
    assert 0.0 <= percent <= 100.0


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
