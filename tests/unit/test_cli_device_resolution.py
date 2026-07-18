# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Failing tests for the device-resolution control-flow bug (#2241).

`gaia chat`'s NPU/GPU/CPU degradation logic lived inline in
``async_main()`` as an ``if/elif`` chain. When an NPU-configured, non-explicit
request found no NPU, it reassigned the working device to "gpu" and then fell
straight out of the ``elif`` chain WITHOUT ever probing for an actual GPU —
silently mislabeling a CPU-only machine as "GPU".

These tests target the extracted, pure ``resolve_effective_device()`` function
that fixes this by letting the npu->gpu reassignment fall through to the gpu
check in the same call. The function does not exist yet — import failure here
is the expected red-phase result; do not implement it in this change.
"""

import json
import os

import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "hardware")


def load_devices(name: str):
    with open(os.path.join(FIXTURES_DIR, name), "r") as f:
        return json.load(f)["devices"]


# ── npu -> gpu fallthrough (the actual bug) ──────────────────────────────────


def test_npu_unavailable_no_gpu_falls_through_to_cpu(capsys):
    from gaia.cli import resolve_effective_device

    devices = load_devices("cpu_only.json")
    result = resolve_effective_device("npu", False, devices)

    assert result == "cpu"
    assert "No GPU detected" in capsys.readouterr().out


def test_npu_unavailable_gpu_present_ends_on_gpu(capsys):
    from gaia.cli import resolve_effective_device

    devices = load_devices("lemonade11_amd_dgpu_windows.json")
    result = resolve_effective_device("npu", False, devices)

    assert result == "gpu"
    assert "No GPU detected" not in capsys.readouterr().out


def test_explicit_npu_unavailable_exits_nonzero_no_fallback(capsys):
    from gaia.cli import resolve_effective_device

    devices = load_devices("lemonade11_amd_dgpu_windows.json")
    with pytest.raises(SystemExit) as exc_info:
        resolve_effective_device("npu", True, devices)

    assert exc_info.value.code == 1
    assert "npu not available" in capsys.readouterr().err.lower()


def test_no_config_file_default_device_used_without_crash(tmp_path, monkeypatch):
    """Sanity check: GaiaConfig.load() with no file on disk still resolves
    default_device == "gpu" without crashing (pre-existing invariant that this
    control-flow fix builds on; see test_cli_config.py for the broader suite).
    """
    from gaia import config as config_mod
    from gaia.config import GaiaConfig

    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "missing.json")

    cfg = GaiaConfig.load()
    assert cfg.default_device == "gpu"


# ── direct gpu-requested paths (unaffected by the npu bug, same function) ────


def test_gpu_requested_no_gpu_falls_to_cpu(capsys):
    from gaia.cli import resolve_effective_device

    devices = load_devices("cpu_only.json")
    result = resolve_effective_device("gpu", False, devices)

    assert result == "cpu"
    assert "No GPU detected" in capsys.readouterr().out


def test_gpu_requested_metal_counts_as_gpu(capsys):
    from gaia.cli import resolve_effective_device

    devices = load_devices("lemonade11_metal_macos.json")
    result = resolve_effective_device("gpu", False, devices)

    assert result == "gpu"
    assert capsys.readouterr().out == ""


# ── cpu requires no validation ────────────────────────────────────────────


def test_cpu_requested_returns_unchanged_no_output(capsys):
    from gaia.cli import resolve_effective_device

    devices = load_devices("cpu_only.json")
    result = resolve_effective_device("cpu", False, devices)

    assert result == "cpu"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ── explicit + available npu is untouched ────────────────────────────────


def test_explicit_npu_available_returns_unchanged_no_output(capsys):
    from gaia.cli import resolve_effective_device

    devices = load_devices("lemonade11_amd_igpu_linux.json")
    result = resolve_effective_device("npu", True, devices)

    assert result == "npu"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
