# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.hub.compatibility — the system-requirements checker."""

import pytest

from gaia.hub import compatibility
from gaia.hub.compatibility import check_compatibility


def test_no_requirements_is_compatible():
    report = check_compatibility({"platforms": []})
    assert report.compatible is True
    assert report.platform_ok is True
    assert report.blockers == []


def test_current_platform_passes():
    current = compatibility.detect_platform()
    report = check_compatibility({"platforms": [current]})
    assert report.platform_ok is True
    assert report.compatible is True


def test_wrong_platform_is_blocker():
    current = compatibility.detect_platform()
    others = [
        p
        for p in ("win-x64", "linux-x64", "darwin-arm64", "linux-arm64")
        if p != current
    ]
    report = check_compatibility({"platforms": others})
    assert report.platform_ok is False
    assert report.compatible is False
    assert any("not supported" in b for b in report.blockers)


def test_unsupported_platform_detected_none(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_platform", lambda: None)
    report = check_compatibility({"platforms": ["win-x64"]})
    assert report.platform_ok is False
    assert report.compatible is False


def test_low_memory_is_warning_not_blocker(monkeypatch):
    # Plenty of disk, tiny RAM -> warn (advisory), still compatible.
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 2.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility({"platforms": [], "min_memory_gb": 16})
    assert report.memory_ok is False
    assert report.compatible is True  # not a hard blocker
    assert any("RAM" in w for w in report.warnings)


def test_insufficient_disk_is_blocker(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 0.5)
    report = check_compatibility({"platforms": [], "min_disk_gb": 50})
    assert report.disk_ok is False
    assert report.compatible is False
    assert any("disk" in b.lower() for b in report.blockers)


def test_disk_estimated_from_download_size(monkeypatch):
    # No explicit min_disk_gb: estimate from download size * headroom.
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 0.001)
    huge = 5 * 1024**3  # 5 GB artifact -> ~15 GB estimate > 0.001 GB free
    report = check_compatibility({"platforms": []}, download_size_bytes=huge)
    assert report.disk_ok is False
    assert report.compatible is False


def test_npu_and_gpu_emit_warnings(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility({"platforms": [], "npu": True, "gpu_vram_gb": 8})
    assert report.compatible is True
    assert any("NPU" in w for w in report.warnings)
    assert any("VRAM" in w for w in report.warnings)


def test_report_to_dict_round_trips(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 100.0)
    report = check_compatibility({"platforms": []})
    d = report.to_dict()
    assert set(
        ["compatible", "platform_ok", "memory_ok", "disk_ok", "warnings", "blockers"]
    ).issubset(d)
    # Detected NPU/GPU fields are always present (default None).
    assert "detected_npu" in d
    assert "detected_gpu_vram_gb" in d


# ---------------------------------------------------------------------------
# Detected NPU / GPU wiring (#1727): a real hardware scan turns the advisory
# NPU/GPU checks into concrete pass/warning states instead of blanket
# "cannot verify" — but never a silent pass.
# ---------------------------------------------------------------------------


def test_detected_npu_present_suppresses_warning(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility({"platforms": [], "npu": True}, detected_npu=True)
    assert report.compatible is True
    assert not any("NPU" in w for w in report.warnings)
    assert report.detected_npu is True


def test_detected_npu_absent_is_named_warning(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility({"platforms": [], "npu": True}, detected_npu=False)
    assert report.compatible is True  # advisory, not a hard blocker
    assert any("none was detected" in w for w in report.warnings)


def test_unprobed_npu_keeps_cannot_verify_warning(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility({"platforms": [], "npu": True})
    assert any("cannot verify NPU" in w for w in report.warnings)


def test_detected_gpu_vram_below_requirement_warns(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility(
        {"platforms": [], "gpu_vram_gb": 16}, detected_gpu_vram_gb=8.0
    )
    assert any("16 GB of GPU VRAM" in w and "8" in w for w in report.warnings)


def test_detected_gpu_vram_meets_requirement_no_warning(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility(
        {"platforms": [], "gpu_vram_gb": 8}, detected_gpu_vram_gb=16.0
    )
    assert not any("VRAM" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# current_platform_key: npm/artifact-filename style keys (#2084)
#
# Distinct from detect_platform()/current_platform()'s hub-triple vocabulary
# ("win-x64", "darwin-arm64") used for requirements.platforms gates — this is
# the npm-namespace vocabulary ("win32-x64", "darwin-arm64") used to select an
# entry from a manifest's versions[v].artifacts[] by filename suffix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plat,arch,expected",
    [
        ("win32", "AMD64", "win32-x64"),
        ("win32", "amd64", "win32-x64"),
        ("win32", "x86_64", "win32-x64"),
        ("win64", "x64", "win32-x64"),
        ("darwin", "arm64", "darwin-arm64"),
        ("darwin", "aarch64", "darwin-arm64"),
        ("darwin", "x86_64", "darwin-x64"),
        ("darwin", "amd64", "darwin-x64"),
        ("linux", "x86_64", "linux-x64"),
        ("linux2", "amd64", "linux-x64"),
        ("linux", "aarch64", "linux-arm64"),
    ],
)
def test_current_platform_key_mapping(plat, arch, expected):
    assert compatibility.current_platform_key(plat, arch) == expected


def test_current_platform_key_no_override_returns_nonempty_string():
    key = compatibility.current_platform_key()
    assert isinstance(key, str)
    assert key
