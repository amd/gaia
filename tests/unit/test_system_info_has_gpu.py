# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GPU-presence probe against real-shape Lemonade 11 ``system_info`` payloads.

Regression guard for #2241: live Lemonade returns ``amd_gpu``/``nvidia_gpu`` as
*lists* and Apple GPUs under ``metal``. The old inline probe required a dict and
a ``"gpu"`` substring, so it reported "No GPU detected" on every machine.
"""

import json
import os

import pytest

from gaia.llm.lemonade_manager import system_info_has_gpu

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "hardware")


def load_devices(name: str):
    with open(os.path.join(FIXTURES_DIR, name), "r") as f:
        return json.load(f)["devices"]


@pytest.mark.parametrize(
    "fixture",
    [
        "lemonade11_amd_igpu_linux.json",  # amd_gpu as a 1-entry list
        "lemonade11_amd_dgpu_windows.json",  # amd_gpu as a 2-entry list (RX 7900 XTX)
        "lemonade11_metal_macos.json",  # GPU only under `metal`
    ],
)
def test_gpu_present_on_real_lemonade_payloads(fixture):
    """Every GPU-capable machine's payload is detected as GPU-present."""
    assert system_info_has_gpu(load_devices(fixture)) is True


def test_cpu_only_reports_no_gpu():
    assert system_info_has_gpu({"cpu": {"available": True}}) is False


def test_unavailable_nvidia_list_does_not_count():
    """A present-but-``available: false`` discrete GPU must not report present."""
    devices = {
        "cpu": {"available": True},
        "nvidia_gpu": [{"available": False, "error": "No NVIDIA discrete GPU found"}],
    }
    assert system_info_has_gpu(devices) is False


def test_empty_amd_gpu_list_without_metal_reports_no_gpu():
    assert system_info_has_gpu({"amd_gpu": [], "cpu": {"available": True}}) is False


def test_legacy_list_shaped_devices_still_detected():
    """The pre-11 list-of-device-dicts shape keeps working."""
    devices = [
        {"device_type": "amd_igpu", "name": "Radeon Vega"},
        {"device_type": "cpu"},
    ]
    assert system_info_has_gpu(devices) is True
