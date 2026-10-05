# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the first-run onboarding router (gaia.ui.routers.onboarding)."""

import pytest
from fastapi.testclient import TestClient

from gaia.hub import compatibility
from gaia.ui.routers import onboarding as onboarding_mod
from gaia.ui.server import create_app


@pytest.fixture
def gaia_home_dir(tmp_path, monkeypatch):
    """Point GAIA_HOME at a temp dir so tests never touch ~/.gaia."""
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def default_model(monkeypatch):
    """The machine's default chat model, never this developer's own config."""
    chosen = {"model": "Gemma-4-E4B-it-GGUF"}
    monkeypatch.setattr(
        onboarding_mod, "resolve_default_chat_model", lambda: chosen["model"]
    )
    return chosen


@pytest.fixture
def client(gaia_home_dir):  # noqa: ARG001 - fixture applies the env override
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


@pytest.mark.allow_network
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


@pytest.mark.allow_network
def test_preflight_recommends_the_model_chat_will_load(
    client, monkeypatch, default_model
):
    """On a machine whose default is Qwen3.6, setup pulls Qwen3.6, not Gemma.

    The wizard said "Gemma-4-E4B is downloaded and ready" on a Strix Halo whose
    chat then asked for the 23 GB Qwen3.6 nobody had pulled.
    """
    default_model["model"] = "Qwen3.6-35B-A3B-GGUF"
    monkeypatch.setattr(onboarding_mod, "_probe_lemonade_devices", _stub_devices())
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 64.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)

    body = client.get("/api/onboarding/preflight").json()

    assert body["recommended_model"] == "Qwen3.6-35B-A3B-GGUF"
    assert body["required_disk_gb"] > 23.3


@pytest.mark.allow_network
def test_preflight_blocks_when_the_default_model_does_not_fit_on_disk(
    client, monkeypatch, default_model
):
    default_model["model"] = "Qwen3.6-35B-A3B-GGUF"
    monkeypatch.setattr(onboarding_mod, "_probe_lemonade_devices", _stub_devices())
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 64.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 10.0)

    body = client.get("/api/onboarding/preflight").json()

    assert body["compatible"] is False
    assert body["blockers"]


@pytest.mark.allow_network
def test_preflight_no_npu_with_gpu_is_informational(client, monkeypatch):
    # No-NPU box with a capable GPU (the #2396 Radeon dGPU case): the Hardware
    # check must read as informational — no "agent" framing, no "install your
    # Ryzen AI NPU driver" CTA.
    monkeypatch.setattr(
        onboarding_mod, "_probe_lemonade_devices", _stub_devices(npu_detected=False)
    )
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 16.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)

    body = client.get("/api/onboarding/preflight").json()
    assert body["compatible"] is True  # advisory, still runnable
    assert body["tier"] == "standard"
    npu_warnings = [w for w in body["warnings"] if "NPU" in w]
    assert npu_warnings, "expected an informational NPU line"
    joined = " ".join(npu_warnings)
    assert "agent" not in joined.lower()
    assert "install" not in joined.lower()
    assert "optional" in joined.lower()


@pytest.mark.allow_network
def test_preflight_no_npu_no_gpu_still_warns_to_remediate(client, monkeypatch):
    # A box with neither NPU nor a detectable GPU keeps the remediation warning.
    monkeypatch.setattr(
        onboarding_mod,
        "_probe_lemonade_devices",
        _stub_devices(npu_detected=False, gpu_vram_gb=None, gpu_name=None),
    )
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 16.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)

    body = client.get("/api/onboarding/preflight").json()
    assert any("none was detected" in w for w in body["warnings"])
    # Still no "agent" framing in the onboarding context.
    assert not any("agent" in w.lower() for w in body["warnings"])


@pytest.mark.allow_network
def test_preflight_low_disk_is_blocker(client, monkeypatch):
    monkeypatch.setattr(onboarding_mod, "_probe_lemonade_devices", _stub_devices())
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 32.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 0.5)

    body = client.get("/api/onboarding/preflight").json()
    assert body["compatible"] is False
    assert any("disk" in b.lower() for b in body["blockers"])


@pytest.mark.allow_network
def test_preflight_insufficient_ram_warns(client, monkeypatch):
    monkeypatch.setattr(onboarding_mod, "_probe_lemonade_devices", _stub_devices())
    monkeypatch.setattr(compatibility, "detect_total_memory_gb", lambda: 4.0)
    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 500.0)

    body = client.get("/api/onboarding/preflight").json()
    assert body["tier"] == "insufficient"
    assert any("RAM" in w for w in body["warnings"])
    # Low RAM is advisory, not a hard block.
    assert body["compatible"] is True


@pytest.mark.allow_network
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
    assert body["lemonade_error"] is None
    assert any("cannot verify NPU" in w for w in body["warnings"])


@pytest.mark.allow_network
def test_preflight_checks_disk_under_gaia_home(client, gaia_home_dir, monkeypatch):
    monkeypatch.setattr(onboarding_mod, "_probe_lemonade_devices", _stub_devices())
    real_check = onboarding_mod.check_compatibility
    seen = {}

    def _spy(reqs, **kwargs):
        seen["install_dir"] = kwargs["install_dir"]
        return real_check(reqs, **kwargs)

    monkeypatch.setattr(onboarding_mod, "check_compatibility", _spy)
    assert client.get("/api/onboarding/preflight").status_code == 200
    assert seen["install_dir"] == gaia_home_dir


def test_detected_npu_flows_into_shared_checker():
    """Regression guard: the router feeds detected NPU into check_compatibility."""
    from gaia.hub.manifest import Requirements

    report = compatibility.check_compatibility(
        Requirements(npu=True), detected_npu=False
    )
    assert any("none was detected" in w for w in report.warnings)
