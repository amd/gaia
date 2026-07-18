# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GPU detection in the Agent UI against real captured Lemonade payloads.

The UI's two GPU probes (`/api/system/status` and the onboarding preflight)
both used `isinstance(dev, dict)` and a `"gpu" in key` substring test. Live
Lemonade reports `amd_gpu`/`nvidia_gpu` as *lists*, and Apple Silicon reports
its GPU under `metal` — so on real hardware both probes silently reported a
blank GPU name and blank VRAM (#2244 fixed the same shape mismatch in the CLI).

These tests stub only the HTTP boundary and feed the *real* captured payloads
from tests/fixtures/hardware/, so they pin the shapes rather than a mock's idea
of them.
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from gaia.llm.lemonade_manager import gpu_display_info, system_info_has_gpu
from gaia.ui.routers import onboarding as onboarding_mod
from gaia.ui.server import create_app

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "hardware")


def load_devices(name: str):
    with open(os.path.join(FIXTURES_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)["devices"]


# Real captured payloads -> the GPU the UI should name.
# amd_dgpu_windows lists the APU's integrated graphics *first* and the discrete
# RX 7900 XTX second, with neither reporting vram_gb or `integrated` — the
# discrete card must still win, or a 7900 XTX owner is told they have
# "AMD Radeon(TM) Graphics".
REAL_PAYLOADS = [
    ("lemonade11_amd_dgpu_windows.json", "AMD Radeon RX 7900 XTX", None),
    ("lemonade11_amd_igpu_linux.json", "110501", 0.5),
    ("lemonade11_metal_macos.json", "Apple M3 Max", 51.84),
]


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Routes Lemonade GETs by path suffix; unknown paths 404."""

    def __init__(self, routes):
        self._routes = routes

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, **_kwargs):
        for suffix, payload in self._routes.items():
            if url.endswith(suffix):
                return _FakeResponse(payload)
        return _FakeResponse({}, status_code=404)


@pytest.fixture
def stub_lemonade(monkeypatch):
    """Patch httpx.AsyncClient so the routers' real parsing runs on a fixture."""

    def _apply(routes):
        import httpx

        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(routes)
        )

    return _apply


# ── the shared helper ────────────────────────────────────────────────────


@pytest.mark.parametrize("fixture,expected_name,expected_vram", REAL_PAYLOADS)
def test_gpu_display_info_reads_real_payloads(fixture, expected_name, expected_vram):
    name, vram = gpu_display_info(load_devices(fixture))

    assert name == expected_name
    assert vram == expected_vram


def test_gpu_display_info_no_gpu_reports_none_not_blank():
    name, vram = gpu_display_info(load_devices("cpu_only.json"))

    # None means "not detected"; "" would render as success in the UI.
    assert name is None
    assert vram is None


def test_gpu_display_info_legacy_dict_shape_still_works():
    """The pre-Lemonade-11 dict-keyed shape must keep working."""
    name, vram = gpu_display_info(load_devices("dgpu_only.json"))

    assert name == "AMD Radeon RX 7900 XTX"
    # The legacy fixtures report `memory_mb`, not `vram_gb`. We deliberately
    # don't read it: it is a synthetic shape real Lemonade 11 no longer emits,
    # and an undetected VRAM is honest where a unit-guess would not be.
    assert vram is None


def test_gpu_display_info_agrees_with_system_info_has_gpu_on_list_payload():
    """Both helpers must accept the top-level-list shape, or UI and CLI disagree."""
    with open(
        os.path.join(FIXTURES_DIR, "hardware_list.json"), "r", encoding="utf-8"
    ) as f:
        devices = json.load(f)["devices"]

    assert system_info_has_gpu(devices) is True
    assert gpu_display_info(devices) == ("Radeon Vega", None)


def test_gpu_display_info_skips_unavailable_gpu():
    """An entry present but available:false is not a detected GPU."""
    devices = {
        "nvidia_gpu": [{"available": False, "error": "no NVIDIA GPU", "name": ""}],
        "cpu": {"available": True},
    }

    assert gpu_display_info(devices) == (None, None)


def test_gpu_display_info_prefers_larger_vram():
    devices = {
        "amd_gpu": [
            {"available": True, "name": "iGPU", "vram_gb": 0.5, "integrated": True},
            {"available": True, "name": "dGPU", "vram_gb": 24.0},
        ]
    }

    assert gpu_display_info(devices) == ("dGPU", 24.0)


def test_gpu_display_info_non_numeric_vram_is_undetected_not_zero(caplog):
    devices = {"amd_gpu": [{"available": True, "name": "GPU", "vram_gb": "lots"}]}

    name, vram = gpu_display_info(devices)

    assert name == "GPU"
    assert vram is None  # never a silently-wrong 0.0
    assert "vram_gb" in caplog.text


# ── /api/system/status ───────────────────────────────────────────────────


@pytest.mark.parametrize("fixture,expected_name,expected_vram", REAL_PAYLOADS)
def test_system_status_reports_gpu(
    fixture, expected_name, expected_vram, stub_lemonade
):
    stub_lemonade(
        {
            "/health": {"model_loaded": "Gemma-4-E4B-it-GGUF"},
            "/models": {"data": []},
            "/system-info": {"devices": load_devices(fixture)},
        }
    )

    body = TestClient(create_app(db_path=":memory:")).get("/api/system/status").json()

    assert body["gpu_name"] == expected_name
    assert body["gpu_vram_gb"] == expected_vram


def test_system_status_no_gpu_reports_null(stub_lemonade):
    stub_lemonade(
        {
            "/health": {"model_loaded": "Gemma-4-E4B-it-GGUF"},
            "/models": {"data": []},
            "/system-info": {"devices": load_devices("cpu_only.json")},
        }
    )

    body = TestClient(create_app(db_path=":memory:")).get("/api/system/status").json()

    assert body["gpu_name"] is None
    assert body["gpu_vram_gb"] is None


def test_system_status_detects_npu_from_real_payload(stub_lemonade):
    """NPU detection must survive the GPU refactor (linux fixture has an NPU)."""
    stub_lemonade(
        {
            "/health": {"model_loaded": "Gemma-4-E4B-it-GGUF"},
            "/models": {"data": []},
            "/system-info": {"devices": load_devices("lemonade11_amd_igpu_linux.json")},
        }
    )

    body = TestClient(create_app(db_path=":memory:")).get("/api/system/status").json()

    assert "npu" in body["detected_devices"]


def test_system_status_no_npu_on_macos(stub_lemonade):
    stub_lemonade(
        {
            "/health": {"model_loaded": "Gemma-4-E4B-it-GGUF"},
            "/models": {"data": []},
            "/system-info": {"devices": load_devices("lemonade11_metal_macos.json")},
        }
    )

    body = TestClient(create_app(db_path=":memory:")).get("/api/system/status").json()

    assert "npu" not in body["detected_devices"]


# ── onboarding preflight probe ───────────────────────────────────────────


@pytest.mark.parametrize("fixture,expected_name,expected_vram", REAL_PAYLOADS)
@pytest.mark.asyncio
async def test_onboarding_probe_reports_gpu(
    fixture, expected_name, expected_vram, stub_lemonade
):
    stub_lemonade({"/system-info": {"devices": load_devices(fixture)}})

    result = await onboarding_mod._probe_lemonade_devices()

    assert result["lemonade_running"] is True
    assert result["gpu_name"] == expected_name
    assert result["gpu_vram_gb"] == expected_vram


@pytest.mark.asyncio
async def test_onboarding_probe_no_gpu_reports_none(stub_lemonade):
    stub_lemonade({"/system-info": {"devices": load_devices("cpu_only.json")}})

    result = await onboarding_mod._probe_lemonade_devices()

    assert result["gpu_name"] is None
    assert result["gpu_vram_gb"] is None


@pytest.mark.asyncio
async def test_onboarding_probe_npu_flags_from_real_payloads(stub_lemonade):
    stub_lemonade(
        {"/system-info": {"devices": load_devices("lemonade11_amd_igpu_linux.json")}}
    )

    result = await onboarding_mod._probe_lemonade_devices()

    assert result["npu_detected"] is True


@pytest.mark.asyncio
async def test_onboarding_probe_npu_absent_is_false_not_unknown(stub_lemonade):
    """Lemonade answered, so a missing NPU is a definite False (not None)."""
    stub_lemonade(
        {"/system-info": {"devices": load_devices("lemonade11_metal_macos.json")}}
    )

    result = await onboarding_mod._probe_lemonade_devices()

    assert result["npu_detected"] is False
