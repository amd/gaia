# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.hub.compatibility — the system-requirements checker."""

import math

import pytest

from gaia.hub import compatibility
from gaia.hub.compatibility import check_compatibility


@pytest.fixture
def off_mac(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_platform", lambda: "linux-x64")


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


_GB = 1024**3


def _free_disk(monkeypatch, gb):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: gb)


def _disk_ok(size_bytes, kind):
    report = check_compatibility(
        {"platforms": []}, download_size_bytes=size_bytes, artifact_kind=kind
    )
    return report.disk_ok


def test_binary_install_needs_about_its_own_size(monkeypatch):
    # A 10 GB binary is written once; 12 GB free is plenty for it.
    _free_disk(monkeypatch, 12.0)
    assert _disk_ok(10 * _GB, "binary") is True


@pytest.mark.parametrize("kind", ["wheel", "cpp"])
def test_unpacked_installs_keep_triple_headroom(monkeypatch, kind):
    # The same 12 GB is not enough to unpack 10 GB of wheel or archive.
    _free_disk(monkeypatch, 12.0)
    assert _disk_ok(10 * _GB, kind) is False
    _free_disk(monkeypatch, 30.0)
    assert _disk_ok(10 * _GB, kind) is True


@pytest.mark.parametrize("kind,factor", [("binary", 1.1), ("wheel", 3.0)])
def test_disk_boundary_is_inclusive(monkeypatch, kind, factor):
    size = 10 * _GB
    required = size * factor / _GB
    _free_disk(monkeypatch, required)
    assert _disk_ok(size, kind) is True
    _free_disk(monkeypatch, math.nextafter(required, 0.0))
    assert _disk_ok(size, kind) is False


def test_binary_below_ten_percent_headroom_is_blocked(monkeypatch):
    _free_disk(monkeypatch, 10.5)
    report = check_compatibility(
        {"platforms": []}, download_size_bytes=10 * _GB, artifact_kind="binary"
    )
    assert report.compatible is False
    assert any("~11.0 GB" in b for b in report.blockers)


def test_zero_min_disk_does_not_disable_size_check(monkeypatch):
    # Published manifests carry min_disk_gb: 0; a 54 MB binary on 10 MB free
    # must still be blocked, with the sizes in MB.
    _free_disk(monkeypatch, 10 / 1024)
    report = check_compatibility(
        {"platforms": [], "min_disk_gb": 0},
        download_size_bytes=54.4 * 1024**2,
        artifact_kind="binary",
    )
    assert report.disk_ok is False
    assert any(
        "needs ~60 MB but only 10 MB is free" in b for b in report.blockers
    ), report.blockers


def test_declared_min_disk_below_estimate_uses_estimate(monkeypatch):
    _free_disk(monkeypatch, 10.5)
    report = check_compatibility(
        {"platforms": [], "min_disk_gb": 1},
        download_size_bytes=10 * _GB,
        artifact_kind="binary",
    )
    assert report.disk_ok is False
    assert any("~11.0 GB" in b for b in report.blockers)


def test_declared_min_disk_above_estimate_wins(monkeypatch):
    _free_disk(monkeypatch, 5.0)
    report = check_compatibility(
        {"platforms": [], "min_disk_gb": 8},
        download_size_bytes=1 * _GB,
        artifact_kind="binary",
    )
    assert report.disk_ok is False
    assert any("~8.0 GB" in b for b in report.blockers)


def test_unknown_artifact_kind_is_refused():
    with pytest.raises(ValueError, match="Unknown artifact kind 'appimage'"):
        check_compatibility({"platforms": []}, artifact_kind="appimage")


def test_installer_gate_charges_binary_by_kind(monkeypatch, tmp_path):
    from gaia.hub import installer

    _free_disk(monkeypatch, 12.0)
    binary = {"size_bytes": 10 * _GB, "artifact_kind": installer.ARTIFACT_KIND_BINARY}
    installer._machine_gate({"requirements": {"platforms": []}}, binary, tmp_path)

    wheel = {"size_bytes": 10 * _GB, "artifact_kind": installer.ARTIFACT_KIND_WHEEL}
    with pytest.raises(installer.DiskSpaceError):
        installer._machine_gate({"requirements": {"platforms": []}}, wheel, tmp_path)


@pytest.mark.usefixtures("off_mac")
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


@pytest.mark.usefixtures("off_mac")
def test_detected_npu_absent_is_named_warning(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility({"platforms": [], "npu": True}, detected_npu=False)
    assert report.compatible is True  # advisory, not a hard blocker
    assert any("none was detected" in w for w in report.warnings)


@pytest.mark.usefixtures("off_mac")
def test_unprobed_npu_keeps_cannot_verify_warning(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility({"platforms": [], "npu": True})
    assert any("cannot verify NPU" in w for w in report.warnings)


@pytest.mark.usefixtures("off_mac")
def test_failed_npu_probe_does_not_suggest_driver_remediation(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility(
        {"platforms": [], "npu": True},
        npu_probe_error="Lemonade system-info query failed",
    )
    assert any("could not query Lemonade" in w for w in report.warnings)
    assert not any("driver is installed" in w for w in report.warnings)


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
# subject parameter + NPU-optional gating (#2396): the hub-install path keeps
# its "This agent…" wording, but the onboarding preflight passes its own
# subject and a capable GPU turns the NPU warning purely informational.
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("off_mac")
def test_npu_absent_default_subject_keeps_agent_wording(monkeypatch):
    # Regression guard: the per-agent hub-install path is unchanged when no
    # capable GPU is present (acceptance criterion #3).
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility({"platforms": [], "npu": True}, detected_npu=False)
    assert any(
        "This agent requests an NPU" in w and "Ryzen AI NPU driver" in w
        for w in report.warnings
    )


@pytest.mark.usefixtures("off_mac")
def test_npu_absent_with_capable_gpu_is_informational(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility(
        {"platforms": [], "npu": True},
        detected_npu=False,
        detected_gpu_vram_gb=24.0,
        subject="The recommended model",
    )
    assert report.compatible is True
    npu_warnings = [w for w in report.warnings if "NPU" in w]
    assert npu_warnings, "expected an informational NPU line"
    joined = " ".join(npu_warnings)
    # No agent framing and no driver-install CTA on a dGPU box.
    assert "agent" not in joined.lower()
    assert "install" not in joined.lower()
    assert "optional" in joined.lower()


@pytest.mark.parametrize(
    "scan",
    [
        {"detected_npu": False, "detected_gpu_vram_gb": 24.0},
        {"detected_npu": False},
        {"npu_probe_error": "Lemonade system-info query failed"},
        {},
    ],
)
def test_mac_never_gets_an_npu_line(monkeypatch, scan):
    monkeypatch.setattr(compatibility, "detect_platform", lambda: "darwin-arm64")
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    report = check_compatibility({"platforms": [], "npu": True}, **scan)
    assert not any("NPU" in w for w in report.warnings)


def test_subject_flows_into_memory_and_vram_warnings(monkeypatch):
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 4.0)
    report = check_compatibility(
        {"platforms": [], "min_memory_gb": 16, "gpu_vram_gb": 24},
        detected_gpu_vram_gb=8.0,
        subject="The recommended model",
    )
    joined = " ".join(report.warnings)
    assert "The recommended model recommends 16 GB RAM" in joined
    assert "The recommended model recommends 24 GB of GPU VRAM" in joined
    assert "This agent" not in joined


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
