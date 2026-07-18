# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the first-run onboarding router (gaia.ui.routers.onboarding)."""

import pytest
from fastapi.testclient import TestClient

from gaia.hub import compatibility
from gaia.ui.routers import onboarding as onboarding_mod
from gaia.ui.server import create_app


@pytest.fixture
def marker(tmp_path, monkeypatch):
    """Redirect the init marker to a temp file so tests never touch ~/.gaia."""
    path = tmp_path / ".gaia" / "chat" / "initialized"
    monkeypatch.setattr(onboarding_mod, "_INIT_MARKER", path)
    return path


@pytest.fixture
def client(marker):  # noqa: ARG001 - marker fixture applies the monkeypatch
    app = create_app(db_path=":memory:")
    return TestClient(app)


def _stub_devices(**overrides):
    base = {
        "lemonade_running": True,
        "npu_detected": True,
        "gpu_name": "Radeon 780M",
        "gpu_vram_gb": 16.0,
    }
    base.update(overrides)

    async def _probe():
        return base

    return _probe


# ── preflight ────────────────────────────────────────────────────────────


def test_preflight_full_tier_compatible(client, monkeypatch):
    monkeypatch.setattr(onboarding_mod, "_probe_lemonade_devices", _stub_devices())
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 32.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)

    resp = client.get("/api/onboarding/preflight")
    assert resp.status_code == 200
    body = resp.json()
    assert body["compatible"] is True
    assert body["blockers"] == []
    assert body["tier"] == "full"
    assert body["npu_detected"] is True
    assert body["recommended_model"] == "Gemma-4-E4B-it-GGUF"
    # NPU present ⇒ no NPU warning.
    assert not any("NPU" in w for w in body["warnings"])


def test_preflight_no_npu_emits_warning(client, monkeypatch):
    monkeypatch.setattr(
        onboarding_mod, "_probe_lemonade_devices", _stub_devices(npu_detected=False)
    )
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 16.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)

    body = client.get("/api/onboarding/preflight").json()
    assert body["compatible"] is True  # advisory, still runnable
    assert body["tier"] == "standard"
    assert any("none was detected" in w for w in body["warnings"])


def test_preflight_low_disk_is_blocker(client, monkeypatch):
    monkeypatch.setattr(onboarding_mod, "_probe_lemonade_devices", _stub_devices())
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 32.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 0.5)

    body = client.get("/api/onboarding/preflight").json()
    assert body["compatible"] is False
    assert any("disk" in b.lower() for b in body["blockers"])


def test_preflight_insufficient_ram_warns(client, monkeypatch):
    monkeypatch.setattr(onboarding_mod, "_probe_lemonade_devices", _stub_devices())
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 4.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)

    body = client.get("/api/onboarding/preflight").json()
    assert body["tier"] == "insufficient"
    assert any("RAM" in w for w in body["warnings"])
    # Low RAM is advisory, not a hard block.
    assert body["compatible"] is True


def test_preflight_lemonade_down_leaves_npu_unknown(client, monkeypatch):
    async def _probe():
        return {
            "lemonade_running": False,
            "npu_detected": None,
            "gpu_name": None,
            "gpu_vram_gb": None,
        }

    monkeypatch.setattr(onboarding_mod, "_probe_lemonade_devices", _probe)
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 16.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)

    body = client.get("/api/onboarding/preflight").json()
    assert body["npu_detected"] is None
    assert body["lemonade_running"] is False
    # Unprobed NPU falls back to the conservative "cannot verify" message.
    assert any("cannot verify NPU" in w for w in body["warnings"])


# ── status / complete ──────────────────────────────────────────────────────


def test_status_uninitialized(client, marker):
    assert not marker.exists()
    body = client.get("/api/onboarding/status").json()
    assert body["initialized"] is False
    assert body["skipped"] is False


def test_complete_then_status(client, marker):
    resp = client.post(
        "/api/onboarding/complete",
        json={"skipped": False, "completed_at": "2026-07-17T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert marker.exists()

    body = client.get("/api/onboarding/status").json()
    assert body["initialized"] is True
    assert body["skipped"] is False
    assert body["completed_at"] == "2026-07-17T00:00:00Z"


def test_complete_skipped_records_skip(client, marker):
    client.post("/api/onboarding/complete", json={"skipped": True})
    body = client.get("/api/onboarding/status").json()
    assert body["initialized"] is True
    assert body["skipped"] is True


def test_legacy_empty_marker_counts_as_initialized(client, marker):
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")
    body = client.get("/api/onboarding/status").json()
    assert body["initialized"] is True
    assert body["skipped"] is False


def test_detected_npu_flows_into_shared_checker():
    """Regression guard: the router feeds detected NPU into check_compatibility."""
    from gaia.hub.manifest import Requirements

    report = compatibility.check_compatibility(
        Requirements(npu=True), detected_npu=False
    )
    assert any("none was detected" in w for w in report.warnings)
